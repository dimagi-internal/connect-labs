// tab_planning_detail.jsx — Microplan drawer + create/edit + target/budget modals
const { useState: useStateMd, useEffect: useEffectMd } = React;

const MP_STATUS_TONE = {
  'On track': 'success',
  Behind: 'warning',
  'At risk': 'danger',
  Planned: 'info',
};
function MpStatus({ status }) {
  return (
    <Badge
      tone={MP_STATUS_TONE[status] || 'neutral'}
      dot={status === 'At risk' || status === 'On track'}
    >
      {status}
    </Badge>
  );
}

// small labelled tile used in the drawer
function MpTile({ label, value, sub }) {
  return (
    <div
      style={{
        background: CUTC.surface,
        borderRadius: 10,
        padding: '12px 14px',
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
        {label}
      </div>
      <div
        style={{
          fontSize: 17,
          fontWeight: 600,
          color: CUTC.purple,
          marginTop: 4,
        }}
      >
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 11.5, color: CUTC.muted, marginTop: 2 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function DrawerSection({ title, sub, action, children }) {
  return (
    <div style={{ marginTop: 26 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-end',
          marginBottom: 12,
        }}
      >
        <div>
          <h3
            style={{
              margin: 0,
              fontSize: 14,
              color: CUTC.purple,
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '.04em',
            }}
          >
            {title}
          </h3>
          {sub && (
            <p style={{ margin: '3px 0 0', fontSize: 12.5, color: CUTC.muted }}>
              {sub}
            </p>
          )}
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

// ---------- MICROPLAN DETAIL DRAWER ----------
function MicroplanDrawer({ mp, role, onClose, onEdit }) {
  const D = window.CUT_DATA;
  if (!mp) return null;
  const canManage = window.CUT_RBAC.can(role, 'planning', 'edit');
  const fill = mp.plannedWf ? mp.actualWf / mp.plannedWf : 0;
  const cov = mp.target ? mp.reached / mp.target : 0;
  const util = mp.budget ? mp.spent / mp.budget : 0;
  const variance = mp.spent - mp.plannedToDate;
  const wfGap = mp.plannedWf - mp.actualWf;

  return (
    <Drawer open={!!mp} onClose={onClose} width={760}>
      <div
        style={{
          padding: '20px 28px',
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
            {mp.id} · {mp.region} state
          </div>
          <h2
            style={{
              margin: '4px 0 0',
              fontSize: 21,
              color: CUTC.purple,
              fontWeight: 600,
            }}
          >
            {mp.lga} LGA
          </h2>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              marginTop: 8,
            }}
          >
            <MpStatus status={mp.status} />
            <span style={{ fontSize: 12, color: CUTC.muted }}>
              Owner {mp.owner} · updated {mp.updated}
            </span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {canManage && (
            <Button
              variant="secondary"
              size="sm"
              icon="pen"
              onClick={() => onEdit(mp)}
            >
              Edit microplan
            </Button>
          )}
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
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 28px 32px' }}>
        <DrawerSection
          title="Catchment & targets"
          sub="Geographic scope and the campaign objective for this location"
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(4, 1fr)',
              gap: 12,
            }}
          >
            <MpTile label="Settlements" value={mp.settlements} />
            <MpTile label="Wards" value={mp.wards} />
            <MpTile
              label="Target pop."
              value={D.num(mp.target)}
              sub="eligible children"
            />
            <MpTile
              label="Coverage goal"
              value={mp.goalPct + '%'}
              sub={D.num(mp.objective) + ' to immunize'}
            />
          </div>
          <div
            style={{
              marginTop: 14,
              background: '#fff',
              border: '1px solid ' + CUTC.border,
              borderRadius: 10,
              padding: '16px 18px',
            }}
          >
            <Progress
              value={mp.reached}
              max={mp.objective}
              height={10}
              label={'Children reached vs objective'}
              right={Math.round(cov * 100) + '%'}
              color={cov < 0.4 ? '#E13019' : cov < 0.55 ? '#E8A317' : '#1E7B33'}
            />
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                marginTop: 8,
                fontSize: 12,
                color: CUTC.muted,
              }}
            >
              <span>{D.num(mp.reached)} reached</span>
              <span>
                {D.num(Math.max(0, mp.objective - mp.reached))} remaining
              </span>
            </div>
          </div>
        </DrawerSection>

        <DrawerSection
          title="Workforce requirements"
          sub="Planned headcount by role versus workers actually deployed"
        >
          <Card padding={0}>
            <Table
              density="compact"
              columns={[
                { label: 'Role' },
                { label: 'Day rate', align: 'right' },
                { label: 'Required', align: 'right' },
                { label: 'Deployed', align: 'right' },
                { label: 'Fill', width: 150 },
                { label: 'Gap', align: 'right' },
              ]}
            >
              {mp.roles.map((r) => {
                const rf = r.planned ? r.actual / r.planned : 1;
                const gap = r.planned - r.actual;
                return (
                  <Row key={r.roleId}>
                    <Cell strong>{r.role}</Cell>
                    <Cell align="right" mono>
                      {D.money(r.rate)}
                    </Cell>
                    <Cell align="right" mono>
                      {r.planned}
                    </Cell>
                    <Cell align="right" mono strong>
                      {r.actual}
                    </Cell>
                    <Cell>
                      <Progress
                        value={r.actual}
                        max={r.planned || 1}
                        height={7}
                        right={Math.round(rf * 100) + '%'}
                        color={
                          rf < 0.75
                            ? '#E13019'
                            : rf < 0.9
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
                        {gap > 0 ? '−' + gap : '0'}
                      </span>
                    </Cell>
                  </Row>
                );
              })}
            </Table>
            <div
              style={{
                padding: '12px 16px',
                borderTop: '1px solid ' + CUTC.border,
                background: CUTC.surface,
                display: 'flex',
                justifyContent: 'space-between',
                fontSize: 13,
              }}
            >
              <span style={{ fontWeight: 600, color: CUTC.purple }}>
                Total workforce
              </span>
              <span
                style={{
                  fontFamily: 'ui-monospace, monospace',
                  color: CUTC.body,
                }}
              >
                <strong style={{ color: CUTC.purple }}>{mp.actualWf}</strong>{' '}
                deployed of {mp.plannedWf} required{' '}
                {wfGap > 0 && (
                  <span style={{ color: '#B22312', fontWeight: 600 }}>
                    · {wfGap} short
                  </span>
                )}
              </span>
            </div>
          </Card>
        </DrawerSection>

        <DrawerSection
          title="Resource allocations"
          sub="Commodities and logistics planned for this location"
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: 12,
            }}
          >
            <div
              style={{
                background: '#fff',
                border: '1px solid ' + CUTC.border,
                borderRadius: 10,
                padding: '14px 16px',
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  color: CUTC.muted,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '.05em',
                }}
              >
                <i
                  className="fa fa-syringe"
                  style={{ color: 'var(--accent)', marginRight: 7 }}
                ></i>
                Vaccine doses
              </div>
              <div
                style={{
                  fontSize: 18,
                  fontWeight: 600,
                  color: CUTC.purple,
                  margin: '8px 0 6px',
                }}
              >
                {D.num(mp.doses)}
              </div>
              <Progress
                value={mp.dosesUsed}
                max={mp.doses || 1}
                height={6}
                right={Math.round((mp.dosesUsed / (mp.doses || 1)) * 100) + '%'}
                color="var(--accent)"
              />
              <div style={{ fontSize: 11.5, color: CUTC.muted, marginTop: 6 }}>
                {D.num(mp.dosesUsed)} used
              </div>
            </div>
            <MpTile
              label="Cold-chain boxes"
              value={mp.coldBoxes}
              sub="vaccine carriers + ice packs"
            />
            <MpTile
              label="Vehicles"
              value={mp.vehicles}
              sub="teams + supervision"
            />
          </div>
        </DrawerSection>

        <DrawerSection
          title="Budget"
          sub="Allocation, utilization and planned-versus-actual expenditure to date"
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(4, 1fr)',
              gap: 12,
              marginBottom: 14,
            }}
          >
            <MpTile label="Allocated" value={D.moneyK(mp.budget)} />
            <MpTile
              label="Spent"
              value={D.moneyK(mp.spent)}
              sub={Math.round(util * 100) + '% utilized'}
            />
            <MpTile
              label="Planned to date"
              value={D.moneyK(mp.plannedToDate)}
              sub={
                'day ' + D.CAMPAIGN.daysElapsed + ' of ' + D.CAMPAIGN.daysTotal
              }
            />
            <MpTile
              label="Variance"
              value={(variance >= 0 ? '+' : '−') + D.moneyK(Math.abs(variance))}
              sub={variance >= 0 ? 'over plan' : 'under plan'}
            />
          </div>
          <div
            style={{
              background: '#fff',
              border: '1px solid ' + CUTC.border,
              borderRadius: 10,
              padding: '16px 18px',
            }}
          >
            <Progress
              value={mp.spent}
              max={mp.budget || 1}
              height={10}
              label="Budget utilization"
              right={D.moneyK(mp.spent) + ' / ' + D.moneyK(mp.budget)}
              color={util > 0.95 ? '#E13019' : 'var(--accent)'}
            />
            <div style={{ fontSize: 12, color: CUTC.muted, marginTop: 8 }}>
              {D.moneyK(Math.max(0, mp.budget - mp.spent))} remaining
            </div>
          </div>
        </DrawerSection>
      </div>
    </Drawer>
  );
}

// ---------- small number field ----------
function NumField({ label, value, onChange, prefix, suffix, help, step }) {
  return (
    <Field label={label} help={help}>
      <div style={{ position: 'relative' }}>
        {prefix && (
          <span
            style={{
              position: 'absolute',
              left: 11,
              top: '50%',
              transform: 'translateY(-50%)',
              color: CUTC.muted,
              fontSize: 13,
            }}
          >
            {prefix}
          </span>
        )}
        <TextInput
          type="number"
          min="0"
          step={step || 1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          style={{
            paddingLeft: prefix ? 26 : 12,
            paddingRight: suffix ? 30 : 12,
            fontFamily: 'ui-monospace, monospace',
          }}
        />
        {suffix && (
          <span
            style={{
              position: 'absolute',
              right: 11,
              top: '50%',
              transform: 'translateY(-50%)',
              color: CUTC.muted,
              fontSize: 13,
            }}
          >
            {suffix}
          </span>
        )}
      </div>
    </Field>
  );
}

// ---------- CREATE / EDIT MICROPLAN MODAL ----------
function MicroplanModal({ open, mp, onClose, onSave }) {
  const D = window.CUT_DATA;
  const editing = !!mp;
  const blankRoles = D.ROLES.map((r) => ({
    roleId: r.id,
    role: r.name,
    rate: r.rate,
    planned: 0,
    actual: 0,
  }));
  const [region, setRegion] = useStateMd(D.REGIONS[0].name);
  const [lga, setLga] = useStateMd(D.REGIONS[0].lgas[0]);
  const [settlements, setSettlements] = useStateMd('');
  const [wards, setWards] = useStateMd('');
  const [target, setTarget] = useStateMd('');
  const [goal, setGoal] = useStateMd('95');
  const [roles, setRoles] = useStateMd(blankRoles);
  const [doses, setDoses] = useStateMd('');
  const [coldBoxes, setColdBoxes] = useStateMd('');
  const [vehicles, setVehicles] = useStateMd('');
  const [budget, setBudget] = useStateMd('');

  useEffectMd(() => {
    if (!open) return;
    if (mp) {
      setRegion(mp.region);
      setLga(mp.lga);
      setSettlements(String(mp.settlements));
      setWards(String(mp.wards));
      setTarget(String(mp.target));
      setGoal(String(mp.goalPct));
      setRoles(
        D.ROLES.map((r) => {
          const ex = mp.roles.find((x) => x.roleId === r.id);
          return {
            roleId: r.id,
            role: r.name,
            rate: r.rate,
            planned: ex ? ex.planned : 0,
            actual: ex ? ex.actual : 0,
          };
        }),
      );
      setDoses(String(mp.doses));
      setColdBoxes(String(mp.coldBoxes));
      setVehicles(String(mp.vehicles));
      setBudget(String(mp.budget));
    } else {
      setRegion(D.REGIONS[0].name);
      setLga(D.REGIONS[0].lgas[0]);
      setSettlements('');
      setWards('');
      setTarget('');
      setGoal('95');
      setRoles(
        D.ROLES.map((r) => ({
          roleId: r.id,
          role: r.name,
          rate: r.rate,
          planned: 0,
          actual: 0,
        })),
      );
      setDoses('');
      setColdBoxes('');
      setVehicles('');
      setBudget('');
    }
  }, [open]);

  const regionObj = D.REGIONS.find((r) => r.name === region) || D.REGIONS[0];
  const plannedWf = roles.reduce((a, r) => a + (parseInt(r.planned) || 0), 0);
  const wfCost = roles.reduce(
    (a, r) => a + (parseInt(r.planned) || 0) * r.rate,
    0,
  );
  const valid =
    lga &&
    (parseInt(target) || 0) > 0 &&
    plannedWf > 0 &&
    (parseInt(budget) || 0) > 0;

  const setRolePlanned = (id, v) =>
    setRoles((rs) =>
      rs.map((r) =>
        r.roleId === id
          ? { ...r, planned: v === '' ? 0 : parseInt(v) || 0 }
          : r,
      ),
    );

  const submit = () => {
    const tgt = parseInt(target) || 0;
    const result = {
      regionId: regionObj.id,
      region,
      lga,
      settlements: parseInt(settlements) || 0,
      wards: parseInt(wards) || 0,
      target: tgt,
      goalPct: parseInt(goal) || 95,
      objective: Math.round((tgt * (parseInt(goal) || 95)) / 100),
      roles: roles.map((r) => ({
        ...r,
        planned: parseInt(r.planned) || 0,
        actual: editing ? r.actual : 0,
      })),
      plannedWf,
      doses: parseInt(doses) || 0,
      coldBoxes: parseInt(coldBoxes) || 0,
      vehicles: parseInt(vehicles) || 0,
      budget: parseInt(budget) || 0,
    };
    onSave(result, mp);
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      width={680}
      title={editing ? 'Edit microplan — ' + mp.lga : 'New microplan'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon={editing ? 'check' : 'plus'}
            disabled={!valid}
            onClick={submit}
          >
            {editing ? 'Save changes' : 'Create microplan'}
          </Button>
        </>
      }
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: 'var(--accent)',
          letterSpacing: '.1em',
          textTransform: 'uppercase',
          marginBottom: 10,
        }}
      >
        Location
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <Field label="Region (state)">
          <Select
            value={region}
            disabled={editing}
            onChange={(e) => {
              setRegion(e.target.value);
              const ro = D.REGIONS.find((r) => r.name === e.target.value);
              setLga(ro.lgas[0]);
            }}
          >
            {D.REGIONS.map((r) => (
              <option key={r.id}>{r.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Location (LGA)">
          <Select
            value={lga}
            disabled={editing}
            onChange={(e) => setLga(e.target.value)}
          >
            {regionObj.lgas.map((l) => (
              <option key={l}>{l}</option>
            ))}
          </Select>
        </Field>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <NumField
          label="Settlements"
          value={settlements}
          onChange={setSettlements}
        />
        <NumField label="Wards" value={wards} onChange={setWards} />
      </div>

      <div
        style={{ height: 1, background: CUTC.border, margin: '8px 0 18px' }}
      ></div>
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: 'var(--accent)',
          letterSpacing: '.1em',
          textTransform: 'uppercase',
          marginBottom: 10,
        }}
      >
        Campaign target
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <NumField
          label="Target population"
          value={target}
          onChange={setTarget}
          help="eligible children in catchment"
        />
        <NumField
          label="Coverage goal"
          value={goal}
          onChange={setGoal}
          suffix="%"
          help={
            'objective: ' +
            D.num(
              Math.round(
                ((parseInt(target) || 0) * (parseInt(goal) || 95)) / 100,
              ),
            ) +
            ' children'
          }
        />
      </div>

      <div
        style={{ height: 1, background: CUTC.border, margin: '8px 0 18px' }}
      ></div>
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: 'var(--accent)',
          letterSpacing: '.1em',
          textTransform: 'uppercase',
          marginBottom: 10,
        }}
      >
        Workforce requirements
      </div>
      <div
        style={{
          border: '1px solid ' + CUTC.border,
          borderRadius: 10,
          overflow: 'hidden',
          marginBottom: 16,
        }}
      >
        {roles.map((r, i) => (
          <div
            key={r.roleId}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '10px 14px',
              borderBottom:
                i < roles.length - 1 ? '1px solid ' + CUTC.borderSoft : 'none',
            }}
          >
            <div style={{ flex: 1 }}>
              <div
                style={{ fontSize: 13.5, fontWeight: 600, color: CUTC.purple }}
              >
                {r.role}
              </div>
              <div style={{ fontSize: 11.5, color: CUTC.muted }}>
                {D.money(r.rate)} / day
              </div>
            </div>
            <input
              type="number"
              min="0"
              value={r.planned}
              onChange={(e) => setRolePlanned(r.roleId, e.target.value)}
              style={{
                width: 90,
                fontFamily: 'ui-monospace, monospace',
                fontSize: 13.5,
                padding: '7px 10px',
                border: '1px solid #CED4DA',
                borderRadius: 8,
                textAlign: 'right',
                outline: 'none',
                color: CUTC.purple,
              }}
            />
          </div>
        ))}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            padding: '12px 14px',
            background: CUTC.surface,
            fontSize: 13,
          }}
        >
          <span style={{ fontWeight: 600, color: CUTC.purple }}>
            {plannedWf} workers required
          </span>
          <span style={{ color: CUTC.muted }}>
            est. labour{' '}
            <strong
              style={{
                color: CUTC.purple,
                fontFamily: 'ui-monospace, monospace',
              }}
            >
              {D.moneyK(wfCost)}
            </strong>
            /day
          </span>
        </div>
      </div>

      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: 'var(--accent)',
          letterSpacing: '.1em',
          textTransform: 'uppercase',
          marginBottom: 10,
        }}
      >
        Resource allocations
      </div>
      <div
        style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}
      >
        <NumField label="Vaccine doses" value={doses} onChange={setDoses} />
        <NumField
          label="Cold-chain boxes"
          value={coldBoxes}
          onChange={setColdBoxes}
        />
        <NumField label="Vehicles" value={vehicles} onChange={setVehicles} />
      </div>

      <div
        style={{ height: 1, background: CUTC.border, margin: '8px 0 18px' }}
      ></div>
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: 'var(--accent)',
          letterSpacing: '.1em',
          textTransform: 'uppercase',
          marginBottom: 10,
        }}
      >
        Budget
      </div>
      <NumField
        label="Budget allocated"
        value={budget}
        onChange={setBudget}
        prefix="₦"
        help={
          wfCost
            ? 'workforce alone is ~' +
              D.moneyK(wfCost * D.CAMPAIGN.daysTotal) +
              ' over ' +
              D.CAMPAIGN.daysTotal +
              ' days'
            : null
        }
      />
    </Modal>
  );
}

// ---------- EDIT TARGET MODAL ----------
function TargetModal({ open, mp, onClose, onSave }) {
  const D = window.CUT_DATA;
  const [target, setTarget] = useStateMd('');
  const [goal, setGoal] = useStateMd('95');
  useEffectMd(() => {
    if (open && mp) {
      setTarget(String(mp.target));
      setGoal(String(mp.goalPct));
    }
  }, [open]);
  if (!mp) return null;
  const objective = Math.round(
    ((parseInt(target) || 0) * (parseInt(goal) || 95)) / 100,
  );
  return (
    <Modal
      open={open}
      onClose={onClose}
      width={460}
      title={'Campaign target — ' + mp.lga}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon="check"
            disabled={!(parseInt(target) > 0)}
            onClick={() =>
              onSave(
                {
                  ...mp,
                  target: parseInt(target) || 0,
                  goalPct: parseInt(goal) || 95,
                  objective,
                },
                mp,
              )
            }
          >
            Save target
          </Button>
        </>
      }
    >
      <p style={{ margin: '0 0 16px', fontSize: 13, color: CUTC.body }}>
        Define the planned objective for{' '}
        <strong style={{ color: CUTC.purple }}>
          {mp.lga}, {mp.region}
        </strong>
        . Performance is measured against this objective.
      </p>
      <NumField
        label="Target population"
        value={target}
        onChange={setTarget}
        help="eligible children in catchment"
      />
      <NumField
        label="Coverage goal"
        value={goal}
        onChange={setGoal}
        suffix="%"
      />
      <div
        style={{
          background: 'var(--accent-soft)',
          borderRadius: 10,
          padding: '14px 16px',
          marginTop: 4,
        }}
      >
        <div
          style={{
            fontSize: 11.5,
            color: 'var(--accent-dark)',
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
          }}
        >
          Planned objective
        </div>
        <div
          style={{
            fontSize: 22,
            fontWeight: 600,
            color: CUTC.purple,
            marginTop: 4,
          }}
        >
          {D.num(objective)}{' '}
          <span style={{ fontSize: 13, fontWeight: 500, color: CUTC.body }}>
            children to immunize
          </span>
        </div>
      </div>
    </Modal>
  );
}

// ---------- EDIT BUDGET MODAL ----------
function BudgetModal({ open, mp, onClose, onSave }) {
  const D = window.CUT_DATA;
  const [budget, setBudget] = useStateMd('');
  useEffectMd(() => {
    if (open && mp) setBudget(String(mp.budget));
  }, [open]);
  if (!mp) return null;
  const b = parseInt(budget) || 0;
  const overspent = b < mp.spent;
  return (
    <Modal
      open={open}
      onClose={onClose}
      width={460}
      title={'Budget allocation — ' + mp.lga}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon="check"
            disabled={!(b > 0)}
            onClick={() => onSave({ ...mp, budget: b }, mp)}
          >
            Save allocation
          </Button>
        </>
      }
    >
      <p style={{ margin: '0 0 16px', fontSize: 13, color: CUTC.body }}>
        Allocate budget to{' '}
        <strong style={{ color: CUTC.purple }}>
          {mp.lga}, {mp.region}
        </strong>
        . Spend to date is{' '}
        <strong
          style={{ color: CUTC.purple, fontFamily: 'ui-monospace, monospace' }}
        >
          {D.moneyK(mp.spent)}
        </strong>
        .
      </p>
      <NumField
        label="Budget allocated"
        value={budget}
        onChange={setBudget}
        prefix="₦"
        error={
          overspent
            ? 'Below amount already spent (' + D.moneyK(mp.spent) + ')'
            : null
        }
      />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 12,
          marginTop: 4,
        }}
      >
        <MpTile
          label="Utilization"
          value={b ? Math.round((mp.spent / b) * 100) + '%' : '—'}
        />
        <MpTile label="Remaining" value={D.moneyK(Math.max(0, b - mp.spent))} />
      </div>
    </Modal>
  );
}

Object.assign(window, {
  MpStatus,
  MpTile,
  MicroplanDrawer,
  MicroplanModal,
  TargetModal,
  BudgetModal,
  NumField,
});
