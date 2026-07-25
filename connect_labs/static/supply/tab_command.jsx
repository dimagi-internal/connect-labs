/* Operations command centre — the hero screen.

   Exception-first, following how real control towers are actually laid out
   (project44 / FourKites): the home surface is a prioritised worklist of
   at-risk consignments, and the map is context beside it, not the product.
   Each exception row answers three things in order: what is wrong, why, and
   what to do about it. */

function ExceptionSeverity(shipment) {
  // Rank by tonnage at risk × lateness, so a big late RUTF consignment
  // outranks a small one.
  const late = shipment.eta_delta_days || 0;
  return late * Math.max(1, shipment.quantity / 1000);
}

function buildExceptions(contracts, discrepancies) {
  const rows = [];

  contracts.forEach((contract) => {
    (contract.shipments || []).forEach((shipment) => {
      const late = shipment.eta_delta_days;
      if (
        late !== null &&
        late !== undefined &&
        late > 0 &&
        shipment.status !== 'confirmed'
      ) {
        rows.push({
          key: `late-${shipment.id}`,
          kind: 'Late',
          tone: late >= 3 ? 'bad' : 'warn',
          shipment,
          contract,
          what: `${shipment.reference} is running ${late} day${
            late === 1 ? '' : 's'
          } behind plan`,
          why:
            shipment.status === 'in_transit'
              ? `In transit ${shipment.origin.name} → ${shipment.destination.name}; last confirmed movement has not cleared the plan.`
              : `Awaiting confirmation at ${shipment.destination.name}.`,
          action:
            shipment.status === 'in_transit'
              ? 'Chase the carrier for a position update and re-forecast the arrival.'
              : 'Ask the consignee to confirm receipt so the contract can be drawn down.',
          severity: ExceptionSeverity(shipment),
        });
      }
    });
  });

  (discrepancies || [])
    .filter((d) => d.status === 'open')
    .forEach((d) => {
      rows.push({
        key: `disc-${d.id}`,
        kind: 'Short receipt',
        tone: 'bad',
        what: `${d.shipment_reference} received ${formatNumber(
          d.shortfall,
        )} cartons short`,
        why: `${formatNumber(
          d.expected_quantity,
        )} despatched against ${formatNumber(
          d.received_quantity,
        )} confirmed at destination.`,
        action:
          'Reconcile against the despatch advice, then record the outcome to close it.',
        severity: Number(d.shortfall) * 2,
        discrepancy: d,
      });
    });

  return rows.sort((a, b) => b.severity - a.severity);
}

function CommandTab({ ctx }) {
  const { world, act } = ctx;
  const contracts = world.contracts || [];
  const nodes = world.nodes || [];
  const shipments = contracts.flatMap((c) => c.shipments || []);
  const exceptions = buildExceptions(contracts, world.discrepancies);
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
            label: 'Consignments at risk',
            value: exceptions.length,
            hint: exceptions.length
              ? 'ranked by tonnage at risk'
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
          subtitle="What is wrong, why, and what to do — highest tonnage at risk first."
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
                    <span className="exception-what">{e.what}</span>
                  </div>
                  <div className="exception-why">{e.why}</div>
                  <div className="exception-action">→ {e.action}</div>
                  {e.discrepancy &&
                  supplyCan(world.role, 'execution', 'resolve') ? (
                    <span
                      className="btn btn-sm"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        act(
                          () =>
                            supplyPost(
                              `/supply/api/discrepancies/${e.discrepancy.id}/resolve/`,
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
        subtitle="Requirement against what is confirmed and what has landed."
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
    </Page>
  );
}
