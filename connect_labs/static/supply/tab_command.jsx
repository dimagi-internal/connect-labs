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
            value: formatNumber(
              exceptions.reduce((n, e) => n + (e.children_at_risk || 0), 0),
            ),
            hint: exceptions.length
              ? `across ${exceptions.length} exception${
                  exceptions.length === 1 ? '' : 's'
                }`
              : 'nothing outstanding',
          },
          {
            label: 'In transit',
            value: inTransit.length,
            hint: `${Math.round(tonnesInFlight)} MT moving`,
          },
          {
            label: 'Delivered to date',
            value: formatNumber(deliveredCartons),
            hint: `${Math.round(
              (deliveredCartons * 150 * 92) / 1000000,
            )} MT · ${formatNumber(deliveredCartons)} children treated`,
          },
          { label: 'Active contracts', value: contracts.length },
        ]}
      />

      <div className="command-split">
        <Card
          title="Exceptions"
          subtitle="Ranked by the children behind each one, not by tonnage."
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
                    {e.origin === 'partner' ? (
                      <Badge tone="info">
                        Raised by {e.org_name || 'a partner'}
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
                    </div>
                  ) : null}
                  <div className="exception-action">→ {e.action}</div>
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
          <FlowMap nodes={nodes} shipments={shipments} height={560} />
        </Card>
      </div>

      <Card
        title="Pipeline by corridor"
        subtitle="Contracted quantity against what is confirmed and what has landed. The requirement from caseload sits below."
      >
        <DataTable
          rows={contracts}
          rowKey={(c) => c.id}
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
                label: 'District',
                value: (r) => r.adm1_name,
                render: (r) => (
                  <span title={r.source_note}>
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
        <p className="muted small method-note">
          Coverage is courses delivered divided by the district's monthly SAM
          caseload. Hover a district for how its caseload was estimated. All
          figures in this environment are synthetic.
        </p>
      </Card>

      <Card
        title="Weeks of cover"
        subtitle="Stock on hand against the rate each site is admitting children — the date the store runs dry."
      >
        {cover.length ? (
          <DataTable
            rows={cover.slice(0, 12)}
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
                render: (r) => (
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
                value: (r) => r.stockout_on,
                render: (r) => formatDate(r.stockout_on),
              },
            ]}
          />
        ) : (
          <EmptyState
            title="No cover figures yet."
            hint="No node carries a caseload."
          />
        )}
        <p className="muted small method-note">
          {cover.length ? cover[0].method : ''}
        </p>
      </Card>
    </Page>
  );
}
