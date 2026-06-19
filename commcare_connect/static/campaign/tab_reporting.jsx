// tab_reporting.jsx — Reporting & Monitoring tab
const { useState: useStateR } = React;

function ReportingTab({ density }) {
  const D = window.CUT_DATA;
  const toast = useToast();
  const [metric, setMetric] = useStateR('enrolled');
  const [exportOpen, setExportOpen] = useStateR(false);
  const days = D.REPORT_DAYS;
  const totals = {
    enrolled: days.reduce((a, d) => a + d.enrolled, 0),
    attended: days.reduce((a, d) => a + d.attended, 0),
    paid: days.reduce((a, d) => a + d.paid, 0),
  };
  const metricMeta = {
    enrolled: { label: 'Enrollment', color: '#5D70D2', total: totals.enrolled },
    attended: { label: 'Attendance', color: '#01A2A9', total: totals.attended },
    paid: { label: 'Payments', color: '#1E7B33', total: totals.paid },
  };
  const H = D.HOUSEHOLDS;

  return (
    <Page max={1340}>
      <PageHead
        eyebrow="Reporting & Monitoring"
        title="Campaign-wide monitoring"
        sub="Operational visibility across enrollment, attendance, payments, household coverage and geography. Build and export custom reports."
        actions={
          <>
            <Button
              variant="secondary"
              icon="table-list"
              onClick={() => setExportOpen(true)}
            >
              Custom report
            </Button>
            <Button
              icon="download"
              onClick={() => toast('Report exported to CSV')}
            >
              Export data
            </Button>
          </>
        }
      />

      {/* monitoring stat row */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: 14,
          marginBottom: 18,
        }}
      >
        <Stat
          label="Cumulative enrollment"
          value={D.num(totals.enrolled)}
          icon="user-plus"
          delta="+8.4% vs plan"
          deltaTone="success"
        />
        <Stat
          label="Avg. attendance"
          value={Math.round((totals.attended / totals.enrolled) * 100) + '%'}
          icon="calendar-check"
          sub="check-in vs enrolled"
        />
        <Stat
          label="Payments disbursed"
          value={D.num(totals.paid)}
          icon="money-bill-trend-up"
          delta={
            Math.round((totals.paid / totals.enrolled) * 100) + '% of enrolled'
          }
          deltaTone="primary"
        />
      </div>

      {/* trend chart */}
      <Card style={{ marginBottom: 18 }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 20,
            flexWrap: 'wrap',
            gap: 12,
          }}
        >
          <div>
            <SectionTitle>Daily monitoring trend</SectionTitle>
            <div style={{ fontSize: 13, color: CUTC.muted, marginTop: 2 }}>
              {metricMeta[metric].label} · {D.num(metricMeta[metric].total)}{' '}
              total over {days.length} days
            </div>
          </div>
          <PillTabs
            active={metric}
            onChange={setMetric}
            tabs={[
              { id: 'enrolled', label: 'Enrollment' },
              { id: 'attended', label: 'Attendance' },
              { id: 'paid', label: 'Payments' },
            ]}
          />
        </div>
        <TrendChart
          days={days}
          metric={metric}
          color={metricMeta[metric].color}
        />
      </Card>

      {/* household monitoring */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 18,
          marginBottom: 18,
        }}
      >
        <Card>
          <SectionTitle
            sub="Household visits workflow — registered in CommCare"
            style={{ marginBottom: 18 }}
          >
            Household monitoring
          </SectionTitle>
          <div
            style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}
          >
            <HhStat label="Households registered" value={D.num(H.registered)} />
            <HhStat
              label="Households visited"
              value={D.num(H.visited)}
              pct={Math.round((H.visited / H.registered) * 100)}
            />
            <HhStat label="Members enrolled" value={D.num(H.members)} />
            <HhStat
              label="Members reached"
              value={D.num(H.membersReached)}
              pct={Math.round((H.membersReached / H.members) * 100)}
            />
          </div>
          <div
            style={{
              marginTop: 18,
              paddingTop: 16,
              borderTop: '1px solid ' + CUTC.borderSoft,
            }}
          >
            <Progress
              value={H.visited}
              max={H.registered}
              height={9}
              label="Visit completion"
              right={Math.round((H.visited / H.registered) * 100) + '%'}
              color="#9A5183"
            />
          </div>
        </Card>
        <Card>
          <SectionTitle
            sub="Performance against round target (95% coverage)"
            style={{ marginBottom: 18 }}
          >
            Campaign performance
          </SectionTitle>
          <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
            <Donut
              size={140}
              thickness={18}
              segments={[
                { label: 'Reached', value: H.membersReached, color: '#5D70D2' },
                {
                  label: 'Remaining',
                  value: H.members - H.membersReached,
                  color: '#E9ECEF',
                },
              ]}
              centerTop={Math.round((H.membersReached / H.members) * 100) + '%'}
              centerBottom="coverage"
            />
            <div
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
              }}
            >
              {[
                ['Reached', H.membersReached, '#5D70D2'],
                [
                  'Remaining to target',
                  H.members - H.membersReached,
                  '#ADB5BD',
                ],
              ].map(([l, v, c]) => (
                <div key={l}>
                  <div style={{ fontSize: 12, color: CUTC.muted }}>{l}</div>
                  <div
                    style={{
                      fontSize: 18,
                      fontWeight: 600,
                      color: CUTC.purple,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                    }}
                  >
                    <span
                      style={{
                        width: 9,
                        height: 9,
                        borderRadius: 2,
                        background: c,
                      }}
                    ></span>
                    {D.num(v)}
                  </div>
                </div>
              ))}
              <div style={{ fontSize: 12, color: CUTC.muted, marginTop: 4 }}>
                14 days remaining to close the gap.
              </div>
            </div>
          </div>
        </Card>
      </div>

      {/* geographic coverage */}
      <Card padding={0}>
        <div
          style={{
            padding: '16px 22px',
            borderBottom: '1px solid ' + CUTC.border,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <SectionTitle sub="Household visit coverage by region">
            Geographic coverage
          </SectionTitle>
          <Button size="sm" variant="secondary" icon="map">
            View map
          </Button>
        </div>
        <Table
          density={density}
          columns={[
            { label: 'Region' },
            { label: 'Households', align: 'right' },
            { label: 'Visited', align: 'right' },
            { label: 'Coverage', width: 240 },
            { label: 'Status' },
          ]}
        >
          {H.coverage.map((c) => {
            const pct = c.visited / c.hh;
            return (
              <Row key={c.name}>
                <Cell strong>{c.name}</Cell>
                <Cell align="right" mono>
                  {D.num(c.hh)}
                </Cell>
                <Cell align="right" mono>
                  {D.num(c.visited)}
                </Cell>
                <Cell>
                  <Progress
                    value={c.visited}
                    max={c.hh}
                    height={8}
                    right={Math.round(pct * 100) + '%'}
                    color={
                      pct < 0.5 ? '#E13019' : pct < 0.65 ? '#E8A317' : '#1E7B33'
                    }
                  />
                </Cell>
                <Cell>
                  <Badge
                    tone={
                      pct < 0.5 ? 'danger' : pct < 0.65 ? 'warning' : 'success'
                    }
                    dot
                  >
                    {pct < 0.5 ? 'Behind' : pct < 0.65 ? 'At risk' : 'On track'}
                  </Badge>
                </Cell>
              </Row>
            );
          })}
        </Table>
      </Card>

      <CustomReportModal
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        onRun={() => {
          toast('Custom report generated');
          setExportOpen(false);
        }}
      />
    </Page>
  );
}

function HhStat({ label, value, pct }) {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
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
          fontSize: 23,
          fontWeight: 600,
          color: CUTC.purple,
          marginTop: 4,
          display: 'flex',
          alignItems: 'baseline',
          gap: 8,
        }}
      >
        {value}
        {pct != null && (
          <span style={{ fontSize: 12, color: '#1E7B33', fontWeight: 600 }}>
            {pct}%
          </span>
        )}
      </div>
    </div>
  );
}

function TrendChart({ days, metric, color }) {
  const vals = days.map((d) => d[metric]);
  const max = Math.max(...vals) * 1.1;
  const W = 1000,
    Hh = 220,
    pad = 8;
  const step = (W - pad * 2) / (days.length - 1);
  const pts = vals.map((v, i) => [pad + i * step, Hh - (v / max) * (Hh - 20)]);
  const linePath = pts
    .map(
      (p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1),
    )
    .join(' ');
  const areaPath =
    linePath +
    ` L${pts[pts.length - 1][0].toFixed(1)} ${Hh} L${pts[0][0].toFixed(
      1,
    )} ${Hh} Z`;
  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${Hh}`}
        style={{ width: '100%', height: 220, display: 'block' }}
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id="trendGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.22" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75, 1].map((g) => (
          <line
            key={g}
            x1="0"
            y1={Hh - g * (Hh - 20)}
            x2={W}
            y2={Hh - g * (Hh - 20)}
            stroke="#F1F3F5"
            strokeWidth="1"
          />
        ))}
        <path d={areaPath} fill="url(#trendGrad)" />
        <path
          d={linePath}
          fill="none"
          stroke={color}
          strokeWidth="2.5"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
        {pts.map((p, i) => (
          <circle
            key={i}
            cx={p[0]}
            cy={p[1]}
            r="3"
            fill="#fff"
            stroke={color}
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          marginTop: 8,
        }}
      >
        {days.map((d, i) => (
          <div
            key={i}
            style={{
              fontSize: 10,
              color: CUTC.faint,
              fontFamily: 'ui-monospace, monospace',
            }}
          >
            {i % 2 === 0 ? d.day : ''}
          </div>
        ))}
      </div>
    </div>
  );
}

function CustomReportModal({ open, onClose, onRun }) {
  const [fields, setFields] = useStateR(
    new Set(['Worker ID', 'Name', 'Region', 'Payment status']),
  );
  const all = [
    'Worker ID',
    'Name',
    'Region',
    'LGA',
    'Role',
    'Gender',
    'Days worked',
    'Amount',
    'KYC status',
    'Payment status',
    'Activity',
    'Enrollment date',
  ];
  const toggle = (f) =>
    setFields((s) => {
      const n = new Set(s);
      n.has(f) ? n.delete(f) : n.add(f);
      return n;
    });
  return (
    <Modal
      open={open}
      onClose={onClose}
      width={600}
      title="Build a custom report"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" icon="play" onClick={onRun}>
            Generate report ({fields.size} columns)
          </Button>
        </>
      }
    >
      <Field label="Report type">
        <Select defaultValue="Worker payments">
          <option>Worker payments</option>
          <option>KYC status</option>
          <option>Attendance</option>
          <option>Household coverage</option>
          <option>Activity performance</option>
        </Select>
      </Field>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 14,
          marginBottom: 14,
        }}
      >
        <Field label="Date range" style={{ marginBottom: 0 }}>
          <Select defaultValue="This round">
            <option>This round</option>
            <option>Last 7 days</option>
            <option>Last 30 days</option>
            <option>Custom…</option>
          </Select>
        </Field>
        <Field label="Group by" style={{ marginBottom: 0 }}>
          <Select defaultValue="Region">
            <option>Region</option>
            <option>Activity</option>
            <option>Role</option>
            <option>Donor</option>
          </Select>
        </Field>
      </div>
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: CUTC.purple,
          marginBottom: 8,
        }}
      >
        Columns
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {all.map((f) => {
          const on = fields.has(f);
          return (
            <button
              key={f}
              onClick={() => toggle(f)}
              style={{
                fontFamily: 'inherit',
                fontSize: 12.5,
                fontWeight: 600,
                padding: '6px 12px',
                borderRadius: 999,
                cursor: 'pointer',
                border: '1px solid ' + (on ? 'var(--accent)' : CUTC.border),
                background: on ? 'var(--accent-soft)' : '#fff',
                color: on ? 'var(--accent-dark)' : CUTC.body,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              {on && <i className="fa fa-check" style={{ fontSize: 10 }}></i>}
              {f}
            </button>
          );
        })}
      </div>
    </Modal>
  );
}

Object.assign(window, { ReportingTab });
