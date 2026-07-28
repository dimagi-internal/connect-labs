/* The implementing partner's surface — Komadugu Health Initiative.

   Everything else in this app looks outward from the centre. This is the one
   surface that looks the other way, and it is deliberately not a recoloured
   copy of the command centre:

   - The unit of planning is a DISTRIBUTION DAY at a named site with a known
     number of children expected, not a shipment sorted by arrival date. A
     shipment table is the supplier's view of the world.
   - Weeks of cover and the stockout date come from the server, from the same
     services/cover.py the command centre reads. Two implementations would
     drift, and a partner told they have eleven days while the centre reads
     three weeks is worse than neither having the figure.
   - A shortfall is raised from HERE. That inverts the direction a monitoring
     product runs in: the ground reports upward into a system that can respond.
*/

function PartnerTab({ ctx }) {
  const { world, act } = ctx;
  const plans = world.distribution_plans || [];
  const cover = world.cover || [];
  const sites = world.sites || [];
  const signals = world.shortfall_signals || [];
  const records = world.distribution_records || [];
  const [raising, setRaising] = useState(null);
  const [batch, setBatch] = useState(null);

  const worst = cover.length ? cover[0] : null;
  const openSignals = signals.filter((s) => s.status !== 'resolved');
  const atRisk = plans.filter((p) => p.state !== 'covered');

  return (
    <Page
      title={world.org ? world.org.legal_name : 'Partner'}
      lede="Inbound supply against the distributions you have planned — and how long each site's stock lasts."
    >
      <KeyFigures
        figures={[
          { label: 'Feeding sites', value: sites.length },
          {
            label: 'Distributions not covered',
            value: atRisk.length,
            hint: atRisk.length
              ? 'inbound supply falls short of what is booked in'
              : 'every planned day is covered',
          },
          {
            label: 'Thinnest cover',
            value: worst ? `${worst.weeks_of_cover} wk` : '—',
            hint: worst
              ? `${worst.node_name} runs dry ${formatDate(worst.stockout_on)}`
              : '',
          },
          {
            label: 'Shortfalls raised',
            value: openSignals.length,
            hint: openSignals.length
              ? 'awaiting an answer from OES'
              : 'none open',
          },
        ]}
      />

      <Card
        title="Distribution calendar"
        subtitle="The frame you actually plan in: a day, a site, and the children booked in for it."
      >
        {plans.length ? (
          <DataTable
            rows={plans}
            rowKey={(p) => p.id}
            columns={[
              {
                key: 'when',
                label: 'Distribution day',
                value: (p) => p.scheduled_for,
                render: (p) => formatDate(p.scheduled_for),
              },
              { key: 'site', label: 'Site', value: (p) => p.site_name },
              {
                key: 'children',
                label: 'Children expected',
                value: (p) => p.expected_children,
                render: (p) => formatNumber(p.expected_children),
              },
              {
                key: 'required',
                label: 'Cartons required',
                value: (p) => p.cartons_required,
                render: (p) => formatNumber(p.cartons_required),
              },
              {
                key: 'available',
                label: 'On hand + inbound',
                value: (p) => p.cartons_on_hand + p.cartons_inbound,
                render: (p) =>
                  `${formatNumber(p.cartons_on_hand)} + ${formatNumber(
                    p.cartons_inbound,
                  )}`,
              },
              {
                key: 'state',
                label: 'Cover',
                value: (p) => p.state,
                render: (p) => (
                  <Badge
                    tone={
                      p.state === 'covered'
                        ? 'good'
                        : p.state === 'at_risk'
                        ? 'warn'
                        : 'bad'
                    }
                  >
                    {p.state === 'at_risk'
                      ? 'at risk'
                      : p.state === 'uncovered'
                      ? 'uncovered'
                      : 'covered'}
                  </Badge>
                ),
              },
            ]}
          />
        ) : (
          <EmptyState
            title="No distributions planned."
            hint="Plan a distribution day to see whether inbound supply covers it."
          />
        )}
      </Card>

      <Card
        title="Weeks of cover by site"
        subtitle="From what has physically arrived and the rate you are admitting children. At four weeks you plan; at one you triage."
      >
        {cover.length ? (
          <DataTable
            rows={cover}
            rowKey={(c) => c.node_id}
            columns={[
              { key: 'site', label: 'Site', value: (c) => c.node_name },
              {
                key: 'stock',
                label: 'On hand',
                value: (c) => c.stock_on_hand,
                render: (c) => `${formatNumber(c.stock_on_hand)} cartons`,
              },
              {
                key: 'children',
                label: 'Children served / month',
                value: (c) => c.served_children,
                render: (c) => formatNumber(c.served_children),
              },
              {
                key: 'weeks',
                label: 'Weeks of cover',
                value: (c) => c.weeks_of_cover,
                render: (c) => (
                  <Badge
                    tone={
                      c.weeks_of_cover < 2
                        ? 'bad'
                        : c.weeks_of_cover < 4
                        ? 'warn'
                        : 'good'
                    }
                  >
                    {c.weeks_of_cover}
                  </Badge>
                ),
              },
              {
                key: 'dry',
                label: 'Runs dry',
                value: (c) => c.stockout_on,
                render: (c) => formatDate(c.stockout_on),
              },
              {
                key: 'act',
                label: '',
                value: () => '',
                render: (c) =>
                  supplyCan(world.role, 'signals', 'raise') ? (
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => setRaising(c)}
                    >
                      Raise a shortfall
                    </button>
                  ) : null,
              },
            ]}
          />
        ) : (
          <EmptyState
            title="No cover figures yet."
            hint="Cover appears once stock has been received at a site."
          />
        )}
        <p className="muted small method-note">
          {cover.length ? cover[0].method : ''} These are the same figures the
          OES command centre reads for these sites.
        </p>
      </Card>

      {signals.length ? (
        <Card
          title="Shortfalls you have raised"
          subtitle="Reported upward from this screen, and answered against the same record."
        >
          <DataTable
            rows={signals}
            rowKey={(s) => s.id}
            columns={[
              {
                key: 'raised',
                label: 'Raised',
                value: (s) => s.raised_on,
                render: (s) => formatDate(s.raised_on),
              },
              { key: 'site', label: 'Site', value: (s) => s.site_name },
              {
                key: 'children',
                label: 'Children affected',
                value: (s) => s.children_affected,
                render: (s) => formatNumber(s.children_affected),
              },
              {
                key: 'by',
                label: 'Needed by',
                value: (s) => s.needed_by,
                render: (s) => formatDate(s.needed_by),
              },
              {
                key: 'status',
                label: 'Status',
                value: (s) => s.status,
                render: (s) => (
                  <Badge tone={s.status === 'resolved' ? 'good' : 'warn'}>
                    {s.status}
                  </Badge>
                ),
              },
            ]}
          />
        </Card>
      ) : null}

      <Card
        title="From a batch to the children it treated"
        subtitle="Both ends of this chain already exist. Nothing currently holds them as one record."
      >
        {records.length ? (
          <DataTable
            rows={records}
            rowKey={(r) => r.id}
            onRowClick={(r) => setBatch(r)}
            columns={[
              {
                key: 'when',
                label: 'Distributed',
                value: (r) => r.distributed_on,
                render: (r) => formatDate(r.distributed_on),
              },
              { key: 'site', label: 'Site', value: (r) => r.site_name },
              { key: 'batch', label: 'Batch', value: (r) => r.batch_lot },
              {
                key: 'consignment',
                label: 'Arrived on',
                value: (r) => r.shipment_reference || '',
                render: (r) => r.shipment_reference || '—',
              },
              {
                key: 'children',
                label: 'Children served',
                value: (r) => r.children_served,
                render: (r) => formatNumber(r.children_served),
              },
              {
                key: 'outcomes',
                label: 'Outcomes recorded',
                value: (r) => (r.outcomes || []).length,
                render: (r) => formatNumber((r.outcomes || []).length),
              },
            ]}
          />
        ) : (
          <EmptyState
            title="No distributions recorded yet."
            hint="A recorded distribution links a received batch to the children admitted on it."
          />
        )}
        <p className="muted small method-note">
          Treatment outcomes in this environment are synthetic and labelled as
          such wherever they appear.
        </p>
      </Card>

      {raising ? (
        <RaiseShortfall
          node={raising}
          onClose={() => setRaising(null)}
          onSubmit={(payload) =>
            act(
              () => supplyPost('/supply/api/signals/raise/', payload),
              'Shortfall raised. It is now in the OES command centre queue.',
            ).then(() => setRaising(null))
          }
        />
      ) : null}

      {batch ? (
        <BatchDrill record={batch} onClose={() => setBatch(null)} />
      ) : null}
    </Page>
  );
}

function RaiseShortfall({ node, onClose, onSubmit }) {
  const shortfall = Math.max(
    0,
    Math.round(node.weekly_burn * 2 - node.stock_on_hand),
  );
  const [children, setChildren] = useState(String(shortfall || 100));
  const [cartons, setCartons] = useState(String(shortfall || 100));
  const [neededBy, setNeededBy] = useState(node.stockout_on);
  const [note, setNote] = useState('');

  return (
    <Modal
      title={`Raise a shortfall at ${node.node_name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() =>
              onSubmit({
                site_id: node.node_id,
                children_affected: Number(children),
                cartons_short: Number(cartons),
                needed_by: neededBy,
                note,
              })
            }
          >
            Raise it
          </button>
        </>
      }
    >
      <p className="muted small">
        This lands in the OES command centre ranked by the children behind it,
        marked as raised by your organisation — not as a message somebody may or
        may not read.
      </p>
      <FormRow label="Children affected">
        <input
          type="number"
          value={children}
          onChange={(e) => setChildren(e.target.value)}
        />
      </FormRow>
      <FormRow label="Cartons short">
        <input
          type="number"
          value={cartons}
          onChange={(e) => setCartons(e.target.value)}
        />
      </FormRow>
      <FormRow
        label="Needed by"
        hint={`${node.node_name} is projected to run dry on ${formatDate(
          node.stockout_on,
        )}.`}
      >
        <input
          type="date"
          value={neededBy}
          onChange={(e) => setNeededBy(e.target.value)}
        />
      </FormRow>
      <FormRow label="Note">
        <textarea
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="What changed — admissions above plan, a road reopening, a short receipt."
        />
      </FormRow>
    </Modal>
  );
}

function BatchDrill({ record, onClose }) {
  const outcomes = record.outcomes || [];
  const recovered = outcomes.filter((o) => o.discharge_status === 'recovered');

  return (
    <Modal
      title={`Batch ${record.batch_lot}`}
      onClose={onClose}
      wide
      footer={
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      }
    >
      <p className="muted small">
        Arrived on {record.shipment_reference || 'an unrecorded consignment'},
        distributed at {record.site_name} on {formatDate(record.distributed_on)}{' '}
        to {formatNumber(record.children_served)} children.{' '}
        <strong>Treatment records in this environment are synthetic.</strong>
      </p>
      <p className="muted small">
        {recovered.length} of {outcomes.length} children in the recorded sample
        were discharged as recovered.
      </p>
      <div className="outcome-list">
        {outcomes.map((child) => (
          <MuacSeries key={child.id} child={child} />
        ))}
      </div>
    </Modal>
  );
}

function MuacSeries({ child }) {
  const series = child.measurements || [];
  if (!series.length) return null;
  const width = 220;
  const height = 56;
  const lo = 95;
  const hi = 135;
  const x = (i) => (i / Math.max(series.length - 1, 1)) * width;
  const y = (mm) => height - ((mm - lo) / (hi - lo)) * height;
  const path = series
    .map(
      (m, i) =>
        `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(m.muac_mm).toFixed(1)}`,
    )
    .join(' ');

  return (
    <div className="outcome-row">
      <div className="outcome-id">
        <strong>{child.anon_id}</strong>
        <Badge tone={child.discharge_status === 'recovered' ? 'good' : 'warn'}>
          {child.discharge_label}
        </Badge>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="muac-spark">
        {/* The red / yellow / green bands a MUAC series is read against. */}
        <rect
          x="0"
          y={y(115)}
          width={width}
          height={height - y(115)}
          className="muac-band sam"
        />
        <rect
          x="0"
          y={y(125)}
          width={width}
          height={y(115) - y(125)}
          className="muac-band mam"
        />
        <rect
          x="0"
          y="0"
          width={width}
          height={y(125)}
          className="muac-band ok"
        />
        <path d={path} className="muac-line" />
      </svg>
      <div className="outcome-figures">
        {child.admission_muac_mm} → {child.latest_muac_mm} mm
      </div>
    </div>
  );
}
