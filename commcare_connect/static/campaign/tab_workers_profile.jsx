// tab_workers_profile.jsx — Worker Profile sub-tab (master list + detail)
const { useState: useStateP } = React;

function ProfileSub({ density }) {
  const D = window.CUT_DATA;
  const [selId, setSelId] = useStateP(D.WORKERS[0].id);
  const [q, setQ] = useStateP('');
  const [section, setSection] = useStateP('participation');
  const list = D.WORKERS.filter(
    (w) =>
      !q ||
      w.name.toLowerCase().includes(q.toLowerCase()) ||
      w.id.toLowerCase().includes(q.toLowerCase()),
  );
  const w = D.WORKERS.find((x) => x.id === selId);

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '300px 1fr',
        gap: 18,
        alignItems: 'start',
      }}
    >
      {/* list */}
      <Card
        padding={0}
        style={{ position: 'sticky', top: 128, overflow: 'hidden' }}
      >
        <div style={{ padding: 14, borderBottom: '1px solid ' + CUTC.border }}>
          <div style={{ position: 'relative' }}>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Find worker…"
              style={{
                fontFamily: 'inherit',
                fontSize: 13,
                padding: '8px 12px 8px 30px',
                border: '1px solid ' + CUTC.border,
                borderRadius: 8,
                width: '100%',
                boxSizing: 'border-box',
                outline: 'none',
                color: CUTC.purple,
              }}
            />
            <i
              className="fa fa-search"
              style={{
                position: 'absolute',
                left: 11,
                top: '50%',
                transform: 'translateY(-50%)',
                fontSize: 11,
                color: CUTC.muted,
              }}
            ></i>
          </div>
        </div>
        <div style={{ maxHeight: 560, overflowY: 'auto' }}>
          {list.map((x) => (
            <button
              key={x.id}
              onClick={() => setSelId(x.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 11,
                width: '100%',
                textAlign: 'left',
                fontFamily: 'inherit',
                cursor: 'pointer',
                padding: '11px 14px',
                border: 'none',
                borderBottom: '1px solid ' + CUTC.borderSoft,
                background: x.id === selId ? 'var(--accent-soft)' : '#fff',
              }}
            >
              <Avatar name={x.name} size={32} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontWeight: 600,
                    color: CUTC.purple,
                    fontSize: 13,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {x.name}
                </div>
                <div style={{ fontSize: 11, color: CUTC.muted }}>{x.role}</div>
              </div>
              {x.duplicate && (
                <i
                  className="fa fa-clone"
                  style={{ color: '#E13019', fontSize: 11 }}
                ></i>
              )}
            </button>
          ))}
        </div>
      </Card>

      {/* detail */}
      <div>
        <Card style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', gap: 18, alignItems: 'flex-start' }}>
            <Avatar name={w.name} size={64} />
            <div style={{ flex: 1 }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  flexWrap: 'wrap',
                }}
              >
                <h2
                  style={{
                    margin: 0,
                    fontSize: 23,
                    color: CUTC.purple,
                    fontWeight: 600,
                  }}
                >
                  {w.name}
                </h2>
                <KycBadge status={w.kyc} />
                {w.duplicate && (
                  <Badge tone="danger" dot>
                    Duplicate flag
                  </Badge>
                )}
              </div>
              <div style={{ fontSize: 13, color: CUTC.muted, marginTop: 4 }}>
                {w.id} · {w.role} · {w.region}, {w.lga}
              </div>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(4, 1fr)',
                  gap: 16,
                  marginTop: 18,
                }}
              >
                {[
                  ['Phone', w.phone],
                  ['NIN', w.nin],
                  ['Bank', w.bank],
                  ['Enrolled', w.enrolled],
                  ['Gender', w.gender === 'F' ? 'Female' : 'Male'],
                  ['Attendance', w.attendance + '%'],
                  ['Prior campaigns', w.priorCampaigns],
                  ['Total earned', D.money(w.amount)],
                ].map(([l, v]) => (
                  <div key={l}>
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
                        fontSize: 13.5,
                        color: CUTC.purple,
                        fontWeight: 500,
                        marginTop: 3,
                        fontFamily:
                          l === 'NIN' ? 'ui-monospace, monospace' : 'inherit',
                      }}
                    >
                      {v}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Card>

        <div style={{ marginBottom: 16 }}>
          <PillTabs
            active={section}
            onChange={setSection}
            tabs={[
              {
                id: 'participation',
                label: 'Participation',
                icon: 'clock-rotate-left',
              },
              {
                id: 'verification',
                label: 'Verification',
                icon: 'shield-halved',
              },
              { id: 'attendance', label: 'Attendance', icon: 'calendar-check' },
              {
                id: 'registration',
                label: 'Registration',
                icon: 'fingerprint',
              },
            ]}
          />
        </div>

        {section === 'participation' && <ParticipationHistory w={w} />}
        {section === 'verification' && <VerificationHistory w={w} />}
        {section === 'attendance' && <AttendanceHistory w={w} />}
        {section === 'registration' && <RegistrationHistory w={w} />}
      </div>
    </div>
  );
}

function ParticipationHistory({ w }) {
  const D = window.CUT_DATA;
  const campaigns = [
    {
      name: 'Measles–Rubella · R2',
      year: '2026',
      role: w.role,
      days: w.daysWorked,
      region: w.region,
      status: 'Active',
    },
    {
      name: 'Polio SIA · Round 4',
      year: '2025',
      role: 'Vaccinator',
      days: 22,
      region: w.region,
      status: 'Completed',
    },
    {
      name: 'Vitamin A + Deworming',
      year: '2025',
      role: 'Social Mobilizer',
      days: 9,
      region: w.region,
      status: 'Completed',
    },
    {
      name: 'COVID-19 Booster Drive',
      year: '2024',
      role: 'Recorder',
      days: 14,
      region: 'Kaduna',
      status: 'Completed',
    },
  ].slice(0, w.priorCampaigns + 1);
  return (
    <Card padding={0}>
      <CardHead
        title="Campaign participation history"
        sub={`${campaigns.length} campaigns · filter by campaign, date, location, role`}
        filter
      />
      <Table
        density="comfortable"
        columns={[
          { label: 'Campaign' },
          { label: 'Year' },
          { label: 'Role' },
          { label: 'Region' },
          { label: 'Days', align: 'center' },
          { label: 'Status' },
        ]}
      >
        {campaigns.map((c, i) => (
          <Row key={i}>
            <Cell strong>{c.name}</Cell>
            <Cell mono>{c.year}</Cell>
            <Cell>{c.role}</Cell>
            <Cell>{c.region}</Cell>
            <Cell align="center" mono>
              {c.days}
            </Cell>
            <Cell>
              <Badge
                tone={c.status === 'Active' ? 'success' : 'neutral'}
                dot={c.status === 'Active'}
              >
                {c.status}
              </Badge>
            </Cell>
          </Row>
        ))}
      </Table>
    </Card>
  );
}

function VerificationHistory({ w }) {
  const events = [
    {
      date: 'Jun 1, 2026',
      event: 'Bank account (BVN) verified',
      by: 'Auto · KYC provider',
      result: w.kyc === 'approved' ? 'Pass' : 'Pending',
    },
    {
      date: 'May 28, 2026',
      event: 'National ID (NIN) submitted',
      by: 'Amara Okafor',
      result: w.kyc === 'rejected' ? 'Fail' : 'Pass',
    },
    {
      date: 'May 19, 2026',
      event: 'Identity check initiated',
      by: 'System',
      result: 'Pass',
    },
    {
      date: 'May 18, 2026',
      event: 'Enrolled in campaign (CommCare)',
      by: 'Field supervisor',
      result: 'Pass',
    },
  ];
  return (
    <Card padding={0}>
      <CardHead
        title="Verification history"
        sub="Filter by verification status and date"
        filter
      />
      <div style={{ padding: '8px 0' }}>
        {events.map((e, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              gap: 16,
              padding: '12px 22px',
              alignItems: 'flex-start',
            }}
          >
            <div
              style={{
                width: 90,
                fontSize: 12,
                color: CUTC.muted,
                fontFamily: 'ui-monospace, monospace',
                flexShrink: 0,
                paddingTop: 2,
              }}
            >
              {e.date}
            </div>
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                flexShrink: 0,
              }}
            >
              <span
                style={{
                  width: 11,
                  height: 11,
                  borderRadius: 999,
                  background:
                    e.result === 'Fail'
                      ? '#E13019'
                      : e.result === 'Pending'
                      ? '#E8A317'
                      : '#1E7B33',
                  marginTop: 3,
                }}
              ></span>
              {i < events.length - 1 && (
                <span
                  style={{
                    width: 2,
                    flex: 1,
                    minHeight: 24,
                    background: CUTC.border,
                    marginTop: 2,
                  }}
                ></span>
              )}
            </div>
            <div style={{ flex: 1 }}>
              <div
                style={{ fontSize: 13.5, fontWeight: 600, color: CUTC.purple }}
              >
                {e.event}
              </div>
              <div style={{ fontSize: 12, color: CUTC.muted, marginTop: 2 }}>
                {e.by}
              </div>
            </div>
            <Badge
              tone={
                e.result === 'Fail'
                  ? 'danger'
                  : e.result === 'Pending'
                  ? 'warning'
                  : 'success'
              }
            >
              {e.result}
            </Badge>
          </div>
        ))}
      </div>
    </Card>
  );
}

function AttendanceHistory({ w }) {
  const D = window.CUT_DATA;
  // build a small calendar grid
  const days = [];
  for (let i = 0; i < 28; i++) {
    const worked = i < w.daysWorked;
    const approved = i < w.daysApproved;
    days.push({
      d: i + 1,
      state:
        i >= 16
          ? 'future'
          : worked
          ? approved
            ? 'present'
            : 'unverified'
          : 'absent',
    });
  }
  const colors = {
    present: '#1E7B33',
    unverified: '#E8A317',
    absent: '#E9ECEF',
    future: '#F8F9FA',
  };
  return (
    <Card>
      <CardHead
        title="Attendance history"
        sub={`${w.daysWorked} of ${D.CAMPAIGN.daysElapsed} field days · ${w.attendance}% check-in rate`}
        pad={0}
      />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(14, 1fr)',
          gap: 6,
          marginTop: 16,
        }}
      >
        {days.map((d) => (
          <div
            key={d.d}
            title={`Day ${d.d}: ${d.state}`}
            style={{
              aspectRatio: '1',
              borderRadius: 6,
              background: colors[d.state],
              border:
                d.state === 'future' ? '1px solid ' + CUTC.border : 'none',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 10.5,
              fontWeight: 600,
              color:
                d.state === 'present' || d.state === 'unverified'
                  ? '#fff'
                  : CUTC.faint,
            }}
          >
            {d.d}
          </div>
        ))}
      </div>
      <div
        style={{
          display: 'flex',
          gap: 18,
          marginTop: 16,
          fontSize: 12,
          color: CUTC.muted,
          flexWrap: 'wrap',
        }}
      >
        {[
          ['present', 'Present & verified'],
          ['unverified', 'Present · unverified'],
          ['absent', 'Absent'],
          ['future', 'Upcoming'],
        ].map(([k, l]) => (
          <span
            key={k}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}
          >
            <span
              style={{
                width: 11,
                height: 11,
                borderRadius: 3,
                background: colors[k],
                border: k === 'future' ? '1px solid ' + CUTC.border : 'none',
              }}
            ></span>
            {l}
          </span>
        ))}
      </div>
    </Card>
  );
}

function RegistrationHistory({ w }) {
  const D = window.CUT_DATA;
  const regs = [
    {
      date: w.enrolled,
      channel: 'CommCare mobile',
      device: 'Tecno POP-7 · IMEI …4471',
      gps: w.region + ', ' + w.lga,
      flag: null,
    },
    {
      date: 'May 17, 2026',
      channel: 'CommCare mobile',
      device: 'Tecno POP-7 · IMEI …4471',
      gps: w.region,
      flag: w.fraudRules.length ? w.fraudRules[0] : null,
    },
  ];
  return (
    <div>
      {w.fraudRules.length > 0 && (
        <div
          style={{
            border: '1px solid #F5C2BB',
            background: '#FCEEEC',
            borderRadius: 10,
            marginBottom: 16,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              display: 'flex',
              gap: 12,
              padding: '13px 16px',
              alignItems: 'flex-start',
            }}
          >
            <i
              className="fa fa-fingerprint"
              style={{ color: '#B22312', marginTop: 2 }}
            ></i>
            <div style={{ fontSize: 12.5, color: '#76190B', lineHeight: 1.45 }}>
              <strong>Fraud suspicion alert.</strong> This worker shares one or
              more configured identifiers with another record — investigate for
              duplicate registration or identity misuse.
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 6,
                  marginTop: 10,
                }}
              >
                {w.fraudRules.map((r, i) => (
                  <Badge key={i} tone="danger">
                    {r}
                  </Badge>
                ))}
              </div>
              {w.linked && w.linked.length > 0 && (
                <div
                  style={{
                    marginTop: 10,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 6,
                  }}
                >
                  {w.linked.map((l, i) => (
                    <div
                      key={i}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        fontSize: 12,
                      }}
                    >
                      <i className="fa fa-link" style={{ fontSize: 10 }}></i>
                      <strong>{l.name}</strong> ({l.id}) — shared{' '}
                      {D.sharedLabel[l.shared] || l.shared}
                    </div>
                  ))}
                </div>
              )}
              {w.investigation && (
                <div style={{ marginTop: 10, fontSize: 12 }}>
                  Investigation status:{' '}
                  <strong>{w.investigation.status}</strong>
                  {w.investigation.outcome
                    ? ' — ' + w.investigation.outcome
                    : ''}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      <Card padding={0}>
        <CardHead
          title="Registration history"
          sub="Used to detect duplicate registrations and investigate suspicious activity"
        />
        <Table
          density="comfortable"
          columns={[
            { label: 'Registered' },
            { label: 'Channel' },
            { label: 'Device' },
            { label: 'Location' },
            { label: 'Flag' },
          ]}
        >
          {regs.map((r, i) => (
            <Row key={i}>
              <Cell strong>{r.date}</Cell>
              <Cell>{r.channel}</Cell>
              <Cell mono style={{ fontSize: 12 }}>
                {r.device}
              </Cell>
              <Cell>{r.gps}</Cell>
              <Cell>
                {r.flag ? (
                  <Badge tone="danger" dot>
                    {r.flag}
                  </Badge>
                ) : (
                  <span style={{ color: CUTC.faint }}>Clean</span>
                )}
              </Cell>
            </Row>
          ))}
        </Table>
      </Card>
    </div>
  );
}

function CardHead({ title, sub, filter, pad }) {
  return (
    <div
      style={{
        padding: pad === 0 ? 0 : '16px 22px',
        borderBottom: pad === 0 ? 'none' : '1px solid ' + CUTC.border,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: pad === 0 ? 0 : undefined,
      }}
    >
      <div>
        <div style={{ fontSize: 15, fontWeight: 600, color: CUTC.purple }}>
          {title}
        </div>
        {sub && (
          <div style={{ fontSize: 12, color: CUTC.muted, marginTop: 2 }}>
            {sub}
          </div>
        )}
      </div>
      {filter && (
        <Button size="sm" variant="secondary" icon="filter">
          Filter
        </Button>
      )}
    </div>
  );
}

Object.assign(window, { ProfileSub });
