// tab_workers_kyc.jsx — Worker KYC sub-tab
const { useState: useStateK, useEffect: useEffectK } = React;

// Debounce a fast-changing value (e.g. a search box) by `ms`.
function useDebouncedK(value, ms) {
  const [v, setV] = useStateK(value);
  useEffectK(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function KycSub({ density, role }) {
  const D = window.CUT_DATA;
  const PAGE_SIZE = D.WORKERS_PAGE_SIZE || 200;
  const toast = useToast();
  const sum = D.WORKERS_SUMMARY;
  const [workers, setWorkers] = useStateK([]);
  const [total, setTotal] = useStateK(D.WORKERS_TOTAL || 0);
  const [page, setPage] = useStateK(1);
  const [loading, setLoading] = useStateK(true);
  const [status, setStatus] = useStateK('all');
  const [q, setQ] = useStateK('');
  const [review, setReview] = useStateK(null); // worker id under review
  const [csvOpen, setCsvOpen] = useStateK(false);
  const canManage = window.CUT_RBAC.can(role, 'kyc', 'approve');
  const dq = useDebouncedK(q, 250);

  // A status pill is either a KYC status filter or the "flagged" fraud filter.
  const kycParam = status === 'all' || status === 'flagged' ? '' : status;
  const fraudParam = status === 'flagged' ? 'flagged' : '';

  useEffectK(() => {
    setPage(1);
  }, [status, dq]);

  const loadPage = React.useCallback(() => {
    setLoading(true);
    return D.fetchWorkers({
      page,
      page_size: PAGE_SIZE,
      q: dq,
      kyc: kycParam,
      fraud: fraudParam,
    })
      .then((res) => {
        setWorkers(res.workers || []);
        setTotal(res.total || 0);
      })
      .catch((e) => toast('Could not load workers: ' + e.message, 'danger'))
      .finally(() => setLoading(false));
  }, [page, dq, kycParam, fraudParam]);

  useEffectK(() => {
    loadPage();
  }, [loadPage]);

  const filtered = workers;
  const applyWorker = (msg, tone) => () => {
    loadPage();
    if (msg) toast(msg, tone);
    setReview(null);
  };
  const failToast = (e) => toast('Action failed: ' + e.message, 'danger');

  const statusTabs = [
    { id: 'all', label: 'All', count: sum.total },
    { id: 'pending', label: 'Pending', count: sum.kyc.pending },
    { id: 'review', label: 'In review', count: sum.kyc.review },
    { id: 'approved', label: 'Approved', count: sum.kyc.approved },
    { id: 'rejected', label: 'Rejected', count: sum.kyc.rejected },
    { id: 'flagged', label: 'Fraud flagged', count: sum.flagged },
  ];
  const reviewWorker = review ? workers.find((w) => w.id === review) : null;
  const rangeStart = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(page * PAGE_SIZE, total);
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 14,
          marginBottom: 18,
        }}
      >
        <Stat
          label="Approved"
          value={sum.kyc.approved}
          icon="circle-check"
          delta={Math.round((sum.kyc.approved / sum.total) * 100) + '%'}
          deltaTone="success"
        />
        <Stat
          label="Awaiting verification"
          value={sum.kyc.pending + sum.kyc.review}
          icon="hourglass-half"
          delta="Action needed"
          deltaTone="warning"
        />
        <Stat
          label="Rejected"
          value={sum.kyc.rejected}
          icon="circle-xmark"
          deltaTone="danger"
          delta="Needs follow-up"
        />
        <Stat
          label="Fraud flagged"
          value={sum.flagged}
          icon="flag"
          delta="Investigate"
          deltaTone="danger"
        />
      </div>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 16,
          marginBottom: 14,
          flexWrap: 'wrap',
        }}
      >
        <PillTabs tabs={statusTabs} active={status} onChange={setStatus} />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <div style={{ position: 'relative' }}>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search name or NIN…"
              style={{
                fontFamily: 'inherit',
                fontSize: 13,
                padding: '8px 12px 8px 30px',
                border: '1px solid ' + CUTC.border,
                borderRadius: 8,
                width: 190,
                outline: 'none',
                color: CUTC.purple,
              }}
            />
            <i
              className="fa fa-search"
              style={{
                position: 'absolute',
                left: 11,
                top: '50%',
                transform: 'translateY(-50%)',
                fontSize: 11,
                color: CUTC.muted,
              }}
            ></i>
          </div>
          <Button
            variant="secondary"
            icon="download"
            size="md"
            onClick={() =>
              toast('Exported ' + filtered.length + ' KYC records to CSV')
            }
          >
            Download CSV
          </Button>
          <Button
            variant="secondary"
            icon="upload"
            size="md"
            onClick={() => setCsvOpen(true)}
          >
            Upload CSV
          </Button>
        </div>
      </div>

      <Card padding={0}>
        <Table
          density={density}
          columns={[
            { label: 'Worker' },
            { label: 'National ID (NIN)' },
            { label: 'Documents' },
            { label: 'Flags' },
            { label: 'KYC status' },
            { label: 'Action', align: 'right' },
          ]}
        >
          {filtered.map((w) => {
            const verified = w.documents.filter(
              (d) => d.status === 'verified',
            ).length;
            return (
              <Row key={w.id} onClick={() => setReview(w.id)}>
                <Cell>
                  <div
                    style={{ display: 'flex', alignItems: 'center', gap: 11 }}
                  >
                    <Avatar name={w.name} />
                    <div>
                      <div style={{ fontWeight: 600, color: CUTC.purple }}>
                        {w.name}
                      </div>
                      <div
                        style={{
                          fontSize: 11.5,
                          color: CUTC.muted,
                          fontFamily: 'ui-monospace, monospace',
                        }}
                      >
                        {w.id} · {w.role}
                      </div>
                    </div>
                  </div>
                </Cell>
                <Cell mono>{w.nin}</Cell>
                <Cell>
                  <span
                    style={{
                      color: verified === 3 ? '#1E7B33' : CUTC.body,
                      fontWeight: 500,
                    }}
                  >
                    {verified}/3 verified
                  </span>
                </Cell>
                <Cell>
                  {w.fraudRules.length > 0 ? (
                    <span
                      style={{
                        display: 'inline-flex',
                        flexDirection: 'column',
                        gap: 3,
                      }}
                    >
                      <Badge tone="danger" dot>
                        {w.fraudRules[0]}
                        {w.fraudRules.length > 1
                          ? ' +' + (w.fraudRules.length - 1)
                          : ''}
                      </Badge>
                      {w.investigation && (
                        <span style={{ fontSize: 10.5, color: CUTC.muted }}>
                          Investigation: {w.investigation.status}
                        </span>
                      )}
                    </span>
                  ) : (
                    <span style={{ color: CUTC.faint }}>—</span>
                  )}
                </Cell>
                <Cell>
                  <KycBadge status={w.kyc} />
                </Cell>
                <Cell align="right">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={(e) => {
                      e.stopPropagation();
                      setReview(w.id);
                    }}
                  >
                    Review
                  </Button>
                </Cell>
              </Row>
            );
          })}
        </Table>
        {filtered.length === 0 && (
          <Empty
            icon="id-card"
            title={loading ? 'Loading…' : 'No records match'}
          />
        )}
        <div
          style={{
            padding: '12px 18px',
            borderTop: '1px solid ' + CUTC.border,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            fontSize: 12.5,
            color: CUTC.muted,
          }}
        >
          <span>
            Showing {rangeStart}–{rangeEnd} of {D.num(total)} records
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Button
              size="sm"
              variant="secondary"
              icon="chevron-left"
              disabled={page <= 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Prev
            </Button>
            <span>
              Page {page} of {maxPage}
            </span>
            <Button
              size="sm"
              variant="secondary"
              icon="chevron-right"
              disabled={page >= maxPage || loading}
              onClick={() => setPage((p) => Math.min(maxPage, p + 1))}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>

      {/* review modal */}
      <KycReviewModal
        worker={reviewWorker}
        onClose={() => setReview(null)}
        canManage={canManage}
        onApprove={(id) =>
          window.campaignActions
            .setKyc(id, 'approved')
            .then(applyWorker('KYC approved'))
            .catch(failToast)
        }
        onReject={(id) =>
          window.campaignActions
            .setKyc(id, 'rejected')
            .then(applyWorker('KYC rejected', 'danger'))
            .catch(failToast)
        }
        onSubmit={(id) =>
          window.campaignActions
            .setKyc(id, 'review')
            .then(applyWorker('Submitted to verification provider'))
            .catch(failToast)
        }
        onResolveDupe={(id, keep) =>
          window.campaignActions
            .resolveDuplicate(id, keep)
            .then(() => {
              loadPage();
              toast(
                keep ? 'Marked as distinct worker' : 'Duplicate archived',
                keep ? undefined : 'danger',
              );
            })
            .catch(failToast)
        }
        onInvestigation={(id, payload) =>
          window.campaignActions
            .saveInvestigation(id, {
              status: payload.status,
              outcome: payload.outcome,
              note: payload.note,
            })
            .then(() => {
              loadPage();
            })
            .catch(failToast)
        }
      />

      {/* CSV upload modal */}
      <CsvUploadModal
        open={csvOpen}
        onClose={() => setCsvOpen(false)}
        onDone={(n) => {
          toast(n + ' KYC records queued for verification');
          setCsvOpen(false);
        }}
      />
    </div>
  );
}

function KycReviewModal({
  worker,
  onClose,
  onApprove,
  onReject,
  onSubmit,
  onResolveDupe,
  canManage,
  onInvestigation,
}) {
  const D = window.CUT_DATA;
  if (!worker) return null;
  const docStatusBadge = (s) => (
    <Badge
      tone={
        s === 'verified'
          ? 'success'
          : s === 'rejected'
          ? 'danger'
          : s === 'submitted'
          ? 'info'
          : 'warning'
      }
    >
      {s[0].toUpperCase() + s.slice(1)}
    </Badge>
  );
  const flagged = worker.fraudRules.length > 0;
  return (
    <Modal
      open={!!worker}
      onClose={onClose}
      width={680}
      title="KYC verification & fraud review"
      footer={
        canManage ? (
          <>
            <Button variant="secondary" onClick={onClose}>
              Close
            </Button>
            {worker.kyc === 'pending' && (
              <Button
                variant="primary"
                icon="paper-plane"
                onClick={() => onSubmit(worker.id)}
              >
                Submit for verification
              </Button>
            )}
            <Button
              variant="dangerSoft"
              icon="xmark"
              onClick={() => onReject(worker.id)}
            >
              Reject
            </Button>
            <Button
              variant="success"
              icon="check"
              disabled={flagged}
              onClick={() => onApprove(worker.id)}
            >
              {flagged ? 'Resolve flags first' : 'Approve KYC'}
            </Button>
          </>
        ) : (
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        )
      }
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          marginBottom: 20,
        }}
      >
        <Avatar name={worker.name} size={46} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 17, fontWeight: 600, color: CUTC.purple }}>
            {worker.name}
          </div>
          <div style={{ fontSize: 12.5, color: CUTC.muted }}>
            {worker.id} · {worker.role} · {worker.region}
          </div>
        </div>
        <KycBadge status={worker.kyc} />
      </div>

      {flagged && (
        <div
          style={{
            border: '1px solid #F5C2BB',
            background: '#FCEEEC',
            borderRadius: 10,
            padding: 16,
            marginBottom: 18,
          }}
        >
          <div
            style={{
              display: 'flex',
              gap: 10,
              alignItems: 'center',
              marginBottom: 10,
            }}
          >
            <i className="fa fa-flag" style={{ color: '#B22312' }}></i>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#76190B' }}>
              Fraud detection rules triggered
            </div>
          </div>
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 7,
              marginBottom: 12,
            }}
          >
            {worker.fraudRules.map((r, i) => (
              <Badge key={i} tone="danger">
                {r}
              </Badge>
            ))}
          </div>
          {worker.linked && worker.linked.length > 0 && (
            <>
              <div
                style={{
                  fontSize: 11.5,
                  fontWeight: 600,
                  color: '#76190B',
                  textTransform: 'uppercase',
                  letterSpacing: '.05em',
                  marginBottom: 8,
                }}
              >
                Linked worker records — shared identifiers
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {worker.linked.map((l, i) => (
                  <div
                    key={i}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 10,
                      background: '#fff',
                      borderRadius: 8,
                      padding: '8px 12px',
                      fontSize: 12.5,
                    }}
                  >
                    <i
                      className="fa fa-link"
                      style={{ color: '#B22312', fontSize: 11 }}
                    ></i>
                    <span style={{ fontWeight: 600, color: CUTC.purple }}>
                      {l.name}
                    </span>
                    <span
                      style={{
                        color: CUTC.muted,
                        fontFamily: 'ui-monospace, monospace',
                      }}
                    >
                      {l.id}
                    </span>
                    <span style={{ flex: 1 }}></span>
                    <Badge tone="warning">
                      Shared {D.sharedLabel[l.shared] || l.shared}
                    </Badge>
                  </div>
                ))}
              </div>
            </>
          )}
          {canManage && (
            <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
              <Button
                size="sm"
                variant="secondary"
                icon="user-check"
                onClick={() => onResolveDupe(worker.id, true)}
              >
                Mark distinct (clear flags)
              </Button>
              <Button
                size="sm"
                variant="dangerSoft"
                icon="box-archive"
                onClick={() => onResolveDupe(worker.id, false)}
              >
                Archive & reject
              </Button>
            </div>
          )}
        </div>
      )}

      {flagged && (
        <InvestigationPanel
          worker={worker}
          canManage={canManage}
          onChange={(inv) => onInvestigation(worker.id, inv)}
        />
      )}

      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: CUTC.purple,
          margin: '4px 0 8px',
        }}
      >
        Personal details
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '10px 20px',
          marginBottom: 22,
          fontSize: 13,
        }}
      >
        {[
          ['NIN', worker.nin],
          ['Phone', worker.phone],
          ['Passport', worker.passport || '—'],
          ['Bank', worker.bank],
          ['Account', worker.acct],
          ['Region', worker.region + ', ' + worker.lga],
        ].map(([l, v]) => (
          <div key={l}>
            <span style={{ color: CUTC.muted }}>{l}</span>
            <div
              style={{
                color: CUTC.purple,
                fontWeight: 500,
                fontFamily:
                  l === 'NIN' || l === 'Account'
                    ? 'ui-monospace, monospace'
                    : 'inherit',
              }}
            >
              {v}
            </div>
          </div>
        ))}
      </div>

      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: CUTC.purple,
          marginBottom: 8,
        }}
      >
        Submitted documentation
      </div>
      <div
        style={{
          border: '1px solid ' + CUTC.border,
          borderRadius: 10,
          overflow: 'hidden',
        }}
      >
        {worker.documents.map((d, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '12px 16px',
              borderBottom:
                i < worker.documents.length - 1
                  ? '1px solid ' + CUTC.borderSoft
                  : 'none',
            }}
          >
            <div
              style={{
                width: 34,
                height: 34,
                borderRadius: 7,
                background: CUTC.surface,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--accent)',
              }}
            >
              <i className="fa fa-file-lines"></i>
            </div>
            <div
              style={{
                flex: 1,
                fontSize: 13,
                fontWeight: 500,
                color: CUTC.purple,
              }}
            >
              {d.type}
            </div>
            {docStatusBadge(d.status)}
            <button
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--accent-dark)',
                cursor: 'pointer',
                fontSize: 12.5,
                fontWeight: 600,
                fontFamily: 'inherit',
              }}
            >
              View
            </button>
          </div>
        ))}
      </div>
    </Modal>
  );
}

// Fraud investigation record (Phase 2 feature, lightweight here)
function InvestigationPanel({ worker, canManage, onChange }) {
  const inv = worker.investigation || {
    status: 'Open',
    notes: [],
    outcome: null,
  };
  const [note, setNote] = useStateK('');
  const [open, setOpen] = useStateK(true);
  const statuses = ['Open', 'Under Review', 'Resolved', 'False Positive'];
  const tone = {
    Open: 'warning',
    'Under Review': 'info',
    Resolved: 'success',
    'False Positive': 'neutral',
  };
  const setStatus = (s) => onChange({ status: s });
  const addNote = () => {
    if (!note.trim()) return;
    onChange({ note: note.trim() });
    setNote('');
  };
  return (
    <div
      style={{
        border: '1px solid ' + CUTC.border,
        borderRadius: 10,
        marginBottom: 20,
        overflow: 'hidden',
      }}
    >
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '12px 16px',
          background: CUTC.surface,
          border: 'none',
          cursor: 'pointer',
          fontFamily: 'inherit',
        }}
      >
        <i className="fa fa-folder-open" style={{ color: 'var(--accent)' }}></i>
        <span style={{ fontSize: 13, fontWeight: 600, color: CUTC.purple }}>
          Fraud investigation record
        </span>
        <Badge tone={tone[inv.status]}>{inv.status}</Badge>
        <span style={{ flex: 1 }}></span>
        <i
          className={`fa fa-chevron-${open ? 'up' : 'down'}`}
          style={{ color: CUTC.muted, fontSize: 11 }}
        ></i>
      </button>
      {open && (
        <div style={{ padding: 16 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              marginBottom: 14,
            }}
          >
            <span style={{ fontSize: 12, color: CUTC.muted }}>Status</span>
            {canManage ? (
              <Select
                value={inv.status}
                onChange={(e) => setStatus(e.target.value)}
                style={{
                  width: 'auto',
                  fontSize: 12.5,
                  padding: '6px 28px 6px 10px',
                }}
              >
                {statuses.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </Select>
            ) : (
              <Badge tone={tone[inv.status]}>{inv.status}</Badge>
            )}
          </div>
          {canManage && (
            <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
              <TextInput
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Add investigation note…"
                style={{ fontSize: 12.5 }}
              />
              <Button
                size="sm"
                variant="secondary"
                icon="plus"
                onClick={addNote}
              >
                Add
              </Button>
            </div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {(inv.notes || []).length === 0 && (
              <div style={{ fontSize: 12.5, color: CUTC.muted }}>
                No investigation notes yet.
              </div>
            )}
            {(inv.notes || []).map((n, i) => (
              <div key={i} style={{ display: 'flex', gap: 10 }}>
                <div
                  style={{
                    width: 26,
                    height: 26,
                    borderRadius: '50%',
                    background: 'var(--accent-soft)',
                    color: 'var(--accent-dark)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 10,
                    fontWeight: 700,
                    flexShrink: 0,
                  }}
                >
                  {n.by === 'You'
                    ? 'AO'
                    : n.by
                        .split(' ')
                        .map((p) => p[0])
                        .join('')}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12.5, color: CUTC.purple }}>
                    {n.text}
                  </div>
                  <div
                    style={{ fontSize: 11, color: CUTC.faint, marginTop: 2 }}
                  >
                    {n.by} · {n.at}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CsvUploadModal({ open, onClose, onDone }) {
  const [stage, setStage] = useStateK('drop'); // drop -> validating -> preview
  const [rows] = useStateK(48);
  React.useEffect(() => {
    if (open) setStage('drop');
  }, [open]);
  const start = () => {
    setStage('validating');
    setTimeout(() => setStage('preview'), 1100);
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      width={560}
      title="Bulk KYC upload"
      footer={
        stage === 'preview' ? (
          <>
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              icon="check"
              onClick={() => onDone(rows - 3)}
            >
              Import {rows - 3} valid records
            </Button>
          </>
        ) : (
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        )
      }
    >
      {stage === 'drop' && (
        <>
          <div
            onClick={start}
            style={{
              border: '2px dashed ' + CUTC.border,
              borderRadius: 12,
              padding: '40px 24px',
              textAlign: 'center',
              cursor: 'pointer',
              background: CUTC.surface,
            }}
          >
            <i
              className="fa fa-cloud-arrow-up"
              style={{ fontSize: 30, color: 'var(--accent)' }}
            ></i>
            <div
              style={{
                fontSize: 14.5,
                fontWeight: 600,
                color: CUTC.purple,
                marginTop: 12,
              }}
            >
              Drop a CSV file or click to browse
            </div>
            <div style={{ fontSize: 12.5, color: CUTC.muted, marginTop: 5 }}>
              Columns: worker_id, name, nin, bvn, bank, account, document_url
            </div>
          </div>
          <div
            style={{
              display: 'flex',
              gap: 10,
              marginTop: 16,
              fontSize: 12.5,
              color: CUTC.muted,
              alignItems: 'center',
            }}
          >
            <i
              className="fa fa-circle-info"
              style={{ color: 'var(--accent)' }}
            ></i>
            <span>
              Need the format?{' '}
              <button
                onClick={() => {}}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--accent-dark)',
                  fontWeight: 600,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                  fontSize: 12.5,
                }}
              >
                Download template
              </button>
            </span>
          </div>
        </>
      )}
      {stage === 'validating' && (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>
          <i
            className="fa fa-spinner fa-spin"
            style={{ fontSize: 28, color: 'var(--accent)' }}
          ></i>
          <div
            style={{
              fontSize: 14,
              color: CUTC.purple,
              fontWeight: 600,
              marginTop: 14,
            }}
          >
            Validating kyc_batch_round2.csv…
          </div>
          <div style={{ fontSize: 12.5, color: CUTC.muted, marginTop: 4 }}>
            Checking NIN format and duplicate records
          </div>
        </div>
      )}
      {stage === 'preview' && (
        <>
          <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
            <div
              style={{
                flex: 1,
                background: '#D9ECD4',
                borderRadius: 10,
                padding: '12px 14px',
              }}
            >
              <div style={{ fontSize: 22, fontWeight: 600, color: '#1E4F12' }}>
                {rows - 3}
              </div>
              <div style={{ fontSize: 11.5, color: '#1E4F12' }}>
                Valid records
              </div>
            </div>
            <div
              style={{
                flex: 1,
                background: '#FCEFCC',
                borderRadius: 10,
                padding: '12px 14px',
              }}
            >
              <div style={{ fontSize: 22, fontWeight: 600, color: '#7A5800' }}>
                2
              </div>
              <div style={{ fontSize: 11.5, color: '#7A5800' }}>
                Duplicate NIN
              </div>
            </div>
            <div
              style={{
                flex: 1,
                background: '#FAD8D4',
                borderRadius: 10,
                padding: '12px 14px',
              }}
            >
              <div style={{ fontSize: 22, fontWeight: 600, color: '#76190B' }}>
                1
              </div>
              <div style={{ fontSize: 11.5, color: '#76190B' }}>
                Invalid format
              </div>
            </div>
          </div>
          <div style={{ fontSize: 12.5, color: CUTC.body, lineHeight: 1.5 }}>
            Row 14 &amp; 31 share an NIN with existing workers and will be
            flagged for review. Row 22 has a malformed NIN and will be skipped.
          </div>
        </>
      )}
    </Modal>
  );
}

Object.assign(window, { KycSub });
