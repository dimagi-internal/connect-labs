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
  const inbound = shipments.filter((s) => s.status === 'in_transit');
  const warehouses = nodes.filter(
    (n) => n.kind === 'warehouse' || n.kind === 'distribution_hub',
  );

  return (
    <Page
      title={`${countryLabel(country)} — supply overview`}
      lede="Commodities entering and moving within the country, and the caseload they are meant to cover."
    >
      <KeyFigures
        figures={[
          {
            label: 'Cartons delivered into districts',
            value: formatNumber(delivered),
            hint: `${Math.round(
              (delivered * 150 * 92) / 1000000,
            )} MT · counted once, where each crossed a district boundary`,
          },
          {
            label: 'Courses delivered',
            value: formatNumber(delivered),
            hint: 'one carton is one full course',
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
            {
              key: 'partner',
              label: 'Partner',
              value: (s) => s.contract.org_name,
            },
            {
              key: 'commodity',
              label: 'Commodity',
              value: (s) => s.contract.category,
              render: (s) => categoryLabel(s.contract.category),
            },
            {
              key: 'dest',
              label: 'Destination',
              value: (s) => s.destination.name,
            },
            {
              key: 'qty',
              label: 'Quantity',
              value: (s) => s.quantity,
              render: (s) => `${formatNumber(s.quantity)} ${s.unit}`,
            },
            // Only a leg that ends where children are treated covers any.
            //
            // This column restated every consignment's carton count as
            // "children covered", so a warehouse-to-warehouse transfer into
            // Kano was credited with covering 20,000 children and a
            // consignment still on the road was credited with covering
            // anybody at all. A storage point serves no caseload — that is
            // precisely why it carries no district — so the honest cell is a
            // dash, and the row's cartons are already in the column beside it.
            {
              key: 'children',
              label: 'Courses on arrival',
              value: (s) => (deliversToChildren(s) ? s.quantity : -1),
              render: (s) =>
                deliversToChildren(s) ? (
                  formatNumber(s.quantity)
                ) : (
                  // Same reason as the caseload cell: a dash that means
                  // something needs to say what, reachably. On a native `title`
                  // the explanation for why a consignment contributes nothing to
                  // coverage was hover-only — so a reader with a keyboard, a
                  // touchscreen, a screen reader or a printout just saw a blank.
                  <span className="cell-with-note muted">
                    —
                    <InfoNote
                      label="why this consignment shows no courses"
                      text="A storage point serves no caseload of its own; these cartons are counted where they reach a district."
                    />
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
                label: 'Children needing treatment',
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
                // "Coverage of need" reads as children reached. It is not: the
                // numerator is everything confirmed INTO the district, including
                // stock still sitting in a district hub — 93% of it on the seeded
                // world, with Gombe and Kassala both reading 91% while zero
                // cartons had been handed to a child. The figure is genuinely
                // useful; the words were wrong, so the words changed and the
                // missing companion figure sits beside it.
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
                key: 'uncovered',
                label: 'Children still uncovered',
                value: (r) => r.uncovered_children,
                render: (r) => formatNumber(r.uncovered_children),
              },
            ]}
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
          , not against a single month. Hover a district name for how its
          caseload was estimated. All figures in this environment are synthetic.
        </p>
        <p className="muted small method-note">
          Two questions, not one. <strong>Supply positioned</strong> is courses
          confirmed into the district against its SAM caseload — severe acute
          malnutrition — and counts stock that has arrived, including stock
          still held in a district hub. <strong>Reached a child</strong> counts
          only courses a site recorded handing out. Food in a warehouse is not
          treatment, so the two are reported apart rather than merged.
        </p>
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
          <div className="bar-value">{formatNumber(r.landed)}</div>
        </div>
      ))}
    </div>
  );
}
