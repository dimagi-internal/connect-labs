// tab_workers.jsx — Workers container + Payments sub-tab + daily payment approval drawer
const { useState: useStateW, useEffect: useEffectW } = React;

// Debounce a fast-changing value (e.g. a search box) by `ms`.
function useDebouncedW(value, ms) {
  const [v, setV] = useStateW(value);
  useEffectW(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function WorkersTab({
  sub,
  onSub,
  density,
  jumpFilter,
  openWorkerId,
  onCloseWorker,
  role,
}) {
  const heads = {
    payments: {
      title: 'Worker Payments',
      sub: 'Review, validate and approve worker payments at the daily level.',
    },
    kyc: {
      title: 'Worker KYC',
      sub: 'Verify worker identity, resolve duplicate records, and manage KYC in bulk.',
    },
    profile: {
      title: 'Worker Profiles',
      sub: 'Worker details, participation, verification, attendance and registration history.',
    },
  };
  const h = heads[sub] || heads.payments;
  return (
    <Page max={1400}>
      <PageHead
        eyebrow="Workers"
        title={h.title}
        sub={h.sub}
        actions={
          <>
            <Button variant="secondary" icon="upload">
              Import CSV
            </Button>
            <Button variant="secondary" icon="download">
              Export
            </Button>
          </>
        }
      />
      {sub === 'payments' && (
        <PaymentsSub density={density} jumpFilter={jumpFilter} role={role} />
      )}
      {sub === 'kyc' && (
        <KycSub density={density} jumpFilter={jumpFilter} role={role} />
      )}
      {sub === 'profile' && (
        <ProfileSub density={density} openWorkerId={openWorkerId} role={role} />
      )}
    </Page>
  );
}

// ============ PAYMENTS SUB-TAB ============
function PaymentsSub({ density, role }) {
  const D = window.CUT_DATA;
  const PAGE_SIZE = D.WORKERS_PAGE_SIZE || 200;
  const toast = useToast();
  // Summary stats come from the server-computed aggregate over ALL workers.
  const sum = D.WORKERS_SUMMARY;
  const [workers, setWorkers] = useStateW([]);
  const [total, setTotal] = useStateW(D.WORKERS_TOTAL || 0);
  const [page, setPage] = useStateW(1);
  const [loading, setLoading] = useStateW(true);
  const [status, setStatus] = useStateW('all');
  const [roleF, setRoleF] = useStateW('all');
  const [region, setRegion] = useStateW('all');
  const [q, setQ] = useStateW('');
  const [fraudF, setFraudF] = useStateW('all');
  const [sel, setSel] = useStateW(new Set());
  const [drawer, setDrawer] = useStateW(null); // worker id
  const canApprove = window.CUT_RBAC.can(role, 'payments', 'approve');
  const dq = useDebouncedW(q, 250);

  // Reset to page 1 whenever a filter/search changes.
  useEffectW(() => {
    setPage(1);
  }, [status, roleF, region, fraudF, dq]);

  const loadPage = React.useCallback(() => {
    setLoading(true);
    return D.fetchWorkers({
      page,
      page_size: PAGE_SIZE,
      q: dq,
      pay: status,
      role: roleF,
      region,
      fraud: fraudF,
    })
      .then((res) => {
        setWorkers(res.workers || []);
        setTotal(res.total || 0);
      })
      .catch((e) => toast('Could not load workers: ' + e.message, 'danger'))
      .finally(() => setLoading(false));
  }, [page, dq, status, roleF, region, fraudF]);

  useEffectW(() => {
    loadPage();
  }, [loadPage]);

  // The fetched page IS the displayed list (server filtered it).
  const filtered = workers;

  const pendingAmt = sum.pendingAmount;

  const setPay = (ids, newStatus) => {
    window.campaignActions
      .setPayStatus(ids, newStatus)
      .then(function (res) {
        if ((res.blocked || []).length)
          toast(res.blocked.length + ' blocked by fraud flags', 'danger');
        // Re-fetch the current page so statuses refresh from the server.
        loadPage();
      })
      .catch(function (e) {
        toast('Action failed: ' + e.message, 'danger');
      });
  };
  const toggle = (id) =>
    setSel((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  const allVisible =
    filtered.length > 0 && filtered.every((w) => sel.has(w.id));
  const toggleAll = () =>
    setSel((s) => {
      const n = new Set(s);
      allVisible
        ? filtered.forEach((w) => n.delete(w.id))
        : filtered.forEach((w) => n.add(w.id));
      return n;
    });

  const statusTabs = [
    { id: 'all', label: 'All', count: sum.total },
    { id: 'pending', label: 'Pending', count: sum.pay.pending },
    { id: 'approved', label: 'Approved', count: sum.pay.approved },
    { id: 'paid', label: 'Paid', count: sum.pay.paid },
    { id: 'hold', label: 'On hold', count: sum.pay.hold },
    { id: 'rejected', label: 'Rejected', count: sum.pay.rejected },
  ];

  const drawerWorker = drawer ? workers.find((w) => w.id === drawer) : null;
  const rangeStart = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(page * PAGE_SIZE, total);
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div>
      {/* summary cards */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 14,
          marginBottom: 18,
        }}
      >
        <Stat
          label="Pending approval"
          value={D.moneyK(pendingAmt)}
          icon="hourglass-half"
          sub={sum.pay.pending + ' workers'}
          delta="Action needed"
          deltaTone="warning"
        />
        <Stat
          label="Approved (unpaid)"
          value={sum.pay.approved}
          icon="circle-check"
          sub="awaiting disbursement"
        />
        <Stat
          label="Paid this round"
          value={D.moneyK(sum.paidAmount)}
          icon="money-bill-wave"
          delta={sum.pay.paid + ' paid'}
          deltaTone="success"
        />
        <Stat
          label="Duplicate flags"
          value={sum.duplicates}
          icon="clone"
          delta="Block payment"
          deltaTone="danger"
        />
      </div>

      {/* status pills + filters */}
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
              placeholder="Search worker…"
              style={{
                fontFamily: 'inherit',
                fontSize: 13,
                padding: '8px 12px 8px 30px',
                border: '1px solid ' + CUTC.border,
                borderRadius: 8,
                width: 180,
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
          <Select
            value={roleF}
            onChange={(e) => setRoleF(e.target.value)}
            style={{
              width: 'auto',
              fontSize: 13,
              padding: '8px 30px 8px 12px',
            }}
          >
            <option value="all">All roles</option>
            {D.ROLES.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </Select>
          <Select
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            style={{
              width: 'auto',
              fontSize: 13,
              padding: '8px 30px 8px 12px',
            }}
          >
            <option value="all">All regions</option>
            {D.REGIONS.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </Select>
          <Select
            value={fraudF}
            onChange={(e) => setFraudF(e.target.value)}
            style={{
              width: 'auto',
              fontSize: 13,
              padding: '8px 30px 8px 12px',
            }}
          >
            <option value="all">All records</option>
            <option value="flagged">Fraud flagged</option>
            <option value="clean">No flags</option>
          </Select>
        </div>
      </div>

      {/* bulk bar */}
      {sel.size > 0 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            padding: '12px 18px',
            background: 'var(--accent-soft)',
            border: '1px solid var(--accent)',
            borderRadius: 10,
            marginBottom: 14,
          }}
        >
          <span
            style={{
              fontSize: 13.5,
              fontWeight: 600,
              color: 'var(--accent-dark)',
            }}
          >
            {sel.size} selected
          </span>
          <span style={{ fontSize: 13, color: CUTC.body }}>
            {D.money(
              [...sel].reduce(
                (a, id) => a + (workers.find((w) => w.id === id)?.amount || 0),
                0,
              ),
            )}{' '}
            total
          </span>
          <div style={{ flex: 1 }}></div>
          {canApprove ? (
            <>
              <Button
                size="sm"
                variant="success"
                icon="check"
                onClick={() => {
                  setPay([...sel], 'approved');
                  toast(`Approved ${sel.size} payments`);
                  setSel(new Set());
                }}
              >
                Approve selected
              </Button>
              <Button
                size="sm"
                variant="dangerSoft"
                icon="xmark"
                onClick={() => {
                  setPay([...sel], 'rejected');
                  toast(`Rejected ${sel.size} payments`, 'danger');
                  setSel(new Set());
                }}
              >
                Reject
              </Button>
            </>
          ) : (
            <Badge tone="neutral">Read-only for {role}</Badge>
          )}
          <Button size="sm" variant="ghost" onClick={() => setSel(new Set())}>
            Clear
          </Button>
        </div>
      )}

      {/* table */}
      <Card padding={0}>
        <Table
          density={density}
          columns={[
            {
              label: (
                <Check
                  checked={allVisible}
                  indeterminate={
                    !allVisible && filtered.some((w) => sel.has(w.id))
                  }
                  onChange={toggleAll}
                />
              ),
              width: 44,
            },
            { label: 'Worker' },
            { label: 'Role / region' },
            { label: 'Days', align: 'center' },
            { label: 'Rate', align: 'right' },
            { label: 'Amount', align: 'right' },
            { label: 'KYC' },
            { label: 'Payment' },
            { label: '', width: 44 },
          ]}
        >
          {filtered.map((w) => (
            <Row
              key={w.id}
              selected={sel.has(w.id)}
              onClick={() => setDrawer(w.id)}
            >
              <Cell>
                <Check checked={sel.has(w.id)} onChange={() => toggle(w.id)} />
              </Cell>
              <Cell>
                <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
                  <Avatar name={w.name} />
                  <div>
                    <div
                      style={{
                        fontWeight: 600,
                        color: CUTC.purple,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 7,
                      }}
                    >
                      {w.name}
                      {w.fraudRules.length > 0 && (
                        <span
                          title={'Fraud flag: ' + w.fraudRules.join(', ')}
                          style={{
                            color: '#E13019',
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 4,
                            fontSize: 11,
                            fontWeight: 600,
                          }}
                        >
                          <i
                            className="fa fa-flag"
                            style={{ fontSize: 10 }}
                          ></i>
                          {w.fraudRules.length}
                        </span>
                      )}
                    </div>
                    <div
                      style={{
                        fontSize: 11.5,
                        color: CUTC.muted,
                        fontFamily: 'ui-monospace, monospace',
                      }}
                    >
                      {w.id}
                    </div>
                  </div>
                </div>
              </Cell>
              <Cell>
                <div style={{ fontWeight: 500, color: CUTC.body }}>
                  {w.role}
                </div>
                <div style={{ fontSize: 11.5, color: CUTC.muted }}>
                  {w.region} · {w.lga}
                </div>
              </Cell>
              <Cell align="center" mono>
                <span style={{ color: CUTC.purple, fontWeight: 600 }}>
                  {w.daysApproved}
                </span>
                <span style={{ color: CUTC.faint }}>/{w.daysWorked}</span>
              </Cell>
              <Cell align="right" mono>
                {D.money(w.rate)}
              </Cell>
              <Cell align="right" mono strong>
                {D.money(w.amount)}
              </Cell>
              <Cell>
                <KycBadge status={w.kyc} />
              </Cell>
              <Cell>
                <PayBadge status={w.pay} />
              </Cell>
              <Cell align="center">
                <i
                  className="fa fa-chevron-right"
                  style={{ color: CUTC.faint, fontSize: 12 }}
                ></i>
              </Cell>
            </Row>
          ))}
        </Table>
        {filtered.length === 0 && (
          <Empty
            icon="money-check-dollar"
            title={loading ? 'Loading…' : 'No payments match your filters'}
            sub={
              loading
                ? 'Fetching workers from CommCare.'
                : 'Try clearing the status or region filter.'
            }
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
            Showing {rangeStart}–{rangeEnd} of {D.num(total)} workers
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

      <PaymentDrawer
        worker={drawerWorker}
        onClose={() => setDrawer(null)}
        canApprove={canApprove}
        onApproveAll={(id) => {
          setPay([id], 'approved');
          toast('All days approved');
        }}
        onReject={(id) => {
          setPay([id], 'rejected');
          toast('Payment rejected', 'danger');
          setDrawer(null);
        }}
        onQueue={(id, count) =>
          window.campaignActions
            .queuePay(id, count)
            .then(function () {
              loadPage();
              toast('Approved & queued for payment');
            })
            .catch(function (e) {
              toast('Could not queue: ' + e.message, 'danger');
            })
        }
      />
    </div>
  );
}

// ============ PAYMENT APPROVAL DRAWER (hero flow) ============
function PaymentDrawer({
  worker,
  onClose,
  onApproveAll,
  onReject,
  canApprove,
  onQueue,
}) {
  const D = window.CUT_DATA;
  const toast = useToast();
  const [days, setDays] = useStateW([]);
  React.useEffect(() => {
    if (worker) setDays(D.dailyForWorker(worker).map((d) => ({ ...d })));
  }, [worker?.id]);
  if (!worker) return null;

  const approvedCount = days.filter((d) => d.status === 'approved').length;
  const approvedAmt = days
    .filter((d) => d.status === 'approved')
    .reduce((a, d) => a + d.amount, 0);
  const pendingCount = days.filter((d) => d.status === 'pending').length;
  const setDay = (i, st) =>
    setDays((ds) => ds.map((d, j) => (j === i ? { ...d, status: st } : d)));
  const approveAllPending = () => {
    setDays((ds) =>
      ds.map((d) =>
        d.status === 'pending' ? { ...d, status: 'approved' } : d,
      ),
    );
    toast('Approved ' + pendingCount + ' days');
  };

  return (
    <Drawer open={!!worker} onClose={onClose} width={780}>
      {/* header */}
      <div
        style={{
          padding: '20px 26px',
          borderBottom: '1px solid ' + CUTC.border,
          display: 'flex',
          alignItems: 'flex-start',
          gap: 16,
        }}
      >
        <Avatar name={worker.name} size={48} />
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h2
              style={{
                margin: 0,
                fontSize: 20,
                color: CUTC.purple,
                fontWeight: 600,
              }}
            >
              {worker.name}
            </h2>
            {worker.fraudRules.length > 0 && (
              <Badge tone="danger" dot>
                {worker.fraudRules.length} fraud flag
                {worker.fraudRules.length > 1 ? 's' : ''}
              </Badge>
            )}
          </div>
          <div style={{ fontSize: 12.5, color: CUTC.muted, marginTop: 3 }}>
            {worker.id} · {worker.role} · {worker.region}, {worker.lga}
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: CUTC.muted,
            fontSize: 18,
            padding: 4,
          }}
        >
          <i className="fa fa-times"></i>
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: 26 }}>
        {worker.fraudRules.length > 0 && (
          <div
            style={{
              border: '1px solid #F5C2BB',
              background: '#FCEEEC',
              borderRadius: 10,
              marginBottom: 20,
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                padding: '12px 16px',
                background: '#FAD8D4',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
              }}
            >
              <i className="fa fa-flag" style={{ color: '#B22312' }}></i>
              <span style={{ fontSize: 13, fontWeight: 600, color: '#76190B' }}>
                Payment flagged — review before approval
              </span>
            </div>
            <div
              style={{
                padding: '12px 16px',
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              {worker.fraudRules.map((r, i) => (
                <div
                  key={i}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 9,
                    fontSize: 12.5,
                    color: '#76190B',
                  }}
                >
                  <i
                    className="fa fa-circle"
                    style={{ fontSize: 5, marginTop: 6 }}
                  ></i>
                  <div>
                    <strong>{r}</strong>
                    {r.startsWith('Duplicate') ||
                    r.startsWith('Shared') ||
                    r.startsWith('Matching') ? (
                      <span> — shared with worker {worker.dupWith}.</span>
                    ) : (
                      '.'
                    )}
                  </div>
                </div>
              ))}
              <div
                style={{
                  fontSize: 12,
                  color: '#76190B',
                  opacity: 0.85,
                  marginTop: 2,
                }}
              >
                Resolve these in Worker KYC (fraud investigation) before
                approving to prevent inappropriate payment.
              </div>
            </div>
          </div>
        )}
        {/* payment summary */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 12,
            marginBottom: 22,
          }}
        >
          {[
            ['Daily rate', D.money(worker.rate)],
            ['Days worked', worker.daysWorked],
            ['Days approved', approvedCount + ' / ' + worker.daysWorked],
            ['Approved amount', D.money(approvedAmt)],
          ].map(([l, v], i) => (
            <div
              key={i}
              style={{
                background: CUTC.surface,
                borderRadius: 10,
                padding: '13px 15px',
                border: '1px solid ' + CUTC.border,
              }}
            >
              <div
                style={{
                  fontSize: 10.5,
                  color: CUTC.muted,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '.06em',
                }}
              >
                {l}
              </div>
              <div
                style={{
                  fontSize: 18,
                  fontWeight: 600,
                  color: CUTC.purple,
                  marginTop: 4,
                  fontFamily: 'ui-monospace, monospace',
                }}
              >
                {v}
              </div>
            </div>
          ))}
        </div>

        {/* daily approval table */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 12,
          }}
        >
          <SectionTitle sub="Approve attendance at the daily level. Flagged days need review before approval.">
            Daily payment breakdown
          </SectionTitle>
          {canApprove && pendingCount > 0 && (
            <Button
              size="sm"
              variant="ghost"
              icon="check-double"
              onClick={approveAllPending}
            >
              Approve {pendingCount} pending
            </Button>
          )}
        </div>
        <Card padding={0}>
          <Table
            density="compact"
            columns={[
              { label: 'Date' },
              { label: 'Units logged', align: 'center' },
              { label: 'Amount', align: 'right' },
              { label: 'Flag' },
              { label: 'Status' },
              { label: 'Action', align: 'right' },
            ]}
          >
            {days.map((d, i) => (
              <Row key={i}>
                <Cell strong>{d.date}</Cell>
                <Cell align="center" mono>
                  {d.units}
                </Cell>
                <Cell align="right" mono>
                  {D.money(d.amount)}
                </Cell>
                <Cell>
                  {d.flag ? (
                    <Badge tone="danger">{d.flag}</Badge>
                  ) : (
                    <span style={{ color: CUTC.faint }}>—</span>
                  )}
                </Cell>
                <Cell>
                  <Badge
                    tone={
                      d.status === 'approved'
                        ? 'success'
                        : d.status === 'rejected'
                        ? 'danger'
                        : 'warning'
                    }
                    dot
                  >
                    {d.status[0].toUpperCase() + d.status.slice(1)}
                  </Badge>
                </Cell>
                <Cell align="right">
                  {canApprove ? (
                    d.status === 'pending' ? (
                      <div style={{ display: 'inline-flex', gap: 6 }}>
                        <button
                          onClick={() => setDay(i, 'approved')}
                          title="Approve"
                          style={iconBtn('#1E7B33', '#D9ECD4')}
                        >
                          <i className="fa fa-check"></i>
                        </button>
                        <button
                          onClick={() => setDay(i, 'rejected')}
                          title="Reject"
                          style={iconBtn('#B22312', '#FAD8D4')}
                        >
                          <i className="fa fa-times"></i>
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setDay(i, 'pending')}
                        style={{
                          fontFamily: 'inherit',
                          background: 'transparent',
                          border: 'none',
                          color: CUTC.muted,
                          fontSize: 12,
                          cursor: 'pointer',
                          textDecoration: 'underline',
                          textUnderlineOffset: 2,
                        }}
                      >
                        Undo
                      </button>
                    )
                  ) : (
                    <span style={{ color: CUTC.faint, fontSize: 12 }}>—</span>
                  )}
                </Cell>
              </Row>
            ))}
          </Table>
        </Card>
      </div>

      {/* footer actions */}
      <div
        style={{
          padding: '16px 26px',
          borderTop: '1px solid ' + CUTC.border,
          background: CUTC.surface,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 11.5, color: CUTC.muted }}>
            Approved total
          </div>
          <div
            style={{
              fontSize: 20,
              fontWeight: 600,
              color: CUTC.purple,
              fontFamily: 'ui-monospace, monospace',
            }}
          >
            {D.money(approvedAmt)}
          </div>
        </div>
        {canApprove ? (
          <>
            <Button
              variant="dangerSoft"
              icon="ban"
              onClick={() => onReject(worker.id)}
            >
              Reject payment
            </Button>
            <Button
              variant="success"
              icon="paper-plane"
              disabled={worker.fraudRules.length > 0}
              onClick={() => {
                onQueue(worker.id, approvedCount);
                onClose();
              }}
            >
              {worker.fraudRules.length > 0
                ? 'Resolve flags first'
                : 'Approve & queue for payment'}
            </Button>
          </>
        ) : (
          <Badge tone="neutral">Read-only access for {'this role'}</Badge>
        )}
      </div>
    </Drawer>
  );
}
function iconBtn(fg, bg) {
  return {
    width: 28,
    height: 28,
    borderRadius: 7,
    border: 'none',
    background: bg,
    color: fg,
    cursor: 'pointer',
    fontSize: 12,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
  };
}

Object.assign(window, { WorkersTab });
