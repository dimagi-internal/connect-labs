/* Operations command centre — the hero screen.

   Exception-first, following how real control towers are actually laid out
   (project44 / FourKites): the home surface is a prioritised worklist of
   at-risk consignments, and the map is context beside it, not the product.
   Each exception row answers three things in order: what is wrong, why, and
   what to do about it. */

/* Severity is NOT computed here.

   It used to be: tonnage x lateness for a late consignment, raw shortfall x 2
   for a short receipt. Those are not comparable, so the ordering of the queue
   was arbitrary at exactly the point it mattered most — and no test in this
   repo could reach a function in this file.

   The queue now arrives ranked from services/exceptions.py, where every row
   carries the same unit (children who miss a full course) and the derivation
   that produced it. The partner surface reads the same numbers from the same
   place, so the two cannot disagree about a node. */

function CommandTab({ ctx }) {
  const { world, act } = ctx;
  const contracts = world.contracts || [];
  const nodes = world.nodes || [];
  const shipments = contracts.flatMap((c) => c.shipments || []);
  const exceptions = world.exceptions || [];
  const cover = world.cover || [];
  const coverage = world.coverage || [];
  const [selected, setSelected] = useState(null);
  const [openContract, setOpenContract] = useState(null);
  const [reallocatingFor, setReallocatingFor] = useState(null);
  const [expeditingFor, setExpeditingFor] = useState(null);
  const [openShipmentId, setOpenShipmentId] = useState(null);

  // Split the cover table: somewhere with stock has a run-dry date, somewhere
  // awaiting its first consignment does not, and mixing them ranks the second
  // group above the first because zero sorts lowest.
  const served = cover.filter((r) => !r.awaiting_first_delivery);
  const awaitingFirst = cover.filter((r) => r.awaiting_first_delivery);

  const inTransit = shipments.filter((s) => s.status === 'in_transit');
  const deliveredCartons = contracts.reduce(
    (n, c) => n + c.delivered_quantity,
    0,
  );
  const tonnesInFlight = inTransit.reduce(
    (n, s) => n + (s.metric_tonnes || 0),
    0,
  );

  return (
    <Page
      title="Operations command centre"
      lede="Consignments at risk first; the network map for context."
    >
      <KeyFigures
        figures={[
          {
            label: 'Children at risk',
            // Children behind rows nobody has acted on. A row with cartons
            // already on the road is still outstanding, but counting it here
            // meant the figure could not move when Ada did something — the
            // headline was identical before and after a reallocation.
            lead: true,
            tone: 'critical',
            method:
              'Every exception on this screen, converted to the same unit: children who miss a full course if nothing is done. Rows somebody has already acted on are excluded, so this figure moves when a decision is taken. Ranked on the children who go without inside the next 30 days, because harm falling outside the window a decision can still affect is real but is not what this worklist is for.',
            value: formatNumber(
              exceptions
                .filter((e) => !e.answered_by && !e.resolved_by)
                .reduce((n, e) => n + (e.children_at_risk || 0), 0),
            ),
            hint: (() => {
              const open = exceptions.filter(
                (e) => !e.answered_by && !e.resolved_by,
              ).length;
              const closed = exceptions.filter((e) => e.resolved_by).length;
              const answered = exceptions.length - open - closed;
              if (!exceptions.length) return 'nothing outstanding';
              return `across ${open} unanswered exception${
                open === 1 ? '' : 's'
              }${answered ? ` · ${answered} answered` : ''}${
                closed ? ` · ${closed} closed` : ''
              }`;
            })(),
          },
          // A method on all four, not only on the one that happens to be
          // contestable. The `i` bubble sat on "Children at risk" alone, so the
          // three tiles beside it read as facts needing no explanation while the
          // page's whole argument is that every figure carries a method.
          {
            label: 'In transit',
            value: inTransit.length,
            hint: `${Math.round(tonnesInFlight)} MT moving`,
            method:
              'Consignments whose status is in transit — despatched and not yet arrived. Counted as consignments, not cartons, because one lorry is one thing that can be expedited. The tonnage beside it is the same set converted at 150 x 92 g sachets per carton.',
          },
          {
            label: 'Delivered to date',
            value: formatNumber(deliveredCartons),
            // Courses, not children — and it says which contracts it counts.
            // "N children treated" over a carton count is the conflation the
            // funder page's own card exists to attack, and this tile was a
            // third number wearing that same label.
            hint: `${Math.round(
              (deliveredCartons * 150 * 92) / 1000000,
            )} MT · ${formatNumber(
              deliveredCartons,
            )} courses, at contracted delivery points`,
            method:
              'Cartons recorded as delivered against a contract, at the delivery point that contract names. One carton is one full course. This says nothing about whether a course reached a child — the government and funder surfaces report that separately.',
          },
          {
            label: 'Active contracts',
            value: contracts.length,
            method:
              'Contracts with an award behind them that have not been closed out. A contract counts once however many consignments it has moved, so this is a count of commercial relationships under management rather than of activity.',
          },
        ]}
      />

      {/* Two columns only when there is a second thing to put in one. Without
          a Mapbox token the right-hand column was a dashed grey box and the
          worklist — the actual product on this screen — was squeezed into
          about a quarter of the frame. */}
      <div className={`command-split ${mapAvailable() ? '' : 'no-map'}`}>
        <Card
          title="Exceptions"
          // The caption has to name the horizon, because the horizon is what
          // actually orders the list. "Ranked by the children behind each one"
          // alone reads as a promise the order then appears to break: a
          // 907-child expiry due in December sits below a 47-child row due next
          // week, and only the expanded derivation explains why. The rule was
          // right and unstated, which is indistinguishable from wrong.
          subtitle="Ranked by the children behind each one — those inside the next 30 days first, since that is the window a decision today can still change — never by tonnage."
          className="exception-panel"
        >
          {exceptions.length ? (
            <div className="exception-list">
              {exceptions.map((e) => (
                <button
                  type="button"
                  key={e.key}
                  className={`exception ${
                    selected === e.key ? 'selected' : ''
                  }`}
                  onClick={() => setSelected(e.key)}
                >
                  <div className="exception-head">
                    <Badge tone={e.tone}>{e.kind}</Badge>
                    {/* A provenance stamp needs the WHEN, not just the who.
                        The declared feature promises a marker "naming the
                        organisation, site and report date" and the pill carried
                        two of the three; the date existed on the row but rendered
                        only inside the derivation, which shows on the selected
                        row alone. Without it a reader cannot tell whether the
                        partner reported this today or last month — which is the
                        half that makes it provenance rather than a label. */}
                    {e.origin === 'partner' ? (
                      <Badge tone="info">
                        Raised by {e.org_name || 'a partner'}
                        {e.raised_on ? ` · ${formatDate(e.raised_on)}` : ''}
                        {e.raised_on && world.as_of
                          ? ` (${daysBetweenLabel(e.raised_on, world.as_of)})`
                          : ''}
                      </Badge>
                    ) : null}
                    <span className="exception-what">{e.what}</span>
                  </div>
                  {e.children_at_risk ? (
                    <div className="exception-risk">
                      <strong>{formatNumber(e.children_at_risk)}</strong>{' '}
                      children lose a full course
                      {e.by_date ? ` by ${formatDate(e.by_date)}` : ''}
                    </div>
                  ) : null}
                  <div className="exception-why">{e.why}</div>
                  {selected === e.key && e.derivation ? (
                    <div className="exception-derivation">
                      How this was ranked: {e.derivation}
                      {/* Why it sits where it sits, when the harm falls
                          outside the window a decision today can affect.
                          Ranking on the raw figure alone put 907 children due
                          in December above 87 due next week, on a screen that
                          promises "where, and by when". */}
                      {e.children_at_risk && !e.children_at_risk_soon
                        ? ` Ranked below rows costing children within ${
                            e.decision_horizon_days
                          } days: this falls due ${formatDate(
                            e.by_date,
                          )}, outside the window a decision taken today can change.`
                        : ''}
                    </div>
                  ) : null}
                  {/* Closed and answered are different states and the
                      difference is the product's own argument. A partner
                      signal RESOLVES: the thing that was reported is no longer
                      true. A derived row can only be ANSWERED — cartons are on
                      the road, and until they land the children behind it are
                      still at risk. Rendering both as "done" would claim the
                      invariant the rest of this screen exists to keep. */}
                  {/* The status pill sits on its own line, not as the first
                      token of the sentence beside it. Set inline, "Closed" read
                      as the subject of "the reallocation moved 900 cartons…" —
                      a chip pressed into service as a word. */}
                  {e.resolved_by ? (
                    <div className="exception-answered">
                      <div className="exception-answered-head">
                        <Badge tone="good">Closed</Badge>
                      </div>
                      {e.resolved_by.effect}
                      <div className="muted small">
                        {e.resolved_by.rationale}
                      </div>
                      <div className="muted small">
                        Closed by {e.resolved_by.actor} on{' '}
                        {formatDate(e.resolved_by.resolved_on)}, against the
                        reallocation that answered it.
                      </div>
                    </div>
                  ) : e.answered_by ? (
                    <div className="exception-answered">
                      <div className="exception-answered-head">
                        <Badge tone="good">Answered</Badge>
                      </div>
                      {e.answered_by.effect}
                      <div className="muted small">
                        {e.answered_by.rationale}
                      </div>
                    </div>
                  ) : (
                    <div
                      className={`exception-action${
                        e.monitor_only ? ' monitor-only' : ''
                      }`}
                    >
                      {e.monitor_only ? 'Monitor · ' : '→ '}
                      {e.action}
                    </div>
                  )}
                  {/* A late row names a consignment and could not open it.
                      The milestone rail (planned / estimated / actual kept
                      apart) and the append-only event log behind that status
                      are the two things that make this queue trustworthy, and
                      both were one component away with no route to them from
                      the surface that depends on them. */}
                  {/* Inspect is the primary; commit is not.
                      The state-changing "Reallocate to …" was the solid-green
                      primary while the read-only "Open SHP-…" was the outline
                      secondary, so the loudest control on the worklist was the
                      one that moves stock. Reading the consignment first is what
                      a reader should be nudged toward. */}
                  {e.shipment_id ? (
                    <span
                      className="btn btn-sm"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setOpenShipmentId(e.shipment_id);
                      }}
                    >
                      Open {e.shipment_reference}
                    </span>
                  ) : null}
                  {/* The queue has always ADVISED a reallocation and never
                      offered one, so the single sentence that tells the reader
                      what to do about a row was the only thing on the card
                      they could not act on. */}
                  {/* The verb the recommendation opens with, wired at last.
                      The endpoint, service and action kind existed the whole
                      time; only the control was missing, and an earlier fix
                      reworded the sentence to match the gap instead of closing
                      it. Same secondary weight as Reallocate — both commit. */}
                  {/* A monitor-only row offers no commit control. Its own
                      sentence says no action is needed; putting Expedite and
                      Reallocate on it anyway contradicts that in the loudest
                      element on the card. Open stays — reading the consignment
                      is exactly what "watch it" means. */}
                  {e.shipment_id &&
                  !e.answered_by &&
                  !e.monitor_only &&
                  /expedite/i.test(e.action || '') &&
                  supplyCan(world.role, 'actions', 'create') ? (
                    <span
                      className="btn btn-sm btn-secondary"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setExpeditingFor(e);
                      }}
                    >
                      Expedite {e.shipment_reference}
                    </span>
                  ) : null}
                  {e.node_id &&
                  !e.answered_by &&
                  !e.monitor_only &&
                  /reallocate/i.test(e.action || '') &&
                  supplyCan(world.role, 'actions', 'create') ? (
                    <span
                      className="btn btn-sm btn-secondary"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setReallocatingFor(e);
                      }}
                    >
                      {e.reallocation_role === 'source'
                        ? `Reallocate from ${e.node_name}`
                        : `Reallocate to ${e.node_name}`}
                    </span>
                  ) : null}
                  {e.discrepancy_id &&
                  supplyCan(world.role, 'execution', 'resolve') ? (
                    <span
                      className="btn btn-sm"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        act(
                          () =>
                            supplyPost(
                              `/supply/api/discrepancies/${e.discrepancy_id}/resolve/`,
                              { note: 'Reconciled from the command centre.' },
                            ),
                          'Discrepancy resolved.',
                        );
                      }}
                    >
                      Resolve
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No consignments at risk."
              hint="Every shipment is tracking to plan."
            />
          )}
        </Card>

        <Card
          title="Network"
          subtitle="Flows follow real road and sea corridors."
          className="map-panel"
        >
          <FlowMap nodes={nodes} shipments={shipments} fill />
        </Card>
      </div>

      <Card
        title="Pipeline by corridor"
        subtitle="Contracted quantity against what is confirmed and what has landed. The requirement from caseload sits below."
      >
        <DataTable
          rows={contracts}
          rowKey={(c) => c.id}
          onRowClick={(c) => setOpenContract(c)}
          columns={[
            { key: 'ref', label: 'Contract', value: (c) => c.reference },
            { key: 'org', label: 'Supplier', value: (c) => c.org_name },
            {
              key: 'dest',
              label: 'Destination',
              value: (c) => c.destination,
              render: (c) =>
                `${c.destination}, ${countryLabel(c.destination_country)}`,
            },
            {
              key: 'req',
              label: 'Requirement',
              value: (c) => c.total_quantity,
              render: (c) => `${formatNumber(c.total_quantity)} ${c.unit}`,
            },
            {
              key: 'shipped',
              label: 'Shipped',
              value: (c) => c.shipped_quantity,
              render: (c) => formatNumber(c.shipped_quantity),
            },
            {
              key: 'delivered',
              label: 'Delivered',
              value: (c) => c.delivered_quantity,
              render: (c) => formatNumber(c.delivered_quantity),
            },
            {
              key: 'gap',
              label: 'Gap',
              value: (c) => c.total_quantity - c.delivered_quantity,
              render: (c) => {
                const gap = c.total_quantity - c.delivered_quantity;
                return gap > 0 ? (
                  <Badge tone="warn">{formatNumber(gap)}</Badge>
                ) : (
                  <Badge tone="good">met</Badge>
                );
              },
            },
          ]}
        />
      </Card>

      <Card
        title="Requirement from caseload"
        subtitle="What the districts need, against what has actually landed in them. A contract quantity is what was bought; this is what is required."
      >
        {coverage.length ? (
          <DataTable
            rows={coverage}
            rowKey={(r) => r.adm1_code}
            columns={[
              {
                key: 'district',
                // adm1 is the FIRST administrative level — Borno, Yobe, Kassala
                // are states and regions, not districts. Labelling them
                // "District" invited a reader to compare these caseloads against
                // district-level figures they might have from elsewhere, which
                // are a level finer and an order of magnitude smaller.
                label: 'State / region',
                value: (r) => r.adm1_name,
                render: (r) => (
                  <span>
                    {r.adm1_name}{' '}
                    <Badge
                      tone={
                        r.ipc_phase >= 5
                          ? 'bad'
                          : r.ipc_phase >= 4
                          ? 'warn'
                          : 'muted'
                      }
                    >
                      IPC {r.ipc_phase}
                    </Badge>
                  </span>
                ),
              },
              {
                key: 'caseload',
                label: 'Requirement (children)',
                value: (r) => r.caseload,
                // The provenance of the denominator, reachable. It used to ride
                // on the row's native `title` attribute — no touch path, no
                // keyboard path, nothing for a screen reader, and invisible in
                // anything printed or projected. It is the estimate every
                // coverage figure on this page divides by, so it cannot be
                // hover-only.
                render: (r) => (
                  <span className="cell-with-note">
                    {formatNumber(r.caseload)}
                    {r.source_note ? (
                      <InfoNote
                        label={`the ${r.adm1_name} caseload`}
                        text={r.source_note}
                      />
                    ) : null}
                  </span>
                ),
              },
              {
                key: 'delivered',
                label: 'Courses delivered',
                value: (r) => r.courses_delivered,
                render: (r) => formatNumber(r.courses_delivered),
              },
              {
                key: 'coverage',
                // Not "Coverage": the numerator is everything confirmed INTO the
                // district, hub stock included — 93% of it on the seeded world.
                // See services/coverage._dispensed_cartons_by_district.
                label: 'Supply positioned',
                value: (r) => r.coverage_percent || 0,
                render: (r) =>
                  r.coverage_percent === null ? (
                    <Badge tone="muted">no data</Badge>
                  ) : (
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
                key: 'gap',
                label: 'Gap to need (children)',
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
        {/* One legend line, with the method behind the `i`.
            Two dense grey paragraphs stacked under the table read as small
            print — on the card whose claim is that every figure carries its
            method. The window is the load-bearing half (the denominator is the
            caseload summed across the response window, not a single month);
            the rest is available on demand. */}
        <p className="muted small method-note">
          Courses delivered ÷ SAM caseload over the{' '}
          {coverage[0] && coverage[0].window_months
            ? `${coverage[0].window_months}-month response window`
            : 'response window'}
          . All figures synthetic.
          <InfoNote
            label="this coverage figure"
            text="Coverage is courses delivered divided by the district's SAM caseload summed over the whole response window, not against a single month — a monthly denominator would overstate coverage roughly fourfold. The 'i' beside each caseload gives the method that caseload was estimated by, so the denominator can be checked as well as the numerator. Every figure in this environment is synthetic."
          />
        </p>
        <CoverageBandsNote />
      </Card>

      <Card
        title="Weeks of cover"
        subtitle="Stock on hand against the rate each site is admitting children — the date the store runs dry."
      >
        {/* Nodes that have actually been served, thinnest first.
            The card sorted every node by weeks of cover and truncated at
            twelve — and a node that has never received anything scores zero,
            so ten never-served hubs and transit points led the table and
            pushed the two sites with real stock off the bottom. The card
            exists to say WHEN A STORE RUNS DRY, and it was showing the nodes
            for which that question has no answer yet. */}
        {cover.length ? (
          <DataTable
            rows={served}
            rowKey={(r) => r.node_id}
            columns={[
              { key: 'node', label: 'Node', value: (r) => r.node_name },
              {
                key: 'stock',
                label: 'On hand (cartons)',
                value: (r) => r.stock_on_hand,
                render: (r) => formatNumber(r.stock_on_hand),
              },
              {
                key: 'burn',
                label: 'Weekly burn',
                value: (r) => r.weekly_burn,
                render: (r) => formatNumber(Math.round(r.weekly_burn)),
              },
              {
                key: 'weeks',
                label: 'Weeks of cover',
                value: (r) => r.weeks_of_cover,
                // A site awaiting its first delivery has no cover figure to
                // colour. Showing it as a red 0 puts it in the same visual
                // language as a site two days from running dry, and the two
                // need opposite responses.
                render: (r) =>
                  r.awaiting_first_delivery ? (
                    <Badge tone="info">Not yet served</Badge>
                  ) : (
                    <Badge
                      tone={
                        r.weeks_of_cover < 2
                          ? 'bad'
                          : r.weeks_of_cover < 4
                          ? 'warn'
                          : 'good'
                      }
                    >
                      {r.weeks_of_cover}
                    </Badge>
                  ),
              },
              {
                key: 'dry',
                label: 'Runs dry',
                value: (r) => r.stockout_on || '',
                render: (r) =>
                  r.stockout_on ? (
                    formatDate(r.stockout_on)
                  ) : (
                    <span className="muted">awaiting first consignment</span>
                  ),
              },
            ]}
          />
        ) : (
          <EmptyState
            title="No cover figures yet."
            hint="No node carries a caseload."
          />
        )}
        {/* Reported, not dropped. Ten nodes with no receipt behind them are a
            fact about the network — a hub that has never been served is worth
            knowing about — they just are not rows in a run-dry table. */}
        {awaitingFirst.length ? (
          <p className="muted small">
            <strong>{awaitingFirst.length}</strong> further node
            {awaitingFirst.length === 1 ? ' has' : 's have'} received nothing
            yet and so have no run-dry date:{' '}
            {awaitingFirst.map((r) => r.node_name).join(', ')}.
          </p>
        ) : null}
        <p className="muted small method-note">
          {cover.length ? cover[0].method : ''}
        </p>
      </Card>

      {openShipmentId ? (
        <ShipmentDetail
          ctx={ctx}
          shipmentId={openShipmentId}
          onClose={() => setOpenShipmentId(null)}
        />
      ) : null}

      {expeditingFor ? (
        <ExpediteModal
          ctx={ctx}
          exception={expeditingFor}
          onClose={() => setExpeditingFor(null)}
        />
      ) : null}

      {reallocatingFor ? (
        <ReallocateModal
          ctx={ctx}
          exception={reallocatingFor}
          surplus={world.surplus_nodes || []}
          cover={world.cover || []}
          nodes={nodes}
          onClose={() => setReallocatingFor(null)}
        />
      ) : null}

      {openContract ? (
        <ContractDetailModal
          contract={openContract}
          onClose={() => setOpenContract(null)}
        />
      ) : null}
    </Page>
  );
}

/* What the award became.

   The award is the immutable decision; this is the instrument that carries it
   out. Until this existed the pipeline table was the end of the road — four
   reference strings in a column — and the claim that a dollar can be traced to
   a carton had nowhere on screen to be true. The three things that make the
   trace possible are the three things this shows: the funding envelope the
   money is drawn from, the IATI activity identifier that makes it reconcilable
   against a published aid dataset, and the consignments the quantity is
   actually moving on. */
function ContractDetailModal({ contract, onClose }) {
  const appropriation = contract.appropriation;
  const shipments = contract.shipments || [];
  const gap = contract.total_quantity - contract.delivered_quantity;

  return (
    <Modal wide title={contract.reference} onClose={onClose}>
      <div className="detail-head">
        <StatusChip status={contract.status} />
        {/* The immutability the narration asserts, as a fact on the screen.
            "The award is immutable" had no on-screen correlate at all — the
            reader was asked to take the load-bearing property of the whole
            procurement chain on the voiceover's word. */}
        {contract.awarded_at ? (
          <span className="cell-with-note">
            <Badge tone="verified">Awarded · locked</Badge>
            <InfoNote
              label="the locked award"
              text="The award this contract carries out is a closed decision: the winning supplier, the quantity and the unit price were fixed when the lot was awarded and cannot be edited afterwards. Changing any of them means a new award. The contract is the mutable part — consignments, milestones and disbursements accumulate against it — which is why the two are separate records."
            />
          </span>
        ) : null}
        <span className="muted">
          {contract.org_name} · {contract.destination},{' '}
          {countryLabel(contract.destination_country)}
        </span>
      </div>
      <p className="modal-lede">
        {contract.lot_description}
        {contract.source_solicitation ? (
          <span className="muted">
            {' '}
            · awarded under {contract.source_solicitation}
            {contract.awarded_at ? `, ${formatDate(contract.awarded_at)}` : ''}
          </span>
        ) : null}
      </p>

      <Card
        title="Drawn against"
        subtitle="The appropriation this contract obligates money from."
      >
        {appropriation ? (
          <div className="kv-grid">
            <div>
              <span className="muted small">Funder</span>
              <div>{appropriation.funder_name}</div>
            </div>
            <div>
              <span className="muted small">Appropriation</span>
              {/* The fiscal year, once. Every seeded appropriation title opens
                  with its own FY token, so appending the field rendered
                  "FY2026 Emergency Food Security — … · FY2026". */}
              <div>
                {appropriation.title}
                {appropriation.fiscal_year &&
                !String(appropriation.title || '').includes(
                  String(appropriation.fiscal_year),
                )
                  ? ` · ${appropriation.fiscal_year}`
                  : ''}
              </div>
            </div>
            <div>
              <span className="muted small">IATI activity</span>
              <div>
                {contract.iati_activity_id ||
                  appropriation.iati_activity_id ||
                  '—'}
              </div>
            </div>
            <div>
              <span className="muted small">Obligated</span>
              <div>
                {formatMoney(contract.obligated_value, contract.currency)}
              </div>
            </div>
            <div>
              <span className="muted small">Disbursed</span>
              <div>
                {formatMoney(contract.disbursed_value, contract.currency)}
              </div>
              {/* The qualifier as a normal secondary caption on its own line.
                  Trailing the figure in 11px muted caps it read as a cramped
                  annotation on the number rather than the substantive
                  restriction it is. */}
              <div className="kv-caption">against confirmed delivery only</div>
            </div>
            <div>
              <span className="muted small">Unit price</span>
              <div>{formatMoney(contract.unit_price, contract.currency)}</div>
            </div>
          </div>
        ) : (
          <EmptyState title="No appropriation linked." />
        )}
      </Card>

      <Card
        title="Delivery schedule"
        subtitle="The consignments this contract is moving on, and where each one has reached."
      >
        <DataTable
          rows={shipments}
          rowKey={(s) => s.id}
          empty="No consignments raised against this contract yet."
          columns={[
            { key: 'ref', label: 'Consignment', value: (s) => s.reference },
            {
              key: 'route',
              label: 'Route',
              sortable: false,
              value: () => '',
              render: (s) => `${s.origin.name} → ${s.destination.name}`,
            },
            {
              key: 'qty',
              label: 'Quantity',
              value: (s) => s.quantity,
              render: (s) => `${formatNumber(s.quantity)} ${s.unit}`,
            },
            {
              key: 'eta',
              label: 'Due',
              value: (s) => s.eta,
              render: (s) => formatDate(s.eta),
            },
            {
              key: 'tier',
              label: 'Reported by',
              sortable: false,
              value: () => '',
              // The picture is brightest where access is easiest, and saying so
              // on the same row as the delivery is the difference between an
              // honest map and a confident one.
              render: (s) => <TierBadge tier={s.source_tier} />,
            },
            {
              key: 'status',
              label: 'Status',
              value: (s) => s.status,
              render: (s) => <StatusChip status={s.status} />,
            },
          ]}
        />
        <p className="muted small method-note">
          {formatNumber(contract.total_quantity)} {contract.unit} contracted ·{' '}
          {formatNumber(contract.delivered_quantity)} confirmed at destination ·{' '}
          {gap > 0 ? `${formatNumber(gap)} outstanding` : 'requirement met'}.
          Status and quantities are derived from the event log, not entered.
        </p>
      </Card>
    </Modal>
  );
}

/* Moving surplus is a decision, so it records one.

   `services/actions.reallocate` and `POST api/actions/reallocate/` have existed
   since the demand stage landed; what did not exist was any way to reach them.
   The exception queue advised "reallocate from a node holding surplus" on every
   late consignment and every partner shortfall, and that advice was the one
   thing on the card a reader could not act on.

   The source list is not a node picker over the whole network. It is the nodes
   that genuinely hold more than their own caseload can consume, each showing
   what it could spare without dropping below its own threshold — because a
   reallocation that solves one stockout by causing another is not a decision
   anybody would defend afterwards. */
/* Chase one consignment, on the record.

   An expedite moves no stock and changes no figure — the cartons are exactly
   where they were. What it creates is the append-only decision record the rest
   of this screen is built on: who chased which consignment, when, and why. The
   rationale is required by the service, so the form says so up front instead of
   letting a submit bounce. */
function ExpediteModal({ ctx, exception, onClose }) {
  const { act } = ctx;
  const [rationale, setRationale] = useState('');
  const ready = rationale.trim().length > 0;
  return (
    <Modal
      title={`Expedite ${exception.shipment_reference}`}
      onClose={onClose}
      footer={
        <React.Fragment>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn"
            disabled={ctx.busy || !ready}
            onClick={() =>
              act(
                () =>
                  supplyPost(
                    `/supply/api/actions/expedite/${exception.shipment_id}/`,
                    { rationale: rationale.trim() },
                  ),
                `${exception.shipment_reference} escalated with the carrier.`,
                // act() swallows errors and returns null; closing
                // unconditionally would dismiss a failure worth retrying.
              ).then((result) => {
                if (result) onClose();
              })
            }
          >
            Record the expedite
          </button>
        </React.Fragment>
      }
    >
      <p>
        {exception.what}
        {exception.why ? ` ${exception.why}` : ''}
      </p>
      <p className="muted small">
        Expediting records the decision to chase this consignment with the
        carrier. It moves no stock and changes no figure — the row stays in the
        queue as answered until the cartons actually land.
      </p>
      <FormRow
        label="Why this consignment, now"
        hint="Required — a decision with no stated reason cannot be defended later."
      >
        <textarea
          rows={3}
          value={rationale}
          onChange={(ev) => setRationale(ev.target.value)}
        />
      </FormRow>
    </Modal>
  );
}

function ReallocateModal({ ctx, exception, surplus, cover, onClose, nodes }) {
  // An expiry row names the node the cartons must leave, not the node they
  // must reach — it is the one exception kind whose subject is holding TOO
  // MUCH. Treating its node as the destination made the queue advise moving
  // stock into the node that already cannot consume what it has, which is the
  // opposite of the row's own sentence. So the fixed end of the move depends
  // on which kind of row opened this.
  const fixedIsSource = exception.reallocation_role === 'source';

  // Nearest usable counterpart first, not largest. Ranking purely by size
  // offered a Burkina Faso hub as the counterpart for a Sudanese one — a
  // correct answer to "who has the most" and an absurd answer to "where should
  // this come from". A corridor within the same country moves in days; the
  // same cartons across two borders do not arrive in time to matter.
  const byId = {};
  (nodes || []).forEach((n) => {
    byId[n.id] = n;
  });
  const fixedCountry = (byId[exception.node_id] || {}).country;
  const near = (list) =>
    list
      .filter((n) => n.node_id !== exception.node_id)
      .map((n) => ({
        ...n,
        sameCountry:
          !!fixedCountry && (byId[n.node_id] || {}).country === fixedCountry,
      }))
      .sort((a, b) =>
        a.sameCountry === b.sameCountry
          ? (b.rank || 0) - (a.rank || 0)
          : a.sameCountry
          ? -1
          : 1,
      );

  // Sources are nodes holding more than their own caseload can consume.
  // Destinations are nodes running below plan — the row's advice is
  // "reallocate the surplus to a node with cover below plan", so the picker
  // has to be able to offer exactly those.
  const sourceOptions = near(
    surplus.map((n) => ({ ...n, rank: n.spare_cartons })),
  );
  const destOptions = near(
    (cover || [])
      .filter((n) => (n.weeks_of_cover ?? 99) < 4)
      .map((n) => ({
        node_id: n.node_id,
        node_name: n.node_name,
        weeks_of_cover: n.weeks_of_cover,
        rank: -(n.weeks_of_cover ?? 0),
      })),
  );

  const spareAtFixed = (
    surplus.find((n) => n.node_id === exception.node_id) || {}
  ).spare_cartons;
  const [counterpartId, setCounterpartId] = useState(() => {
    const options = fixedIsSource ? destOptions : sourceOptions;
    return options.length ? String(options[0].node_id) : '';
  });
  const suggested = Math.max(exception.children_at_risk || 0, 0);
  const [quantity, setQuantity] = useState(String(suggested || 100));
  const [rationale, setRationale] = useState('');

  const sourceId = fixedIsSource ? exception.node_id : Number(counterpartId);
  const targetId = fixedIsSource ? Number(counterpartId) : exception.node_id;
  const spare = fixedIsSource
    ? spareAtFixed
    : (sourceOptions.find((n) => String(n.node_id) === counterpartId) || {})
        .spare_cartons;
  const overdrawn = spare !== undefined && Number(quantity) > spare;

  const submit = async () => {
    const ok = await ctx.act(
      () =>
        supplyPost('/supply/api/actions/reallocate/', {
          source_node_id: sourceId,
          target_node_id: targetId,
          quantity: Number(quantity),
          rationale,
          signal_id: exception.signal_id || null,
        }),
      'Reallocated — a consignment is on the map with planned milestones.',
    );
    if (ok) onClose();
  };

  const options = fixedIsSource ? destOptions : sourceOptions;
  const sourceName = fixedIsSource
    ? exception.node_name
    : (sourceOptions.find((n) => String(n.node_id) === counterpartId) || {})
        .node_name;

  return (
    <Modal
      title={
        fixedIsSource
          ? `Reallocate from ${exception.node_name}`
          : `Reallocate to ${exception.node_name}`
      }
      onClose={onClose}
      footer={
        <React.Fragment>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn"
            onClick={submit}
            disabled={
              ctx.busy ||
              !counterpartId ||
              !rationale.trim() ||
              overdrawn ||
              Number(quantity) <= 0
            }
          >
            Reallocate
          </button>
        </React.Fragment>
      }
    >
      <p className="modal-lede">{exception.why}</p>
      {options.length ? (
        <React.Fragment>
          <FormRow
            label={fixedIsSource ? 'Move to' : 'Move from'}
            hint={
              fixedIsSource
                ? 'Only nodes running below four weeks of cover — the surplus should go where it will be used.'
                : 'Only nodes holding more than their own caseload can consume.'
            }
          >
            <select
              value={counterpartId}
              onChange={(e) => setCounterpartId(e.target.value)}
            >
              {options.map((n) => (
                <option key={n.node_id} value={n.node_id}>
                  {n.node_name} —{' '}
                  {fixedIsSource
                    ? `${n.weeks_of_cover} wk cover`
                    : `${formatNumber(n.spare_cartons)} cartons spare (${
                        n.weeks_of_cover
                      } wk cover)`}
                  {n.sameCountry ? ' · same corridor' : ' · cross-border'}
                </option>
              ))}
            </select>
          </FormRow>
          <FormRow
            label="Cartons"
            hint={
              spare !== undefined
                ? `${formatNumber(
                    spare,
                  )} can move without taking ${sourceName} below six weeks.`
                : ''
            }
          >
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
            />
          </FormRow>
          {overdrawn ? (
            <div className="form-error">
              That would take {sourceName} below its own threshold.
            </div>
          ) : null}
          <FormRow
            label="Why"
            hint="Recorded against the action, and against the signal it answers."
          >
            <textarea
              rows="3"
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
            />
          </FormRow>
        </React.Fragment>
      ) : (
        <EmptyState
          title={
            fixedIsSource
              ? 'No node is running below plan.'
              : 'No node is holding surplus.'
          }
          hint={
            fixedIsSource
              ? 'There is nowhere the surplus would be used sooner than it expires here.'
              : 'Nothing can be moved without causing a stockout somewhere else.'
          }
        />
      )}
    </Modal>
  );
}

/* How a consignment is known.

   Four tiers, weakest to strongest: a hand-keyed portal entry, a driver's phone
   check-in, a despatch advice, a machine-to-machine EPCIS feed. A consignment
   reported at more than one is labelled with its WEAKEST, because that is what
   the confidence in it actually rests on.

   Naming the tier is the point rather than a caveat: Sudan has no domestic
   producer and its corridor runs on paper waybills, so the lowest tier is not a
   fallback, it is the honest case — and it is the one serving the worst famine
   phases. A picture that hid that would be more confident and less true. */
/* TIER_LABELS is declared once, in tab_ops.jsx. The supply bundle concatenates
   every file into ONE scope, so a second top-level const with the same name is
   not shadowing — it is a SyntaxError that blanks the entire app at parse time.
   Preflight reported it as four unresolved selectors; the page was simply dead. */
function TierBadge({ tier }) {
  const label = TIER_LABELS[tier] || 'Unreported';
  const tone = tier === 'epcis' ? 'good' : tier === 'asn' ? 'info' : 'warn';
  return <Badge tone={tone}>{label}</Badge>;
}
