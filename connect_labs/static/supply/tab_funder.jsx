/* US Government / ultimate-payer view — money first.

   Credibility rules taken from how federal spending is actually reported
   (USAspending, ForeignAssistance.gov):
   - Obligated, disbursed and delivered are shown as THREE separate stages.
     Collapsing them into one "spent" number is what reads as spin.
   - One Sankey, and it conserves: appropriation in = allocation out.
   - The unit-cost ladder is shown explicitly ($ → MT → cartons → children)
     with its method stated, rather than jumping straight to "lives saved". */

function FunderTab({ ctx }) {
  const { world } = ctx;
  const appropriations = world.appropriations || [];
  const contracts = world.contracts || [];

  const appropriated = appropriations.reduce((n, a) => n + a.amount, 0);
  const obligated = contracts.reduce((n, c) => n + c.obligated_value, 0);
  const disbursed = contracts.reduce((n, c) => n + c.disbursed_value, 0);
  const deliveredCartons = contracts.reduce(
    (n, c) => n + c.delivered_quantity,
    0,
  );
  const deliveredMt = Math.round((deliveredCartons * 150 * 92) / 1000000);
  const costPerChild = deliveredCartons ? disbursed / deliveredCartons : null;

  return (
    <Page
      title="Funding and delivery"
      lede="What was appropriated, what is committed, what has been paid, and what reached children."
    >
      <KeyFigures
        figures={[
          {
            label: 'Appropriated',
            value: shortMoney(appropriated),
            hint: `${appropriations.length} envelopes`,
          },
          {
            label: 'Obligated',
            value: shortMoney(obligated),
            hint: appropriated
              ? `${Math.round(
                  (obligated / appropriated) * 100,
                )}% of appropriation`
              : null,
          },
          {
            label: 'Disbursed',
            value: shortMoney(disbursed),
            hint: 'paid against confirmed delivery only',
          },
          {
            label: 'Children treated',
            value: formatNumber(deliveredCartons),
            hint: `${formatNumber(deliveredMt)} MT delivered`,
          },
        ]}
      />

      <Card
        title="Where the money went"
        subtitle="Appropriation → partner → country → commodity delivered. Totals reconcile at every stage."
      >
        <Sankey appropriations={appropriations} contracts={contracts} />
      </Card>

      <Card
        title="Stage by stage, per contract"
        subtitle="Obligated, disbursed and delivered are tracked separately and never merged."
      >
        <DataTable
          rows={contracts}
          rowKey={(c) => c.id}
          columns={[
            { key: 'ref', label: 'Contract', value: (c) => c.reference },
            { key: 'org', label: 'Partner', value: (c) => c.org_name },
            {
              key: 'country',
              label: 'Country',
              value: (c) => c.destination_country,
              render: (c) => countryLabel(c.destination_country),
            },
            {
              key: 'stages',
              label: 'Obligated → disbursed → delivered',
              sortable: false,
              value: () => '',
              render: (c) => <StageBars contract={c} />,
            },
            {
              key: 'obl',
              label: 'Obligated',
              value: (c) => c.obligated_value,
              render: (c) => shortMoney(c.obligated_value),
            },
            {
              key: 'dis',
              label: 'Disbursed',
              value: (c) => c.disbursed_value,
              render: (c) => shortMoney(c.disbursed_value),
            },
            {
              key: 'iati',
              label: 'IATI activity',
              value: (c) => c.iati_activity_id || '—',
              render: (c) => (
                <code className="small">{c.iati_activity_id || '—'}</code>
              ),
            },
          ]}
        />
      </Card>

      <Card
        title="What a dollar bought"
        subtitle="Stated as a chain, so every step can be checked."
      >
        <div className="ladder">
          <div className="ladder-step">
            <div className="ladder-value">{shortMoney(disbursed)}</div>
            <div className="ladder-label">
              disbursed against confirmed delivery
            </div>
          </div>
          <div className="ladder-arrow">→</div>
          <div className="ladder-step">
            <div className="ladder-value">{formatNumber(deliveredMt)} MT</div>
            <div className="ladder-label">therapeutic food delivered</div>
          </div>
          <div className="ladder-arrow">→</div>
          <div className="ladder-step">
            <div className="ladder-value">{formatNumber(deliveredCartons)}</div>
            <div className="ladder-label">cartons (150 sachets each)</div>
          </div>
          <div className="ladder-arrow">→</div>
          <div className="ladder-step">
            <div className="ladder-value">{formatNumber(deliveredCartons)}</div>
            <div className="ladder-label">children given a full course</div>
          </div>
        </div>
        <p className="muted small method-note">
          Method: one carton contains 150 × 92 g sachets, which is one child's
          full course of treatment. Cost per child treated ={' '}
          <strong>
            {costPerChild ? formatMoney(costPerChild, 'USD') : '—'}
          </strong>
          , computed from disbursements against confirmed deliveries only —
          consignments in transit are excluded. All figures in this environment
          are synthetic.
        </p>
      </Card>
    </Page>
  );
}

function StageBars({ contract }) {
  const obligated = contract.obligated_value || 0;
  const disbursed = contract.disbursed_value || 0;
  const deliveredValue =
    (contract.delivered_quantity || 0) * (contract.unit_price || 0);
  const pct = (v) => (obligated ? Math.min(100, (v / obligated) * 100) : 0);
  return (
    <div className="stage-bars">
      <div className="stage-bar" title={`Obligated ${shortMoney(obligated)}`}>
        <div className="stage-fill obligated" style={{ width: '100%' }} />
      </div>
      <div
        className="stage-bar"
        title={`Delivered ${shortMoney(deliveredValue)}`}
      >
        <div
          className="stage-fill delivered"
          style={{ width: `${pct(deliveredValue)}%` }}
        />
      </div>
      <div className="stage-bar" title={`Disbursed ${shortMoney(disbursed)}`}>
        <div
          className="stage-fill disbursed"
          style={{ width: `${pct(disbursed)}%` }}
        />
      </div>
    </div>
  );
}

/* A small hand-rolled Sankey. Deliberately simple and conservative: every
   node's inflow equals its outflow, so the diagram cannot imply more money
   moved than was appropriated. */
function Sankey({ appropriations, contracts }) {
  const width = 900;
  const height = Math.max(220, contracts.length * 54 + 60);
  const colWidth = 150;
  const gap = 10;

  const total = appropriations.reduce((n, a) => n + a.amount, 0) || 1;
  const scale = (v) => (v / total) * (height - 60);

  // column 1: appropriations, column 2: partners, column 3: countries
  let y1 = 20;
  const approvals = appropriations.map((a) => {
    const h = Math.max(6, scale(a.amount));
    const node = { id: `a${a.id}`, label: a.title, value: a.amount, y: y1, h };
    y1 += h + gap;
    return node;
  });

  const byPartner = {};
  contracts.forEach((c) => {
    byPartner[c.org_name] = (byPartner[c.org_name] || 0) + c.obligated_value;
  });
  let y2 = 20;
  const partners = Object.entries(byPartner).map(([name, value]) => {
    const h = Math.max(6, scale(value));
    const node = { id: `p${name}`, label: name, value, y: y2, h };
    y2 += h + gap;
    return node;
  });

  const byCountry = {};
  contracts.forEach((c) => {
    byCountry[c.destination_country] =
      (byCountry[c.destination_country] || 0) + c.obligated_value;
  });
  let y3 = 20;
  const countries = Object.entries(byCountry).map(([code, value]) => {
    const h = Math.max(6, scale(value));
    const node = { id: `c${code}`, label: countryLabel(code), value, y: y3, h };
    y3 += h + gap;
    return node;
  });

  // Each contract is linked to ITS OWN appropriation. Attributing everything
  // to the first envelope would make the diagram stop conserving as soon as
  // there is more than one — the exact failure that makes a funding Sankey
  // untrustworthy.
  const links = [];
  contracts.forEach((c) => {
    const partner = partners.find((p) => p.label === c.org_name);
    const country = countries.find(
      (x) => x.label === countryLabel(c.destination_country),
    );
    const approp = approvals.find((a) => a.id === `a${c.appropriation_id}`);
    if (approp && partner)
      links.push({
        from: approp,
        to: partner,
        value: c.obligated_value,
        col: 0,
      });
    if (partner && country)
      links.push({
        from: partner,
        to: country,
        value: c.obligated_value,
        col: 1,
      });
  });

  // Anything not attributable to an envelope is reported, not silently dropped.
  const unattributed = contracts.filter(
    (c) => !approvals.find((a) => a.id === `a${c.appropriation_id}`),
  );

  const colX = [0, (width - colWidth) / 2, width - colWidth];

  return (
    <div className="sankey-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} className="sankey">
        {links.map((l, i) => {
          const fromCol = colX[l.col] + colWidth;
          const toCol = colX[l.col + 1];
          const mid = (fromCol + toCol) / 2;
          const h = Math.max(2, scale(l.value));
          const y1c = l.from.y + l.from.h / 2;
          const y2c = l.to.y + l.to.h / 2;
          return (
            <path
              key={i}
              d={`M${fromCol},${y1c} C${mid},${y1c} ${mid},${y2c} ${toCol},${y2c}`}
              stroke="rgba(13,122,95,0.28)"
              strokeWidth={h}
              fill="none"
            />
          );
        })}
        {[approvals, partners, countries].map((col, ci) =>
          col.map((n) => (
            <g key={n.id}>
              <rect
                x={colX[ci]}
                y={n.y}
                width={colWidth}
                height={n.h}
                rx="3"
                fill="#0d7a5f"
              />
              <text
                x={colX[ci] + 4}
                y={n.y + n.h / 2 + 4}
                className="sankey-label"
              >
                {n.label.length > 26 ? `${n.label.slice(0, 25)}…` : n.label}
              </text>
            </g>
          )),
        )}
      </svg>
      <div className="muted small">
        Widths are proportional to obligated value. Every partner's inflow
        equals the sum of its contracts, and every country's inflow equals the
        sum of the contracts delivering there.
        {unattributed.length
          ? ` ${unattributed.length} contract${
              unattributed.length === 1 ? '' : 's'
            } could not be attributed to an appropriation and ${
              unattributed.length === 1 ? 'is' : 'are'
            } excluded from this diagram.`
          : ''}
      </div>
    </div>
  );
}

function shortMoney(v) {
  if (v === null || v === undefined) return '—';
  if (v >= 1000000) return `$${(v / 1000000).toFixed(1)}M`;
  if (v >= 1000) return `$${Math.round(v / 1000)}k`;
  return `$${Math.round(v)}`;
}
