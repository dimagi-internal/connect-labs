function RoundsTab({ ctx }) {
  const { world, act } = ctx;
  const canManage = supplyCan(world.role, 'rounds', 'manage');
  const [creating, setCreating] = useState(false);
  const [reviewing, setReviewing] = useState(null);
  const [viewingRound, setViewingRound] = useState(null);
  const [closingRound, setClosingRound] = useState(null);
  const rounds = world.rounds || [];
  const queue = world.review_queue || [];
  const allSubmissions = world.eoi_submissions || [];
  // The two counts on this page were 8 and 4 with nothing explaining the gap.
  const totalApplications = allSubmissions.length || queue.length;

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
      {/* "APPLICATIONS 8" sat in the Rounds table below a queue showing four
          rows, with nothing on the page reconciling the two. The queue is a
          worklist — it holds only what is still awaiting a decision — and that
          was true and unstated, which reads exactly like an inconsistency. */}
      <Card
        title="Review queue"
        subtitle={`Applications are assessed against the profile frozen at submission.${
          totalApplications > queue.length
            ? ` Showing the ${queue.length} awaiting a decision, of ${totalApplications} received — the decided ones are on their round below.`
            : ''
        }`}
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
                render: (r) => {
                  const b = r.submission_breakdown || {};
                  const decided = (b.qualified || 0) + (b.rejected || 0);
                  return (
                    <span>
                      {formatNumber(r.submission_count)}
                      {decided ? (
                        <span className="muted">
                          {' '}
                          · {b.qualified || 0} qualified, {b.rejected || 0}{' '}
                          rejected
                        </span>
                      ) : null}
                      {b.submitted ? (
                        <span className="muted"> · {b.submitted} pending</span>
                      ) : null}
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
                    // Closing stops suppliers applying and cannot be undone from
                    // this screen, and it was a single unguarded click styled
                    // SOFTER than the benign "Review" beside it — the reversible
                    // action shouted and the irreversible one whispered.
                    return (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => setClosingRound(r)}
                      >
                        Close round…
                      </button>
                    );
                  }
                  // A closed round held 14 applications behind a bare em-dash.
                  return (
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      onClick={() => setViewingRound(r)}
                    >
                      View applications
                    </button>
                  );
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
      {viewingRound ? (
        <RoundApplicationsModal
          round={viewingRound}
          submissions={allSubmissions.filter(
            (s) => s.round_id === viewingRound.id,
          )}
          onOpen={(s) => {
            setViewingRound(null);
            setReviewing(s);
          }}
          onClose={() => setViewingRound(null)}
        />
      ) : null}
      {closingRound ? (
        <ConfirmCloseRoundModal
          ctx={ctx}
          round={closingRound}
          onClose={() => setClosingRound(null)}
        />
      ) : null}
    </Page>
  );
}

/* Every application a round attracted, with what was decided about it.

   The count was in the table and the outcomes were nowhere: a closed round
   rendered a bare em-dash beside "14", so fourteen decisions were unreachable
   from the surface that counted them. */
function RoundApplicationsModal({ round, submissions, onOpen, onClose }) {
  return (
    <Modal title={`${round.title} — applications`} onClose={onClose} wide>
      <DataTable
        rows={submissions}
        rowKey={(s) => s.id}
        empty="This round attracted no applications."
        emptyHint="Nothing was submitted before it closed."
        onRowClick={onOpen}
        columns={[
          { key: 'org', label: 'Supplier', value: (s) => s.org_name },
          {
            key: 'country',
            label: 'Country',
            value: (s) => s.org_country,
            render: (s) => countryLabel(s.org_country),
          },
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
            key: 'status',
            label: 'Outcome',
            value: (s) => s.status,
            render: (s) => <StatusChip status={s.status} />,
          },
        ]}
      />
      <p className="muted small method-note">
        Each row opens the application as it was assessed — against the profile
        frozen at submission, not the supplier's profile today.
      </p>
    </Modal>
  );
}

/* Closing a round stops suppliers applying, and it was one unguarded click.

   It also sat in softer treatment than the benign "Review" in the row above,
   so the irreversible control was the quieter one. The confirmation states what
   closing prevents rather than asking "are you sure". */
function ConfirmCloseRoundModal({ ctx, round, onClose }) {
  const { act } = ctx;
  const pending = (round.submission_breakdown || {}).submitted || 0;
  return (
    <Modal
      title={`Close ${round.title}?`}
      onClose={onClose}
      footer={
        <React.Fragment>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Keep it open
          </button>
          <button
            type="button"
            className="btn"
            disabled={ctx.busy}
            onClick={() =>
              act(
                () =>
                  supplyPost(`/supply/api/eoi/rounds/${round.id}/transition/`, {
                    status: 'closed',
                  }),
                'Round closed.',
                // `act` swallows the error and returns null, so closing
                // unconditionally would dismiss the dialog on a failure the
                // reader might want to retry from.
              ).then((result) => {
                if (result) onClose();
              })
            }
          >
            Close the round
          </button>
        </React.Fragment>
      }
    >
      <p>
        Closing stops suppliers submitting new applications to this round. It
        cannot be reopened from this screen.
      </p>
      <p className="muted small">
        {round.submission_count
          ? `${round.submission_count} application${
              round.submission_count === 1 ? '' : 's'
            } already received are unaffected${
              pending
                ? `, and the ${pending} awaiting a decision stay in the review queue.`
                : '.'
            }`
          : 'No applications have been received, so nothing is preserved by closing later.'}
      </p>
    </Modal>
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

/* Mirrors QUALIFICATION_TERM_MONTHS in services/eoi_actions.py. Shown, not
   enforced — the server stamps the real date; this is what the reviewer is told
   they are about to grant, so the two have to agree.

   CALENDAR months, matching the server. The preview added 540 days while the
   rule on the same card said "18 months": 18 x 30 days from 22 July 2026 is
   22 January 2028 and the calendar answer is the 31st, so the date shown to the
   reviewer was up to nine days off the date actually stored. */
const QUALIFICATION_TERM_MONTHS = 18;

function qualificationExpiry() {
  const now = new Date();
  const target = new Date(
    now.getFullYear(),
    now.getMonth() + QUALIFICATION_TERM_MONTHS,
    1,
  );
  // Clamp to the target month's length, so 31 August + 18 months is the end of
  // February rather than rolling into March.
  const lastDay = new Date(
    target.getFullYear(),
    target.getMonth() + 1,
    0,
  ).getDate();
  target.setDate(Math.min(now.getDate(), lastDay));
  const month = String(target.getMonth() + 1).padStart(2, '0');
  const day = String(target.getDate()).padStart(2, '0');
  return `${target.getFullYear()}-${month}-${day}`;
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
            <dd>{contactLine(snap)}</dd>
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
              <div className="decision-cat">
                {categoryLabel(cat)}
                {/* The date the reviewer is actually granting, not the term it
                    is derived from. "18 months" is a rule; a roster is only
                    current if somebody can see when this particular supplier
                    falls off it, and that is a date. */}
                {/* PROSPECTIVE, because nothing has been granted yet. Qualify
                    and Reject only set local state — the POST fires from
                    "Record decision" — and the row asserted "qualified until
                    22 January 2028" in the present tense over an organisation
                    that was not qualified at all until the button below was
                    pressed. */}
                {decisions[cat] === 'qualify' ? (
                  <div className="muted small">
                    will be qualified until {formatDate(qualificationExpiry())},
                    once recorded
                  </div>
                ) : null}
                {decisions[cat] === 'reject' ? (
                  <div className="muted small">
                    will be declined — no qualification granted
                  </div>
                ) : null}
              </div>
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
