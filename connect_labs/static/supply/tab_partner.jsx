/* The implementing partner's surface — Komadugu Health Initiative.

   Everything else in this app looks outward from the centre. This is the one
   surface that looks the other way, and it is deliberately not a recoloured
   copy of the command centre:

   - The unit of planning is a DISTRIBUTION DAY at a named site with a known
     number of children expected, not a shipment sorted by arrival date. A
     shipment table is the supplier's view of the world.
   - Weeks of cover and the stockout date come from the server, from the same
     services/cover.py the command centre reads. Two implementations would
     drift, and a partner told they have eleven days while the centre reads
     three weeks is worse than neither having the figure.
   - A shortfall is raised from HERE. That inverts the direction a monitoring
     product runs in: the ground reports upward into a system that can respond.
*/

function PartnerTab({ ctx }) {
  const { world, act } = ctx;
  const plans = world.distribution_plans || [];
  const cover = world.cover || [];
  const sites = world.sites || [];
  const signals = world.shortfall_signals || [];
  const records = world.distribution_records || [];
  const [raising, setRaising] = useState(null);
  const [batch, setBatch] = useState(null);

  const worst = cover.length ? cover[0] : null;
  const openSignals = signals.filter((s) => s.status !== 'resolved');
  const atRisk = plans.filter((p) => p.state !== 'covered');

  // The thresholds the cover card already states in words: at four weeks you
  // plan, at one you triage. Stated once, here, so the tile and the table
  // cannot disagree about what counts as bad.
  // Distributions that resolve to something, first.
  //
  // A distribution handed out three days ago legitimately has no treatment
  // records yet, and one per batch received means there are now several of
  // them. Sorted by date alone, the card whose whole point is that a batch
  // resolves FORWARD opened with seven consecutive rows reading zero.
  const traceable = [...records].sort(
    (a, b) =>
      (b.outcomes || []).length - (a.outcomes || []).length ||
      (a.distributed_on < b.distributed_on ? 1 : -1),
  );

  const worstWeeks = worst ? worst.weeks_of_cover : null;
  const worstTone =
    worstWeeks === null
      ? undefined
      : worstWeeks < 2
      ? 'critical'
      : worstWeeks < 4
      ? 'at-risk'
      : 'ok';

  return (
    <Page
      title={world.org ? world.org.legal_name : 'Partner'}
      lede="Inbound supply against the distributions you have planned — and how long each site's stock lasts."
    >
      {/* Thinnest cover LEADS. It is the only figure here that says a child
          may go without, and it used to be rendered at the same size and
          weight as a static count of feeding sites. */}
      <KeyFigures
        figures={[
          {
            label: 'Thinnest cover',
            value: worst ? `${worst.weeks_of_cover} wk` : '—',
            lead: true,
            tone: worstTone,
            hint: worst
              ? worst.stockout_on
                ? `${worst.node_name} runs dry ${formatDate(worst.stockout_on)}`
                : `${worst.node_name} is awaiting its first consignment`
              : '',
            method:
              'Stock on hand divided by the rate this site is admitting children. Stock is receipts minus despatches from the event log; the rate is the district SAM caseload shared between the sites serving it, at one carton per full course. At four weeks you plan; at one you triage.',
          },
          {
            label: 'Distributions not covered',
            value: atRisk.length,
            tone: atRisk.length ? 'at-risk' : 'ok',
            hint: atRisk.length
              ? 'inbound supply falls short of what is booked in'
              : 'every planned day is covered',
            method:
              'A planned distribution is covered when the cartons on hand that day reach the children booked in for it. On hand means opening stock plus every consignment that lands on or before that day, less what earlier distributions spent. Cartons still in transit on the day arrive too late to cover it and are not counted — a distribution can be short with a lorry already on the way.',
          },
          {
            // Counts what is OPEN, and now says so. The label read
            // "Shortfalls raised" above a table listing one already raised.
            label: 'Shortfalls awaiting an answer',
            value: openSignals.length,
            tone: openSignals.length ? 'at-risk' : undefined,
            hint: openSignals.length
              ? 'reported to OES, not yet answered'
              : `${signals.length} raised, all answered`,
          },
          { label: 'Feeding sites', value: sites.length },
        ]}
      />

      <Card
        title="Distribution calendar"
        subtitle="The frame you actually plan in: a week, a site, and the children booked in for each day. Stock on the day includes consignments scheduled to land by then — the cover table below counts only what has physically arrived, which is why a site can run dry there and still be covered here."
      >
        {plans.length ? (
          <DistributionWeekGrid plans={plans} />
        ) : (
          <EmptyState
            title="No distributions planned."
            hint="Plan a distribution day to see inbound supply against it."
          />
        )}
      </Card>

      <Card
        title="Weeks of cover by site"
        subtitle="From what has physically arrived and the rate you are admitting children. At four weeks you plan; at one you triage."
      >
        {cover.length ? (
          <DataTable
            rows={cover}
            rowKey={(c) => c.node_id}
            columns={[
              { key: 'site', label: 'Site', value: (c) => c.node_name },
              {
                key: 'stock',
                label: 'On hand',
                value: (c) => c.stock_on_hand,
                render: (c) => `${formatNumber(c.stock_on_hand)} cartons`,
              },
              {
                key: 'children',
                label: 'Children served / month',
                value: (c) => c.served_children,
                render: (c) => formatNumber(c.served_children),
              },
              {
                key: 'weeks',
                label: 'Weeks of cover',
                value: (c) => c.weeks_of_cover,
                render: (c) => (
                  <Badge
                    tone={
                      c.weeks_of_cover < 2
                        ? 'bad'
                        : c.weeks_of_cover < 4
                        ? 'warn'
                        : 'good'
                    }
                  >
                    {c.weeks_of_cover}
                  </Badge>
                ),
              },
              {
                key: 'dry',
                label: 'Runs dry',
                value: (c) => c.stockout_on,
                render: (c) => {
                  // Nothing has arrived, so there is no burn-down to date.
                  if (!c.stockout_on)
                    return (
                      <span className="muted">awaiting first consignment</span>
                    );
                  // Through the shared parser, so this day-count and every
                  // rendered date in the app agree about what day it is.
                  const days = Math.round(
                    (parseSupplyDate(c.stockout_on) -
                      parseSupplyDate(c.as_of)) /
                      86400000,
                  );
                  return (
                    <span>
                      {formatDate(c.stockout_on)}
                      <span className="muted small">
                        {' '}
                        · {days} day{days === 1 ? '' : 's'}
                      </span>
                    </span>
                  );
                },
              },
              {
                key: 'act',
                label: '',
                value: () => '',
                render: (c) => {
                  if (!supplyCan(world.role, 'signals', 'raise')) return null;
                  // Eleven identical filled buttons were the highest-contrast
                  // column on the card and out-shouted the cover figures they
                  // sat beside — a site with six weeks of stock offered the
                  // same call to action as one already dry. The filled variant
                  // is now reserved for sites at or below the stated triage
                  // threshold; everything else gets a quiet ghost control.
                  const open = openSignals.find((s) => s.site_id === c.node_id);
                  if (open) {
                    return (
                      <span className="muted small">
                        Raised {formatDate(open.raised_on)}
                      </span>
                    );
                  }
                  const urgent = c.weeks_of_cover < 2;
                  return (
                    <button
                      type="button"
                      className={`btn btn-sm ${
                        urgent ? 'btn-primary' : 'btn-ghost'
                      }`}
                      onClick={() => setRaising(c)}
                    >
                      Raise a shortfall
                    </button>
                  );
                },
              },
            ]}
          />
        ) : (
          <EmptyState
            title="No cover figures yet."
            hint="Cover appears once stock has been received at a site."
          />
        )}
        <p className="muted small method-note">
          {cover.length ? `As of ${formatDate(cover[0].as_of)}. ` : ''}
          {cover.length ? cover[0].method : ''} These are the same figures the
          OES command centre reads for these sites.
        </p>
      </Card>

      {signals.length ? (
        <Card
          title="Shortfalls you have raised"
          subtitle="Reported upward from this screen, and answered against the same record."
        >
          <DataTable
            rows={signals}
            rowKey={(s) => s.id}
            columns={[
              {
                key: 'raised',
                label: 'Raised',
                value: (s) => s.raised_on,
                render: (s) => formatDate(s.raised_on),
              },
              { key: 'site', label: 'Site', value: (s) => s.site_name },
              // The unit the entire supply chain is denominated in, and it was
              // the one field this record dropped. The narration names "the
              // cartons short" as part of what gets reported upward, and the
              // Receiving screen two cards above proves the product can carry
              // it — "ADVISED 900 / YOU COUNTED 840 / SHORT 60". A shortfall
              // stated only in children cannot be checked against the count
              // that produced it.
              {
                key: 'cartons',
                label: 'Cartons short',
                value: (s) => s.cartons_short,
                render: (s) => formatNumber(Math.round(s.cartons_short)),
              },
              {
                key: 'children',
                label: 'Children affected',
                value: (s) => s.children_affected,
                render: (s) => formatNumber(s.children_affected),
              },
              // "Marked as raised by Komadugu rather than derived centrally" is
              // the scene's claim, and it was carried only by the card title.
              // The command centre renders the same distinction as a badge on
              // the row; the partner's own screen should agree with it.
              {
                key: 'origin',
                label: 'Reported by',
                value: (s) => s.org_name,
                render: (s) => <Badge tone="info">{s.org_name}</Badge>,
              },
              {
                key: 'by',
                label: 'Needed by',
                value: (s) => s.needed_by,
                render: (s) => formatDate(s.needed_by),
              },
              {
                key: 'status',
                label: 'Status',
                value: (s) => s.status,
                render: (s) => (
                  <Badge tone={s.status === 'resolved' ? 'good' : 'warn'}>
                    {s.status}
                  </Badge>
                ),
              },
            ]}
          />
        </Card>
      ) : null}

      <Card
        title="From a batch to the children it treated"
        subtitle="Each distribution resolves to the batch that supplied it and to the treatment records of the children it fed. Distributions with outcomes recorded are listed first — a batch handed out last week has none yet."
      >
        {records.length ? (
          <DataTable
            rows={traceable}
            rowKey={(r) => r.id}
            onRowClick={(r) => setBatch(r)}
            columns={[
              {
                key: 'when',
                label: 'Distributed',
                value: (r) => r.distributed_on,
                render: (r) => formatDate(r.distributed_on),
              },
              { key: 'site', label: 'Site', value: (r) => r.site_name },
              { key: 'batch', label: 'Batch', value: (r) => r.batch_lot },
              {
                key: 'consignment',
                label: 'Shipment',
                value: (r) => r.shipment_reference || '',
                render: (r) => r.shipment_reference || '—',
              },
              {
                key: 'children',
                label: 'Children served',
                value: (r) => r.children_served,
                render: (r) => formatNumber(r.children_served),
              },
              {
                key: 'outcomes',
                label: 'Outcomes recorded',
                value: (r) => (r.outcomes || []).length,
                render: (r) => formatNumber((r.outcomes || []).length),
              },
            ]}
          />
        ) : (
          <EmptyState
            title="No distributions recorded yet."
            hint="A recorded distribution links a received batch to the children admitted on it."
          />
        )}
        <p className="muted small method-note">
          Treatment outcomes in this environment are synthetic and labelled as
          such wherever they appear.
        </p>
      </Card>

      {raising ? (
        <RaiseShortfall
          node={raising}
          onClose={() => setRaising(null)}
          onSubmit={(payload) =>
            act(
              () => supplyPost('/supply/api/signals/raise/', payload),
              'Shortfall raised. It is now in the OES command centre queue.',
            ).then(() => setRaising(null))
          }
        />
      ) : null}

      {batch ? (
        <BatchDrill
          record={batch}
          allRecords={records}
          onClose={() => setBatch(null)}
        />
      ) : null}
    </Page>
  );
}

function RaiseShortfall({ node, onClose, onSubmit }) {
  const shortfall = Math.max(
    0,
    Math.round(node.weekly_burn * 2 - node.stock_on_hand),
  );
  const [children, setChildren] = useState(String(shortfall || 100));
  const [cartons, setCartons] = useState(String(shortfall || 100));
  const [neededBy, setNeededBy] = useState(node.stockout_on || '');
  const [note, setNote] = useState('');

  return (
    <Modal
      title={`Raise a shortfall at ${node.node_name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() =>
              onSubmit({
                site_id: node.node_id,
                children_affected: Number(children),
                cartons_short: Number(cartons),
                needed_by: neededBy,
                note,
              })
            }
          >
            Raise it
          </button>
        </>
      }
    >
      <p className="muted small">
        This lands in the OES command centre ranked by the children behind it,
        marked as raised by your organisation — not as a message somebody may or
        may not read.
      </p>
      <FormRow label="Children affected">
        <input
          type="number"
          value={children}
          onChange={(e) => setChildren(e.target.value)}
        />
      </FormRow>
      <FormRow label="Cartons short">
        <input
          type="number"
          value={cartons}
          onChange={(e) => setCartons(e.target.value)}
        />
      </FormRow>
      <FormRow
        label="Needed by"
        hint={
          node.stockout_on
            ? `${node.node_name} is projected to run dry on ${formatDate(
                node.stockout_on,
              )}.`
            : `${node.node_name} has not received a consignment yet, so there is no projected stockout date.`
        }
      >
        <input
          type="date"
          value={neededBy}
          onChange={(e) => setNeededBy(e.target.value)}
        />
      </FormRow>
      <FormRow label="Note">
        <textarea
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="What changed — admissions above plan, a road reopening, a short receipt."
        />
      </FormRow>
    </Modal>
  );
}

function BatchDrill({ record, onClose, allRecords }) {
  // A lot split across sites is the normal case, and the product already holds
  // both legs — scoping a batch-titled modal to one of them contradicted the
  // table it was opened from and left the narration's plural "distributions"
  // with nothing on screen behind it.
  const siblings = (allRecords || [record]).filter(
    (r) => r.batch_lot === record.batch_lot,
  );
  const outcomes = siblings.flatMap((r) => r.outcomes || []);
  const recovered = outcomes.filter((o) => o.discharge_status === 'recovered');
  // The narration follows ONE child. An undifferentiated list makes the viewer
  // pick for themselves, so open on the strongest arc — the child who starts
  // deepest in the red and finishes clearest inside green.
  const strongest = outcomes.reduce(
    (best, o) =>
      !best ||
      o.latest_muac_mm - o.admission_muac_mm >
        best.latest_muac_mm - best.admission_muac_mm
        ? o
        : best,
    null,
  );
  const [focus, setFocus] = useState(strongest ? strongest.id : null);
  // The focused record leads. It was being built correctly and then rendered
  // eighth of thirteen, below the modal's own scroll fold — so the one child
  // the story is about was not on screen while it was being described.
  const ordered = focus
    ? [
        ...outcomes.filter((o) => o.id === focus),
        ...outcomes.filter((o) => o.id !== focus),
      ]
    : outcomes;

  return (
    <Modal
      title={`Batch ${record.batch_lot}`}
      onClose={onClose}
      wide
      footer={
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      }
    >
      <p className="muted small">
        Arrived on {record.shipment_reference || 'an unrecorded consignment'},
        distributed across {siblings.length}{' '}
        {siblings.length === 1 ? 'site' : 'sites'} —{' '}
        {siblings
          .map(
            (r) =>
              `${r.site_name} on ${formatDate(
                r.distributed_on,
              )} (${formatNumber(r.children_served)} children)`,
          )
          .join('; ')}
        . <strong>Treatment records in this environment are synthetic.</strong>
      </p>
      <p className="muted small">
        {recovered.length} of {outcomes.length} children in the recorded sample
        were discharged as recovered.{' '}
        <strong>
          That sample is {outcomes.length} of the{' '}
          {formatNumber(siblings.reduce((n, r) => n + r.children_served, 0))}{' '}
          children this batch fed
        </strong>{' '}
        — a rate from {outcomes.length} children carries a wide interval and is
        not the batch&rsquo;s recovery rate.
      </p>
      <MuacLegend />
      <div className="outcome-list">
        {ordered.map((child) => (
          <MuacSeries
            key={child.id}
            child={child}
            focused={child.id === focus}
            onFocus={() => setFocus(child.id)}
          />
        ))}
      </div>
    </Modal>
  );
}

/* The bands carry the entire clinical claim and were never defined on screen.
   The stated audience is a supply manager, not a clinician — without a key,
   "out of the red and into green" is undecodable colour. */
function MuacLegend() {
  return (
    <div className="muac-legend">
      <span>
        <i className="muac-swatch sam" /> Severe · under 115 mm
      </span>
      <span>
        <i className="muac-swatch mam" /> Moderate · 115–124 mm
      </span>
      <span>
        <i className="muac-swatch ok" /> Recovered · 125 mm and above
      </span>
      <span className="muted">
        WHO mid-upper-arm-circumference thresholds, children 6–59 months
      </span>
    </div>
  );
}

function MuacSeries({ child, focused, onFocus }) {
  const series = child.measurements || [];
  if (!series.length) return null;
  // A viewBox wide enough to match the box it is drawn into.
  //
  // The SVG is `width: 100%; height: 56px` in a column several hundred pixels
  // wide, and the default preserveAspectRatio (xMidYMid meet) scales to
  // whichever axis binds — with a 220-wide viewBox against a 56px height, that
  // is the height, so it rendered at 1:1 and letterboxed the rest. The
  // narrative's closing payoff, a child's arm circumference climbing out of the
  // red, drew at roughly a third of its own width with 7px labels.
  //
  // Widening the viewBox fixes the ratio rather than stretching it with
  // `preserveAspectRatio="none"`, which would horizontally distort the two WHO
  // threshold labels drawn inside it.
  const width = 560;
  // ONE height for every child in the batch, so the vertical scale is the
  // same on every row and two series can be compared by eye. The focused row
  // used to be drawn 96px tall against 56 for the rest — the same millimetre
  // domain, but 2.5px/mm against 1.4px/mm, so the child the reader is looking
  // at climbed more steeply than the others purely because it was selected.
  // Focus now adds information (the visit count, the labelled thresholds),
  // not vertical exaggeration.
  const height = 68;
  const lo = 95;
  const hi = 135;
  const x = (i) => (i / Math.max(series.length - 1, 1)) * width;
  const y = (mm) => height - ((mm - lo) / (hi - lo)) * height;
  const path = series
    .map(
      (m, i) =>
        `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(m.muac_mm).toFixed(1)}`,
    )
    .join(' ');

  return (
    <div
      className={`outcome-row ${focused ? 'focused' : ''}`}
      onClick={onFocus}
      role="button"
      tabIndex={0}
    >
      <div className="outcome-id">
        <strong>{child.anon_id}</strong>
        <Badge tone={child.discharge_status === 'recovered' ? 'good' : 'warn'}>
          {child.discharge_label}
        </Badge>
        {/* "2 visits over 1 weeks" — in the most-looked-at text in the modal,
            since it only renders on the focused row. */}
        {focused ? (
          <span className="muted small">
            {series.length} {series.length === 1 ? 'visit' : 'visits'}
            {series.length > 1
              ? ` over ${series.length - 1} ${
                  series.length - 1 === 1 ? 'week' : 'weeks'
                }`
              : ''}
          </span>
        ) : null}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="muac-spark">
        {/* The red / amber / green bands a MUAC series is read against. */}
        <rect
          x="0"
          y={y(115)}
          width={width}
          height={height - y(115)}
          className="muac-band sam"
        />
        <rect
          x="0"
          y={y(125)}
          width={width}
          height={y(115) - y(125)}
          className="muac-band mam"
        />
        <rect
          x="0"
          y="0"
          width={width}
          height={y(125)}
          className="muac-band ok"
        />
        {/* The two thresholds, drawn and labelled, so colour is not the only
            thing carrying the clinical meaning. */}
        <line x1="0" x2={width} y1={y(115)} y2={y(115)} className="muac-rule" />
        <line x1="0" x2={width} y1={y(125)} y2={y(125)} className="muac-rule" />
        {focused ? (
          <>
            <text x="2" y={y(125) - 2} className="muac-rule-label">
              125 mm · recovered
            </text>
            <text x="2" y={y(115) - 2} className="muac-rule-label">
              115 mm · severe
            </text>
          </>
        ) : null}
        <path d={path} className="muac-line" />
        {/* One marker per visit — a bare line reads as a two-point
            interpolation, which is not what "across their visits" claims. */}
        {series.map((m, i) => (
          <circle
            key={m.date}
            cx={x(i)}
            cy={y(m.muac_mm)}
            r={focused ? 3 : 2}
            className="muac-point"
          >
            <title>{`${m.date}: ${m.muac_mm} mm`}</title>
          </circle>
        ))}
      </svg>
      <div className="outcome-figures">
        {child.admission_muac_mm} → {child.latest_muac_mm} mm
        {focused ? (
          <div className="muted small">
            +{child.latest_muac_mm - child.admission_muac_mm} mm
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* A week, not a list sorted by date.

   The narration this card exists to carry says it out loud: "a shipment table
   sorted by arrival date is a supplier's view of the world; a partner's unit of
   planning is a distribution day, at a named site, with a known number of
   children expected." It was rendered as a table sorted by arrival date — the
   exact artifact the sentence disowns, so the contrast was asserted over its
   own counterexample and a viewer had to take it on trust.

   A site per row and a day per column is the shape a distribution plan is
   actually held in. It also makes the thing worth seeing visible without
   reading: an uncovered cell is a hole in a grid, and the eye finds it before
   the number in it. */
function DistributionWeekGrid({ plans }) {
  // A week. The narration calls this "the frame you actually plan in: a week, a
  // site, and the children booked in for each day", and a fortnight of columns
  // both contradicts that and overflows the content area — which scrolled the
  // whole page sideways and took the nav rail off the left edge.
  // A CONTIGUOUS seven days, not the first seven days that happen to have a
  // distribution on them. Taking the distinct planned dates worked only while
  // every site sat on its own day; once the schedule was allowed to cluster
  // the way a real one does, seven distinct dates spanned eleven calendar days
  // with the gaps silently removed — a grid headed "a week" running Thu 30 Jul
  // to Sun 9 Aug. The empty columns are the point: the legend already says an
  // empty cell is a day with nothing planned.
  const planned = Array.from(new Set(plans.map((p) => p.scheduled_for))).sort();
  const first = parseSupplyDate(planned[0]);
  const days = [];
  for (let i = 0; i < 7 && first; i += 1) {
    const d = new Date(
      first.getFullYear(),
      first.getMonth(),
      first.getDate() + i,
    );
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(
      2,
      '0',
    )}-${String(d.getDate()).padStart(2, '0')}`;
    days.push(iso);
  }
  const daySet = new Set(days);
  plans = plans.filter((p) => daySet.has(p.scheduled_for));
  const sites = Array.from(new Set(plans.map((p) => p.site_name))).sort();
  const byKey = {};
  plans.forEach((p) => {
    byKey[`${p.site_name}|${p.scheduled_for}`] = p;
  });

  const dayLabel = (iso) => {
    const d = parseSupplyDate(iso);
    if (!d) return iso;
    return d.toLocaleDateString(undefined, {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
    });
  };

  return (
    <div className="week-grid-wrap">
      <table className="week-grid">
        <thead>
          <tr>
            <th className="week-grid-site">Site</th>
            {days.map((d) => (
              <th key={d}>{dayLabel(d)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sites.map((site) => (
            <tr key={site}>
              <th className="week-grid-site">{site}</th>
              {days.map((d) => {
                const plan = byKey[`${site}|${d}`];
                if (!plan) return <td key={d} className="week-cell empty" />;
                return (
                  <td key={d} className={`week-cell ${plan.state}`}>
                    <div className="week-cell-children">
                      {formatNumber(plan.expected_children)}
                    </div>
                    <div className="week-cell-stock">
                      {formatNumber(plan.cartons_on_hand)} on hand
                    </div>
                    {/* NOT "+141". cartons_inbound is what is still on the road
                        AFTER this day (see serializers/demand.distribution_plan_dict),
                        so it deliberately does not cover this distribution — and
                        rendering it with a plus sign, beside the number that IS
                        the coverage basis, under a legend saying a cell is short
                        "when the second number is below the first", invited
                        exactly the addition that contradicts the colour. Biu read
                        "0 on hand +141" for 103 children in red; Askira "38 on
                        hand +94" for 65 in amber. Both look fine if you add. */}
                    {plan.cartons_inbound ? (
                      <div className="week-cell-later muted">
                        {formatNumber(plan.cartons_inbound)} arrives after
                      </div>
                    ) : null}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small method-note">
        Each cell is the children booked in that day and the cartons on hand for
        them — opening stock plus every consignment that lands on or before that
        day, minus what earlier distributions already spent. One carton is one
        child's full course, so a cell is short exactly when the stock figure is
        below the children figure. Cartons still in transit are shown as
        &ldquo;arrives after&rdquo;: they land later than this distribution and
        so do not cover it, which is why a cell can be red with a lorry on the
        way. Green is covered, amber at risk, red uncovered; an empty cell is a
        day with no distribution planned.
      </p>
    </div>
  );
}
