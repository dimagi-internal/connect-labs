/* What the implementing partner sees when a truck arrives.

   This tab used to render the SUPPLIER's Operations surface — the same
   component, so a partner who feeds children opened "Receiving" and was shown
   "Declare a despatch", contracts they do not hold, and plant-to-warehouse legs
   they have no part in. That is the exact view this whole surface exists to
   invert, rendered on the one screen where the inversion has to become
   physical.

   A partner's relationship with a consignment starts when it arrives. So the
   frame is: what is coming to my sites, what has landed, and what I counted
   when it did — with the short counts I raised standing as evidence, because
   the person beside the pallets is the one who knows. */

function PartnerReceivingTab({ ctx }) {
  const { world } = ctx;
  const siteIds = new Set((world.sites || []).map((s) => s.id));
  const contracts = world.contracts || [];
  const discrepancies = world.discrepancies || [];
  const [openShipment, setOpenShipment] = useState(null);

  // Only legs terminating at one of this organisation's own sites. A leg
  // between two nodes upstream of them is not their business and showing it
  // was most of what made this page read as somebody else's.
  const inbound = contracts
    .flatMap((c) => (c.shipments || []).map((s) => ({ ...s, contract: c })))
    .filter((s) => siteIds.has(s.destination.id));

  const arriving = inbound.filter(
    (s) => s.status === 'in_transit' || s.status === 'planned',
  );
  const openDiscrepancies = discrepancies.filter((d) => d.status === 'open');
  // A consignment whose count has already been recorded is not awaiting one,
  // even though it is still "delivered" rather than "confirmed". Counting them
  // together offered "Record the count" on a row whose short count is reported
  // twelve lines below, and overstated the awaiting tile by exactly those rows
  // — on the scene whose thesis is that a discrepancy exists the moment the
  // count is recorded.
  const countedRefs = new Set(discrepancies.map((d) => d.shipment_reference));
  const isCounted = (s) =>
    countedRefs.has(s.reference) || s.status === 'confirmed';
  const awaitingCount = inbound.filter(
    (s) => s.status === 'delivered' && !isCounted(s),
  ).length;
  const cartonsShort = openDiscrepancies.reduce(
    (n, d) => n + (d.shortfall || 0),
    0,
  );

  return (
    <Page
      title="Receiving"
      lede="What is coming to your sites, what has landed, and what you counted when it did."
    >
      <KeyFigures
        figures={[
          {
            label: 'Consignments in transit',
            value: arriving.length,
            hint: arriving.length
              ? `${formatNumber(
                  arriving.reduce((n, s) => n + s.quantity, 0),
                )} cartons on the way`
              : 'nothing inbound',
          },
          {
            label: 'Awaiting your count',
            tone: awaitingCount ? 'at-risk' : 'ok',
            value: awaitingCount,
            hint: awaitingCount
              ? 'delivered but not yet confirmed'
              : 'every receipt recorded',
          },
          {
            label: 'Short on receipt',
            tone: cartonsShort ? 'critical' : 'ok',
            method:
              "The difference between what a despatch advice said was sent and what the storekeeper counted at the door. One carton is one child's full course, so a carton short is a child short — and the figure travels to the OES command centre in that unit, ranked against every other exception by the children behind it.",
            value: formatNumber(Math.abs(cartonsShort)),
            hint: openDiscrepancies.length
              ? `${openDiscrepancies.length} open discrepanc${
                  openDiscrepancies.length === 1 ? 'y' : 'ies'
                }`
              : 'every count reconciled',
          },
        ]}
      />

      <Card
        title="Arriving at your sites"
        subtitle="Record the receipt when the truck reaches the store — the count you take is what the figure becomes."
      >
        <DataTable
          rows={inbound}
          rowKey={(s) => s.id}
          empty="Nothing is inbound to your sites right now."
          onRowClick={(s) => setOpenShipment(s)}
          columns={[
            { key: 'ref', label: 'Consignment', value: (s) => s.reference },
            {
              key: 'site',
              label: 'Arriving at',
              value: (s) => s.destination.name,
            },
            {
              key: 'qty',
              label: 'Advised',
              value: (s) => s.quantity,
              render: (s) => `${formatNumber(s.quantity)} ${s.unit}`,
            },
            {
              key: 'eta',
              label: 'Expected',
              value: (s) => s.eta,
              render: (s) => formatDate(s.eta),
            },
            {
              key: 'tier',
              label: 'Reported by',
              sortable: false,
              value: () => '',
              // The tier is the honesty marker: a hand-keyed check-in says so,
              // here, where the person who keyed it can see that it did.
              render: (s) => (
                <Badge tone={s.asn_reference ? 'info' : 'warn'}>
                  {s.asn_reference ? 'Despatch advice' : 'Entered by hand'}
                </Badge>
              ),
            },
            {
              key: 'status',
              label: 'Status',
              value: (s) => s.status,
              render: (s) => <StatusChip status={s.status} />,
            },
            {
              key: 'act',
              label: '',
              sortable: false,
              value: () => '',
              render: (s) => {
                if (isCounted(s)) {
                  const d = discrepancies.find(
                    (x) => x.shipment_reference === s.reference,
                  );
                  return (
                    <Badge tone={d ? 'bad' : 'good'}>
                      {d
                        ? `Counted · ${formatNumber(
                            Math.abs(d.shortfall || 0),
                          )} short`
                        : 'Counted'}
                    </Badge>
                  );
                }
                return s.status === 'delivered' ? (
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={(ev) => {
                      ev.stopPropagation();
                      setOpenShipment(s);
                    }}
                  >
                    Record the count
                  </button>
                ) : null;
              },
            },
          ]}
        />
      </Card>

      {/* Objective title and subtitle. "Short counts you have raised" sat two
          cards from the partner page's "Shortfalls you have raised" — two
          near-identical titles for different concepts — and the subtitle was a
          verdict sentence stamped on the card face. The provenance columns and
          the `i` carry what the sentence was asserting. */}
      <Card
        title="Receiving discrepancies you have reported"
        subtitle="Each row is a count taken at the door, recorded against the consignment's despatch advice."
      >
        <DataTable
          rows={openDiscrepancies}
          rowKey={(d) => d.id}
          empty="Every consignment reconciled against its advice."
          columns={[
            {
              key: 'ship',
              label: 'Consignment',
              value: (d) => d.shipment_reference,
            },
            {
              key: 'counted_on',
              label: 'Counted',
              value: (d) => d.created_at || '',
              render: (d) => (
                <span className="nowrap">
                  {d.created_at ? formatDate(d.created_at) : '—'}
                  <span className="muted small"> · at the store</span>
                  <InfoNote
                    label="this count"
                    text="Recorded by the receiving storekeeper at the door, against the consignment's despatch advice — not asserted later from a spreadsheet. The count taken beside the pallets is the figure of record; the discrepancy exists from the moment it is recorded."
                  />
                </span>
              ),
            },
            {
              key: 'advised',
              label: 'Advised',
              value: (d) => d.expected_quantity,
              render: (d) => formatNumber(d.expected_quantity),
            },
            {
              key: 'counted',
              label: 'You counted',
              value: (d) => d.received_quantity,
              render: (d) => formatNumber(d.received_quantity),
            },
            {
              key: 'short',
              label: 'Short',
              value: (d) => d.shortfall,
              render: (d) => (
                <Badge tone="bad">
                  {formatNumber(Math.abs(d.shortfall || 0))}
                </Badge>
              ),
            },
            {
              key: 'children',
              label: 'Children affected',
              value: (d) => Math.abs(d.shortfall || 0),
              render: (d) => (
                <span>
                  {formatNumber(Math.abs(d.shortfall || 0))}
                  <span className="muted small"> lose a full course</span>
                </span>
              ),
            },
          ]}
        />
        <p className="muted small method-note">
          One carton is one child's full course of treatment, so a carton short
          is a child short. The figure travels to the OES command centre in that
          unit, ranked against every other exception by the children behind it.
        </p>
      </Card>

      {openShipment ? (
        <ShipmentDetail
          ctx={ctx}
          shipmentId={openShipment.id}
          onClose={() => setOpenShipment(null)}
        />
      ) : null}
    </Page>
  );
}
