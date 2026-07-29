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
  const outcomes = world.outcomes;
  const coverageByCountry = world.coverage_by_country || [];
  const obligated = contracts.reduce((n, c) => n + c.obligated_value, 0);
  const disbursed = contracts.reduce((n, c) => n + c.disbursed_value, 0);
  // The ladder is about food, so it sums only the contracts that bought food.
  // A haulage contract's dollars buy movement; letting its spend and its
  // cartons into this chain attributes food money that was never spent on food
  // and pulls cost per child below the price of a single carton.
  const goods = contracts.filter((c) => c.buys_goods);
  const goodsDisbursed = goods.reduce((n, c) => n + c.disbursed_value, 0);
  const deliveredCartons = goods.reduce((n, c) => n + c.delivered_quantity, 0);
  // ONE basis for the whole ladder, end to end: money paid against confirmed
  // delivery, and the cartons that same confirmation covers.
  //
  // The rungs used to mix. Rung 1 was confirmed money and rungs 2-4 were every
  // delivered carton, so the chain's own endpoints divided to $15.21 while the
  // note three lines below asserted $41.80 — on a card captioned "stated as a
  // chain, so every step can be checked". The first thing a reader checks is
  // the first and last rung against each other, and it did not hold.
  const confirmedCartons = goods.reduce((n, c) => n + c.confirmed_quantity, 0);
  const confirmedMt = Math.round((confirmedCartons * 150 * 92) / 1000000);
  const deliveredMt = Math.round((deliveredCartons * 150 * 92) / 1000000);
  const costPerChild = confirmedCartons
    ? goodsDisbursed / confirmedCartons
    : null;

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
            method:
              'The sum of every contract signed against these envelopes, at its contracted quantity and price. Money committed — not yet paid, and not yet food.',
          },
          {
            label: 'Disbursed',
            value: shortMoney(disbursed),
            hint: 'paid against confirmed delivery only',
            method:
              'Paid only against consignments CONFIRMED at the place their contract names. A consignment on the road has moved no money, which is why this figure sits so far below obligated.',
          },
          // Courses, not children. Every one of these tiles used to say
          // "children treated" over a carton count, which is the exact
          // conflation the card further down this page exists to attack — and
          // it made the same phrase name three different numbers across the
          // demo. A carton delivered is a course delivered; whether a child
          // completed it is what the measured recoveries below are for.
          {
            label: 'Courses delivered under contract',
            value: formatNumber(deliveredCartons),
            hint: `${formatNumber(deliveredMt)} MT · treatment outcomes below`,
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
        {/* The bars encoded three stages and explained them only in a native
            `title` tooltip — which does not render in a screenshot, does not
            survive a projector, and cannot be read by anyone using a keyboard.
            Three grey-green bars with no key is decoration. */}
        <div className="stage-legend">
          <span className="stage-key">
            <i className="stage-swatch obligated" /> obligated
          </span>
          <span className="stage-key">
            <i className="stage-swatch delivered" /> delivered
          </span>
          <span className="stage-key">
            <i className="stage-swatch disbursed" /> disbursed
          </span>
          <span className="muted small">
            each bar is a share of what that contract obligated
          </span>
        </div>
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
            <div className="ladder-value">{shortMoney(goodsDisbursed)}</div>
            <div className="ladder-label">
              disbursed on supply contracts, against confirmed delivery
            </div>
          </div>
          <div className="ladder-arrow">→</div>
          <div className="ladder-step">
            <div className="ladder-value">{formatNumber(confirmedMt)} MT</div>
            <div className="ladder-label">
              therapeutic food confirmed received
            </div>
          </div>
          <div className="ladder-arrow">→</div>
          <div className="ladder-step">
            <div className="ladder-value">{formatNumber(confirmedCartons)}</div>
            <div className="ladder-label">cartons (150 sachets each)</div>
          </div>
          <div className="ladder-arrow">→</div>
          <div className="ladder-step">
            <div className="ladder-value">{formatNumber(confirmedCartons)}</div>
            <div className="ladder-label">
              children given a full course, paid for and confirmed
            </div>
          </div>
        </div>
        {/* The figure, at the size of a finding — with its method one click
            away rather than five lines of grey beneath it. */}
        <div className="ladder-result">
          <div className="ladder-result-value">
            {costPerChild ? formatMoney(costPerChild, 'USD') : '—'}
          </div>
          <div className="ladder-result-label">
            cost per child treated
            <InfoNote
              label="cost per child treated"
              text="One carton is 150 × 92 g sachets — one child's full course. Computed from disbursements against CONFIRMED deliveries only, so consignments in transit are excluded from both sides. A carton counts once, on the leg arriving at the delivery place its contract names, so a consignment moving in hops is not counted again at every hop. Haulage and storage contracts are excluded: they buy movement, not cartons."
            />
          </div>
        </div>
      </Card>

      <Card
        title="Coverage against need, by country"
        subtitle="Tonnage cannot distinguish a large delivery into a large caseload from a small one into a small caseload. Coverage can."
      >
        {coverageByCountry.length ? (
          <DataTable
            rows={coverageByCountry}
            rowKey={(r) => r.country}
            columns={[
              {
                key: 'country',
                label: 'Country',
                value: (r) => r.country,
                render: (r) => countryLabel(r.country),
              },
              {
                key: 'caseload',
                label: 'Caseload (children)',
                value: (r) => r.caseload,
                render: (r) => formatNumber(r.caseload),
              },
              {
                key: 'delivered',
                label: 'Courses delivered',
                value: (r) => r.courses_delivered,
                render: (r) => formatNumber(r.courses_delivered),
              },
              {
                key: 'coverage',
                label: 'Coverage',
                value: (r) => r.coverage_percent || 0,
                render: (r) =>
                  r.coverage_percent === null ? (
                    <Badge tone="muted">no data</Badge>
                  ) : (
                    <Badge
                      tone={
                        r.coverage_percent >= 80
                          ? 'good'
                          : r.coverage_percent >= 50
                          ? 'warn'
                          : 'bad'
                      }
                    >
                      {r.coverage_percent}%
                    </Badge>
                  ),
              },
              {
                key: 'uncovered',
                label: 'Still uncovered',
                value: (r) => r.uncovered_children,
                render: (r) => formatNumber(r.uncovered_children),
              },
            ]}
          />
        ) : (
          <EmptyState
            title="No caseload estimates loaded."
            hint="Coverage cannot be reported without a denominator."
          />
        )}
      </Card>

      {/* The measured half comes LAST, after the coverage table.
          It sat directly under the unit ladder, so the ladder scene's frame
          also contained the finale's reveal — 58,251 courses and the observed
          recovery rate — and the closing scene then announced as new a figure
          the viewer had already read three scenes earlier. A 250px card cannot
          exclude the card beneath it in a 720px viewport, so the fix is the
          page order, not the camera. It also reads better: coverage against
          need is the last SUPPLY question, and this is the first outcome one. */}
      <TwoFiguresAndTheGap
        outcomes={outcomes}
        records={world.distribution_records || []}
      />
    </Page>
  );
}

/* The number every report leads with, and how it is almost always made.

   "Children treated" is cartons divided by a treatment factor — arithmetic
   presented as an outcome. Beside it sits a different figure, built from
   measurements taken at the point of treatment. They do not agree, because not
   every child admitted on a batch completes treatment.

   That gap is the most useful thing on this page. It is the difference between
   what was shipped and what is known to have worked, and reporting one without
   the other is how a funder ends up defending a number nobody measured. */
function TwoFiguresAndTheGap({ outcomes, records }) {
  const [openBatch, setOpenBatch] = useState(null);
  if (!outcomes) return null;

  // The batch drill has existed as a tested endpoint and a built component
  // since the demand stage, reachable only from the implementing partner's own
  // page. The funder narrative's closing beat is Dale following a delivered
  // batch forward to one child's arm circumference — the single human image in
  // a narrative otherwise made of arithmetic — and there was no route to it
  // from his surface. Same component, same records, one route.
  const batches = [];
  const seen = new Set();
  (records || []).forEach((r) => {
    if (!r.batch_lot || seen.has(r.batch_lot)) return;
    if (!(r.outcomes || []).length) return;
    seen.add(r.batch_lot);
    batches.push(r);
  });
  const breakdown = outcomes.discharge_breakdown || {};
  const labels = {
    recovered: 'Recovered',
    defaulted: 'Defaulted',
    transferred: 'Transferred to inpatient care',
    non_response: 'Non-response',
    in_treatment: 'Still in treatment',
  };
  const rows = Object.entries(breakdown)
    .filter(([, n]) => n > 0)
    .map(([status, n]) => ({ status, label: labels[status] || status, n }));

  return (
    <Card
      title="Children treated — the figure, and the measurement"
      subtitle="Two numbers, two methods, reported side by side and never reconciled into one."
    >
      <div className="two-figures">
        <div className="figure-block">
          <div className="figure-value">
            {formatNumber(outcomes.courses_delivered)}
          </div>
          <div className="figure-label">
            Courses delivered
            <InfoNote
              label="courses delivered"
              text={outcomes.courses_method}
            />
          </div>
          <p className="muted small">Arithmetic on the supply record.</p>
        </div>
        <div className="figure-block">
          <div className="figure-value">
            {formatNumber(outcomes.children_recovered)}
            <span className="figure-of">
              {' '}
              / {formatNumber(outcomes.children_observed)}
            </span>
          </div>
          <div className="figure-label">
            Recovered, of children discharged
            <InfoNote
              label="recorded recoveries"
              text={outcomes.recovery_method}
            />
          </div>
          <p className="muted small">
            {formatNumber(outcomes.children_in_treatment)} more are still in
            treatment and count on neither side.
          </p>
        </div>
        <div className="figure-block figure-gap">
          <div className="figure-value">
            {outcomes.observed_recovery_rate === null
              ? '—'
              : `${outcomes.observed_recovery_rate}%`}
          </div>
          <div className="figure-label">
            Observed recovery rate
            <InfoNote label="the gap" text={outcomes.gap_note} />
          </div>
          <p className="muted small">Sphere expects above 75%.</p>
        </div>
      </div>
      {batches.length ? (
        <div className="figure-drill">
          <span className="muted small">
            The gap is the finding, and it is followable:{' '}
          </span>
          <button
            type="button"
            className="btn-link"
            onClick={() => setOpenBatch(batches[0])}
          >
            follow batch {batches[0].batch_lot} to the children it treated
          </button>
        </div>
      ) : null}
      {openBatch ? (
        <BatchDrill
          record={openBatch}
          allRecords={records}
          onClose={() => setOpenBatch(null)}
        />
      ) : null}
      {rows.length ? (
        <DataTable
          rows={rows}
          rowKey={(r) => r.status}
          columns={[
            { key: 'label', label: 'Discharge outcome', value: (r) => r.label },
            {
              key: 'n',
              label: 'Children',
              value: (r) => r.n,
              render: (r) => formatNumber(r.n),
            },
            {
              key: 'pct',
              label: 'Share',
              value: (r) => r.n,
              render: (r) =>
                outcomes.children_observed
                  ? `${((r.n / outcomes.children_observed) * 100).toFixed(1)}%`
                  : '—',
            },
          ]}
        />
      ) : null}
      <p className="muted small method-note">
        Treatment outcomes in this environment are synthetic, seeded against the
        Sphere performance thresholds for severe acute malnutrition programmes
        (recovery above 75%, defaulting below 15%).
      </p>
    </Card>
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
  const height = Math.max(240, contracts.length * 62 + 80);
  const colWidth = 150;
  const gap = 10;
  // The gaps between stacked bands have to come OUT of the height the bands
  // are scaled into, not be added on top of it. Scaling the bands to fill the
  // whole box and then inserting a gap between each pair pushed the last band
  // past the bottom edge, which clipped the smallest contract in the diagram —
  // and the smallest contract is the one most likely to be the interesting one.
  const bandCount = Math.max(
    appropriations.length,
    new Set(contracts.map((c) => c.org_name)).size,
    new Set(contracts.map((c) => c.destination_country)).size,
    1,
  );
  const drawable = height - 60 - (bandCount - 1) * gap;

  const appropriated = appropriations.reduce((n, a) => n + a.amount, 0) || 1;

  // What each envelope has actually committed. The diagram traces OBLIGATED
  // dollars through partner to country, so the first column has to be drawn in
  // the same currency as the other two: it was drawn in appropriated dollars
  // while they were drawn in obligated ones, and about 93% of the height
  // evaporated between the first column and the second on a card whose caption
  // promises the flow conserves.
  //
  // The unobligated balance is REPORTED below rather than drawn. Drawn to
  // scale it is 93% of the diagram and every contract band collapses — Blue
  // Nile's $128k is 0.18% of the appropriation and cannot be a visible band on
  // any linear scale that also contains the balance. A number in the caption
  // can be read; a band under a pixel cannot.
  const obligatedByAppropriation = {};
  contracts.forEach((c) => {
    const key = `a${c.appropriation_id}`;
    obligatedByAppropriation[key] =
      (obligatedByAppropriation[key] || 0) + c.obligated_value;
  });
  const total = contracts.reduce((n, c) => n + c.obligated_value, 0) || 1;
  const scale = (v) => (v / total) * drawable;

  // column 1: appropriations, column 2: partners, column 3: countries
  let y1 = 20;
  const approvals = appropriations.map((a) => {
    const committed = obligatedByAppropriation[`a${a.id}`] || 0;
    const h = Math.max(6, scale(committed));
    const node = {
      id: `a${a.id}`,
      label: a.title,
      value: committed,
      envelope: a.amount,
      y: y1,
      h,
    };
    y1 += h + gap;
    return node;
  });

  const residualTotal = Math.max(0, appropriated - total);

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
          col.map((n) => {
            // A funding diagram with no figures on it cannot be checked, which
            // is the one thing this card exists to allow. Every band carries
            // its own amount: on its own line where the band is tall enough to
            // hold two, appended to the label where it is not.
            //
            // A band too thin to contain 10px text puts its label OUTSIDE, in
            // dark ink beside the bar, rather than spilling white letters over
            // a 6px sliver and the background behind it. The smallest band is
            // the one a reader is most likely to be hunting for — Blue Nile's
            // $128k is 2.6% of the diagram — so it is the last label that
            // should be the hardest to read.
            const label =
              n.label.length > 24 ? `${n.label.slice(0, 23)}…` : n.label;
            const roomy = n.h >= 30;
            const thin = n.h < 14;
            if (thin) {
              const last = ci === 2;
              return (
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
                    x={last ? colX[ci] - 8 : colX[ci] + colWidth + 8}
                    y={n.y + n.h / 2 + 3.5}
                    className="sankey-label sankey-label-outside"
                    textAnchor={last ? 'end' : 'start'}
                  >
                    {label} · {shortMoney(n.value)}
                  </text>
                </g>
              );
            }
            return (
              <g key={n.id}>
                <rect
                  x={colX[ci]}
                  y={n.y}
                  width={colWidth}
                  height={n.h}
                  rx="3"
                  fill="#0d7a5f"
                />
                {roomy ? (
                  <React.Fragment>
                    <text
                      x={colX[ci] + 6}
                      y={n.y + n.h / 2 - 2}
                      className="sankey-label"
                    >
                      {label}
                    </text>
                    <text
                      x={colX[ci] + 6}
                      y={n.y + n.h / 2 + 13}
                      className="sankey-label sankey-value"
                    >
                      {shortMoney(n.value)}
                    </text>
                  </React.Fragment>
                ) : (
                  <text
                    x={colX[ci] + 6}
                    y={n.y + n.h / 2 + 4}
                    className="sankey-label"
                  >
                    {label} · {shortMoney(n.value)}
                  </text>
                )}
              </g>
            );
          }),
        )}
      </svg>
      <div className="muted small">
        Every column sums to {shortMoney(total)} obligated.{' '}
        <strong>{shortMoney(residualTotal)}</strong> of the{' '}
        {shortMoney(appropriated)} appropriated is not yet under contract.
        <InfoNote
          label="the funding diagram"
          text="Widths are proportional to obligated dollars on one scale across all three columns, so each column sums to the same total. Every partner's inflow equals the sum of its contracts, and every country's inflow equals the sum of the contracts delivering there. The first column shows what each envelope has COMMITTED, not its size — the unobligated balance is reported rather than drawn, because at true scale it is most of the diagram and every contract band collapses below a pixel."
        />
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
