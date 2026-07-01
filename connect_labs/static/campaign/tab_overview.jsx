// tab_overview.jsx — Overview dashboard
const { useState: useStateOv } = React;

function OverviewTab({ scenario, showAlerts, onJump }) {
  const D = window.CUT_DATA;
  const sum = D.WORKERS_SUMMARY;
  const atRisk = scenario === 'atrisk';
  const funded = D.DONORS.reduce((a, d) => a + d.committed, 0);
  const budgetTotal = D.PLANNING.reduce((a, p) => a + p.budget, 0);
  const spent = D.PLANNING.reduce((a, p) => a + p.spent, 0);
  const reached = D.PLANNING.reduce((a, p) => a + p.reached, 0);
  const target = D.PLANNING.reduce((a, p) => a + p.target, 0);

  const alerts = [
    {
      tone: 'danger',
      icon: 'triangle-exclamation',
      t: `${sum.duplicates} workers flagged as potential duplicates`,
      s: 'Shared NIN across payment records — review before next disbursement run.',
      cta: 'Review duplicates',
      go: ['workers', 'kyc'],
    },
    {
      tone: 'danger',
      icon: 'location-crosshairs',
      t: 'Overlapping GPS detected on 14 daily logs',
      s: 'Borno · IDP camp outreach — vaccinator check-ins within 5m radius.',
      cta: 'Open activity',
      go: ['activity'],
    },
    {
      tone: 'warning',
      icon: 'id-card',
      t: `${
        sum.kyc.pending + sum.kyc.review
      } KYC records awaiting verification`,
      s:
        'Blocks ₦' +
        (sum.pendingAmount / 1e6).toFixed(1) +
        'M in pending payments.',
      cta: 'Review KYC',
      go: ['workers', 'kyc'],
    },
    {
      tone: 'warning',
      icon: 'gauge-high',
      t: 'Borno coverage 27% — well below round target',
      s: 'Insecurity delaying mobile teams; consider reallocating workforce.',
      cta: 'Open planning',
      go: ['planning'],
    },
  ];

  return (
    <Page>
      <PageHead
        eyebrow={D.CAMPAIGN.country + ' · ' + D.CAMPAIGN.round}
        title={D.CAMPAIGN.name}
        sub={`${D.CAMPAIGN.period} · Day ${D.CAMPAIGN.daysElapsed} of ${D.CAMPAIGN.daysTotal}. A unified view across funding, workforce, KYC, payments and field activity.`}
        actions={
          <>
            <Badge
              tone="success"
              dot
              style={{ padding: '6px 12px', fontSize: 12 }}
            >
              Active
            </Badge>
            <Button variant="secondary" icon="download">
              Export summary
            </Button>
          </>
        }
      />

      {/* filters */}
      <div
        style={{
          display: 'flex',
          gap: 10,
          marginBottom: 18,
          flexWrap: 'wrap',
          alignItems: 'center',
        }}
      >
        <span style={{ fontSize: 12, color: CUTC.muted, fontWeight: 600 }}>
          <i
            className="fa fa-filter"
            style={{ marginRight: 7, color: 'var(--accent)' }}
          ></i>
          Filter metrics
        </span>
        <Select
          defaultValue="all"
          style={{ width: 'auto', fontSize: 13, padding: '7px 28px 7px 11px' }}
        >
          <option value="all">All regions</option>
          {D.REGIONS.map((r) => (
            <option key={r.id}>{r.name}</option>
          ))}
        </Select>
        <Select
          defaultValue="round"
          style={{ width: 'auto', fontSize: 13, padding: '7px 28px 7px 11px' }}
        >
          <option value="round">This round</option>
          <option>Last 7 days</option>
          <option>Last 30 days</option>
        </Select>
        <Select
          defaultValue="all"
          style={{ width: 'auto', fontSize: 13, padding: '7px 28px 7px 11px' }}
        >
          <option value="all">All donors</option>
          {D.DONORS.map((d) => (
            <option key={d.id}>{d.short}</option>
          ))}
        </Select>
      </div>

      {/* progress strip */}
      <Card padding={0} style={{ marginBottom: 18, overflow: 'hidden' }}>
        <div
          style={{
            padding: '14px 22px',
            borderBottom: '1px solid ' + CUTC.border,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 600, color: CUTC.purple }}>
            Campaign progress
          </div>
          <div style={{ fontSize: 12, color: CUTC.muted }}>
            Round timeline ·{' '}
            {Math.round((D.CAMPAIGN.daysElapsed / D.CAMPAIGN.daysTotal) * 100)}%
            elapsed
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)' }}>
          {[
            {
              l: 'Enrollment',
              v: reached,
              m: target,
              c: '#5D70D2',
              sub: D.num(reached) + ' / ' + D.num(target),
            },
            {
              l: 'Attendance',
              v: 91,
              m: 100,
              c: '#01A2A9',
              sub: '91% avg check-in',
            },
            {
              l: 'Verification (KYC)',
              v: sum.kyc.approved,
              m: sum.total,
              c: '#694AAA',
              sub: sum.kyc.approved + ' / ' + sum.total + ' approved',
            },
            {
              l: 'Payments',
              v: sum.pay.paid,
              m: sum.total,
              c: '#1E7B33',
              sub: sum.pay.paid + ' / ' + sum.total + ' paid',
            },
          ].map((x, i) => (
            <div
              key={i}
              style={{
                padding: '18px 22px',
                borderRight: i < 3 ? '1px solid ' + CUTC.borderSoft : 'none',
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  color: CUTC.muted,
                  fontWeight: 600,
                  letterSpacing: '.06em',
                  textTransform: 'uppercase',
                }}
              >
                {x.l}
              </div>
              <div
                style={{
                  fontSize: 25,
                  fontWeight: 600,
                  color: CUTC.purple,
                  margin: '6px 0 10px',
                  letterSpacing: '-0.01em',
                }}
              >
                {Math.round((x.v / x.m) * 100)}%
              </div>
              <Progress value={x.v} max={x.m} color={x.c} height={6} />
              <div style={{ fontSize: 11.5, color: CUTC.muted, marginTop: 7 }}>
                {x.sub}
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* stat row */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 16,
          marginBottom: 18,
        }}
      >
        <Stat
          label="Total funding"
          value={D.moneyK(funded)}
          icon="hand-holding-dollar"
          delta={D.DONORS.length + ' donors'}
          deltaTone="primary"
        />
        <Stat
          label="Budget utilized"
          value={Math.round((spent / budgetTotal) * 100) + '%'}
          icon="wallet"
          sub={D.moneyK(spent) + ' of ' + D.moneyK(budgetTotal)}
          delta={atRisk ? 'Over pace' : 'On pace'}
          deltaTone={atRisk ? 'warning' : 'success'}
        />
        <Stat
          label="Active workers"
          value={D.num(sum.total)}
          icon="users"
          sub={Math.round((sum.female / sum.total) * 100) + '% female'}
          delta={sum.pay.paid + ' paid'}
          deltaTone="success"
        />
        <Stat
          label="Open exceptions"
          value={sum.duplicates + 14}
          icon="triangle-exclamation"
          delta={sum.duplicates + ' duplicates'}
          deltaTone="danger"
          sub="14 GPS flags"
        />
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1.45fr 1fr',
          gap: 18,
          marginBottom: 18,
        }}
      >
        {/* funding by donor */}
        <Card>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              marginBottom: 18,
            }}
          >
            <SectionTitle>Funder contributions</SectionTitle>
            <span style={{ fontSize: 12.5, color: CUTC.muted }}>
              Total committed {D.moneyK(funded)}
            </span>
          </div>
          {D.DONORS.map((d) => (
            <div key={d.id} style={{ marginBottom: 16 }}>
              <Progress
                value={d.committed}
                max={D.DONORS[0].committed}
                color={d.color}
                label={d.name}
                right={D.moneyK(d.committed)}
                height={9}
              />
            </div>
          ))}
          <div
            style={{
              borderTop: '1px solid ' + CUTC.borderSoft,
              marginTop: 18,
              paddingTop: 16,
              display: 'flex',
              gap: 26,
            }}
          >
            <div>
              <div
                style={{
                  fontSize: 11,
                  color: CUTC.muted,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '.06em',
                }}
              >
                Disbursed
              </div>
              <div
                style={{
                  fontSize: 19,
                  fontWeight: 600,
                  color: CUTC.purple,
                  marginTop: 3,
                }}
              >
                {D.moneyK(spent)}
              </div>
            </div>
            <div>
              <div
                style={{
                  fontSize: 11,
                  color: CUTC.muted,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '.06em',
                }}
              >
                Remaining
              </div>
              <div
                style={{
                  fontSize: 19,
                  fontWeight: 600,
                  color: CUTC.purple,
                  marginTop: 3,
                }}
              >
                {D.moneyK(funded - spent)}
              </div>
            </div>
            <div>
              <div
                style={{
                  fontSize: 11,
                  color: CUTC.muted,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '.06em',
                }}
              >
                Cost / child
              </div>
              <div
                style={{
                  fontSize: 19,
                  fontWeight: 600,
                  color: CUTC.purple,
                  marginTop: 3,
                }}
              >
                ₦{Math.round(spent / reached)}
              </div>
            </div>
          </div>
        </Card>

        {/* KYC + payment donuts */}
        <Card>
          <SectionTitle style={{ marginBottom: 16 }}>
            Verification &amp; payments
          </SectionTitle>
          <div
            style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}
          >
            <DonutBlock
              title="KYC status"
              segments={[
                {
                  label: 'Approved',
                  value: sum.kyc.approved,
                  color: '#1E7B33',
                },
                { label: 'Pending', value: sum.kyc.pending, color: '#E8A317' },
                { label: 'In review', value: sum.kyc.review, color: '#01A2A9' },
                {
                  label: 'Rejected',
                  value: sum.kyc.rejected,
                  color: '#E13019',
                },
              ]}
              center={Math.round((sum.kyc.approved / sum.total) * 100) + '%'}
              centerSub="approved"
              total={sum.total}
            />
            <DonutBlock
              title="Payment status"
              segments={[
                { label: 'Paid', value: sum.pay.paid, color: '#1E7B33' },
                {
                  label: 'Approved',
                  value: sum.pay.approved,
                  color: '#5D70D2',
                },
                { label: 'Pending', value: sum.pay.pending, color: '#E8A317' },
                {
                  label: 'Hold / rej.',
                  value: sum.pay.hold + sum.pay.rejected,
                  color: '#ADB5BD',
                },
              ]}
              center={sum.pay.paid}
              centerSub="paid"
              total={sum.total}
            />
          </div>
        </Card>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: showAlerts ? '1fr 1fr' : '1fr',
          gap: 18,
        }}
      >
        {/* workforce distribution */}
        <Card>
          <SectionTitle
            sub="By role and gender across all activities"
            style={{ marginBottom: 18 }}
          >
            Workforce distribution
          </SectionTitle>
          <WorkforceBars />
        </Card>

        {/* alerts */}
        {showAlerts && (
          <Card padding={0}>
            <div
              style={{
                padding: '18px 22px 14px',
                borderBottom: '1px solid ' + CUTC.border,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <SectionTitle>Fraud &amp; verification alerts</SectionTitle>
              <Badge tone="danger">{alerts.length} open</Badge>
            </div>
            <div>
              {alerts.map((a, i) => (
                <div
                  key={i}
                  style={{
                    padding: '15px 22px',
                    borderBottom:
                      i < alerts.length - 1
                        ? '1px solid ' + CUTC.borderSoft
                        : 'none',
                    display: 'flex',
                    gap: 14,
                    alignItems: 'flex-start',
                  }}
                >
                  <div
                    style={{
                      width: 34,
                      height: 34,
                      borderRadius: 9,
                      flexShrink: 0,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: a.tone === 'danger' ? '#FAD8D4' : '#FCEFCC',
                      color: a.tone === 'danger' ? '#B22312' : '#7A5800',
                    }}
                  >
                    <i
                      className={`fa fa-${a.icon}`}
                      style={{ fontSize: 14 }}
                    ></i>
                  </div>
                  <div style={{ flex: 1 }}>
                    <div
                      style={{
                        fontSize: 13.5,
                        fontWeight: 600,
                        color: CUTC.purple,
                        lineHeight: 1.35,
                      }}
                    >
                      {a.t}
                    </div>
                    <div
                      style={{
                        fontSize: 12.5,
                        color: CUTC.body,
                        marginTop: 3,
                        lineHeight: 1.4,
                      }}
                    >
                      {a.s}
                    </div>
                    <button
                      onClick={() => onJump(a.go[0], a.go[1])}
                      style={{
                        marginTop: 8,
                        fontFamily: 'inherit',
                        background: 'transparent',
                        border: 'none',
                        color: 'var(--accent-dark)',
                        fontWeight: 600,
                        fontSize: 12.5,
                        cursor: 'pointer',
                        padding: 0,
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 6,
                      }}
                    >
                      {a.cta}{' '}
                      <i
                        className="fa fa-arrow-right"
                        style={{ fontSize: 10 }}
                      ></i>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
    </Page>
  );
}

function DonutBlock({ title, segments, center, centerSub, total }) {
  return (
    <div>
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: CUTC.body,
          marginBottom: 10,
          textAlign: 'center',
        }}
      >
        {title}
      </div>
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        <Donut
          segments={segments}
          size={112}
          thickness={14}
          centerTop={center}
          centerBottom={centerSub}
        />
      </div>
      <div
        style={{
          marginTop: 12,
          display: 'flex',
          flexDirection: 'column',
          gap: 5,
        }}
      >
        {segments.map((s) => (
          <div
            key={s.label}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 7,
              fontSize: 11.5,
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: 2,
                background: s.color,
                flexShrink: 0,
              }}
            ></span>
            <span style={{ color: CUTC.body, flex: 1 }}>{s.label}</span>
            <span
              style={{
                color: CUTC.purple,
                fontWeight: 600,
                fontFamily: 'ui-monospace, monospace',
              }}
            >
              {s.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function WorkforceBars() {
  const D = window.CUT_DATA;
  const byRole = D.WORKERS_SUMMARY.byRole || {};
  const rows = Object.entries(byRole).sort(
    (a, b) => b[1].m + b[1].f - (a[1].m + a[1].f),
  );
  const max = Math.max(...rows.map(([, v]) => v.m + v.f));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {rows.map(([role, v]) => (
        <div
          key={role}
          style={{
            display: 'grid',
            gridTemplateColumns: '130px 1fr 64px',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <div style={{ fontSize: 12.5, color: CUTC.body, fontWeight: 500 }}>
            {role}
          </div>
          <div
            style={{
              display: 'flex',
              height: 16,
              borderRadius: 4,
              overflow: 'hidden',
              background: CUTC.borderSoft,
            }}
          >
            <div
              style={{ width: (v.m / max) * 100 + '%', background: '#5D70D2' }}
              title={v.m + ' male'}
            ></div>
            <div
              style={{ width: (v.f / max) * 100 + '%', background: '#9A5183' }}
              title={v.f + ' female'}
            ></div>
          </div>
          <div
            style={{
              fontSize: 12.5,
              color: CUTC.purple,
              fontWeight: 600,
              textAlign: 'right',
              fontFamily: 'ui-monospace, monospace',
            }}
          >
            {v.m + v.f}
          </div>
        </div>
      ))}
      <div
        style={{
          display: 'flex',
          gap: 18,
          marginTop: 6,
          fontSize: 11.5,
          color: CUTC.muted,
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: 2,
              background: '#5D70D2',
            }}
          ></span>
          Male
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: 2,
              background: '#9A5183',
            }}
          ></span>
          Female
        </span>
      </div>
    </div>
  );
}

Object.assign(window, { OverviewTab });
