// tab_activity.jsx — Activity tab
const { useState: useStateA } = React;

function ActivityDetails({ density, role }) {
  const D = window.CUT_DATA;
  const toast = useToast();
  const canManage = window.CUT_RBAC.can(role, 'activities', 'create');
  const [acts, setActs] = useStateA(D.ACTIVITIES.map((a) => ({ ...a })));
  const [detail, setDetail] = useStateA(null);
  const [createOpen, setCreateOpen] = useStateA(false);
  const [statusF, setStatusF] = useStateA('all');

  const statusTone = {
    Active: 'success',
    'At risk': 'danger',
    Planned: 'info',
    Completed: 'neutral',
  };
  const filtered = acts.filter(
    (a) => statusF === 'all' || a.status === statusF,
  );
  const tabs = [
    { id: 'all', label: 'All', count: acts.length },
    ...['Active', 'At risk', 'Planned', 'Completed'].map((s) => ({
      id: s,
      label: s,
      count: acts.filter((a) => a.status === s).length,
    })),
  ];

  const totalWorkers = acts.reduce((a, x) => a + x.workers, 0);
  const totalReached = acts.reduce((a, x) => a + x.reached, 0);
  const totalTarget = acts.reduce((a, x) => a + x.target, 0);

  return (
    <Page max={1400}>
      <PageHead
        eyebrow="Activity"
        title="Activity Details"
        sub="Every distribution activity, its donor, coverage and assigned workforce. Create activities here and sync them to CommCare."
        actions={
          canManage ? (
            <Button icon="plus" onClick={() => setCreateOpen(true)}>
              New activity
            </Button>
          ) : (
            <Badge tone="neutral">View-only for {role}</Badge>
          )
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
          label="Activities"
          value={acts.length}
          icon="list-check"
          sub={acts.filter((a) => a.status === 'Active').length + ' active'}
        />
        <Stat
          label="Workers assigned"
          value={D.num(totalWorkers)}
          icon="people-group"
        />
        <Stat
          label="Coverage"
          value={Math.round((totalReached / totalTarget) * 100) + '%'}
          icon="bullseye"
          sub={D.num(totalReached) + ' / ' + D.num(totalTarget)}
          delta="vs target"
          deltaTone="primary"
        />
        <Stat
          label="Synced to CommCare"
          value={acts.filter((a) => a.synced).length + '/' + acts.length}
          icon="rotate"
          delta={acts.filter((a) => !a.synced).length + ' pending'}
          deltaTone="warning"
        />
      </div>

      <div style={{ marginBottom: 14 }}>
        <PillTabs tabs={tabs} active={statusF} onChange={setStatusF} />
      </div>

      <Card padding={0}>
        <Table
          density={density}
          columns={[
            { label: 'Activity' },
            { label: 'Donor' },
            { label: 'Timeframe' },
            { label: 'Requests', align: 'right' },
            { label: 'Workers', align: 'right' },
            { label: 'Coverage', width: 180 },
            { label: 'Status' },
            { label: 'Sync' },
          ]}
        >
          {filtered.map((a) => (
            <Row key={a.id} onClick={() => setDetail(a)}>
              <Cell>
                <div style={{ fontWeight: 600, color: CUTC.purple }}>
                  {a.name}
                </div>
                <div
                  style={{
                    fontSize: 11.5,
                    color: CUTC.muted,
                    fontFamily: 'ui-monospace, monospace',
                  }}
                >
                  {a.id} · {a.region}
                </div>
              </Cell>
              <Cell>{a.donor}</Cell>
              <Cell mono style={{ fontSize: 12.5 }}>
                {a.start} – {a.end}
              </Cell>
              <Cell align="right" mono>
                {D.num(a.requests)}
              </Cell>
              <Cell align="right" mono strong>
                {a.workers}
              </Cell>
              <Cell>
                <Progress
                  value={a.reached}
                  max={a.target}
                  height={7}
                  right={Math.round((a.reached / a.target) * 100) + '%'}
                  color={a.status === 'At risk' ? '#E13019' : 'var(--accent)'}
                />
              </Cell>
              <Cell>
                <Badge
                  tone={statusTone[a.status]}
                  dot={a.status === 'Active' || a.status === 'At risk'}
                >
                  {a.status}
                </Badge>
              </Cell>
              <Cell>
                {a.synced ? (
                  <span
                    style={{
                      color: '#1E7B33',
                      fontSize: 12.5,
                      fontWeight: 600,
                    }}
                  >
                    <i className="fa fa-check-circle"></i> Synced
                  </span>
                ) : canManage ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    icon="rotate"
                    onClick={(e) => {
                      e.stopPropagation();
                      window.campaignActions
                        .syncActivity(a.id)
                        .then((res) => {
                          setActs((as) =>
                            as.map((x) =>
                              x.id === res.activity.id ? res.activity : x,
                            ),
                          );
                          toast(a.id + ' synced to CommCare');
                        })
                        .catch((e) =>
                          toast('Sync failed: ' + e.message, 'danger'),
                        );
                    }}
                  >
                    Sync
                  </Button>
                ) : (
                  <Badge tone="warning">Not synced</Badge>
                )}
              </Cell>
            </Row>
          ))}
        </Table>
      </Card>

      <ActivityDrawer act={detail} onClose={() => setDetail(null)} />
      <CreateActivityModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={(a, sync) => {
          window.campaignActions
            .createActivity({
              name: a.name,
              donor: a.donor,
              region: a.region,
              start: a.start,
              end: a.end,
              target: a.target,
              sync: sync,
            })
            .then((res) => {
              setActs((as) => [res.activity, ...as]);
              toast(
                sync
                  ? 'Activity created & synced to CommCare'
                  : 'Activity created (not synced)',
              );
              setCreateOpen(false);
            })
            .catch((e) => toast('Create failed: ' + e.message, 'danger'));
        }}
      />
    </Page>
  );
}

function ActivityDrawer({ act, onClose }) {
  const D = window.CUT_DATA;
  if (!act) return null;
  const assigned = D.WORKERS.filter((w) => w.region === act.region).slice(0, 8);
  const byRole = {};
  D.WORKERS.filter((w) => w.region === act.region).forEach(
    (w) => (byRole[w.role] = (byRole[w.role] || 0) + 1),
  );
  return (
    <Drawer open={!!act} onClose={onClose} width={720}>
      <div
        style={{
          padding: '20px 26px',
          borderBottom: '1px solid ' + CUTC.border,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
        }}
      >
        <div>
          <div
            style={{
              fontSize: 11,
              color: 'var(--accent)',
              fontWeight: 600,
              letterSpacing: '.1em',
              textTransform: 'uppercase',
            }}
          >
            {act.id} · {act.region}
          </div>
          <h2
            style={{
              margin: '4px 0 0',
              fontSize: 20,
              color: CUTC.purple,
              fontWeight: 600,
            }}
          >
            {act.name}
          </h2>
        </div>
        <button
          onClick={onClose}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: CUTC.muted,
            fontSize: 18,
            padding: 4,
          }}
        >
          <i className="fa fa-times"></i>
        </button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 26 }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 12,
            marginBottom: 24,
          }}
        >
          {[
            ['Donor', act.donor],
            ['Timeframe', act.start + ' – ' + act.end],
            ['Requests', D.num(act.requests)],
            ['Children reached', D.num(act.reached)],
            ['Target', D.num(act.target)],
            ['Coverage', Math.round((act.reached / act.target) * 100) + '%'],
          ].map(([l, v]) => (
            <div
              key={l}
              style={{
                background: CUTC.surface,
                borderRadius: 10,
                padding: '13px 15px',
                border: '1px solid ' + CUTC.border,
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
                  fontSize: 16,
                  fontWeight: 600,
                  color: CUTC.purple,
                  marginTop: 4,
                }}
              >
                {v}
              </div>
            </div>
          ))}
        </div>
        <SectionTitle
          sub="Workforce composition for this activity"
          style={{ marginBottom: 14 }}
        >
          Worker assignments
        </SectionTitle>
        <div
          style={{
            display: 'flex',
            gap: 10,
            flexWrap: 'wrap',
            marginBottom: 18,
          }}
        >
          {Object.entries(byRole).map(([r, n]) => (
            <Badge key={r} tone="primary">
              {r}: {n}
            </Badge>
          ))}
        </div>
        <Card padding={0}>
          <Table
            density="compact"
            columns={[
              { label: 'Worker' },
              { label: 'Role' },
              { label: 'Days', align: 'center' },
              { label: 'KYC' },
            ]}
          >
            {assigned.map((w) => (
              <Row key={w.id}>
                <Cell>
                  <div
                    style={{ display: 'flex', alignItems: 'center', gap: 10 }}
                  >
                    <Avatar name={w.name} size={28} />
                    <span style={{ fontWeight: 600, color: CUTC.purple }}>
                      {w.name}
                    </span>
                  </div>
                </Cell>
                <Cell>{w.role}</Cell>
                <Cell align="center" mono>
                  {w.daysWorked}
                </Cell>
                <Cell>
                  <KycBadge status={w.kyc} />
                </Cell>
              </Row>
            ))}
          </Table>
        </Card>
      </div>
    </Drawer>
  );
}

function CreateActivityModal({ open, onClose, onCreate }) {
  const D = window.CUT_DATA;
  const [name, setName] = useStateA('');
  const [donor, setDonor] = useStateA(D.DONORS[0].short);
  const [region, setRegion] = useStateA(D.REGIONS[0].name);
  const [start, setStart] = useStateA('');
  const [end, setEnd] = useStateA('');
  const [target, setTarget] = useStateA('');
  const [sync, setSync] = useStateA(true);
  React.useEffect(() => {
    if (open) {
      setName('');
      setTarget('');
      setStart('');
      setEnd('');
      setSync(true);
    }
  }, [open]);
  const valid = name.trim().length > 2;
  return (
    <Modal
      open={open}
      onClose={onClose}
      width={580}
      title="Create activity"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon="plus"
            disabled={!valid}
            onClick={() =>
              onCreate(
                {
                  name,
                  donor,
                  region,
                  start: start || 'Jun 3',
                  end: end || 'Jun 14',
                  target: parseInt(target) || 100000,
                },
                sync,
              )
            }
          >
            Create activity
          </Button>
        </>
      }
    >
      <Field label="Activity name">
        <TextInput
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Door-to-door catch-up — Bauchi"
        />
      </Field>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <Field label="Donor">
          <Select value={donor} onChange={(e) => setDonor(e.target.value)}>
            {D.DONORS.map((d) => (
              <option key={d.id}>{d.short}</option>
            ))}
          </Select>
        </Field>
        <Field label="Region">
          <Select value={region} onChange={(e) => setRegion(e.target.value)}>
            {D.REGIONS.map((r) => (
              <option key={r.id}>{r.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Start date">
          <TextInput
            value={start}
            onChange={(e) => setStart(e.target.value)}
            placeholder="Jun 3"
          />
        </Field>
        <Field label="End date">
          <TextInput
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            placeholder="Jun 14"
          />
        </Field>
      </div>
      <Field label="Target population">
        <TextInput
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="e.g. 320000"
        />
      </Field>
      <div
        onClick={() => setSync((s) => !s)}
        style={{
          display: 'flex',
          gap: 12,
          alignItems: 'flex-start',
          padding: '14px 16px',
          background: sync ? 'var(--accent-soft)' : CUTC.surface,
          border: '1px solid ' + (sync ? 'var(--accent)' : CUTC.border),
          borderRadius: 10,
          cursor: 'pointer',
          marginTop: 6,
        }}
      >
        <Check checked={sync} onChange={() => setSync((s) => !s)} />
        <div>
          <div
            style={{
              fontSize: 13.5,
              fontWeight: 600,
              color: CUTC.purple,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <img
              src={
                (window.__resources && window.__resources.commcarePinwheel) ||
                'assets/commcare-pinwheel.png'
              }
              style={{ height: 16 }}
            />{' '}
            Sync to CommCare via API
          </div>
          <div
            style={{
              fontSize: 12,
              color: CUTC.body,
              marginTop: 3,
              lineHeight: 1.4,
            }}
          >
            Creates the matching case/activity in CommCare so field teams see it
            on mobile. Leave off to manage manually in CommCare.
          </div>
        </div>
      </div>
    </Modal>
  );
}

Object.assign(window, { ActivityDetails });
