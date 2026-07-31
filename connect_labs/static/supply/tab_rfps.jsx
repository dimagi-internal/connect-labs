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
              label: 'Visible to',
              sortable: false,
              value: () => '',
              // The reach of a published solicitation, on the page of the person
              // who published it. Server-side eligibility was completely
              // invisible here: the row showed categories and lots, and the
              // number of suppliers that will ever receive the call — the whole
              // point of qualifying a registry first — appeared nowhere.
              render: (r) => (
                <span>
                  <CategoryPills categories={r.categories} />
                  <span className="cell-with-note muted small">
                    {r.eligible_supplier_count === 1
                      ? '1 qualified supplier'
                      : `${formatNumber(
                          r.eligible_supplier_count || 0,
                        )} qualified suppliers`}
                    <InfoNote
                      label="who receives this solicitation"
                      text="Organisations holding a live qualification in at least one of this solicitation's categories. Eligibility is applied when the supplier's payload is built, so anyone outside that set never receives the solicitation at all — they are not shown it with the bid button hidden. The count moves on its own as qualifications lapse."
                    />
                  </span>
                </span>
              ),
            },
            {
              key: 'countries',
              label: 'Countries',
              sortable: false,
              value: () => '',
              render: (r) => r.countries.map(countryLabel).join(', ') || '—',
            },
            {
              key: 'lots',
              label: 'Lots',
              value: (r) => r.lots.length,
              // A solicitation IS its lots. Listing only how many there are
              // makes the one question a supplier or a funder actually asks —
              // what is being bought, and where — require opening every row,
              // and puts the quantities two clicks from the page that exists to
              // publish them.
              render: (r) =>
                r.lots.length ? (
                  <div className="lot-summary">
                    {r.lots.map((l) => (
                      <div key={l.id}>
                        {formatNumber(l.quantity)} {l.unit} → {l.delivery_place}
                      </div>
                    ))}
                  </div>
                ) : (
                  '—'
                ),
            },
            // A bare "0 / 4" sat beside STATUS: Published — two undefined
            // completion affordances on one row, competing to tell the reader how
            // far along the tender is, neither saying what it counted. A ratio
            // needs its noun.
            {
              key: 'awarded',
              label: 'Lots awarded',
              value: (r) => r.lots.filter((l) => l.awarded_org).length,
              render: (r) => {
                const awarded = r.lots.filter((l) => l.awarded_org).length;
                return (
                  <span>
                    {awarded} of {r.lots.length}
                    <span className="muted">
                      {' '}
                      {r.lots.length === 1 ? 'lot' : 'lots'}
                    </span>
                  </span>
                );
              },
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
                  {/* A tender with lots still to award is work; one already
                      awarded is history. Six identical solid buttons down the
                      column said every row needed the same attention, when
                      five of them were finished months ago. */}
                  <button
                    type="button"
                    className={`btn btn-sm ${
                      r.lots.some((l) => !l.awarded_org) ? '' : 'btn-ghost'
                    }`}
                    onClick={() => setOpenRfp(r)}
                  >
                    {r.status === 'draft'
                      ? 'Edit lots'
                      : r.lots.some((l) => !l.awarded_org)
                      ? 'Compare & award'
                      : 'View bids'}
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
            .map((c) => c.trim().toUpperCase())
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
