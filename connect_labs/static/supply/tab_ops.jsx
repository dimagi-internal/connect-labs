/* Supplier operations: contracts, shipment pipeline, and the webforms that
   mirror every machine-API capability (declare a despatch, record an event,
   check in, confirm delivery) so a supplier with no integration can do
   everything an integrated one can. */

const BIZ_STEPS = [
  { key: 'commissioning', label: 'Produced / commissioned' },
  { key: 'packing', label: 'Packed' },
  { key: 'loading', label: 'Loaded' },
  { key: 'departing', label: 'Departed' },
  { key: 'arriving', label: 'Arrived' },
  { key: 'receiving', label: 'Received' },
  { key: 'inspecting', label: 'Inspected' },
  { key: 'storing', label: 'Stored' },
];

const TIER_LABELS = {
  epcis: 'EPCIS feed',
  asn: 'Despatch advice',
  checkin: 'Check-in',
  portal: 'Entered by hand',
};

function stepLabel(key) {
  const found = BIZ_STEPS.find((s) => s.key === key);
  return found ? found.label : key;
}

function OpsTab({ ctx }) {
  const { world } = ctx;
  const contracts = world.contracts || [];
  const discrepancies = (world.discrepancies || []).filter(
    (d) => d.status === 'open',
  );
  const [detailId, setDetailId] = useState(null);
  const [declaring, setDeclaring] = useState(false);

  const shipments = contracts.flatMap((c) =>
    (c.shipments || []).map((s) => ({ ...s, contract: c })),
  );
  const inTransit = shipments.filter((s) => s.status === 'in_transit');
  const late = shipments.filter(
    (s) =>
      s.eta_delta_days !== null &&
      s.eta_delta_days !== undefined &&
      s.eta_delta_days > 0 &&
      s.status !== 'confirmed',
  );
  const deliveredCartons = contracts.reduce(
    (n, c) => n + c.delivered_quantity,
    0,
  );
  const contractedCartons = contracts.reduce((n, c) => n + c.total_quantity, 0);

  return (
    <Page
      title="Operations"
      lede="Your contracts, shipments in flight, and delivery reporting."
      actions={
        contracts.length ? (
          <button
            type="button"
            className="btn"
            onClick={() => setDeclaring(true)}
          >
            Declare a despatch
          </button>
        ) : null
      }
    >
      <KeyFigures
        figures={[
          {
            label: 'Cartons delivered',
            value: formatNumber(deliveredCartons),
            hint: contractedCartons
              ? `${Math.round(
                  (deliveredCartons / contractedCartons) * 100,
                )}% of contracted`
              : null,
          },
          {
            label: 'Metric tonnes',
            value: cartonsToMt(deliveredCartons).toLocaleString(),
            hint: '150 sachets × 92 g per carton',
          },
          {
            label: 'Children treated',
            value: formatNumber(deliveredCartons),
            hint: 'one carton ≈ one full course',
          },
          {
            label: 'Shipments in transit',
            value: inTransit.length,
            hint: late.length
              ? `${late.length} running late`
              : 'all on schedule',
          },
        ]}
      />

      {discrepancies.length ? (
        <Card
          title="Open discrepancies"
          subtitle="Receipts that do not reconcile with what was despatched."
        >
          <DataTable
            rows={discrepancies}
            rowKey={(d) => d.id}
            columns={[
              {
                key: 'ship',
                label: 'Shipment',
                value: (d) => d.shipment_reference,
              },
              {
                key: 'exp',
                label: 'Despatched',
                value: (d) => d.expected_quantity,
                render: (d) => formatNumber(d.expected_quantity),
              },
              {
                key: 'rec',
                label: 'Received',
                value: (d) => d.received_quantity,
                render: (d) => formatNumber(d.received_quantity),
              },
              {
                key: 'short',
                label: 'Shortfall',
                value: (d) => d.shortfall,
                render: (d) => (
                  <Badge tone="bad">{formatNumber(d.shortfall)}</Badge>
                ),
              },
            ]}
          />
        </Card>
      ) : null}

      {contracts.map((contract) => (
        <Card
          key={contract.id}
          title={contract.reference}
          subtitle={`${contract.lot_description} · ${
            contract.destination
          }, ${countryLabel(contract.destination_country)}`}
          actions={
            <span className="muted small">
              {formatNumber(contract.delivered_quantity)} /{' '}
              {formatNumber(contract.total_quantity)} {contract.unit} delivered
            </span>
          }
        >
          <ProgressBar
            value={contract.delivered_quantity}
            total={contract.total_quantity}
            shipped={contract.shipped_quantity}
          />
          <DataTable
            rows={contract.shipments || []}
            rowKey={(s) => s.id}
            empty="No shipments declared against this contract yet."
            columns={[
              { key: 'ref', label: 'Shipment', value: (s) => s.reference },
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
                key: 'status',
                label: 'Status',
                value: (s) => s.status,
                render: (s) => <StatusChip status={s.status} />,
              },
              {
                key: 'eta',
                label: 'ETA vs plan',
                value: (s) => s.eta_delta_days,
                render: (s) => <EtaChip shipment={s} />,
              },
              {
                key: 'act',
                label: '',
                sortable: false,
                value: () => '',
                render: (s) => (
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => setDetailId(s.id)}
                  >
                    Open
                  </button>
                ),
              },
            ]}
          />
        </Card>
      ))}

      {!contracts.length ? (
        <EmptyState
          title="No contracts yet"
          hint="Contracts appear here once one of your bids is awarded."
        />
      ) : null}

      {declaring ? (
        <DespatchForm
          ctx={ctx}
          contracts={contracts}
          onClose={() => setDeclaring(false)}
        />
      ) : null}
      {detailId ? (
        <ShipmentDetail
          ctx={ctx}
          shipmentId={detailId}
          onClose={() => setDetailId(null)}
        />
      ) : null}
    </Page>
  );
}

function cartonsToMt(cartons) {
  // Mirrors gs1.cartons_to_mt on the server: 150 sachets x 92 g.
  return Math.round(((cartons * 150 * 92) / 1000000) * 10) / 10;
}

function ProgressBar({ value, total, shipped }) {
  const pct = total ? Math.min(100, (value / total) * 100) : 0;
  const shippedPct = total ? Math.min(100, (shipped / total) * 100) : 0;
  return (
    <div
      className="progress"
      title={`${formatNumber(value)} of ${formatNumber(total)}`}
    >
      <div className="progress-shipped" style={{ width: `${shippedPct}%` }} />
      <div className="progress-delivered" style={{ width: `${pct}%` }} />
    </div>
  );
}

function EtaChip({ shipment }) {
  if (shipment.status === 'confirmed' || shipment.status === 'delivered') {
    return <span className="muted">{formatDate(shipment.delivered_at)}</span>;
  }
  const delta = shipment.eta_delta_days;
  if (delta === null || delta === undefined) {
    return <span className="muted">{formatDate(shipment.eta)}</span>;
  }
  if (delta > 0) return <Badge tone="bad">{`+${delta}d vs plan`}</Badge>;
  if (delta < 0) return <Badge tone="good">{`${delta}d vs plan`}</Badge>;
  return <Badge tone="good">on plan</Badge>;
}
