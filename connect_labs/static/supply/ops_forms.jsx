/* The supplier reporting webforms.

   Each is the hand-keyed equivalent of a machine-API call, posting the same
   payload shape to the same services — only the recorded source tier differs.
   Kept separate from the operations tab because they are the part most likely
   to change as the ingestion contract evolves.
*/

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
