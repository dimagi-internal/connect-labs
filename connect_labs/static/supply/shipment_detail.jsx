/* One consignment, in full: milestone rail, contents, and the append-only
   event log with the provenance of every entry.

   A viewer rather than a form — the reporting actions it launches live in
   ops_forms.jsx.
*/

/* ---------- shipment detail: milestone rail, event log, reporting forms ---------- */

function ShipmentDetail({ ctx, shipmentId, onClose }) {
  const [shipment, setShipment] = useState(null);
  const [recording, setRecording] = useState(false);

  const load = useCallback(async () => {
    const body = await supplyGet(`/supply/api/shipments/${shipmentId}/`);
    setShipment(body.shipment);
  }, [shipmentId]);

  useEffect(() => {
    load();
  }, [load]);

  if (!shipment) {
    return (
      <Modal title="Shipment" onClose={onClose}>
        <EmptyState title="Loading…" />
      </Modal>
    );
  }

  const confirm = () =>
    ctx.act(async () => {
      const r = await supplyPost(
        `/supply/api/shipments/${shipmentId}/confirm/`,
        {
          quantity: shipment.quantity,
        },
      );
      await load();
      return r;
    }, 'Delivery confirmed.');

  return (
    <Modal
      wide
      title={`${shipment.reference} — ${shipment.origin.name} → ${shipment.destination.name}`}
      onClose={onClose}
      footer={
        <React.Fragment>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setRecording(true)}
          >
            Record an event
          </button>
          {shipment.status === 'delivered' ? (
            <button
              type="button"
              className="btn"
              onClick={confirm}
              disabled={ctx.busy}
            >
              Confirm delivery
            </button>
          ) : null}
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Close
          </button>
        </React.Fragment>
      }
    >
      <div className="detail-head">
        <StatusChip status={shipment.status} />
        <span className="muted">
          {formatNumber(shipment.quantity)} {shipment.unit}
          {shipment.metric_tonnes ? ` · ${shipment.metric_tonnes} MT` : ''}
          {shipment.asn_reference
            ? ` · despatch advice ${shipment.asn_reference}`
            : ''}
        </span>
      </div>

      <Card
        title="Milestones"
        subtitle="Planned, estimated and actual — the three are never collapsed."
      >
        <div className="rail">
          {shipment.milestones.map((m) => (
            <div
              className={`rail-step ${m.actual_at ? 'done' : ''}`}
              key={m.id}
            >
              <div className="rail-dot" />
              <div className="rail-body">
                <div className="rail-title">
                  {m.kind === 'depart' ? 'Depart' : 'Arrive'} · {m.node_name}
                </div>
                <div className="muted small">
                  {m.actual_at
                    ? `Actual ${formatDate(m.actual_at)}`
                    : `Planned ${formatDate(m.planned_at)}`}
                  {m.delta_days !== null && m.delta_days !== undefined
                    ? ` · ${m.delta_days > 0 ? '+' : ''}${
                        m.delta_days
                      }d vs plan`
                    : ''}
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Consignment lines">
        <DataTable
          rows={shipment.lines || []}
          rowKey={(l) => l.id}
          empty="No lines recorded."
          columns={[
            { key: 'gtin', label: 'GTIN', value: (l) => l.gtin || '—' },
            {
              key: 'lot',
              label: 'Batch / lot',
              value: (l) => l.batch_lot || '—',
            },
            {
              key: 'exp',
              label: 'Expires',
              value: (l) => l.expiry_date,
              render: (l) => formatDate(l.expiry_date),
            },
            {
              key: 'qty',
              label: 'Quantity',
              value: (l) => l.quantity,
              render: (l) => `${formatNumber(l.quantity)} ${l.unit}`,
            },
            { key: 'sscc', label: 'Pallet SSCC', value: (l) => l.sscc || '—' },
          ]}
        />
      </Card>

      <Card
        title="Event log"
        subtitle="Append-only. How each event reached us is recorded alongside it."
      >
        <DataTable
          rows={shipment.events || []}
          rowKey={(e) => e.id}
          empty="No events yet."
          columns={[
            {
              key: 'time',
              label: 'When',
              value: (e) => e.event_time,
              render: (e) => formatDate(e.event_time),
            },
            {
              key: 'step',
              label: 'What happened',
              value: (e) => e.biz_step,
              render: (e) => stepLabel(e.biz_step),
            },
            { key: 'where', label: 'Where', value: (e) => e.read_point || '—' },
            {
              key: 'qty',
              label: 'Quantity',
              sortable: false,
              value: () => '',
              render: (e) =>
                e.quantity_list && e.quantity_list.length
                  ? formatNumber(e.quantity_list[0].quantity)
                  : '—',
            },
            {
              key: 'src',
              label: 'Source',
              value: (e) => e.source_tier,
              render: (e) => (
                <Badge tone={e.source_tier === 'portal' ? 'warn' : 'info'}>
                  {TIER_LABELS[e.source_tier]}
                </Badge>
              ),
            },
          ]}
        />
      </Card>

      {recording ? (
        <EventForm
          ctx={ctx}
          shipment={shipment}
          onClose={() => setRecording(false)}
          onSaved={load}
        />
      ) : null}
    </Modal>
  );
}
