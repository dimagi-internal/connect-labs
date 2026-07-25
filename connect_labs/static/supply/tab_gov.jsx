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

  const delivered = shipments
    .filter((s) => s.status === 'delivered' || s.status === 'confirmed')
    .reduce((n, s) => n + s.quantity, 0);
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
            label: 'Cartons delivered',
            value: formatNumber(delivered),
            hint: `${Math.round(
              (delivered * 150 * 92) / 1000000,
            )} MT of therapeutic food`,
          },
          {
            label: 'Children treated',
            value: formatNumber(delivered),
            hint: 'one carton ≈ one full course',
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
            {
              key: 'children',
              label: 'Children covered',
              value: (s) => s.quantity,
              render: (s) => formatNumber(s.quantity),
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
