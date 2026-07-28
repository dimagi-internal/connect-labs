/* Operations command centre — the hero screen.

   Exception-first, following how real control towers are actually laid out
   (project44 / FourKites): the home surface is a prioritised worklist of
   at-risk consignments, and the map is context beside it, not the product.
   Each exception row answers three things in order: what is wrong, why, and
   what to do about it. */

/* Severity is NOT computed here.

   It used to be: tonnage x lateness for a late consignment, raw shortfall x 2
   for a short receipt. Those are not comparable, so the ordering of the queue
   was arbitrary at exactly the point it mattered most — and no test in this
   repo could reach a function in this file.

   The queue now arrives ranked from services/exceptions.py, where every row
   carries the same unit (children who miss a full course) and the derivation
   that produced it. The partner surface reads the same numbers from the same
   place, so the two cannot disagree about a node. */

function CommandTab({ ctx }) {
  const { world, act } = ctx;
  const contracts = world.contracts || [];
  const nodes = world.nodes || [];
  const shipments = contracts.flatMap((c) => c.shipments || []);
  const exceptions = world.exceptions || [];
  const cover = world.cover || [];
  const coverage = world.coverage || [];
  const [selected, setSelected] = useState(null);
  const [openContract, setOpenContract] = useState(null);
  const [reallocatingFor, setReallocatingFor] = useState(null);

  const inTransit = shipments.filter((s) => s.status === 'in_transit');
  const deliveredCartons = contracts.reduce(
    (n, c) => n + c.delivered_quantity,
    0,
  );
  const tonnesInFlight = inTransit.reduce(
    (n, s) => n + (s.metric_tonnes || 0),
    0,
  );

  return (
    <Page
      title="Operations command centre"
      lede="Consignments at risk first; the network map for context."
    >
      <KeyFigures
        figures={[
          {
            label: 'Children at risk',
            value: formatNumber(
              exceptions.reduce((n, e) => n + (e.children_at_risk || 0), 0),
            ),
            hint: exceptions.length
              ? `across ${exceptions.length} exception${
                  exceptions.length === 1 ? '' : 's'
                }`
              : 'nothing outstanding',
          },
          {
            label: 'In transit',
            value: inTransit.length,
            hint: `${Math.round(tonnesInFlight)} MT moving`,
          },
          {
            label: 'Delivered to date',
            value: formatNumber(deliveredCartons),
            hint: `${Math.round(
              (deliveredCartons * 150 * 92) / 1000000,
            )} MT · ${formatNumber(deliveredCartons)} children treated`,
          },
          { label: 'Active contracts', value: contracts.length },
        ]}
      />

      <div className="command-split">
        <Card
          title="Exceptions"
          subtitle="Ranked by the children behind each one, not by tonnage."
          className="exception-panel"
        >
          {exceptions.length ? (
            <div className="exception-list">
              {exceptions.map((e) => (
                <button
                  type="button"
                  key={e.key}
                  className={`exception ${
                    selected === e.key ? 'selected' : ''
                  }`}
                  onClick={() => setSelected(e.key)}
                >
                  <div className="exception-head">
                    <Badge tone={e.tone}>{e.kind}</Badge>
                    {e.origin === 'partner' ? (
                      <Badge tone="info">
                        Raised by {e.org_name || 'a partner'}
                      </Badge>
                    ) : null}
                    <span className="exception-what">{e.what}</span>
                  </div>
                  {e.children_at_risk ? (
                    <div className="exception-risk">
                      <strong>{formatNumber(e.children_at_risk)}</strong>{' '}
                      children lose a full course
                      {e.by_date ? ` by ${formatDate(e.by_date)}` : ''}
                    </div>
                  ) : null}
                  <div className="exception-why">{e.why}</div>
                  {selected === e.key && e.derivation ? (
                    <div className="exception-derivation">
                      How this was ranked: {e.derivation}
                    </div>
                  ) : null}
                  <div className="exception-action">→ {e.action}</div>
                  {/* The queue has always ADVISED a reallocation and never
                      offered one, so the single sentence that tells the reader
                      what to do about a row was the only thing on the card
                      they could not act on. */}
                  {e.node_id &&
                  /reallocate/i.test(e.action || '') &&
                  supplyCan(world.role, 'actions', 'create') ? (
                    <span
                      className="btn btn-sm"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setReallocatingFor(e);
                      }}
                    >
                      Reallocate to {e.node_name}
                    </span>
                  ) : null}
                  {e.discrepancy_id &&
                  supplyCan(world.role, 'execution', 'resolve') ? (
                    <span
                      className="btn btn-sm"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        act(
                          () =>
                            supplyPost(
                              `/supply/api/discrepancies/${e.discrepancy_id}/resolve/`,
                              { note: 'Reconciled from the command centre.' },
                            ),
                          'Discrepancy resolved.',
                        );
                      }}
                    >
                      Resolve
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No consignments at risk."
              hint="Every shipment is tracking to plan."
            />
          )}
        </Card>

        <Card
          title="Network"
          subtitle="Flows follow real road and sea corridors."
          className="map-panel"
        >
          <FlowMap nodes={nodes} shipments={shipments} height={560} />
        </Card>
      </div>

      <Card
        title="Pipeline by corridor"
        subtitle="Contracted quantity against what is confirmed and what has landed. The requirement from caseload sits below."
      >
        <DataTable
          rows={contracts}
          rowKey={(c) => c.id}
          onRowClick={(c) => setOpenContract(c)}
          columns={[
            { key: 'ref', label: 'Contract', value: (c) => c.reference },
            { key: 'org', label: 'Supplier', value: (c) => c.org_name },
            {
              key: 'dest',
              label: 'Destination',
              value: (c) => c.destination,
              render: (c) =>
                `${c.destination}, ${countryLabel(c.destination_country)}`,
            },
            {
              key: 'req',
              label: 'Requirement',
              value: (c) => c.total_quantity,
              render: (c) => `${formatNumber(c.total_quantity)} ${c.unit}`,
            },
            {
              key: 'shipped',
              label: 'Shipped',
              value: (c) => c.shipped_quantity,
              render: (c) => formatNumber(c.shipped_quantity),
            },
            {
              key: 'delivered',
              label: 'Delivered',
              value: (c) => c.delivered_quantity,
              render: (c) => formatNumber(c.delivered_quantity),
            },
            {
              key: 'gap',
              label: 'Gap',
              value: (c) => c.total_quantity - c.delivered_quantity,
              render: (c) => {
                const gap = c.total_quantity - c.delivered_quantity;
                return gap > 0 ? (
                  <Badge tone="warn">{formatNumber(gap)}</Badge>
                ) : (
                  <Badge tone="good">met</Badge>
                );
              },
            },
          ]}
        />
      </Card>

      <Card
        title="Requirement from caseload"
        subtitle="What the districts need, against what has actually landed in them. A contract quantity is what was bought; this is what is required."
      >
        {coverage.length ? (
          <DataTable
            rows={coverage}
            rowKey={(r) => r.adm1_code}
            columns={[
              {
                key: 'district',
                label: 'District',
                value: (r) => r.adm1_name,
                render: (r) => (
                  <span title={r.source_note}>
                    {r.adm1_name}{' '}
                    <Badge
                      tone={
                        r.ipc_phase >= 5
                          ? 'bad'
                          : r.ipc_phase >= 4
                          ? 'warn'
                          : 'muted'
                      }
                    >
                      IPC {r.ipc_phase}
                    </Badge>
                  </span>
                ),
              },
              {
                key: 'caseload',
                label: 'Requirement (children)',
                value: (r) => r.caseload,
                render: (r) => formatNumber(r.caseload),
              },
              {
                key: 'delivered',
                label: 'Courses delivered',
                value: (r) => r.courses_delivered,
                render: (r) => formatNumber(r.courses_delivered),
              },
              {
                key: 'coverage',
                label: 'Coverage',
                value: (r) => r.coverage_percent || 0,
                render: (r) =>
                  r.coverage_percent === null ? (
                    <Badge tone="muted">no data</Badge>
                  ) : (
                    <Badge
                      tone={
                        r.coverage_percent >= 80
                          ? 'good'
                          : r.coverage_percent >= 50
                          ? 'warn'
                          : 'bad'
                      }
                    >
                      {r.coverage_percent}%
                    </Badge>
                  ),
              },
              {
                key: 'gap',
                label: 'Gap to need (children)',
                value: (r) => r.uncovered_children,
                render: (r) => formatNumber(r.uncovered_children),
              },
            ]}
          />
        ) : (
          <EmptyState
            title="No caseload estimates loaded."
            hint="Coverage cannot be reported without a denominator."
          />
        )}
        <p className="muted small method-note">
          Coverage is courses delivered divided by the district's monthly SAM
          caseload. Hover a district for how its caseload was estimated. All
          figures in this environment are synthetic.
        </p>
      </Card>

      <Card
        title="Weeks of cover"
        subtitle="Stock on hand against the rate each site is admitting children — the date the store runs dry."
      >
        {cover.length ? (
          <DataTable
            rows={cover.slice(0, 12)}
            rowKey={(r) => r.node_id}
            columns={[
              { key: 'node', label: 'Node', value: (r) => r.node_name },
              {
                key: 'stock',
                label: 'On hand (cartons)',
                value: (r) => r.stock_on_hand,
                render: (r) => formatNumber(r.stock_on_hand),
              },
              {
                key: 'burn',
                label: 'Weekly burn',
                value: (r) => r.weekly_burn,
                render: (r) => formatNumber(Math.round(r.weekly_burn)),
              },
              {
                key: 'weeks',
                label: 'Weeks of cover',
                value: (r) => r.weeks_of_cover,
                // A site awaiting its first delivery has no cover figure to
                // colour. Showing it as a red 0 puts it in the same visual
                // language as a site two days from running dry, and the two
                // need opposite responses.
                render: (r) =>
                  r.awaiting_first_delivery ? (
                    <Badge tone="info">Not yet served</Badge>
                  ) : (
                    <Badge
                      tone={
                        r.weeks_of_cover < 2
                          ? 'bad'
                          : r.weeks_of_cover < 4
                          ? 'warn'
                          : 'good'
                      }
                    >
                      {r.weeks_of_cover}
                    </Badge>
                  ),
              },
              {
                key: 'dry',
                label: 'Runs dry',
                value: (r) => r.stockout_on || '',
                render: (r) =>
                  r.stockout_on ? (
                    formatDate(r.stockout_on)
                  ) : (
                    <span className="muted">awaiting first consignment</span>
                  ),
              },
            ]}
          />
        ) : (
          <EmptyState
            title="No cover figures yet."
            hint="No node carries a caseload."
          />
        )}
        <p className="muted small method-note">
          {cover.length ? cover[0].method : ''}
        </p>
      </Card>

      {reallocatingFor ? (
        <ReallocateModal
          ctx={ctx}
          exception={reallocatingFor}
          surplus={world.surplus_nodes || []}
          onClose={() => setReallocatingFor(null)}
        />
      ) : null}

      {openContract ? (
        <ContractDetailModal
          contract={openContract}
          onClose={() => setOpenContract(null)}
        />
      ) : null}
    </Page>
  );
}

/* What the award became.

   The award is the immutable decision; this is the instrument that carries it
   out. Until this existed the pipeline table was the end of the road — four
   reference strings in a column — and the claim that a dollar can be traced to
   a carton had nowhere on screen to be true. The three things that make the
   trace possible are the three things this shows: the funding envelope the
   money is drawn from, the IATI activity identifier that makes it reconcilable
   against a published aid dataset, and the consignments the quantity is
   actually moving on. */
function ContractDetailModal({ contract, onClose }) {
  const appropriation = contract.appropriation;
  const shipments = contract.shipments || [];
  const gap = contract.total_quantity - contract.delivered_quantity;

  return (
    <Modal wide title={contract.reference} onClose={onClose}>
      <div className="detail-head">
        <StatusChip status={contract.status} />
        <span className="muted">
          {contract.org_name} · {contract.destination},{' '}
          {countryLabel(contract.destination_country)}
        </span>
      </div>
      <p className="modal-lede">
        {contract.lot_description}
        {contract.source_solicitation ? (
          <span className="muted">
            {' '}
            · awarded under {contract.source_solicitation}
            {contract.awarded_at ? `, ${formatDate(contract.awarded_at)}` : ''}
          </span>
        ) : null}
      </p>

      <Card
        title="Drawn against"
        subtitle="The appropriation this contract obligates money from."
      >
        {appropriation ? (
          <div className="kv-grid">
            <div>
              <span className="muted small">Funder</span>
              <div>{appropriation.funder_name}</div>
            </div>
            <div>
              <span className="muted small">Appropriation</span>
              <div>
                {appropriation.title} · {appropriation.fiscal_year}
              </div>
            </div>
            <div>
              <span className="muted small">IATI activity</span>
              <div>
                {contract.iati_activity_id ||
                  appropriation.iati_activity_id ||
                  '—'}
              </div>
            </div>
            <div>
              <span className="muted small">Obligated</span>
              <div>
                {formatMoney(contract.obligated_value, contract.currency)}
              </div>
            </div>
            <div>
              <span className="muted small">Disbursed</span>
              <div>
                {formatMoney(contract.disbursed_value, contract.currency)}
                <span className="muted small">
                  {' '}
                  · against confirmed delivery only
                </span>
              </div>
            </div>
            <div>
              <span className="muted small">Unit price</span>
              <div>{formatMoney(contract.unit_price, contract.currency)}</div>
            </div>
          </div>
        ) : (
          <EmptyState title="No appropriation linked." />
        )}
      </Card>

      <Card
        title="Delivery schedule"
        subtitle="The consignments this contract is moving on, and where each one has reached."
      >
        <DataTable
          rows={shipments}
          rowKey={(s) => s.id}
          empty="No consignments raised against this contract yet."
          columns={[
            { key: 'ref', label: 'Consignment', value: (s) => s.reference },
            {
              key: 'route',
              label: 'Route',
              sortable: false,
              value: () => '',
              render: (s) => `${s.origin.name} → ${s.destination.name}`,
            },
            {
              key: 'qty',
              label: 'Quantity',
              value: (s) => s.quantity,
              render: (s) => `${formatNumber(s.quantity)} ${s.unit}`,
            },
            {
              key: 'eta',
              label: 'Due',
              value: (s) => s.eta,
              render: (s) => formatDate(s.eta),
            },
            {
              key: 'status',
              label: 'Status',
              value: (s) => s.status,
              render: (s) => <StatusChip status={s.status} />,
            },
          ]}
        />
        <p className="muted small method-note">
          {formatNumber(contract.total_quantity)} {contract.unit} contracted ·{' '}
          {formatNumber(contract.delivered_quantity)} confirmed at destination ·{' '}
          {gap > 0 ? `${formatNumber(gap)} outstanding` : 'requirement met'}.
          Status and quantities are derived from the event log, not entered.
        </p>
      </Card>
    </Modal>
  );
}

/* Moving surplus is a decision, so it records one.

   `services/actions.reallocate` and `POST api/actions/reallocate/` have existed
   since the demand stage landed; what did not exist was any way to reach them.
   The exception queue advised "reallocate from a node holding surplus" on every
   late consignment and every partner shortfall, and that advice was the one
   thing on the card a reader could not act on.

   The source list is not a node picker over the whole network. It is the nodes
   that genuinely hold more than their own caseload can consume, each showing
   what it could spare without dropping below its own threshold — because a
   reallocation that solves one stockout by causing another is not a decision
   anybody would defend afterwards. */
function ReallocateModal({ ctx, exception, surplus, onClose }) {
  const candidates = surplus.filter((n) => n.node_id !== exception.node_id);
  const [sourceId, setSourceId] = useState(
    candidates.length ? String(candidates[0].node_id) : '',
  );
  const suggested = Math.max(exception.children_at_risk || 0, 0);
  const [quantity, setQuantity] = useState(String(suggested || 100));
  const [rationale, setRationale] = useState('');

  const source = candidates.find((n) => String(n.node_id) === sourceId);
  const overdrawn = source && Number(quantity) > source.spare_cartons;

  const submit = async () => {
    const ok = await ctx.act(
      () =>
        supplyPost('/supply/api/actions/reallocate/', {
          source_node_id: Number(sourceId),
          target_node_id: exception.node_id,
          quantity: Number(quantity),
          rationale,
          signal_id: exception.signal_id || null,
        }),
      'Reallocated — a consignment is on the map with planned milestones.',
    );
    if (ok) onClose();
  };

  return (
    <Modal
      title={`Reallocate to ${exception.node_name}`}
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
            disabled={
              ctx.busy ||
              !sourceId ||
              !rationale.trim() ||
              overdrawn ||
              Number(quantity) <= 0
            }
          >
            Reallocate
          </button>
        </React.Fragment>
      }
    >
      <p className="modal-lede">{exception.why}</p>
      {candidates.length ? (
        <React.Fragment>
          <FormRow
            label="Move from"
            hint="Only nodes holding more than their own caseload can consume."
          >
            <select
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value)}
            >
              {candidates.map((n) => (
                <option key={n.node_id} value={n.node_id}>
                  {n.node_name} — {formatNumber(n.spare_cartons)} cartons spare
                  ({n.weeks_of_cover} wk cover)
                </option>
              ))}
            </select>
          </FormRow>
          <FormRow
            label="Cartons"
            hint={
              source
                ? `${formatNumber(
                    source.spare_cartons,
                  )} can move without taking ${
                    source.node_name
                  } below six weeks.`
                : ''
            }
          >
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
            />
          </FormRow>
          {overdrawn ? (
            <div className="form-error">
              That would take {source.node_name} below its own threshold.
            </div>
          ) : null}
          <FormRow
            label="Why"
            hint="Recorded against the action, and against the signal it answers."
          >
            <textarea
              rows="3"
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
            />
          </FormRow>
        </React.Fragment>
      ) : (
        <EmptyState
          title="No node is holding surplus."
          hint="Nothing can be moved without causing a stockout somewhere else."
        />
      )}
    </Modal>
  );
}
