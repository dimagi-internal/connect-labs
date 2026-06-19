// tab_planning.jsx — Microplanning & Budget sub-tab
// Location-level microplans are the unit of planning: workforce requirements, resource
// allocations, campaign targets and budget all live on a microplan. Region rows are
// rollups. Campaign admins can create & manage microplans, targets and budgets;
// other roles get a read-only view.
const { useState: useStatePl } = React;

const REGION_STATUS_ORDER = ['At risk', 'Behind', 'On track', 'Planned'];
function rollupStatus(items) {
  for (const s of REGION_STATUS_ORDER)
    if (items.some((m) => m.status === s)) return s;
  return 'On track';
}
function groupByRegion(mps, D) {
  return D.REGIONS.map((r) => ({
    region: r.name,
    regionId: r.id,
    items: mps.filter((m) => m.regionId === r.id),
  })).filter((g) => g.items.length);
}

// region-grouped, expandable table
function GroupedTable({ density, columns, groups, renderHeader, renderRow }) {
  const [open, setOpen] = useStatePl(
    () => new Set(groups.map((g) => g.region)),
  );
  const toggle = (r) =>
    setOpen((s) => {
      const n = new Set(s);
      n.has(r) ? n.delete(r) : n.add(r);
      return n;
    });
  return (
    <Card padding={0}>
      <Table density={density} columns={columns}>
        {groups.flatMap((g) => {
          const isOpen = open.has(g.region);
          const rows = [
            <Row
              key={g.region + '__hdr'}
              onClick={() => toggle(g.region)}
              style={{ background: CUTC.surface }}
            >
              {renderHeader(g, isOpen)}
            </Row>,
          ];
          if (isOpen) g.items.forEach((it) => rows.push(renderRow(it)));
          return rows;
        })}
      </Table>
    </Card>
  );
}

// chevron + region label cell used by every grouped header
function RegionLabel({ region, count, open }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 9,
        fontWeight: 600,
        color: CUTC.purple,
      }}
    >
      <i
        className={'fa fa-chevron-' + (open ? 'down' : 'right')}
        style={{ fontSize: 10, color: CUTC.faint, width: 10 }}
      ></i>
      {region}{' '}
      <span
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: CUTC.muted,
          background: '#E9ECEF',
          padding: '1px 8px',
          borderRadius: 999,
        }}
      >
        {count}
      </span>
    </span>
  );
}

function PlanningTab({ density, role }) {
  const D = window.CUT_DATA;
  const canManage = window.CUT_RBAC.can(role, 'planning', 'create');
  const canEdit = window.CUT_RBAC.can(role, 'planning', 'edit');
  const toast = useToast();
  const [mps, setMps] = useStatePl(() => D.MICROPLANS.map((m) => ({ ...m })));
  const [view, setView] = useStatePl('microplans');
  const [detail, setDetail] = useStatePl(null); // drawer microplan
  const [mpForm, setMpForm] = useStatePl({ open: false, mp: null }); // create/edit
  const [targetMp, setTargetMp] = useStatePl(null);
  const [budgetMp, setBudgetMp] = useStatePl(null);

  const elapsedFrac = D.CAMPAIGN.daysElapsed / D.CAMPAIGN.daysTotal;
  const sum = (k) => mps.reduce((a, m) => a + (m[k] || 0), 0);
  const totBudget = sum('budget'),
    totSpent = sum('spent'),
    totPlanWf = sum('plannedWf'),
    totActWf = sum('actualWf');
  const totTarget = sum('target'),
    totReached = sum('reached'),
    totObjective = sum('objective');
  const totPlannedTD = sum('plannedToDate');
  const onTrack = mps.filter((m) => m.status === 'On track').length;
  const atRisk = mps.filter((m) => m.status === 'At risk').length;
  const groups = groupByRegion(mps, D);

  // ---- mutations ----
  const saveMp = (result, orig) => {
    if (orig) {
      window.campaignActions
        .updateMicroplan(orig.id, result)
        .then((res) => {
          setMps((ms) =>
            ms.map((m) => (m.id === res.microplan.id ? res.microplan : m)),
          );
          toast('Microplan updated — ' + result.lga);
        })
        .catch((e) => toast('Update failed: ' + e.message, 'danger'));
    } else {
      window.campaignActions
        .createMicroplan(result)
        .then((res) => {
          setMps((ms) => [res.microplan, ...ms]);
          toast('Microplan created — ' + result.lga + ', ' + result.region);
        })
        .catch((e) => toast('Create failed: ' + e.message, 'danger'));
    }
    setMpForm({ open: false, mp: null });
  };
  const saveTarget = (next, orig) => {
    window.campaignActions
      .setMicroplanTarget(orig.id, next.target, next.goalPct)
      .then((res) => {
        setMps((ms) =>
          ms.map((m) => (m.id === res.microplan.id ? res.microplan : m)),
        );
        setTargetMp(null);
        toast('Target updated — ' + orig.lga);
      })
      .catch((e) => toast('Target update failed: ' + e.message, 'danger'));
  };
  const saveBudget = (next, orig) => {
    window.campaignActions
      .setMicroplanBudget(orig.id, next.budget)
      .then((res) => {
        setMps((ms) =>
          ms.map((m) => (m.id === res.microplan.id ? res.microplan : m)),
        );
        setBudgetMp(null);
        toast('Budget allocation updated — ' + orig.lga);
      })
      .catch((e) => toast('Budget update failed: ' + e.message, 'danger'));
  };

  return (
    <Page max={1380}>
      <PageHead
        eyebrow="Activity · Microplanning & Budget"
        title="Microplanning & Budget"
        sub="Build and manage regional and location-level microplans — workforce requirements, resource allocations, campaign targets and budgets — and track planned versus actual throughout the campaign."
        actions={
          <div style={{ display: 'flex', gap: 10 }}>
            <Button variant="secondary" icon="download">
              Export plan
            </Button>
            {canManage ? (
              <Button
                icon="plus"
                onClick={() => setMpForm({ open: true, mp: null })}
              >
                New microplan
              </Button>
            ) : (
              <Badge tone="neutral">View-only for {role}</Badge>
            )}
          </div>
        }
      />

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 14,
          marginBottom: 18,
        }}
      >
        <Stat
          label="Budget utilization"
          value={Math.round((totSpent / totBudget) * 100) + '%'}
          icon="wallet"
          sub={D.moneyK(totSpent) + ' of ' + D.moneyK(totBudget)}
        />
        <Stat
          label="Workforce deployed"
          value={Math.round((totActWf / totPlanWf) * 100) + '%'}
          icon="people-group"
          sub={D.num(totActWf) + ' of ' + D.num(totPlanWf) + ' required'}
          delta={
            totPlanWf - totActWf > 0
              ? D.num(totPlanWf - totActWf) + ' short'
              : 'fully staffed'
          }
          deltaTone={totPlanWf - totActWf > 0 ? 'warning' : 'success'}
        />
        <Stat
          label="Coverage vs target"
          value={Math.round((totReached / totObjective) * 100) + '%'}
          icon="bullseye"
          sub={D.num(totReached) + ' of ' + D.num(totObjective) + ' objective'}
        />
        <Stat
          label="Microplans on track"
          value={onTrack + '/' + mps.length}
          icon="map-location-dot"
          delta={atRisk + ' at risk'}
          deltaTone="danger"
        />
      </div>

      <div style={{ marginBottom: 16 }}>
        <PillTabs
          active={view}
          onChange={setView}
          tabs={[
            {
              id: 'microplans',
              label: 'Microplans',
              icon: 'map-location-dot',
              count: mps.length,
            },
            { id: 'workforce', label: 'Workforce', icon: 'people-group' },
            { id: 'targets', label: 'Targets', icon: 'bullseye' },
            { id: 'budget', label: 'Budget', icon: 'wallet' },
          ]}
        />
      </div>

      {view === 'microplans' && (
        <>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 12,
            }}
          >
            <SectionTitle sub="Regional and location-level plans — workforce, resources, target and budget per location. Open a row for the full microplan.">
              All microplans
            </SectionTitle>
          </div>
          <GroupedTable
            density={density}
            groups={groups}
            columns={[
              { label: 'Location' },
              { label: 'Settlements', align: 'center' },
              { label: 'Workforce', width: 190 },
              { label: 'Target pop.', align: 'right' },
              { label: 'Coverage', width: 150 },
              { label: 'Budget used', width: 150 },
              { label: 'Status' },
            ]}
            renderHeader={(g, open) => {
              const p = g.items.reduce((a, m) => a + m.plannedWf, 0),
                a = g.items.reduce((x, m) => x + m.actualWf, 0);
              const tgt = g.items.reduce((x, m) => x + m.target, 0),
                rc = g.items.reduce((x, m) => x + m.reached, 0);
              const bud = g.items.reduce((x, m) => x + m.budget, 0),
                sp = g.items.reduce((x, m) => x + m.spent, 0);
              const set = g.items.reduce((x, m) => x + m.settlements, 0);
              return (
                <>
                  <Cell strong>
                    <RegionLabel
                      region={g.region}
                      count={g.items.length}
                      open={open}
                    />
                  </Cell>
                  <Cell align="center" mono style={{ color: CUTC.muted }}>
                    {set}
                  </Cell>
                  <Cell mono strong>
                    {D.num(a)} / {D.num(p)}
                  </Cell>
                  <Cell align="right" mono>
                    {D.num(tgt)}
                  </Cell>
                  <Cell mono strong>
                    {Math.round((rc / (tgt || 1)) * 100)}%
                  </Cell>
                  <Cell mono strong>
                    {Math.round((sp / (bud || 1)) * 100)}%
                  </Cell>
                  <Cell>
                    <MpStatus status={rollupStatus(g.items)} />
                  </Cell>
                </>
              );
            }}
            renderRow={(m) => {
              const fill = m.plannedWf ? m.actualWf / m.plannedWf : 0,
                cov = m.target ? m.reached / m.target : 0,
                util = m.budget ? m.spent / m.budget : 0;
              return (
                <Row key={m.id} onClick={() => setDetail(m)}>
                  <Cell>
                    <div style={{ paddingLeft: 19 }}>
                      <span style={{ fontWeight: 600, color: CUTC.purple }}>
                        {m.lga}
                      </span>
                      <div
                        style={{
                          fontSize: 11.5,
                          color: CUTC.muted,
                          fontFamily: 'ui-monospace, monospace',
                        }}
                      >
                        {m.id} · {m.wards} wards
                      </div>
                    </div>
                  </Cell>
                  <Cell align="center" mono>
                    {m.settlements}
                  </Cell>
                  <Cell>
                    <Progress
                      value={m.actualWf}
                      max={m.plannedWf || 1}
                      height={7}
                      right={m.actualWf + '/' + m.plannedWf}
                      color={
                        fill < 0.75
                          ? '#E13019'
                          : fill < 0.9
                          ? '#E8A317'
                          : '#1E7B33'
                      }
                    />
                  </Cell>
                  <Cell align="right" mono>
                    {D.num(m.target)}
                  </Cell>
                  <Cell>
                    <Progress
                      value={m.reached}
                      max={m.objective || 1}
                      height={7}
                      right={Math.round(cov * 100) + '%'}
                      color={
                        cov < 0.4
                          ? '#E13019'
                          : cov < 0.55
                          ? '#E8A317'
                          : '#1E7B33'
                      }
                    />
                  </Cell>
                  <Cell>
                    <Progress
                      value={m.spent}
                      max={m.budget || 1}
                      height={7}
                      right={Math.round(util * 100) + '%'}
                      color="var(--accent)"
                    />
                  </Cell>
                  <Cell>
                    <MpStatus status={m.status} />
                  </Cell>
                </Row>
              );
            }}
          />
        </>
      )}

      {view === 'workforce' && (
        <>
          <SectionTitle
            sub="Planned (required) versus actual (deployed) workforce by location, with deployment gaps highlighted."
            style={{ marginBottom: 12 }}
          >
            Workforce planning & allocation
          </SectionTitle>
          <GroupedTable
            density={density}
            groups={groups}
            columns={[
              { label: 'Location' },
              { label: 'Required', align: 'right' },
              { label: 'Deployed', align: 'right' },
              { label: 'Fill rate', width: 220 },
              { label: 'Gap', align: 'right' },
            ]}
            renderHeader={(g, open) => {
              const p = g.items.reduce((a, m) => a + m.plannedWf, 0),
                a = g.items.reduce((x, m) => x + m.actualWf, 0);
              return (
                <>
                  <Cell strong>
                    <RegionLabel
                      region={g.region}
                      count={g.items.length}
                      open={open}
                    />
                  </Cell>
                  <Cell align="right" mono>
                    {D.num(p)}
                  </Cell>
                  <Cell align="right" mono strong>
                    {D.num(a)}
                  </Cell>
                  <Cell mono strong>
                    {Math.round((a / (p || 1)) * 100)}%
                  </Cell>
                  <Cell align="right" mono>
                    <span
                      style={{
                        color: p - a > 0 ? '#B22312' : '#1E7B33',
                        fontWeight: 600,
                      }}
                    >
                      {p - a > 0 ? '−' + D.num(p - a) : '0'}
                    </span>
                  </Cell>
                </>
              );
            }}
            renderRow={(m) => {
              const fill = m.plannedWf ? m.actualWf / m.plannedWf : 0,
                gap = m.plannedWf - m.actualWf;
              return (
                <Row key={m.id} onClick={() => setDetail(m)}>
                  <Cell>
                    <div
                      style={{
                        paddingLeft: 19,
                        fontWeight: 600,
                        color: CUTC.purple,
                      }}
                    >
                      {m.lga}
                    </div>
                  </Cell>
                  <Cell align="right" mono>
                    {D.num(m.plannedWf)}
                  </Cell>
                  <Cell align="right" mono strong>
                    {D.num(m.actualWf)}
                  </Cell>
                  <Cell>
                    <Progress
                      value={m.actualWf}
                      max={m.plannedWf || 1}
                      height={8}
                      right={Math.round(fill * 100) + '%'}
                      color={
                        fill < 0.75
                          ? '#E13019'
                          : fill < 0.9
                          ? '#E8A317'
                          : '#1E7B33'
                      }
                    />
                  </Cell>
                  <Cell align="right" mono>
                    <span
                      style={{
                        color: gap > 0 ? '#B22312' : '#1E7B33',
                        fontWeight: 600,
                      }}
                    >
                      {gap > 0 ? '−' + D.num(gap) : '0'}
                    </span>
                  </Cell>
                </Row>
              );
            }}
          />
          <Card style={{ marginTop: 18 }}>
            <SectionTitle
              sub="Campaign-wide headcount by role — required versus deployed"
              style={{ marginBottom: 18 }}
            >
              Workforce composition by role
            </SectionTitle>
            {D.ROLES.map((role) => {
              const planned = mps.reduce(
                (a, m) =>
                  a +
                  ((m.roles.find((r) => r.roleId === role.id) || {}).planned ||
                    0),
                0,
              );
              const actual = mps.reduce(
                (a, m) =>
                  a +
                  ((m.roles.find((r) => r.roleId === role.id) || {}).actual ||
                    0),
                0,
              );
              const f = planned ? actual / planned : 0;
              return (
                <div
                  key={role.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 16,
                    marginBottom: 14,
                  }}
                >
                  <div
                    style={{
                      width: 150,
                      fontSize: 13,
                      fontWeight: 600,
                      color: CUTC.purple,
                    }}
                  >
                    {role.name}
                  </div>
                  <div style={{ flex: 1 }}>
                    <Progress
                      value={actual}
                      max={planned || 1}
                      height={9}
                      color={f < 0.9 ? '#E8A317' : 'var(--accent)'}
                    />
                  </div>
                  <div
                    style={{
                      width: 140,
                      textAlign: 'right',
                      fontSize: 12.5,
                      color: CUTC.body,
                      fontFamily: 'ui-monospace, monospace',
                    }}
                  >
                    <strong style={{ color: CUTC.purple }}>
                      {D.num(actual)}
                    </strong>{' '}
                    / {D.num(planned)}
                  </div>
                </div>
              );
            })}
          </Card>
        </>
      )}

      {view === 'targets' && (
        <>
          <Card
            style={{
              marginBottom: 18,
              background: CUTC.purple,
              border: 'none',
            }}
          >
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1.4fr 1fr 1fr',
                gap: 28,
                alignItems: 'center',
              }}
            >
              <div>
                <div
                  style={{
                    fontSize: 11,
                    color: 'rgba(255,255,255,.7)',
                    fontWeight: 600,
                    letterSpacing: '.1em',
                    textTransform: 'uppercase',
                  }}
                >
                  Campaign objective
                </div>
                <div
                  style={{
                    fontSize: 34,
                    fontWeight: 600,
                    color: '#fff',
                    marginTop: 6,
                    lineHeight: 1,
                  }}
                >
                  {D.num(totReached)}{' '}
                  <span
                    style={{
                      fontSize: 16,
                      fontWeight: 500,
                      color: 'rgba(255,255,255,.7)',
                    }}
                  >
                    / {D.num(totObjective)}
                  </span>
                </div>
                <div
                  style={{
                    fontSize: 13,
                    color: 'rgba(255,255,255,.8)',
                    marginTop: 6,
                  }}
                >
                  children immunized against planned objective
                </div>
              </div>
              <div>
                <div
                  style={{
                    fontSize: 11,
                    color: 'rgba(255,255,255,.7)',
                    fontWeight: 600,
                    letterSpacing: '.08em',
                    textTransform: 'uppercase',
                  }}
                >
                  Target population
                </div>
                <div
                  style={{
                    fontSize: 22,
                    fontWeight: 600,
                    color: '#fff',
                    marginTop: 6,
                  }}
                >
                  {D.num(totTarget)}
                </div>
              </div>
              <div>
                <div
                  style={{
                    fontSize: 11,
                    color: 'rgba(255,255,255,.7)',
                    fontWeight: 600,
                    letterSpacing: '.08em',
                    textTransform: 'uppercase',
                  }}
                >
                  Coverage achieved
                </div>
                <div
                  style={{
                    fontSize: 22,
                    fontWeight: 600,
                    color: '#fff',
                    marginTop: 6,
                  }}
                >
                  {Math.round((totReached / totObjective) * 100)}%
                </div>
              </div>
            </div>
          </Card>
          <SectionTitle
            sub="Planned objectives by location and actual children reached. Set targets to measure performance against plan."
            style={{ marginBottom: 12 }}
          >
            Campaign targets
          </SectionTitle>
          <GroupedTable
            density={density}
            groups={groups}
            columns={[
              { label: 'Location' },
              { label: 'Target pop.', align: 'right' },
              { label: 'Goal', align: 'center' },
              { label: 'Objective', align: 'right' },
              { label: 'Reached', align: 'right' },
              { label: 'Coverage', width: 160 },
              { label: '', align: 'right', width: 70 },
            ]}
            renderHeader={(g, open) => {
              const tgt = g.items.reduce((x, m) => x + m.target, 0),
                obj = g.items.reduce((x, m) => x + m.objective, 0),
                rc = g.items.reduce((x, m) => x + m.reached, 0);
              return (
                <>
                  <Cell strong>
                    <RegionLabel
                      region={g.region}
                      count={g.items.length}
                      open={open}
                    />
                  </Cell>
                  <Cell align="right" mono>
                    {D.num(tgt)}
                  </Cell>
                  <Cell align="center" mono style={{ color: CUTC.muted }}>
                    —
                  </Cell>
                  <Cell align="right" mono>
                    {D.num(obj)}
                  </Cell>
                  <Cell align="right" mono strong>
                    {D.num(rc)}
                  </Cell>
                  <Cell mono strong>
                    {Math.round((rc / (obj || 1)) * 100)}%
                  </Cell>
                  <Cell></Cell>
                </>
              );
            }}
            renderRow={(m) => {
              const cov = m.objective ? m.reached / m.objective : 0;
              return (
                <Row key={m.id}>
                  <Cell>
                    <div
                      style={{
                        paddingLeft: 19,
                        fontWeight: 600,
                        color: CUTC.purple,
                      }}
                    >
                      {m.lga}
                    </div>
                  </Cell>
                  <Cell align="right" mono>
                    {D.num(m.target)}
                  </Cell>
                  <Cell align="center" mono>
                    {m.goalPct}%
                  </Cell>
                  <Cell align="right" mono>
                    {D.num(m.objective)}
                  </Cell>
                  <Cell align="right" mono strong>
                    {D.num(m.reached)}
                  </Cell>
                  <Cell>
                    <Progress
                      value={m.reached}
                      max={m.objective || 1}
                      height={7}
                      right={Math.round(cov * 100) + '%'}
                      color={
                        cov < 0.4
                          ? '#E13019'
                          : cov < 0.55
                          ? '#E8A317'
                          : '#1E7B33'
                      }
                    />
                  </Cell>
                  <Cell align="right">
                    {canEdit && (
                      <Button
                        size="sm"
                        variant="ghost"
                        icon="pen"
                        onClick={() => setTargetMp(m)}
                      >
                        Set
                      </Button>
                    )}
                  </Cell>
                </Row>
              );
            }}
          />
        </>
      )}

      {view === 'budget' && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1.55fr 1fr',
            gap: 18,
            alignItems: 'start',
          }}
        >
          <div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4,1fr)',
                gap: 12,
                marginBottom: 16,
              }}
            >
              {[
                ['Allocated', D.moneyK(totBudget), null],
                [
                  'Spent',
                  D.moneyK(totSpent),
                  Math.round((totSpent / totBudget) * 100) + '% utilized',
                ],
                [
                  'Planned to date',
                  D.moneyK(totPlannedTD),
                  'day ' + D.CAMPAIGN.daysElapsed + '/' + D.CAMPAIGN.daysTotal,
                ],
                [
                  'Variance',
                  (totSpent - totPlannedTD >= 0 ? '+' : '−') +
                    D.moneyK(Math.abs(totSpent - totPlannedTD)),
                  totSpent - totPlannedTD >= 0 ? 'over plan' : 'under plan',
                ],
              ].map(([l, v, s]) => (
                <div
                  key={l}
                  style={{
                    background: '#fff',
                    border: '1px solid ' + CUTC.border,
                    borderRadius: 10,
                    padding: '14px 16px',
                  }}
                >
                  <div
                    style={{
                      fontSize: 10.5,
                      color: CUTC.muted,
                      fontWeight: 600,
                      textTransform: 'uppercase',
                      letterSpacing: '.05em',
                    }}
                  >
                    {l}
                  </div>
                  <div
                    style={{
                      fontSize: 19,
                      fontWeight: 600,
                      color: CUTC.purple,
                      marginTop: 5,
                    }}
                  >
                    {v}
                  </div>
                  {s && (
                    <div
                      style={{ fontSize: 11, color: CUTC.muted, marginTop: 2 }}
                    >
                      {s}
                    </div>
                  )}
                </div>
              ))}
            </div>
            <SectionTitle
              sub="Allocation, utilization and planned-versus-actual expenditure by location."
              style={{ marginBottom: 12 }}
            >
              Budget by location
            </SectionTitle>
            <GroupedTable
              density={density}
              groups={groups}
              columns={[
                { label: 'Location' },
                { label: 'Allocated', align: 'right' },
                { label: 'Spent', align: 'right' },
                { label: 'Utilization', width: 150 },
                { label: 'Variance', align: 'right' },
                { label: '', align: 'right', width: 70 },
              ]}
              renderHeader={(g, open) => {
                const bud = g.items.reduce((x, m) => x + m.budget, 0),
                  sp = g.items.reduce((x, m) => x + m.spent, 0),
                  ptd = g.items.reduce((x, m) => x + m.plannedToDate, 0);
                const vr = sp - ptd;
                return (
                  <>
                    <Cell strong>
                      <RegionLabel
                        region={g.region}
                        count={g.items.length}
                        open={open}
                      />
                    </Cell>
                    <Cell align="right" mono>
                      {D.moneyK(bud)}
                    </Cell>
                    <Cell align="right" mono strong>
                      {D.moneyK(sp)}
                    </Cell>
                    <Cell mono strong>
                      {Math.round((sp / (bud || 1)) * 100)}%
                    </Cell>
                    <Cell align="right" mono>
                      <span
                        style={{
                          color: vr > 0 ? '#B22312' : '#1E7B33',
                          fontWeight: 600,
                        }}
                      >
                        {(vr >= 0 ? '+' : '−') + D.moneyK(Math.abs(vr))}
                      </span>
                    </Cell>
                    <Cell></Cell>
                  </>
                );
              }}
              renderRow={(m) => {
                const util = m.budget ? m.spent / m.budget : 0,
                  vr = m.spent - m.plannedToDate;
                return (
                  <Row key={m.id}>
                    <Cell>
                      <div
                        style={{
                          paddingLeft: 19,
                          fontWeight: 600,
                          color: CUTC.purple,
                        }}
                      >
                        {m.lga}
                      </div>
                    </Cell>
                    <Cell align="right" mono>
                      {D.moneyK(m.budget)}
                    </Cell>
                    <Cell align="right" mono>
                      {D.moneyK(m.spent)}
                    </Cell>
                    <Cell>
                      <Progress
                        value={m.spent}
                        max={m.budget || 1}
                        height={7}
                        right={Math.round(util * 100) + '%'}
                        color={util > 0.95 ? '#E13019' : 'var(--accent)'}
                      />
                    </Cell>
                    <Cell align="right" mono>
                      <span
                        style={{
                          color: vr > 0 ? '#B22312' : '#1E7B33',
                          fontWeight: 600,
                        }}
                      >
                        {(vr >= 0 ? '+' : '−') + D.moneyK(Math.abs(vr))}
                      </span>
                    </Cell>
                    <Cell align="right">
                      {canEdit && (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon="pen"
                          onClick={() => setBudgetMp(m)}
                        >
                          Edit
                        </Button>
                      )}
                    </Cell>
                  </Row>
                );
              }}
            />
          </div>
          <Card>
            <SectionTitle
              sub="Estimated spend by category this round"
              style={{ marginBottom: 18 }}
            >
              Expenditure breakdown
            </SectionTitle>
            {[
              ['Worker payments', 0.58, '#5D70D2'],
              ['Vaccine & cold chain', 0.21, '#01A2A9'],
              ['Logistics & transport', 0.12, '#9A5183'],
              ['Social mobilization', 0.06, '#694AAA'],
              ['Other', 0.03, '#ADB5BD'],
            ].map(([l, v, c]) => (
              <div key={l} style={{ marginBottom: 15 }}>
                <Progress
                  value={v * 100}
                  max={100}
                  color={c}
                  height={9}
                  label={l}
                  right={D.moneyK(totSpent * v)}
                />
              </div>
            ))}
            <div
              style={{
                borderTop: '1px solid ' + CUTC.borderSoft,
                marginTop: 18,
                paddingTop: 14,
                display: 'flex',
                justifyContent: 'space-between',
              }}
            >
              <span style={{ fontSize: 13, color: CUTC.body, fontWeight: 500 }}>
                Total spent
              </span>
              <span
                style={{
                  fontSize: 15,
                  color: CUTC.purple,
                  fontWeight: 600,
                  fontFamily: 'ui-monospace, monospace',
                }}
              >
                {D.moneyK(totSpent)}
              </span>
            </div>
            <div
              style={{
                marginTop: 16,
                padding: '12px 14px',
                background: CUTC.surface,
                borderRadius: 10,
                fontSize: 12.5,
                color: CUTC.muted,
                display: 'flex',
                gap: 8,
                alignItems: 'flex-start',
              }}
            >
              <i
                className="fa fa-circle-info"
                style={{ color: 'var(--accent)', marginTop: 2 }}
              ></i>
              <span>
                Variance compares actual spend to a straight-line plan at day{' '}
                {D.CAMPAIGN.daysElapsed} of {D.CAMPAIGN.daysTotal}. Positive =
                ahead of planned burn.
              </span>
            </div>
          </Card>
        </div>
      )}

      <MicroplanDrawer
        mp={detail}
        role={role}
        onClose={() => setDetail(null)}
        onEdit={(m) => {
          setDetail(null);
          setMpForm({ open: true, mp: m });
        }}
      />
      <MicroplanModal
        open={mpForm.open}
        mp={mpForm.mp}
        onClose={() => setMpForm({ open: false, mp: null })}
        onSave={saveMp}
      />
      <TargetModal
        open={!!targetMp}
        mp={targetMp}
        onClose={() => setTargetMp(null)}
        onSave={saveTarget}
      />
      <BudgetModal
        open={!!budgetMp}
        mp={budgetMp}
        onClose={() => setBudgetMp(null)}
        onSave={saveBudget}
      />
    </Page>
  );
}

Object.assign(window, { PlanningTab });
