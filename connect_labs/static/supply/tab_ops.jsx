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
    (s) => (s.eta_delta_days || 0) > 0 && s.status !== 'confirmed',
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

/* ---------- webform: declare a despatch (mirrors POST /api/v1/shipments/) ---------- */

function DespatchForm({ ctx, contracts, onClose }) {
  const nodes = ctx.world.nodes || [];
  const [form, setForm] = useState({
    contract_reference: contracts[0] ? contracts[0].reference : '',
    asn_reference: '',
    ship_from_gln: '',
    ship_to_gln: '',
    departed_at: '',
    eta: '',
    sscc: '',
    gtin: '',
    batch_lot: '',
    expiry_date: '',
    quantity: '',
  });
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  const submit = async () => {
    const payload = {
      asn_reference: form.asn_reference,
      contract_reference: form.contract_reference,
      ship_from_gln: form.ship_from_gln,
      ship_to_gln: form.ship_to_gln,
      departed_at: form.departed_at || null,
      eta: form.eta || null,
      packages: [
        {
          sscc: form.sscc,
          items: [
            {
              gtin: form.gtin,
              batch_lot: form.batch_lot,
              expiry_date: form.expiry_date || null,
              quantity: Number(form.quantity),
              unit: 'cartons',
            },
          ],
        },
      ],
    };
    const ok = await ctx.act(
      () => supplyPost('/supply/api/shipments/', payload),
      'Despatch recorded.',
    );
    if (ok) onClose();
  };

  const complete =
    form.contract_reference &&
    form.asn_reference &&
    form.ship_from_gln &&
    form.ship_to_gln &&
    form.quantity;

  return (
    <Modal
      wide
      title="Declare a despatch"
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
            disabled={ctx.busy || !complete}
          >
            Record despatch
          </button>
        </React.Fragment>
      }
    >
      <div className="notice">
        This form records exactly what a despatch advice (X12 856 / EDIFACT
        DESADV) posted to the API would record. If your systems can send it
        automatically, mint an API token under Integration instead.
      </div>

      <div className="field-row-2">
        <FormRow label="Contract">
          <select
            value={form.contract_reference}
            onChange={set('contract_reference')}
          >
            {contracts.map((c) => (
              <option key={c.id} value={c.reference}>
                {c.reference} — {c.lot_description}
              </option>
            ))}
          </select>
        </FormRow>
        <FormRow
          label="Despatch advice number"
          hint="Your own reference for this consignment."
        >
          <input
            type="text"
            value={form.asn_reference}
            onChange={set('asn_reference')}
          />
        </FormRow>
      </div>

      <div className="field-row-2">
        <FormRow label="Ship from">
          <select value={form.ship_from_gln} onChange={set('ship_from_gln')}>
            <option value="">Select a location…</option>
            {nodes.map((n) => (
              <option key={n.id} value={n.gln}>
                {n.name} ({n.gln})
              </option>
            ))}
          </select>
        </FormRow>
        <FormRow label="Ship to">
          <select value={form.ship_to_gln} onChange={set('ship_to_gln')}>
            <option value="">Select a location…</option>
            {nodes.map((n) => (
              <option key={n.id} value={n.gln}>
                {n.name} ({n.gln})
              </option>
            ))}
          </select>
        </FormRow>
      </div>

      <div className="field-row-2">
        <FormRow label="Departed at">
          <input
            type="datetime-local"
            value={form.departed_at}
            onChange={set('departed_at')}
          />
        </FormRow>
        <FormRow label="Estimated arrival">
          <input type="datetime-local" value={form.eta} onChange={set('eta')} />
        </FormRow>
      </div>

      <h4>Consignment contents</h4>
      <div className="field-row-2">
        <FormRow
          label="Pallet SSCC"
          hint="The 18-digit licence plate on the pallet label."
        >
          <input
            type="text"
            value={form.sscc}
            onChange={set('sscc')}
            maxLength="18"
          />
        </FormRow>
        <FormRow label="Product GTIN" hint="14 digits, carton level.">
          <input
            type="text"
            value={form.gtin}
            onChange={set('gtin')}
            maxLength="14"
          />
        </FormRow>
      </div>
      <div className="field-row-2">
        <FormRow label="Batch / lot">
          <input
            type="text"
            value={form.batch_lot}
            onChange={set('batch_lot')}
          />
        </FormRow>
        <FormRow label="Expiry date">
          <input
            type="date"
            value={form.expiry_date}
            onChange={set('expiry_date')}
          />
        </FormRow>
      </div>
      <FormRow label="Quantity (cartons)">
        <input type="number" value={form.quantity} onChange={set('quantity')} />
      </FormRow>
    </Modal>
  );
}

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

/* ---------- webform: record an event (mirrors POST /api/v1/epcis/capture/) ---------- */

function EventForm({ ctx, shipment, onClose, onSaved }) {
  const nodes = ctx.world.nodes || [];
  const [form, setForm] = useState({
    biz_step: 'arriving',
    node_id: shipment.destination.id,
    event_time: '',
    quantity: '',
    gtin: shipment.lines && shipment.lines.length ? shipment.lines[0].gtin : '',
    batch_lot:
      shipment.lines && shipment.lines.length
        ? shipment.lines[0].batch_lot
        : '',
    note: '',
  });
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  const submit = async () => {
    const ok = await ctx.act(async () => {
      const r = await supplyPost(
        `/supply/api/shipments/${shipment.id}/events/`,
        {
          ...form,
          node_id: Number(form.node_id),
          quantity: form.quantity ? Number(form.quantity) : null,
          event_time: form.event_time || null,
        },
      );
      await onSaved();
      return r;
    }, 'Event recorded.');
    if (ok) onClose();
  };

  return (
    <Modal
      title="Record an event"
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
            disabled={ctx.busy}
          >
            Record event
          </button>
        </React.Fragment>
      }
    >
      <div className="notice">
        Records the same event a GS1 EPCIS feed would send — what happened,
        where, when, and to how much. It will be marked as entered by hand.
      </div>
      <FormRow label="What happened">
        <select value={form.biz_step} onChange={set('biz_step')}>
          {BIZ_STEPS.map((s) => (
            <option key={s.key} value={s.key}>
              {s.label}
            </option>
          ))}
        </select>
      </FormRow>
      <FormRow label="Where">
        <select value={form.node_id} onChange={set('node_id')}>
          {nodes.map((n) => (
            <option key={n.id} value={n.id}>
              {n.name}
            </option>
          ))}
        </select>
      </FormRow>
      <FormRow label="When" hint="Leave blank for now.">
        <input
          type="datetime-local"
          value={form.event_time}
          onChange={set('event_time')}
        />
      </FormRow>
      <div className="field-row-2">
        <FormRow label="Quantity (cartons)">
          <input
            type="number"
            value={form.quantity}
            onChange={set('quantity')}
          />
        </FormRow>
        <FormRow label="Batch / lot">
          <input
            type="text"
            value={form.batch_lot}
            onChange={set('batch_lot')}
          />
        </FormRow>
      </div>
      <FormRow label="Note">
        <textarea rows="3" value={form.note} onChange={set('note')} />
      </FormRow>
    </Modal>
  );
}
