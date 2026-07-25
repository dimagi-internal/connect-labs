function RFPsTab({ ctx }) {
  const { world, act } = ctx;
  const canManage = supplyCan(world.role, 'rfps', 'manage');
  const canAward = supplyCan(world.role, 'rfps', 'award');
  const [creating, setCreating] = useState(false);
  const [openRfp, setOpenRfp] = useState(null);
  const rfps = world.rfps || [];

  return (
    <Page
      title="Solicitations"
      lede="Publish lots to the qualified registry, compare bids and award."
      actions={
        canManage ? (
          <button
            type="button"
            className="btn"
            onClick={() => setCreating(true)}
          >
            New solicitation
          </button>
        ) : null
      }
    >
      <Card title="All solicitations">
        <DataTable
          rows={rfps}
          rowKey={(r) => r.id}
          empty="No solicitations yet."
          columns={[
            { key: 'title', label: 'Solicitation', value: (r) => r.title },
            {
              key: 'cats',
              label: 'Categories',
              sortable: false,
              value: () => '',
              render: (r) => <CategoryPills categories={r.categories} />,
            },
            {
              key: 'countries',
              label: 'Countries',
              sortable: false,
              value: () => '',
              render: (r) => r.countries.map(countryLabel).join(', ') || '—',
            },
            { key: 'lots', label: 'Lots', value: (r) => r.lots.length },
            {
              key: 'awarded',
              label: 'Awarded',
              value: (r) => r.lots.filter((l) => l.awarded_org).length,
              render: (r) =>
                `${r.lots.filter((l) => l.awarded_org).length} / ${
                  r.lots.length
                }`,
            },
            {
              key: 'status',
              label: 'Status',
              value: (r) => r.status,
              render: (r) => <StatusChip status={r.status} />,
            },
            {
              key: 'act',
              label: '',
              sortable: false,
              value: () => '',
              render: (r) => (
                <div className="row-actions">
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => setOpenRfp(r)}
                  >
                    {r.status === 'draft' ? 'Edit lots' : 'Compare bids'}
                  </button>
                  {canManage && r.status === 'draft' ? (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() =>
                        act(
                          () =>
                            supplyPost(`/supply/api/rfps/${r.id}/transition/`, {
                              status: 'published',
                            }),
                          'Solicitation published to the registry.',
                        )
                      }
                    >
                      Publish
                    </button>
                  ) : null}
                </div>
              ),
            },
          ]}
        />
      </Card>

      {creating ? (
        <NewRFPModal ctx={ctx} onClose={() => setCreating(false)} />
      ) : null}
      {openRfp ? (
        <RFPDetailModal
          ctx={ctx}
          rfp={rfps.find((r) => r.id === openRfp.id) || openRfp}
          canAward={canAward}
          canManage={canManage}
          onClose={() => setOpenRfp(null)}
        />
      ) : null}
    </Page>
  );
}

function NewRFPModal({ ctx, onClose }) {
  const [form, setForm] = useState({
    title: '',
    brief: '',
    categories: [],
    countries: '',
    bid_deadline: '',
  });
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  const toggle = (key) =>
    setForm((cur) => ({
      ...cur,
      categories: cur.categories.includes(key)
        ? cur.categories.filter((c) => c !== key)
        : [...cur.categories, key],
    }));

  const submit = async () => {
    const ok = await ctx.act(
      () =>
        supplyPost('/supply/api/rfps/', {
          title: form.title,
          brief: form.brief,
          categories: form.categories,
          countries: form.countries
            .split(',')
            .map((c) => c.trim())
            .filter(Boolean),
          bid_deadline: form.bid_deadline || null,
        }),
      'Solicitation created as a draft — add lots, then publish.',
    );
    if (ok) onClose();
  };

  return (
    <Modal
      title="New solicitation"
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
            disabled={ctx.busy || !form.title || !form.categories.length}
          >
            Create
          </button>
        </React.Fragment>
      }
    >
      <FormRow label="Title">
        <input
          type="text"
          value={form.title}
          onChange={set('title')}
          autoFocus
        />
      </FormRow>
      <FormRow label="Brief">
        <textarea rows="3" value={form.brief} onChange={set('brief')} />
      </FormRow>
      <FormRow
        label="Categories"
        hint="Only suppliers qualified in these categories will see it."
      >
        {SUPPLY_CATEGORIES.map((c) => (
          <label className="check-row" key={c.key}>
            <input
              type="checkbox"
              checked={form.categories.includes(c.key)}
              onChange={() => toggle(c.key)}
            />
            <span>{c.label}</span>
          </label>
        ))}
      </FormRow>
      <div className="field-row-2">
        <FormRow
          label="Countries"
          hint="Comma-separated ISO codes, e.g. NG, SD"
        >
          <input
            type="text"
            value={form.countries}
            onChange={set('countries')}
          />
        </FormRow>
        <FormRow label="Bid deadline">
          <input
            type="date"
            value={form.bid_deadline}
            onChange={set('bid_deadline')}
          />
        </FormRow>
      </div>
    </Modal>
  );
}

function RFPDetailModal({ ctx, rfp, canAward, canManage, onClose }) {
  const isDraft = rfp.status === 'draft';
  const [comparison, setComparison] = useState(null);
  const [addingLot, setAddingLot] = useState(false);
  const [scoring, setScoring] = useState(null);

  const loadComparison = useCallback(async () => {
    if (isDraft) return;
    const body = await supplyGet(`/supply/api/rfps/${rfp.id}/comparison/`);
    setComparison(body.lots);
  }, [rfp.id, isDraft]);

  useEffect(() => {
    loadComparison();
  }, [loadComparison]);

  const award = (lot, lotBid) =>
    ctx.act(async () => {
      const result = await supplyPost(`/supply/api/lots/${lot.id}/award/`, {
        lot_bid_id: lotBid.id,
      });
      await loadComparison();
      return result;
    }, `Lot awarded to ${lotBid.org_name}.`);

  return (
    <Modal wide title={rfp.title} onClose={onClose}>
      <div className="detail-head">
        <StatusChip status={rfp.status} />
        <CategoryPills categories={rfp.categories} />
        <span className="muted">
          {rfp.countries.map(countryLabel).join(', ')} · bids due{' '}
          {formatDate(rfp.bid_deadline)}
        </span>
      </div>
      {rfp.brief ? <p className="modal-lede">{rfp.brief}</p> : null}

      {isDraft ? (
        <Card
          title="Lots"
          subtitle="Add every lot before publishing — lots cannot be added once published."
          actions={
            canManage ? (
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setAddingLot(true)}
              >
                Add lot
              </button>
            ) : null
          }
        >
          <DataTable
            rows={rfp.lots}
            rowKey={(l) => l.id}
            empty="No lots yet."
            columns={[
              { key: 'desc', label: 'Lot', value: (l) => l.description },
              {
                key: 'cat',
                label: 'Category',
                value: (l) => categoryLabel(l.category),
              },
              {
                key: 'qty',
                label: 'Quantity',
                value: (l) => l.quantity,
                render: (l) => `${formatNumber(l.quantity)} ${l.unit}`,
              },
              {
                key: 'dest',
                label: 'Destination',
                value: (l) => l.delivery_place,
                render: (l) =>
                  `${l.delivery_place}, ${countryLabel(l.delivery_country)}`,
              },
              {
                key: 'due',
                label: 'Delivery by',
                value: (l) => l.delivery_deadline,
                render: (l) => formatDate(l.delivery_deadline),
              },
            ]}
          />
        </Card>
      ) : null}

      {!isDraft && comparison
        ? comparison.map((entry) => (
            <Card
              key={entry.lot.id}
              title={entry.lot.description}
              subtitle={`${formatNumber(entry.lot.quantity)} ${
                entry.lot.unit
              } → ${entry.lot.delivery_place}, ${countryLabel(
                entry.lot.delivery_country,
              )} · due ${formatDate(entry.lot.delivery_deadline)}`}
              actions={
                entry.lot.awarded_org ? (
                  <Badge tone="accent">Awarded — {entry.lot.awarded_org}</Badge>
                ) : null
              }
            >
              <DataTable
                rows={entry.lot_bids}
                rowKey={(b) => b.id}
                empty="No submitted bids on this lot."
                columns={[
                  { key: 'rank', label: '#', value: (b) => b.price_rank },
                  { key: 'org', label: 'Supplier', value: (b) => b.org_name },
                  {
                    key: 'price',
                    label: 'Unit price',
                    value: (b) => b.unit_price,
                    render: (b) => formatMoney(b.unit_price, b.currency),
                  },
                  {
                    key: 'total',
                    label: 'Lot value',
                    value: (b) => b.unit_price * entry.lot.quantity,
                    render: (b) =>
                      formatMoney(
                        b.unit_price * entry.lot.quantity,
                        b.currency,
                      ),
                  },
                  {
                    key: 'lead',
                    label: 'Lead time',
                    value: (b) => b.lead_time_days,
                    render: (b) =>
                      b.lead_time_days ? `${b.lead_time_days}d` : '—',
                  },
                  {
                    key: 'tech',
                    label: 'Technical',
                    value: (b) => b.avg_technical_score,
                    render: (b) =>
                      b.avg_technical_score === null ? (
                        <button
                          type="button"
                          className="btn-link"
                          onClick={() => setScoring(b)}
                        >
                          Score
                        </button>
                      ) : (
                        <span className="score" onClick={() => setScoring(b)}>
                          {b.avg_technical_score}
                        </span>
                      ),
                  },
                  {
                    key: 'act',
                    label: '',
                    sortable: false,
                    value: () => '',
                    render: (b) =>
                      canAward && !entry.lot.awarded_org ? (
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => award(entry.lot, b)}
                        >
                          Award
                        </button>
                      ) : null,
                  },
                ]}
              />
            </Card>
          ))
        : null}

      {addingLot ? (
        <AddLotModal ctx={ctx} rfp={rfp} onClose={() => setAddingLot(false)} />
      ) : null}
      {scoring ? (
        <ScoreModal
          ctx={ctx}
          lotBid={scoring}
          onClose={() => setScoring(null)}
          onScored={loadComparison}
        />
      ) : null}
    </Modal>
  );
}

function AddLotModal({ ctx, rfp, onClose }) {
  const [form, setForm] = useState({
    category: rfp.categories[0] || 'rutf',
    description: '',
    quantity: '',
    unit: 'cartons',
    delivery_country: rfp.countries[0] || '',
    delivery_place: '',
    delivery_deadline: '',
  });
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  const submit = async () => {
    const ok = await ctx.act(
      () =>
        supplyPost(`/supply/api/rfps/${rfp.id}/lots/`, {
          ...form,
          delivery_deadline: form.delivery_deadline || null,
        }),
      'Lot added.',
    );
    if (ok) onClose();
  };

  return (
    <Modal
      title="Add lot"
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
            disabled={ctx.busy || !form.description || !form.quantity}
          >
            Add lot
          </button>
        </React.Fragment>
      }
    >
      <FormRow label="Category">
        <select value={form.category} onChange={set('category')}>
          {rfp.categories.map((c) => (
            <option key={c} value={c}>
              {categoryLabel(c)}
            </option>
          ))}
        </select>
      </FormRow>
      <FormRow
        label="Description"
        hint="e.g. 60,000 cartons RUTF delivered to Maiduguri"
      >
        <input
          type="text"
          value={form.description}
          onChange={set('description')}
          autoFocus
        />
      </FormRow>
      <div className="field-row-2">
        <FormRow label="Quantity">
          <input
            type="number"
            value={form.quantity}
            onChange={set('quantity')}
          />
        </FormRow>
        <FormRow label="Unit">
          <input type="text" value={form.unit} onChange={set('unit')} />
        </FormRow>
      </div>
      <div className="field-row-2">
        <FormRow label="Delivery country">
          <input
            type="text"
            maxLength="2"
            value={form.delivery_country}
            onChange={set('delivery_country')}
          />
        </FormRow>
        <FormRow label="Delivery place">
          <input
            type="text"
            value={form.delivery_place}
            onChange={set('delivery_place')}
          />
        </FormRow>
      </div>
      <FormRow label="Delivery deadline">
        <input
          type="date"
          value={form.delivery_deadline}
          onChange={set('delivery_deadline')}
        />
      </FormRow>
    </Modal>
  );
}

function ScoreModal({ ctx, lotBid, onClose, onScored }) {
  const [score, setScore] = useState('');
  const [notes, setNotes] = useState('');

  const submit = async () => {
    const ok = await ctx.act(async () => {
      const result = await supplyPost(
        `/supply/api/lot-bids/${lotBid.id}/score/`,
        {
          technical_score: score,
          notes,
        },
      );
      await onScored();
      return result;
    }, 'Technical score recorded.');
    if (ok) onClose();
  };

  return (
    <Modal
      title={`Technical score — ${lotBid.org_name}`}
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
            disabled={ctx.busy || score === ''}
          >
            Save score
          </button>
        </React.Fragment>
      }
    >
      {lotBid.scores && lotBid.scores.length ? (
        <div className="prior-scores">
          <h4>Scores already recorded</h4>
          {lotBid.scores.map((s, i) => (
            <div key={i} className="muted small">
              {s.reviewer || 'Reviewer'}: {s.technical_score}
              {s.notes ? ` — ${s.notes}` : ''}
            </div>
          ))}
        </div>
      ) : null}
      <FormRow
        label="Technical score (0–100)"
        hint="Financial rank is derived from price automatically."
      >
        <input
          type="number"
          min="0"
          max="100"
          value={score}
          onChange={(e) => setScore(e.target.value)}
          autoFocus
        />
      </FormRow>
      <FormRow label="Notes">
        <textarea
          rows="4"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </FormRow>
    </Modal>
  );
}
