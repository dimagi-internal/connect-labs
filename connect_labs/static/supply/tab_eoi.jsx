function EOITab({ ctx }) {
  const { world } = ctx;
  const [wizardRound, setWizardRound] = useState(null);
  const [detail, setDetail] = useState(null);

  const subsByRound = {};
  (world.my_submissions || []).forEach((s) => {
    subsByRound[s.round_id] = s;
  });

  return (
    <Page
      title="Expressions of interest"
      lede="Apply to open rounds to be assessed for the supplier registry."
    >
      <Card title="Open rounds">
        <DataTable
          rows={world.open_rounds || []}
          rowKey={(r) => r.id}
          empty="No rounds are open right now."
          columns={[
            { key: "title", label: "Round", value: (r) => r.title },
            {
              key: "cats",
              label: "Categories",
              sortable: false,
              value: () => "",
              render: (r) => <CategoryPills categories={r.categories} />,
            },
            { key: "closes", label: "Closes", value: (r) => r.closes_at, render: (r) => formatDate(r.closes_at) },
            {
              key: "state",
              label: "Your application",
              sortable: false,
              value: () => "",
              render: (r) => {
                const sub = subsByRound[r.id];
                if (!sub) return <Badge tone="warn">Not started</Badge>;
                return <StatusChip status={sub.status} />;
              },
            },
            {
              key: "act",
              label: "",
              sortable: false,
              value: () => "",
              render: (r) => {
                const sub = subsByRound[r.id];
                if (sub && sub.status !== "draft") {
                  return (
                    <button type="button" className="btn-link" onClick={() => setDetail(sub)}>
                      View
                    </button>
                  );
                }
                return (
                  <button type="button" className="btn btn-sm" onClick={() => setWizardRound(r)}>
                    {sub ? "Continue" : "Apply"}
                  </button>
                );
              },
            },
          ]}
        />
      </Card>

      <Card title="Your applications">
        <DataTable
          rows={world.my_submissions || []}
          rowKey={(s) => s.id}
          empty="You have not applied to any rounds yet."
          onRowClick={(s) => setDetail(s)}
          columns={[
            { key: "round", label: "Round", value: (s) => s.round_title },
            {
              key: "cats",
              label: "Categories",
              sortable: false,
              value: () => "",
              render: (s) => <CategoryPills categories={s.categories} />,
            },
            { key: "submitted", label: "Submitted", value: (s) => s.submitted_at, render: (s) => formatDate(s.submitted_at) },
            { key: "status", label: "Status", value: (s) => s.status, render: (s) => <StatusChip status={s.status} /> },
          ]}
        />
      </Card>

      {wizardRound ? (
        <EOIWizard
          ctx={ctx}
          round={wizardRound}
          existing={subsByRound[wizardRound.id]}
          onClose={() => setWizardRound(null)}
        />
      ) : null}
      {detail ? <SubmissionDetailModal submission={detail} onClose={() => setDetail(null)} /> : null}
    </Page>
  );
}

function EOIWizard({ ctx, round, existing, onClose }) {
  const [step, setStep] = useState(1);
  const [categories, setCategories] = useState(existing ? existing.categories : []);
  const [commitments, setCommitments] = useState(existing ? existing.commitments || {} : {});

  const toggle = (key) =>
    setCategories((cur) => (cur.includes(key) ? cur.filter((c) => c !== key) : [...cur, key]));

  const setCommit = (cat, field) => (e) =>
    setCommitments({
      ...commitments,
      [cat]: { ...(commitments[cat] || {}), [field]: e.target.value },
    });

  const saveDraft = () =>
    ctx.act(
      () =>
        supplyPost("/supply/api/eoi/submissions/", {
          round_id: round.id,
          categories,
          commitments,
        }),
      "Draft saved."
    );

  const submit = async () => {
    const saved = await ctx.act(
      () =>
        supplyPost("/supply/api/eoi/submissions/", {
          round_id: round.id,
          categories,
          commitments,
        }),
      null
    );
    if (!saved) return;
    const done = await ctx.act(
      () => supplyPost(`/supply/api/eoi/submissions/${saved.submission.id}/submit/`, {}),
      "Expression of interest submitted."
    );
    if (done) onClose();
  };

  const openCategories = SUPPLY_CATEGORIES.filter((c) => round.categories.includes(c.key));

  return (
    <Modal
      wide
      title={`Apply — ${round.title}`}
      onClose={onClose}
      footer={
        <React.Fragment>
          <div className="wizard-steps">Step {step} of 3</div>
          <div className="spacer" />
          {step > 1 ? (
            <button type="button" className="btn btn-secondary" onClick={() => setStep(step - 1)}>
              Back
            </button>
          ) : null}
          {step < 3 ? (
            <button
              type="button"
              className="btn"
              onClick={() => setStep(step + 1)}
              disabled={step === 1 && !categories.length}
            >
              Continue
            </button>
          ) : (
            <React.Fragment>
              <button type="button" className="btn btn-secondary" onClick={saveDraft} disabled={ctx.busy}>
                Save draft
              </button>
              <button type="button" className="btn" onClick={submit} disabled={ctx.busy}>
                Submit application
              </button>
            </React.Fragment>
          )}
        </React.Fragment>
      }
    >
      {step === 1 ? (
        <div>
          <p className="modal-lede">Which categories is your organisation applying under?</p>
          {openCategories.map((c) => (
            <label className="check-row" key={c.key}>
              <input type="checkbox" checked={categories.includes(c.key)} onChange={() => toggle(c.key)} />
              <span>{c.label}</span>
            </label>
          ))}
        </div>
      ) : null}

      {step === 2 ? (
        <div>
          <p className="modal-lede">
            What can you commit to this programme, per category? Reviewers score these against the
            round's requirements.
          </p>
          {categories.map((cat) => (
            <div className="commit-block" key={cat}>
              <h3>{categoryLabel(cat)}</h3>
              <FormRow label="Capacity you can dedicate" hint="e.g. 20,000 cartons per month">
                <input
                  type="text"
                  value={(commitments[cat] || {}).capacity || ""}
                  onChange={setCommit(cat, "capacity")}
                />
              </FormRow>
              <div className="field-row-2">
                <FormRow label="Regions served" hint="Comma-separated ISO codes, e.g. NG, SD">
                  <input
                    type="text"
                    value={(commitments[cat] || {}).regions || ""}
                    onChange={setCommit(cat, "regions")}
                  />
                </FormRow>
                <FormRow label="Lead time (days)">
                  <input
                    type="number"
                    value={(commitments[cat] || {}).lead_time_days || ""}
                    onChange={setCommit(cat, "lead_time_days")}
                  />
                </FormRow>
              </div>
              <FormRow label="Notes">
                <textarea
                  rows="2"
                  value={(commitments[cat] || {}).notes || ""}
                  onChange={setCommit(cat, "notes")}
                />
              </FormRow>
            </div>
          ))}
        </div>
      ) : null}

      {step === 3 ? (
        <div>
          <p className="modal-lede">Review and submit.</p>
          <Card title="Categories applied for">
            <CategoryPills categories={categories} />
          </Card>
          <Card title="Commitments">
            {categories.map((cat) => (
              <div className="commit-summary" key={cat}>
                <strong>{categoryLabel(cat)}</strong>
                <div className="muted">
                  {(commitments[cat] || {}).capacity || "No capacity stated"} ·{" "}
                  {(commitments[cat] || {}).regions || "no regions stated"} ·{" "}
                  {(commitments[cat] || {}).lead_time_days || "—"} day lead time
                </div>
              </div>
            ))}
          </Card>
          <div className="notice">
            On submission, a copy of your organisation profile and certifications is frozen and
            attached to this application. Later profile edits will not change what reviewers assess.
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

function SubmissionDetailModal({ submission, onClose }) {
  const snap = submission.profile_snapshot;
  return (
    <Modal wide title={`${submission.round_title} — your application`} onClose={onClose}>
      <div className="detail-head">
        <StatusChip status={submission.status} />
        <span className="muted">Submitted {formatDate(submission.submitted_at)}</span>
      </div>
      <Card title="Categories">
        <CategoryPills categories={submission.categories} />
      </Card>
      <Card title="Commitments">
        {Object.keys(submission.commitments || {}).length ? (
          Object.entries(submission.commitments).map(([cat, c]) => (
            <div className="commit-summary" key={cat}>
              <strong>{categoryLabel(cat)}</strong>
              <div className="muted">
                {c.capacity || "—"} · {c.regions || "—"} · {c.lead_time_days || "—"} day lead time
              </div>
              {c.notes ? <div>{c.notes}</div> : null}
            </div>
          ))
        ) : (
          <EmptyState title="No commitments recorded." />
        )}
      </Card>
      {snap ? (
        <Card title="Profile as submitted" subtitle="Frozen at submission — later edits do not affect it.">
          <div className="muted">{snap.description || "No description."}</div>
          <DataTable
            rows={snap.certifications || []}
            rowKey={(c) => c.id}
            empty="No certifications were on file."
            columns={[
              { key: "type", label: "Certification", value: (c) => c.cert_type },
              { key: "issuer", label: "Issuer", value: (c) => c.issuer || "—" },
              { key: "expiry", label: "Expires", value: (c) => c.expiry_date, render: (c) => formatDate(c.expiry_date) },
            ]}
          />
        </Card>
      ) : null}
    </Modal>
  );
}
