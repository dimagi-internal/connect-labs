/* Host-government view — country-scoped.

   Conventions borrowed from OCHA/HDX humanitarian reporting: a key-figures
   row up top, a 5W-style table (who is delivering what, where, when, for how
   many), and the IPC choropleth as the demand backdrop. Everything is
   filtered to the observer's own country by the server. */

function GovTab({ ctx }) {
  const { world } = ctx;
  const country = world.scope_country;
  const contracts = world.contracts || [];
  const nodes = (world.nodes || []).filter((n) => n.country === country);
  const coverage = world.coverage || [];

  // A node is answerable for children only if it serves a district. A port or
  // a national warehouse sits on the route without any caseload behind it,
  // which is exactly why it carries no adm1_code — the same test the coverage
  // service uses when it decides which arrivals count.
  const nodeById = {};
  (world.nodes || []).forEach((n) => {
    nodeById[n.id] = n;
  });
  const deliversToChildren = (s) =>
    !!(nodeById[s.destination.id] || {}).adm1_code;

  // Only flows that touch this country.
  const shipments = contracts
    .flatMap((c) => (c.shipments || []).map((s) => ({ ...s, contract: c })))
    .filter((s) => {
      const originNode = (world.nodes || []).find((n) => n.id === s.origin.id);
      const destNode = (world.nodes || []).find(
        (n) => n.id === s.destination.id,
      );
      return (
        (originNode && originNode.country === country) ||
        (destNode && destNode.country === country)
      );
    });

  // Read off the same rows as the coverage table further down this page, which
  // counts a leg only where it CROSSES INTO a district.
  //
  // Summing every delivered leg touching the country counted a carton once per
  // hop it made: this tile read 53,246 while the coverage table 800px below
  // totalled 25,863 on the identical one-carton-one-course conversion — 2.06x
  // apart, on one screen, in a demo whose thesis is that a number cannot mean
  // two things. It also credited a warehouse-to-warehouse transfer at Kano as
  // 20,000 children covered, and counted consignments still on the road.
  const delivered = coverage.reduce(
    (n, r) => n + (r.courses_delivered || 0),
    0,
  );
  // The denominator the headline row was missing, off the same rows as the
  // table below so the tile and the table cannot disagree.
  const nationalCaseload = coverage.reduce((n, r) => n + (r.caseload || 0), 0);
  const inbound = shipments.filter((s) => s.status === 'in_transit');
  const warehouses = nodes.filter(
    (n) => n.kind === 'warehouse' || n.kind === 'distribution_hub',
  );

  return (
    <Page
      title={`${countryLabel(country)} — supply overview`}
      lede="Commodities entering and moving within the country, and the caseload they are meant to cover."
      asOf={world.as_of}
    >
      {/* Two tiles printed the identical integer under near-identical labels —
          "25,863 Cartons delivered into districts" and "25,863 Courses
          delivered". Honest, since a carton IS a course, but it spent half the
          headline row restating one number on a page that did not carry the
          figure a ministry is actually here for: national coverage of its own
          need. One tile states the identity; the freed slot carries the
          figure. */}
      <KeyFigures
        figures={[
          {
            label: 'Courses delivered into districts',
            value: formatNumber(delivered),
            hint: `${formatNumber(delivered)} cartons = ${formatNumber(
              delivered,
            )} full courses · ${Math.round(
              (delivered * 150 * 92) / 1000000,
            )} MT, counted once where each crossed a district boundary`,
          },
          {
            label: 'Supply positioned, nationally',
            value:
              nationalCaseload > 0
                ? `${Math.round((delivered / nationalCaseload) * 1000) / 10}%`
                : '—',
            hint: `against ${formatNumber(
              nationalCaseload,
            )} children needing treatment`,
            method:
              "Courses confirmed into the country's districts against the SAM caseload summed over the response window. Counts stock that has arrived, including stock still held in a district hub — the per-district table below reports what reached a child separately.",
          },
          { label: 'Consignments inbound', value: inbound.length },
          {
            label: 'Storage points',
            value: warehouses.length,
            hint: 'warehouses and distribution hubs',
          },
        ]}
      />

      <Card
        title="Where supply is going"
        subtitle="Flows terminating in this country, over current food-insecurity classification."
      >
        <FlowMap
          nodes={nodes}
          shipments={shipments}
          focusCountry={country}
          height={520}
        />
      </Card>

      <Card
        title="Who is delivering what, where"
        subtitle="Partner, commodity, destination, timing and coverage."
      >
        <DataTable
          rows={shipments}
          rowKey={(s) => s.id}
          empty="No consignments recorded for this country."
          columns={[
            // PARTNER and COMMODITY repeated one identical string down 18 rows,
            // eating about 35% of the width and turning 28 distinct consignments
            // into one visual block. A repeat is subdued rather than removed —
            // a sorted table can be re-sorted, so the value has to stay in the
            // cell and stay copyable.
            {
              key: 'partner',
              label: 'Partner',
              value: (s) => s.contract.org_name,
              render: (s, i, rows) => (
                <span
                  className={
                    repeatsAbove(rows, i, (r) => r.contract.org_name)
                      ? 'repeat-value'
                      : ''
                  }
                >
                  {s.contract.org_name}
                </span>
              ),
            },
            {
              key: 'commodity',
              label: 'Commodity',
              value: (s) => s.contract.category,
              render: (s, i, rows) => (
                <span
                  className={
                    repeatsAbove(rows, i, (r) => r.contract.category)
                      ? 'repeat-value'
                      : ''
                  }
                >
                  {categoryLabel(s.contract.category)}
                </span>
              ),
            },
            {
              key: 'dest',
              label: 'Destination',
              value: (s) => s.destination.name,
            },
            // Quantity carries the conversion, so one column does one job.
            //
            // COURSES ON ARRIVAL restated QUANTITY verbatim on 27 of 28 rows and
            // was an unexplained dash on the 28th — a whole column spent
            // repeating the column beside it. A carton IS a course, so the
            // identity belongs in the quantity header, and the reason a storage
            // point contributes no coverage belongs beside the destination that
            // is one.
            {
              key: 'qty',
              label: 'Quantity',
              value: (s) => s.quantity,
              render: (s) => (
                <span>
                  {formatNumber(s.quantity)} {s.unit}
                  {s.unit === 'cartons' && !deliversToChildren(s) ? (
                    <span className="cell-with-note muted">
                      <InfoNote
                        label="why this consignment covers no district"
                        text="This leg ends at a storage point, which serves no caseload of its own. These cartons are counted towards coverage where they reach a district, not here — so the consignment is real and its contribution to coverage is zero."
                      />
                    </span>
                  ) : null}
                </span>
              ),
            },
            {
              key: 'when',
              label: 'When',
              value: (s) => s.delivered_at || s.eta,
              render: (s) =>
                s.delivered_at
                  ? formatDate(s.delivered_at)
                  : `ETA ${formatDate(s.eta)}`,
            },
            {
              key: 'status',
              label: 'Status',
              value: (s) => s.status,
              render: (s) => <StatusChip status={s.status} />,
            },
          ]}
        />
      </Card>

      <Card
        title="Stock by location"
        subtitle="What has landed at each storage point."
      >
        <StockByNode nodes={warehouses} shipments={shipments} />
      </Card>
      <Card
        title="Coverage by district, not tonnage delivered"
        subtitle="Where the response reached, and where the state still has to fill the gap itself."
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
              },
              // IPC phase is its own fact, in its own column.
              //
              // Glued into the name string it read as part of the name, and it
              // rendered inconsistently within three rows: IPC 4 and IPC 5 got
              // filled pills while IPC 3 was bare grey text, so the row a reader
              // is meant to compare against looked like it carried no
              // classification at all. Phase 3 is still a crisis classification;
              // "muted" is a claim about the datum, not about its absence.
              {
                key: 'ipc',
                label: 'IPC phase',
                value: (r) => r.ipc_phase,
                render: (r) => (
                  <Badge
                    tone={
                      r.ipc_phase >= 5
                        ? 'bad'
                        : r.ipc_phase >= 4
                        ? 'warn'
                        : 'info'
                    }
                  >
                    IPC {r.ipc_phase}
                  </Badge>
                ),
              },
              {
                key: 'caseload',
                label: 'Children needing treatment',
                value: (r) => r.caseload,
                // The provenance of the denominator, reachable. It used to ride
                // on the row's native `title` attribute — no touch path, no
                // keyboard path, nothing for a screen reader, and invisible in
                // anything printed or projected. It is the estimate every
                // coverage figure on this page divides by, so it cannot be
                // hover-only.
                total: (rows) =>
                  formatNumber(rows.reduce((n, r) => n + (r.caseload || 0), 0)),
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
                total: (rows) =>
                  formatNumber(
                    rows.reduce((n, r) => n + (r.courses_delivered || 0), 0),
                  ),
              },
              // What is on the road, which decides where a state sends its own.
              //
              // Yobe read as abandoned — 0 delivered, 0%, 18,960 uncovered —
              // while the same page showed 10,000 cartons In transit to Damaturu
              // with an ETA three days out: 53% of Yobe's entire need. Omitting
              // the pipeline was the single most decision-distorting gap on the
              // card whose stated use is deciding where to put state resources.
              {
                key: 'transit',
                label: 'On the road',
                value: (r) => r.courses_in_transit || 0,
                render: (r) =>
                  r.courses_in_transit ? (
                    <span>
                      {formatNumber(r.courses_in_transit)}
                      <span className="muted"> · {r.in_transit_percent}%</span>
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  ),
                total: (rows) =>
                  formatNumber(
                    rows.reduce((n, r) => n + (r.courses_in_transit || 0), 0),
                  ),
              },
              {
                key: 'coverage',
                // "Coverage of need" reads as children reached. It is not: the
                // numerator is everything confirmed INTO the district, including
                // stock still sitting in a district hub — 93% of it on the seeded
                // world, with Gombe and Kassala both reading 91% while zero
                // cartons had been handed to a child. The figure is genuinely
                // useful; the words were wrong, so the words changed and the
                // missing companion figure sits beside it.
                label: 'Supply positioned',
                value: (r) => r.coverage_percent || 0,
                total: (rows) => nationalPercent(rows, 'courses_delivered'),
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
                total: (rows) => nationalPercent(rows, 'courses_dispensed'),
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
                label: 'Children still uncovered',
                value: (r) => r.uncovered_children,
                render: (r) => formatNumber(r.uncovered_children),
                // The national figure a ministry official is here for.
                //
                // It was computable from the three rows shown and left as
                // arithmetic for the reader, on the card whose stated use is
                // "where do I put my own state resources". A table that invites
                // addition should do the addition — and the totals are computed
                // from the unrounded rows, so the national percentages are true
                // ratios of the national sums rather than averages of the rows'
                // percentages, which would weight Gombe's 91% equally with
                // Borno's 34% across five times the caseload.
                total: (rows) =>
                  formatNumber(
                    rows.reduce((n, r) => n + (r.uncovered_children || 0), 0),
                  ),
              },
            ]}
            totalsLabel="National"
          />
        ) : (
          <EmptyState
            title="No caseload estimates for this country."
            hint="Coverage cannot be reported without a denominator."
          />
        )}
        {/* The window, stated. This note said "monthly SAM caseload" while the
            denominator is the caseload summed over the response window — four
            months — so the stated method was about 4x off in the one card whose
            whole claim is that its method can be challenged. The window is
            already on every row; it was simply never rendered. */}
        <p className="muted small method-note">
          Coverage is courses delivered divided by the district's SAM caseload
          summed over the{' '}
          {coverage[0] && coverage[0].window_months
            ? `${
                coverage[0].window_months
              }-month response window from ${formatDate(
                coverage[0].window_from,
              )}`
            : 'response window'}
          , not against a single month. The <em>i</em> beside each caseload
          gives the method it was estimated by. All figures in this environment
          are synthetic.
        </p>
        <p className="muted small method-note">
          Two questions, not one. <strong>Supply positioned</strong> is courses
          confirmed into the district against its SAM caseload — severe acute
          malnutrition — and counts stock that has arrived, including stock
          still held in a district hub. <strong>Reached a child</strong> counts
          only courses a site recorded handing out. Food in a warehouse is not
          treatment, so the two are reported apart rather than merged.
        </p>
        {/* The verdict the colours encode, stated so it can be argued with.
            These pills coloured 0% and 34% red and 91% green against a threshold
            written down nowhere, on the one product whose entire thesis is that
            an interpretation must be stated to be challenged. */}
        <p className="muted small method-note">{COVERAGE_BANDS}</p>
      </Card>
    </Page>
  );
}

function StockByNode({ nodes, shipments }) {
  const rows = nodes
    .map((node) => {
      const landed = shipments
        .filter(
          (s) =>
            s.destination.id === node.id &&
            (s.status === 'delivered' || s.status === 'confirmed'),
        )
        .reduce((n, s) => n + s.quantity, 0);
      const inbound = shipments
        .filter(
          (s) => s.destination.id === node.id && s.status === 'in_transit',
        )
        .reduce((n, s) => n + s.quantity, 0);
      return { node, landed, inbound };
    })
    .filter((r) => r.landed || r.inbound)
    .sort((a, b) => b.landed - a.landed);

  if (!rows.length) {
    return <EmptyState title="No stock movements recorded yet." />;
  }

  const max = Math.max(...rows.map((r) => r.landed + r.inbound), 1);
  return (
    <div className="bar-list">
      {/* A reader saw a half-full bar labelled zero.
          Damaturu printed 0 while drawing a pale bar to ~50% of the track — its
          10,000-carton in-transit consignment on a 20,000 scale — with that
          quantity printed nowhere, no legend separating the pale fill from the
          solid one, and no unit or scale on the axis. The pale series is now
          legended and carries its own figure, so the bar and the number agree
          about what they describe. */}
      <div className="stage-legend">
        <span className="stage-key">
          <i className="bar-swatch on-hand" /> on hand
        </span>
        <span className="stage-key">
          <i className="bar-swatch pending" /> in transit
        </span>
        <span className="muted small">
          cartons · full track is {formatNumber(max)}
        </span>
      </div>
      {rows.map((r) => (
        <div className="bar-row" key={r.node.id}>
          <div className="bar-label">{r.node.name}</div>
          <div className="bar-track">
            <div
              className="bar-fill"
              style={{ width: `${(r.landed / max) * 100}%` }}
            />
            <div
              className="bar-fill-pending"
              style={{
                width: `${(r.inbound / max) * 100}%`,
                left: `${(r.landed / max) * 100}%`,
              }}
            />
          </div>
          <div className="bar-value">
            {formatNumber(r.landed)}
            {r.inbound ? (
              <span className="muted"> + {formatNumber(r.inbound)} due</span>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
