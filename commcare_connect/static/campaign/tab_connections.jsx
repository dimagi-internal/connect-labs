// tab_connections.jsx — System Administration › Connection Settings
const { useState: useStateConn } = React;

// Connection purpose routes a workflow to the right external system (spec: KYC / payments / reporting / other).
const CONN_PURPOSE = {
  kyc: { label: 'KYC verification', tone: 'info' },
  payments: { label: 'Payments', tone: 'primary' },
  reporting: { label: 'Reporting', tone: 'neutral' },
  data: { label: 'Data source', tone: 'success' },
  other: { label: 'Other', tone: 'neutral' },
};

const SEED_CONNS = [
  {
    id: 'C1',
    name: 'CommCare HQ',
    purpose: 'data',
    type: 'Workers, activities, households',
    icon: 'commcare',
    endpoint: 'https://www.commcarehq.org/a/mr-campaign/api/v0.5',
    auth: 'API Key',
    status: 'connected',
    lastSync: '38 min ago',
    freq: 'Every 2 hours',
    records: '64,210',
  },
  {
    id: 'C2',
    name: 'KYC Verification Provider',
    purpose: 'kyc',
    type: 'Identity verification · NIN / BVN checks',
    icon: 'id-card',
    endpoint: 'https://api.verify-id.ng/v2/verify',
    auth: 'Bearer Token',
    status: 'connected',
    lastSync: '1 hr ago',
    freq: 'Real-time (webhook)',
    records: '58 today',
  },
  {
    id: 'C3',
    name: 'Payment Gateway',
    purpose: 'payments',
    type: 'Disbursement status · bank transfers',
    icon: 'building-columns',
    endpoint: 'https://api.paystack.co/transfer',
    auth: 'Bearer Token',
    status: 'error',
    lastSync: '4 hr ago',
    freq: 'Every 30 min',
    records: '—',
    error: '401 Unauthorized — credentials expired',
  },
  {
    id: 'C4',
    name: 'GIS / Coverage Service',
    purpose: 'reporting',
    type: 'Geographic coverage & mapping',
    icon: 'map-location-dot',
    endpoint: 'https://gis.partner.org/arcgis/rest',
    auth: 'API Key',
    status: 'disabled',
    lastSync: 'Never',
    freq: 'Daily',
    records: '—',
  },
];

const SYNC_HISTORY = [
  {
    at: 'Jun 3, 2026 · 09:18',
    conn: 'CommCare HQ',
    result: 'success',
    rows: '1,204 records',
    dur: '42s',
  },
  {
    at: 'Jun 3, 2026 · 08:55',
    conn: 'KYC Verification Provider',
    result: 'success',
    rows: '58 records',
    dur: '6s',
  },
  {
    at: 'Jun 3, 2026 · 07:18',
    conn: 'CommCare HQ',
    result: 'success',
    rows: '980 records',
    dur: '38s',
  },
  {
    at: 'Jun 3, 2026 · 05:30',
    conn: 'Payment Gateway',
    result: 'error',
    rows: '401 Unauthorized',
    dur: '2s',
  },
  {
    at: 'Jun 3, 2026 · 05:18',
    conn: 'CommCare HQ',
    result: 'success',
    rows: '1,431 records',
    dur: '51s',
  },
  {
    at: 'Jun 3, 2026 · 03:18',
    conn: 'CommCare HQ',
    result: 'success',
    rows: '720 records',
    dur: '29s',
  },
];

function ConnectionSettings({ density }) {
  const toast = useToast();
  const [conns, setConns] = useStateConn(SEED_CONNS.map((c) => ({ ...c })));
  const [view, setView] = useStateConn('connections');
  const [config, setConfig] = useStateConn(null);
  const [testing, setTesting] = useStateConn(null);

  const statusMeta = {
    connected: { tone: 'success', label: 'Connected', icon: 'circle-check' },
    error: { tone: 'danger', label: 'Error', icon: 'circle-exclamation' },
    disabled: { tone: 'neutral', label: 'Disabled', icon: 'circle-minus' },
  };
  const test = (c) => {
    setTesting(c.id);
    setTimeout(() => {
      setTesting(null);
      if (c.status === 'error')
        toast('Connection test failed — ' + c.error, 'danger');
      else {
        setConns((cs) =>
          cs.map((x) =>
            x.id === c.id
              ? { ...x, status: 'connected', lastSync: 'Just now' }
              : x,
          ),
        );
        toast(c.name + ' connection OK');
      }
    }, 1200);
  };
  const toggle = (c) => {
    setConns((cs) =>
      cs.map((x) =>
        x.id === c.id
          ? { ...x, status: x.status === 'disabled' ? 'connected' : 'disabled' }
          : x,
      ),
    );
    toast(
      c.status === 'disabled' ? c.name + ' activated' : c.name + ' deactivated',
    );
  };

  return (
    <Page max={1280}>
      <PageHead
        eyebrow="System Administration"
        title="Connection Settings"
        sub="Configure and monitor connections to the external systems the Campaign Utility Tool depends on. The tool reads data via these APIs; it never writes to source systems outside approved workflows."
        actions={
          <Button
            icon="plus"
            onClick={() =>
              setConfig({
                id: 'new',
                name: '',
                purpose: 'kyc',
                auth: 'API Key',
                freq: 'Every 2 hours',
                endpoint: '',
                status: 'disabled',
              })
            }
          >
            Add connection
          </Button>
        }
      />

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 14,
          marginBottom: 18,
        }}
      >
        <Stat
          label="Connections"
          value={conns.length}
          icon="plug"
          sub={conns.filter((c) => c.status === 'connected').length + ' active'}
        />
        <Stat
          label="Healthy"
          value={conns.filter((c) => c.status === 'connected').length}
          icon="circle-check"
          delta="Operational"
          deltaTone="success"
        />
        <Stat
          label="Errors"
          value={conns.filter((c) => c.status === 'error').length}
          icon="circle-exclamation"
          delta="Action needed"
          deltaTone="danger"
        />
        <Stat
          label="Data freshness"
          value="2 hrs"
          icon="rotate"
          sub="max sync interval"
        />
      </div>

      <div style={{ marginBottom: 16 }}>
        <PillTabs
          active={view}
          onChange={setView}
          tabs={[
            { id: 'connections', label: 'Connections', icon: 'plug' },
            {
              id: 'history',
              label: 'Sync history & errors',
              icon: 'clock-rotate-left',
            },
          ]}
        />
      </div>

      {view === 'connections' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {conns.map((c) => {
            const s = statusMeta[c.status];
            return (
              <Card key={c.id} padding={0}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 16,
                    padding: '18px 22px',
                    flexWrap: 'wrap',
                  }}
                >
                  <div
                    style={{
                      width: 46,
                      height: 46,
                      borderRadius: 11,
                      background: CUTC.surface,
                      border: '1px solid ' + CUTC.border,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0,
                    }}
                  >
                    {c.icon === 'commcare' ? (
                      <img
                        src={
                          (window.__resources &&
                            window.__resources.commcarePinwheel) ||
                          'assets/commcare-pinwheel.png'
                        }
                        style={{ height: 24 }}
                      />
                    ) : (
                      <i
                        className={`fa fa-${c.icon}`}
                        style={{ fontSize: 19, color: 'var(--accent)' }}
                      ></i>
                    )}
                  </div>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        flexWrap: 'wrap',
                      }}
                    >
                      <span
                        style={{
                          fontSize: 15.5,
                          fontWeight: 600,
                          color: CUTC.purple,
                        }}
                      >
                        {c.name}
                      </span>
                      <Badge tone={s.tone} dot>
                        {s.label}
                      </Badge>
                      {c.purpose && (
                        <Badge
                          tone={
                            (CONN_PURPOSE[c.purpose] || {}).tone || 'neutral'
                          }
                        >
                          {(CONN_PURPOSE[c.purpose] || {}).label || c.purpose}
                        </Badge>
                      )}
                    </div>
                    <div
                      style={{
                        fontSize: 12.5,
                        color: CUTC.muted,
                        marginTop: 2,
                      }}
                    >
                      {c.type}
                    </div>
                    <div
                      style={{
                        fontSize: 11.5,
                        color: CUTC.faint,
                        marginTop: 4,
                        fontFamily: 'ui-monospace, monospace',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {c.endpoint}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 30, flexShrink: 0 }}>
                    {[
                      ['Auth', c.auth],
                      ['Sync', c.freq],
                      ['Last sync', c.lastSync],
                    ].map(([l, v]) => (
                      <div key={l} style={{ minWidth: 90 }}>
                        <div
                          style={{
                            fontSize: 10.5,
                            color: CUTC.faint,
                            fontWeight: 600,
                            textTransform: 'uppercase',
                            letterSpacing: '.05em',
                          }}
                        >
                          {l}
                        </div>
                        <div
                          style={{
                            fontSize: 13,
                            color: CUTC.purple,
                            fontWeight: 500,
                            marginTop: 3,
                          }}
                        >
                          {v}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div
                    style={{
                      display: 'flex',
                      gap: 8,
                      flexShrink: 0,
                      alignItems: 'center',
                    }}
                  >
                    <Button
                      size="sm"
                      variant="secondary"
                      icon={testing === c.id ? 'spinner' : 'bolt'}
                      onClick={() => test(c)}
                      disabled={testing === c.id}
                    >
                      {testing === c.id ? 'Testing…' : 'Test'}
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      icon="gear"
                      onClick={() => setConfig(c)}
                    >
                      Configure
                    </Button>
                    <Button
                      size="sm"
                      variant={c.status === 'disabled' ? 'success' : 'ghost'}
                      icon={c.status === 'disabled' ? 'play' : 'power-off'}
                      onClick={() => toggle(c)}
                    >
                      {c.status === 'disabled' ? 'Activate' : 'Deactivate'}
                    </Button>
                  </div>
                </div>
                {c.status === 'error' && (
                  <div
                    style={{
                      padding: '11px 22px',
                      background: '#FAD8D4',
                      borderTop: '1px solid #F5C2BB',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 10,
                      fontSize: 12.5,
                      color: '#76190B',
                    }}
                  >
                    <i className="fa fa-triangle-exclamation"></i>
                    <strong>Sync error:</strong> {c.error}
                    <button
                      onClick={() => setConfig(c)}
                      style={{
                        marginLeft: 'auto',
                        background: 'transparent',
                        border: 'none',
                        color: '#76190B',
                        fontWeight: 700,
                        cursor: 'pointer',
                        fontFamily: 'inherit',
                        fontSize: 12.5,
                        textDecoration: 'underline',
                      }}
                    >
                      Update credentials
                    </button>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {view === 'history' && (
        <Card padding={0}>
          <div
            style={{
              padding: '16px 22px',
              borderBottom: '1px solid ' + CUTC.border,
            }}
          >
            <SectionTitle sub="Synchronization runs and connection errors across all integrations">
              Sync history & error logs
            </SectionTitle>
          </div>
          <Table
            density={density}
            columns={[
              { label: 'Timestamp' },
              { label: 'Connection' },
              { label: 'Result' },
              { label: 'Detail' },
              { label: 'Duration', align: 'right' },
            ]}
          >
            {SYNC_HISTORY.map((h, i) => (
              <Row key={i}>
                <Cell mono style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                  {h.at}
                </Cell>
                <Cell strong>{h.conn}</Cell>
                <Cell>
                  <Badge
                    tone={h.result === 'success' ? 'success' : 'danger'}
                    dot
                  >
                    {h.result === 'success' ? 'Success' : 'Error'}
                  </Badge>
                </Cell>
                <Cell
                  style={{
                    color: h.result === 'error' ? '#B22312' : CUTC.body,
                  }}
                >
                  {h.rows}
                </Cell>
                <Cell align="right" mono>
                  {h.dur}
                </Cell>
              </Row>
            ))}
          </Table>
        </Card>
      )}

      <ConnConfigModal
        conn={config}
        onClose={() => setConfig(null)}
        onSave={(c) => {
          setConns((cs) => {
            const ex = cs.find((x) => x.id === c.id);
            if (ex) return cs.map((x) => (x.id === c.id ? { ...x, ...c } : x));
            return [
              ...cs,
              {
                ...c,
                id: 'C' + (cs.length + 1),
                lastSync: 'Never',
                records: '—',
                type:
                  c.type ||
                  (CONN_PURPOSE[c.purpose] || {}).label ||
                  'External system',
                icon: 'plug',
              },
            ];
          });
          toast('Connection saved');
          setConfig(null);
        }}
      />
    </Page>
  );
}

function ConnConfigModal({ conn, onClose, onSave }) {
  const [f, setF] = useStateConn({});
  React.useEffect(() => {
    if (conn) setF({ ...conn });
  }, [conn && conn.id]);
  if (!conn) return null;
  const isNew = conn.id === 'new';
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }));
  const valid =
    (f.name || '').trim().length > 1 && (f.endpoint || '').trim().length > 4;
  return (
    <Modal
      open={!!conn}
      onClose={onClose}
      width={580}
      title={isNew ? 'Add connection' : 'Configure ' + conn.name}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon="check"
            disabled={!valid}
            onClick={() => onSave(f)}
          >
            {isNew ? 'Create & test' : 'Save changes'}
          </Button>
        </>
      }
    >
      <Field label="System name">
        <TextInput
          value={f.name || ''}
          onChange={(e) => set('name', e.target.value)}
          placeholder="e.g. CommCare HQ"
        />
      </Field>
      <Field
        label="Connection purpose"
        help="Routes the matching workflow (KYC checks, payments, reporting) to this system."
      >
        <Select
          value={f.purpose || 'kyc'}
          onChange={(e) => set('purpose', e.target.value)}
        >
          <option value="kyc">KYC verification</option>
          <option value="payments">Payments</option>
          <option value="reporting">Reporting</option>
          <option value="data">Data source</option>
          <option value="other">Other</option>
        </Select>
      </Field>
      <Field label="Endpoint URL">
        <TextInput
          value={f.endpoint || ''}
          onChange={(e) => set('endpoint', e.target.value)}
          placeholder="https://api.example.com/v1"
          style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12.5 }}
        />
      </Field>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <Field label="Authentication method">
          <Select
            value={f.auth || 'API Key'}
            onChange={(e) => set('auth', e.target.value)}
          >
            <option>API Key</option>
            <option>Bearer Token</option>
            <option>Basic Authentication</option>
          </Select>
        </Field>
        <Field label="Sync frequency">
          <Select
            value={f.freq || 'Every 2 hours'}
            onChange={(e) => set('freq', e.target.value)}
          >
            <option>Real-time (webhook)</option>
            <option>Every 30 min</option>
            <option>Every 2 hours</option>
            <option>Daily</option>
          </Select>
        </Field>
      </div>
      <Field
        label={
          f.auth === 'Basic Authentication'
            ? 'Username'
            : f.auth === 'Bearer Token'
            ? 'Bearer token'
            : 'API key'
        }
      >
        <TextInput
          type="password"
          defaultValue="••••••••••••••••"
          style={{ fontFamily: 'ui-monospace, monospace' }}
        />
      </Field>
      {f.auth === 'Basic Authentication' && (
        <Field label="Password">
          <TextInput
            type="password"
            defaultValue="••••••••"
            style={{ fontFamily: 'ui-monospace, monospace' }}
          />
        </Field>
      )}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 14px',
          background: CUTC.surface,
          borderRadius: 10,
          border: '1px solid ' + CUTC.border,
        }}
      >
        <div style={{ fontSize: 12.5, color: CUTC.body }}>
          <i
            className="fa fa-shield-halved"
            style={{ color: 'var(--accent)', marginRight: 8 }}
          ></i>
          Credentials are encrypted at rest. Changes are recorded in the audit
          log.
        </div>
      </div>
    </Modal>
  );
}

Object.assign(window, { ConnectionSettings });
