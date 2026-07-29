/* Integration: mint API tokens and show what to post.
   The forms under Operations do all of this by hand — this tab is for
   suppliers whose own systems can send it automatically. */

function IntegrationTab({ ctx }) {
  const { world, act } = ctx;
  const [label, setLabel] = useState('');
  const [secret, setSecret] = useState(null);
  const tokens = world.api_tokens || [];

  const mint = async () => {
    const result = await act(
      () => supplyPost('/supply/api/tokens/', { label }),
      'Token created — copy it now.',
    );
    if (result) {
      setSecret(result.secret);
      setLabel('');
    }
  };

  return (
    <Page
      title="Integration"
      lede="Send shipment data straight from your own systems. Everything here can also be entered by hand under Operations."
    >
      <Card
        title="API tokens"
        subtitle="Tokens are shown once, at creation. Store yours securely."
        actions={
          <div className="filter-row">
            <input
              type="text"
              placeholder="Token label, e.g. Kano plant feed"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
            <button
              type="button"
              className="btn btn-sm"
              onClick={mint}
              disabled={ctx.busy || !label}
            >
              Create token
            </button>
          </div>
        }
      >
        {secret ? (
          <div className="notice secret-notice">
            <strong>Copy this token now — it will not be shown again:</strong>
            <code className="secret">{secret}</code>
          </div>
        ) : null}
        <DataTable
          rows={tokens}
          rowKey={(t) => t.id}
          empty="No tokens yet."
          emptyHint="Name a token above and create one — it is shown once, at creation. Everything on this page can also be entered by hand under Operations."
          columns={[
            { key: 'label', label: 'Label', value: (t) => t.label },
            { key: 'prefix', label: 'Token', value: (t) => `${t.prefix}…` },
            {
              key: 'created',
              label: 'Created',
              value: (t) => t.created_at,
              render: (t) => formatDate(t.created_at),
            },
            {
              key: 'used',
              label: 'Last used',
              value: (t) => t.last_used_at,
              render: (t) =>
                t.last_used_at ? (
                  formatDate(t.last_used_at)
                ) : (
                  <span className="muted">never</span>
                ),
            },
            {
              key: 'state',
              label: 'Status',
              value: (t) => (t.revoked ? 1 : 0),
              render: (t) =>
                t.revoked ? (
                  <Badge tone="bad">Revoked</Badge>
                ) : (
                  <Badge tone="good">Active</Badge>
                ),
            },
            {
              key: 'act',
              label: '',
              sortable: false,
              value: () => '',
              render: (t) =>
                t.revoked ? null : (
                  <button
                    type="button"
                    className="btn-link danger"
                    onClick={() =>
                      act(
                        () =>
                          supplyPost(`/supply/api/tokens/${t.id}/revoke/`, {}),
                        'Token revoked.',
                      )
                    }
                  >
                    Revoke
                  </button>
                ),
            },
          ]}
        />
      </Card>

      <Card
        title="What to send"
        subtitle="Three ways in, matching what your systems can already produce."
      >
        <div className="endpoint">
          <h4>1. GS1 EPCIS 2.0 events</h4>
          <p className="muted">
            If you run a traceability system, post standard EPCIS documents.
            Object, aggregation and transformation events are all accepted, with
            GS1 Digital Link or <code>urn:epc:</code> identifiers.
          </p>
          <code className="endpoint-url">
            POST /supply/api/v1/epcis/capture/
          </code>
        </div>

        <div className="endpoint">
          <h4>2. Despatch advice</h4>
          <p className="muted">
            The shipment → packages → items tree from an X12 856 or EDIFACT
            DESADV, as JSON. Any EDI translation service emits this shape.
          </p>
          <code className="endpoint-url">POST /supply/api/v1/shipments/</code>
        </div>

        <div className="endpoint">
          <h4>3. Check-ins</h4>
          <p className="muted">
            For corridors without system coverage: a consignment reference, a
            place and what happened. A driver's phone is enough.
          </p>
          <code className="endpoint-url">POST /supply/api/v1/checkins/</code>
        </div>

        <div className="endpoint">
          <h4>Reading back what we hold</h4>
          <p className="muted">
            Returns exactly the events we recorded for a shipment, so you can
            reconcile against your own records.
          </p>
          <code className="endpoint-url">
            GET /supply/api/v1/shipments/&lt;id&gt;/events/
          </code>
        </div>

        <p className="muted small">
          Authenticate with <code>Authorization: Bearer &lt;token&gt;</code>.
          Re-sending an event with the same identifier is safe — it is recorded
          once.
        </p>
      </Card>
    </Page>
  );
}
