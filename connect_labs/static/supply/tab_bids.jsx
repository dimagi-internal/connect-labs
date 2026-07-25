function BidsTab({ ctx }) {
  const { world } = ctx;
  const [openRfp, setOpenRfp] = useState(null);
  const rfps = world.eligible_rfps || [];

  return (
    <Page
      title="Solicitations & bids"
      lede="Solicitations you are qualified to bid on, and the bids you have priced."
    >
      <Card title="Open to your organisation">
        <DataTable
          rows={rfps}
          rowKey={(r) => r.id}
          empty="No solicitations are open to you yet."
          columns={[
            { key: 'title', label: 'Solicitation', value: (r) => r.title },
            {
              key: 'cats',
              label: 'Categories',
              sortable: false,
              value: () => '',
              render: (r) => <CategoryPills categories={r.categories} />,
            },
            { key: 'lots', label: 'Lots', value: (r) => r.lots.length },
            {
              key: 'deadline',
              label: 'Bid deadline',
              value: (r) => r.bid_deadline,
              render: (r) => {
                const d = daysUntil(r.bid_deadline);
                if (d === null) return '—';
                if (d < 0) return <Badge tone="bad">Closed</Badge>;
                if (d <= 7)
                  return (
                    <Badge tone="warn">
                      {formatDate(r.bid_deadline)} · {d}d
                    </Badge>
                  );
                return formatDate(r.bid_deadline);
              },
            },
            {
              key: 'bid',
              label: 'Your bid',
              value: (r) => (r.my_bid ? r.my_bid.status : ''),
              render: (r) =>
                r.my_bid ? (
                  <StatusChip status={r.my_bid.status} />
                ) : (
                  <Badge tone="warn">No bid</Badge>
                ),
            },
            {
              key: 'act',
              label: '',
              sortable: false,
              value: () => '',
              render: (r) => (
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setOpenRfp(r)}
                >
                  {r.my_bid && r.my_bid.status === 'submitted'
                    ? 'View bid'
                    : 'Price lots'}
                </button>
              ),
            },
          ]}
        />
      </Card>

      <Card title="Awarded to you" subtitle="Lots where your bid was selected.">
        <AwardedLots rfps={rfps} />
      </Card>

      {openRfp ? (
        <BidWorkspace
          ctx={ctx}
          rfp={openRfp}
          onClose={() => setOpenRfp(null)}
        />
      ) : null}
    </Page>
  );
}

function AwardedLots({ rfps }) {
  const rows = [];
  rfps.forEach((r) => {
    (r.lots || []).forEach((lot) => {
      if (lot.awarded_lot_bid_id && r.my_bid) {
        const mine = (r.my_bid.lot_bids || []).find(
          (lb) => lb.id === lot.awarded_lot_bid_id,
        );
        if (mine) rows.push({ rfp: r, lot, lot_bid: mine });
      }
    });
  });

  return (
    <DataTable
      rows={rows}
      rowKey={(row) => row.lot.id}
      empty="No lots awarded to you yet."
      columns={[
        { key: 'rfp', label: 'Solicitation', value: (row) => row.rfp.title },
        { key: 'lot', label: 'Lot', value: (row) => row.lot.description },
        {
          key: 'qty',
          label: 'Quantity',
          value: (row) => row.lot.quantity,
          render: (row) => `${formatNumber(row.lot.quantity)} ${row.lot.unit}`,
        },
        {
          key: 'dest',
          label: 'Destination',
          value: (row) => row.lot.delivery_place,
          render: (row) =>
            `${row.lot.delivery_place}, ${countryLabel(
              row.lot.delivery_country,
            )}`,
        },
        {
          key: 'price',
          label: 'Your unit price',
          value: (row) => row.lot_bid.unit_price,
          render: (row) =>
            formatMoney(row.lot_bid.unit_price, row.lot_bid.currency),
        },
      ]}
    />
  );
}

function BidWorkspace({ ctx, rfp, onClose }) {
  const existing = rfp.my_bid;
  const locked = existing && existing.status === 'submitted';
  const byLot = {};
  ((existing && existing.lot_bids) || []).forEach((lb) => {
    byLot[lb.lot_id] = lb;
  });

  const [rows, setRows] = useState(
    rfp.lots.map((lot) => ({
      lot_id: lot.id,
      unit_price: byLot[lot.id] ? byLot[lot.id].unit_price : '',
      currency: byLot[lot.id] ? byLot[lot.id].currency : 'USD',
      lead_time_days: byLot[lot.id] ? byLot[lot.id].lead_time_days || '' : '',
      notes: byLot[lot.id] ? byLot[lot.id].notes || '' : '',
    })),
  );

  const setCell = (lotId, field) => (e) =>
    setRows((cur) =>
      cur.map((r) =>
        r.lot_id === lotId ? { ...r, [field]: e.target.value } : r,
      ),
    );

  const priced = () =>
    rows.filter((r) => r.unit_price !== '' && r.unit_price !== null);

  const save = () =>
    ctx.act(
      () =>
        supplyPost(`/supply/api/rfps/${rfp.id}/bid/`, { lot_bids: priced() }),
      'Bid saved as draft.',
    );

  const submit = async () => {
    const saved = await ctx.act(
      () =>
        supplyPost(`/supply/api/rfps/${rfp.id}/bid/`, { lot_bids: priced() }),
      null,
    );
    if (!saved) return;
    const done = await ctx.act(
      () => supplyPost(`/supply/api/rfps/${rfp.id}/bid/submit/`, {}),
      'Bid submitted.',
    );
    if (done) onClose();
  };

  return (
    <Modal
      wide
      title={rfp.title}
      onClose={onClose}
      footer={
        locked ? (
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Close
          </button>
        ) : (
          <React.Fragment>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={save}
              disabled={ctx.busy}
            >
              Save draft
            </button>
            <button
              type="button"
              className="btn"
              onClick={submit}
              disabled={ctx.busy || !priced().length}
            >
              Submit bid
            </button>
          </React.Fragment>
        )
      }
    >
      {rfp.brief ? <p className="modal-lede">{rfp.brief}</p> : null}
      {locked ? (
        <div className="notice">
          This bid was submitted on {formatDate(existing.submitted_at)} and can
          no longer be edited.
        </div>
      ) : (
        <div className="notice">
          Price only the lots you can serve — leave a lot blank to decline it.
        </div>
      )}

      <table className="data-table bid-table">
        <thead>
          <tr>
            <th>Lot</th>
            <th>Quantity</th>
            <th>Destination</th>
            <th>Delivery by</th>
            <th>Unit price</th>
            <th>Currency</th>
            <th>Lead time (days)</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {rfp.lots.map((lot) => {
            const row = rows.find((r) => r.lot_id === lot.id);
            return (
              <tr key={lot.id}>
                <td>
                  <div>{lot.description}</div>
                  <div className="muted small">
                    {categoryLabel(lot.category)}
                  </div>
                </td>
                <td>
                  {formatNumber(lot.quantity)} {lot.unit}
                </td>
                <td>
                  {lot.delivery_place}, {countryLabel(lot.delivery_country)}
                </td>
                <td>{formatDate(lot.delivery_deadline)}</td>
                <td>
                  <input
                    type="number"
                    step="0.01"
                    className="cell-input"
                    value={row.unit_price}
                    disabled={locked}
                    onChange={setCell(lot.id, 'unit_price')}
                  />
                </td>
                <td>
                  <input
                    type="text"
                    className="cell-input tiny"
                    value={row.currency}
                    disabled={locked}
                    maxLength="3"
                    onChange={setCell(lot.id, 'currency')}
                  />
                </td>
                <td>
                  <input
                    type="number"
                    className="cell-input tiny"
                    value={row.lead_time_days}
                    disabled={locked}
                    onChange={setCell(lot.id, 'lead_time_days')}
                  />
                </td>
                <td>
                  <input
                    type="text"
                    className="cell-input"
                    value={row.notes}
                    disabled={locked}
                    onChange={setCell(lot.id, 'notes')}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Modal>
  );
}
