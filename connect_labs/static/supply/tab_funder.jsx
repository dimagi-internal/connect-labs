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
      asOf={world.as_of}
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
            Three grey-green bars with no key is decoration.

            Swatch order follows the column header's stage chain. The legend read
            "obligated · delivered · disbursed" directly above a header reading
            "Obligated → disbursed → delivered": two contradictory orderings of
            the same three-stage sequence, on the one card whose whole point is
            that the three stages never merge. */}
        <div className="stage-legend">
          <span className="stage-key">
            <i className="stage-swatch obligated" /> obligated
          </span>
          <span className="stage-key">
            <i className="stage-swatch disbursed" /> disbursed
          </span>
          <span className="stage-key">
            <i className="stage-swatch delivered" /> delivered
          </span>
          <span className="muted small">
            each bar is a share of what that contract obligated
          </span>
          {/* The three words the concept rests on had no definition anywhere on
              the card that presents them, while `i` bubbles existed for lesser
              figures on the same page. */}
          <InfoNote
            label="the three stages"
            text="Obligated: money committed by a signed contract — not paid, and not yet food. Disbursed: money actually paid, and only against consignments confirmed at the place their contract names. Delivered: cartons confirmed received. A contract can be fully obligated, partly disbursed and barely delivered at the same time, which is why the three are never merged into one 'spent' figure."
          />
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
            // Exact dollars, and the column adds itself up.
            //
            // These cells were rounded to the nearest thousand individually, so
            // a funder summing DISBURSED by hand got $547k ($0 + $0 + $502k +
            // $45k) against the $546k headline three inches above. Rounding
            // explains it; a reader with a calculator on a page that promises
            // reconciliation does not care. Exact cells sum to an exact total,
            // and the total is now printed rather than left as an exercise.
            {
              key: 'obl',
              label: 'Obligated',
              value: (c) => c.obligated_value,
              render: (c) => exactMoney(c.obligated_value),
              total: (rows) =>
                exactMoney(
                  rows.reduce((n, c) => n + (c.obligated_value || 0), 0),
                ),
            },
            {
              key: 'dis',
              label: 'Disbursed',
              value: (c) => c.disbursed_value,
              render: (c) => exactMoney(c.disbursed_value),
              total: (rows) =>
                exactMoney(
                  rows.reduce((n, c) => n + (c.disbursed_value || 0), 0),
                ),
            },
            // The third stage, as a figure.
            //
            // The card subtitles itself "Obligated, disbursed and delivered are
            // tracked separately and never merged" and shipped TWO figures:
            // delivered existed only as an ~8px unlabelled bar with no value and
            // no scale. The scene's claim — three separate figures per contract —
            // was falsified by the card asserting it. Shown in the unit delivery
            // is actually recorded in, against what the contract bought, since a
            // bare quantity says nothing about whether it is all of it.
            {
              key: 'del',
              label: 'Delivered',
              value: (c) => c.delivered_quantity || 0,
              render: (c) => (
                <span>
                  {formatNumber(c.delivered_quantity || 0)}
                  <span className="muted">
                    {' '}
                    / {formatNumber(c.quantity)} {c.unit}
                  </span>
                </span>
              ),
            },
            {
              key: 'iati',
              label: 'IATI activity',
              value: (c) => c.iati_activity_id || '—',
              // The page's one external-verification affordance, made usable.
              //
              // These were dead grey monospace strings that wrapped mid-token in
              // 4 of 4 rows — an identifier a reader cannot read, let alone
              // check, on the card that offers it as proof. d-portal is the
              // sector's standard viewer for a published IATI activity.
              render: (c) =>
                c.iati_activity_id ? (
                  <a
                    className="iati-id"
                    href={`https://d-portal.org/q.html?aid=${encodeURIComponent(
                      c.iati_activity_id,
                    )}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <code className="small">{c.iati_activity_id}</code>
                  </a>
                ) : (
                  <span className="muted">—</span>
                ),
            },
          ]}
          totalsLabel="All contracts"
        />
      </Card>

      <Card
        title="What a dollar bought"
        subtitle="Stated as a chain, so every step can be checked."
      >
        {/* Every rung exact, and every arrow carrying its constant.
            A chain captioned "so every step can be checked" printed a rounded
            $502k on rung 1, so dividing the chain's own endpoints gave USD 41.83
            against a printed USD 41.80 — and a 3-significant-figure operand
            cannot support cent precision at all, since anything in [$501,500,
            $502,499] renders as $502k and spans USD 41.79–41.87. The middle link
            was uncheckable for a different reason: 166 MT → 12,000 cartons
            requires 13.8 kg per carton, and neither kg-per-sachet nor
            kg-per-carton appeared anywhere on the page. The 1-carton-is-1-course
            identity was likewise only ever in the narration, which is why two
            adjacent rungs showing an identical figure read as a copy-paste bug
            rather than the deliberate 1:1 it is. */}
        <div className="ladder">
          <div className="ladder-step">
            <div className="ladder-value">{exactMoney(goodsDisbursed)}</div>
            <div className="ladder-label">
              disbursed on supply contracts, against confirmed delivery
            </div>
          </div>
          <div className="ladder-arrow">
            →<span className="ladder-op">at contracted unit price</span>
          </div>
          <div className="ladder-step">
            <div className="ladder-value">{formatNumber(confirmedMt)} MT</div>
            <div className="ladder-label">
              therapeutic food confirmed received
            </div>
          </div>
          <div className="ladder-arrow">
            →<span className="ladder-op">÷ 13.8 kg per carton</span>
          </div>
          <div className="ladder-step">
            <div className="ladder-value">{formatNumber(confirmedCartons)}</div>
            <div className="ladder-label">cartons (150 × 92 g sachets)</div>
          </div>
          <div className="ladder-arrow">
            →<span className="ladder-op">1 carton = 1 full course</span>
          </div>
          <div className="ladder-step">
            <div className="ladder-value">{formatNumber(confirmedCartons)}</div>
            <div className="ladder-label">
              full courses paid for and confirmed
            </div>
          </div>
        </div>
        {/* The figure, at the size of a finding — with its method one click
            away rather than five lines of grey beneath it. */}
        <div className="ladder-result">
          <div className="ladder-result-value">
            {costPerChild ? formatMoney(costPerChild, 'USD') : '—'}
          </div>
          {/* "cost per child treated" was contradicted by its own last rung —
              "paid for and confirmed" is a procurement fact, not a treatment
              outcome — and by the measured-recovery card two cards below, which
              exists specifically to separate those two things. The ladder can
              only ever price a course bought; whether a child completed it is
              what the recoveries measure. */}
          <div className="ladder-result-label">
            cost per full course paid for and confirmed
            <InfoNote
              label="cost per full course"
              text="One carton is 150 × 92 g sachets — one child's full course. Computed from disbursements against CONFIRMED deliveries only, so consignments in transit are excluded from both sides. A carton counts once, on the leg arriving at the delivery place its contract names, so a consignment moving in hops is not counted again at every hop. Haulage and storage contracts are excluded: they buy movement, not cartons. This prices a course bought, NOT a child treated — see 'Children treated: the figure, and the measurement' below for what was actually measured."
            />
          </div>
        </div>
        {/* The reconciliation the chain's own first rung demands.
            It opened at "$502k disbursed on supply contracts" while the headline
            three inches above read "$546k Disbursed" — two near-identically
            labelled figures 8% apart with no line explaining the difference.
            Excluding freight from a cost-per-outcome figure is a substantive,
            contestable methodological choice, and it was presented as none. The
            narration also claimed "consignments in transit are excluded, and it
            says so", which was false of this screen: neither phrase appeared on
            it. */}
        <p className="muted small method-note">
          From {exactMoney(disbursed)} disbursed in total, this chain uses{' '}
          {exactMoney(goodsDisbursed)} — the contracts that bought food.{' '}
          {exactMoney(disbursed - goodsDisbursed)} paid to haulage and storage
          contracts is excluded, because those dollars buy movement rather than
          cartons and would price a course below the cost of the food in it.
          Consignments still in transit are excluded from both sides of the
          division: no money has moved against them and no carton has been
          confirmed.
        </p>
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
                // See services/coverage._dispensed_cartons_by_district: the
                // numerator includes stock still in a district hub, which on the
                // seeded world is 93% of it. Gombe and Kassala both read 91%
                // while zero cartons had reached a child.
                label: 'Supply positioned',
                value: (r) => r.coverage_percent || 0,
                render: (r) =>
                  r.coverage_percent === null ? (
                    <Badge tone="muted">no data</Badge>
                  ) : (
                    // The thresholds are 80 and 50, and until now they were
                    // nowhere on the screen — the one place this product
                    // editorialises did it against a rule the reader could not
                    // see. Stated in the column's own note below.
                    //
                    // Over 100% is NOT "better than good": it is more courses
                    // delivered than the caseload needs, which is a positioning
                    // question, not a success. Painting 145.8% the same green as
                    // 91% told a funder that over-supply into one district and
                    // near-complete cover in another were the same outcome.
                    <Badge tone={coverageTone(r.coverage_percent)}>
                      {r.coverage_percent}%
                      {r.coverage_percent > 100 ? ' · over' : ''}
                    </Badge>
                  ),
              },
              {
                key: 'dispensed',
                label: 'Reached a child',
                value: (r) => r.dispensed_percent || 0,
                render: (r) =>
                  r.dispensed_percent === null ? (
                    <Badge tone="muted">no data</Badge>
                  ) : (
                    <Badge tone={coverageTone(r.dispensed_percent)}>
                      {r.dispensed_percent}%
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
        <p className="muted small method-note">
          Two different questions, side by side.{' '}
          <strong>Supply positioned</strong> is courses confirmed into the
          country against its SAM caseload — SAM being severe acute
          malnutrition, the condition this food treats. It counts stock that has
          arrived, including stock still held in a district hub, so it answers
          &ldquo;is the food there&rdquo;. <strong>Reached a child</strong>{' '}
          counts only courses a site recorded handing out. The two are far apart
          on purpose: food in a warehouse is not treatment, and a figure that
          merges them cannot tell a funder which problem they have. Banding for
          both: at or above 80% covered, 50–79% partial, below 50% thin. Above
          100% positioned is marked <em>over</em> rather than green — more
          courses than the caseload calls for is a positioning question, not a
          better outcome than 91%.
        </p>
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
  // Fixed order, and every discharge category present whatever its count.
  //
  // Sphere grades a SAM programme on three rates — recovery, defaulting and
  // DEATH — and the table cited that standard while carrying no mortality row
  // at all, because rows were filtered to n > 0 and no deaths are seeded. The
  // one omitted category is the one whose absence can only ever raise the
  // recovery share, which a paediatric nutrition clinician spots on sight. Zero
  // is the informative value here: "no deaths recorded" can be checked, whereas
  // a missing row is indistinguishable from a programme that does not count
  // them. Explicit order rather than dict order, which does not survive JSON.
  const DISCHARGE_ORDER = [
    'recovered',
    'died',
    'defaulted',
    'transferred',
    'non_response',
  ];
  const labels = {
    recovered: 'Recovered',
    died: 'Died',
    defaulted: 'Defaulted',
    transferred: 'Transferred to inpatient care',
    non_response: 'Non-response',
    in_treatment: 'Still in treatment',
  };
  // A child still in treatment has no discharge outcome, so it cannot be a row
  // in a table headed "Discharge outcome" whose Share column divides by the
  // number DISCHARGED. It was: children_observed is 79 (64 recovered + 10
  // defaulted + 3 transferred + 2 non-response), the four real rows summed to a
  // correct 100.0%, and a fifth row put 55 in-treatment children over the same
  // 79 for 69.6% — so the column totalled 169.6%. The card two blocks above
  // already states these children "count on neither side"; the table then
  // counted them anyway.
  const rows = DISCHARGE_ORDER.map((status) => ({
    status,
    label: labels[status] || status,
    n: breakdown[status] || 0,
  }));
  const stillInTreatment = breakdown.in_treatment || 0;

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
          {/* The base, in the batch modal's own plain register.
              The modal says "6 of the 314 children this batch fed — a rate from
              6 children carries a wide interval". This tile put 79 discharged
              children next to 58,251 courses and stated no cohort, no site
              count and no share, so a reader had no way to know the measured
              figure rests on a fraction of a percent of the delivered one. */}
          {outcomes.children_total ? (
            <p className="muted small">
              Observed on {formatNumber(outcomes.children_total)} children at{' '}
              {outcomes.sites_observed}{' '}
              {outcomes.sites_observed === 1 ? 'site' : 'sites'}
              {outcomes.observed_share_of_courses
                ? ` — ${outcomes.observed_share_of_courses}% of the courses delivered`
                : ''}
              . A rate from a base this small carries a wide interval.
            </p>
          ) : null}
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
      {/* Say what the link does, not what to conclude from it.
          It read "The gap is the finding, and it is followable: follow batch …"
          — an interpretive verdict stamped on an evidence artifact, telling the
          reader what to think about two figures the card deliberately leaves
          unreconciled. The two figures sitting side by side ARE the finding; a
          caption asserting so does the reader's work for them. The 81% tile's
          "Sphere expects above 75%" inside an `i` is the right pattern:
          available on demand, not editorialised on the face. */}
      {batches.length ? (
        <div className="figure-drill">
          <button
            type="button"
            className="btn-link"
            onClick={() => setOpenBatch(batches[0])}
          >
            Follow batch {batches[0].batch_lot} to the children it treated
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
              label: 'Share of discharged',
              value: (r) => r.n,
              render: (r) =>
                outcomes.children_observed
                  ? `${((r.n / outcomes.children_observed) * 100).toFixed(1)}%`
                  : '—',
            },
          ]}
        />
      ) : null}
      {stillInTreatment ? (
        <p className="muted small">
          {formatNumber(stillInTreatment)} further children are still in
          treatment and so have no discharge outcome yet. They are not in the
          table above, and not in the {formatNumber(outcomes.children_observed)}{' '}
          it is a share of — a course that has not finished cannot be counted as
          recovered or as a failure.
        </p>
      ) : null}
      {/* All three Sphere rates, since the card invokes the standard.
          The footnote cited recovery and defaulting and omitted the death rate —
          the one criterion the table also had no row for. Quoting two thirds of
          a standard on the screen that grades itself against it lets the
          omission read as the standard's shape rather than the card's. */}
      <p className="muted small method-note">
        Shares are of the {formatNumber(outcomes.children_observed)} children
        discharged, so they total 100%. Treatment outcomes in this environment
        are synthetic, seeded against the Sphere performance thresholds for
        severe acute malnutrition programmes — recovery above 75%, death rate
        below 10%, defaulting below 15%. Every discharge category is listed
        whatever its count, so a zero is visible as a zero.
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

/* Whole dollars, separated — for anywhere a reader may add a column up.
   shortMoney rounds each value independently, so a column of shortMoney does
   not sum to the shortMoney of the sum. That is fine for a headline tile and
   wrong for a ledger. */
function exactMoney(v) {
  if (v === null || v === undefined) return '—';
  return `$${Math.round(v).toLocaleString()}`;
}
