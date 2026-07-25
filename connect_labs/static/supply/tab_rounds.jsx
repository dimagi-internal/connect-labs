function RoundsTab({ ctx }) {
  const { world, act } = ctx;
  const canManage = supplyCan(world.role, 'rounds', 'manage');
  const [creating, setCreating] = useState(false);
  const [reviewing, setReviewing] = useState(null);
  const rounds = world.rounds || [];
  const queue = world.review_queue || [];

  return (
    <Page
      title="EOI rounds & review"
      lede="Publish expression-of-interest rounds and assess the applications they attract."
      actions={
        canManage ? (
          <button
            type="button"
            className="btn"
            onClick={() => setCreating(true)}
          >
            New round
          </button>
        ) : null
      }
    >
      <Card
        title="Review queue"
        subtitle="Applications are assessed against the profile frozen at submission."
      >
        <DataTable
          rows={queue}
          rowKey={(s) => s.id}
          empty="The review queue is clear."
          columns={[
            { key: 'org', label: 'Supplier', value: (s) => s.org_name },
            {
              key: 'country',
              label: 'Country',
              value: (s) => s.org_country,
              render: (s) => countryLabel(s.org_country),
            },
            { key: 'round', label: 'Round', value: (s) => s.round_title },
            {
              key: 'cats',
              label: 'Applied for',
              sortable: false,
              value: () => '',
              render: (s) => <CategoryPills categories={s.categories} />,
            },
            {
              key: 'submitted',
              label: 'Submitted',
              value: (s) => s.submitted_at,
              render: (s) => formatDate(s.submitted_at),
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
                  onClick={() => setReviewing(s)}
                >
                  Review
                </button>
              ),
            },
          ]}
        />
      </Card>

      {canManage ? (
        <Card title="Rounds">
          <DataTable
            rows={rounds}
            rowKey={(r) => r.id}
            empty="No rounds created yet."
            columns={[
              { key: 'title', label: 'Round', value: (r) => r.title },
              {
                key: 'cats',
                label: 'Categories',
                sortable: false,
                value: () => '',
                render: (r) => <CategoryPills categories={r.categories} />,
              },
              {
                key: 'subs',
                label: 'Applications',
                value: (r) => r.submission_count,
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
                render: (r) => {
                  if (r.status === 'draft') {
                    return (
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() =>
                          act(
                            () =>
                              supplyPost(
                                `/supply/api/eoi/rounds/${r.id}/transition/`,
                                { status: 'open' },
                              ),
                            'Round opened.',
                          )
                        }
                      >
                        Open round
                      </button>
                    );
                  }
                  if (r.status === 'open') {
                    return (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() =>
                          act(
                            () =>
                              supplyPost(
                                `/supply/api/eoi/rounds/${r.id}/transition/`,
                                { status: 'closed' },
                              ),
                            'Round closed.',
                          )
                        }
                      >
                        Close
                      </button>
                    );
                  }
                  return <span className="muted">—</span>;
                },
              },
            ]}
          />
        </Card>
      ) : null}

      {creating ? (
        <NewRoundModal ctx={ctx} onClose={() => setCreating(false)} />
      ) : null}
      {reviewing ? (
        <ReviewModal
          ctx={ctx}
          submission={reviewing}
          onClose={() => setReviewing(null)}
        />
      ) : null}
    </Page>
  );
}

function NewRoundModal({ ctx, onClose }) {
  const [form, setForm] = useState({
    title: '',
    brief: '',
    categories: [],
    opens_at: '',
    closes_at: '',
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
        supplyPost('/supply/api/eoi/rounds/', {
          ...form,
          opens_at: form.opens_at || null,
          closes_at: form.closes_at || null,
        }),
      'Round created as a draft.',
    );
    if (ok) onClose();
  };

  return (
    <Modal
      title="New expression-of-interest round"
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
      <FormRow label="Categories open in this round">
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
        <FormRow label="Opens">
          <input type="date" value={form.opens_at} onChange={set('opens_at')} />
        </FormRow>
        <FormRow label="Closes">
          <input
            type="date"
            value={form.closes_at}
            onChange={set('closes_at')}
          />
        </FormRow>
      </div>
    </Modal>
  );
}

function ReviewModal({ ctx, submission, onClose }) {
  const [decisions, setDecisions] = useState({});
  const [notes, setNotes] = useState('');
  const snap = submission.profile_snapshot || {};

  const decide = (cat, verdict) =>
    setDecisions({ ...decisions, [cat]: verdict });

  const submit = async () => {
    const ok = await ctx.act(
      () =>
        supplyPost(`/supply/api/eoi/submissions/${submission.id}/review/`, {
          decisions,
          notes,
        }),
      'Decision recorded.',
    );
    if (ok) onClose();
  };

  const allDecided = submission.categories.every((c) => decisions[c]);

  return (
    <Modal
      wide
      title={`Review — ${submission.org_name}`}
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
            disabled={ctx.busy || !allDecided}
          >
            Record decision
          </button>
        </React.Fragment>
      }
    >
      <div className="review-split">
        <div className="review-col">
          <h3>Profile as submitted</h3>
          <div className="muted small">
            Frozen {formatDate(submission.submitted_at)} — later edits by the
            supplier do not change this.
          </div>
          <dl className="deflist">
            <dt>Legal name</dt>
            <dd>{snap.legal_name || submission.org_name}</dd>
            <dt>Country</dt>
            <dd>{countryLabel(snap.country || submission.org_country)}</dd>
            <dt>Head office</dt>
            <dd>{snap.hq_city || '—'}</dd>
            <dt>Registration</dt>
            <dd>{snap.registration_number || '—'}</dd>
            <dt>GLN</dt>
            <dd>{snap.gln || '—'}</dd>
            <dt>Contact</dt>
            <dd>
              {snap.contact_name || '—'}
              {snap.contact_email ? ` · ${snap.contact_email}` : ''}
            </dd>
          </dl>
          <p>
            {snap.description || (
              <span className="muted">No description supplied.</span>
            )}
          </p>

          <h4>Certifications</h4>
          <DataTable
            rows={snap.certifications || []}
            rowKey={(c) => c.id}
            empty="No certifications were on file at submission."
            columns={[
              {
                key: 'type',
                label: 'Certification',
                value: (c) => c.cert_type,
              },
              { key: 'issuer', label: 'Issuer', value: (c) => c.issuer || '—' },
              {
                key: 'expiry',
                label: 'Expires',
                value: (c) => c.expiry_date,
                render: (c) => <ExpiryChip iso={c.expiry_date} />,
              },
            ]}
          />

          <h4>Commitments</h4>
          {Object.entries(submission.commitments || {}).map(([cat, c]) => (
            <div className="commit-summary" key={cat}>
              <strong>{categoryLabel(cat)}</strong>
              <div className="muted">
                {c.capacity || '—'} · {c.regions || '—'} ·{' '}
                {c.lead_time_days || '—'} day lead time
              </div>
              {c.notes ? <div>{c.notes}</div> : null}
            </div>
          ))}
        </div>

        <div className="review-col">
          <h3>Decision</h3>
          <div className="muted small">
            Qualifying a category adds the supplier to the registry for 18
            months.
          </div>
          {submission.categories.map((cat) => (
            <div className="decision-row" key={cat}>
              <div className="decision-cat">{categoryLabel(cat)}</div>
              <div className="decision-buttons">
                <button
                  type="button"
                  className={`btn btn-sm ${
                    decisions[cat] === 'qualify' ? '' : 'btn-secondary'
                  }`}
                  onClick={() => decide(cat, 'qualify')}
                >
                  Qualify
                </button>
                <button
                  type="button"
                  className={`btn btn-sm ${
                    decisions[cat] === 'reject' ? 'btn-danger' : 'btn-secondary'
                  }`}
                  onClick={() => decide(cat, 'reject')}
                >
                  Reject
                </button>
              </div>
            </div>
          ))}
          <FormRow label="Reviewer notes">
            <textarea
              rows="5"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </FormRow>
        </div>
      </div>
    </Modal>
  );
}
