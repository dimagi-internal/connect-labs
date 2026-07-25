function SupplierHome({ ctx }) {
  const { world } = ctx;
  const quals = (world.org && world.org.qualifications) || [];
  const live = quals.filter((q) => {
    const d = daysUntil(q.expires_at);
    return q.status === 'active' && d !== null && d >= 0;
  });
  const expiringSoon = live.filter((q) => {
    const d = daysUntil(q.expires_at);
    return d !== null && d <= 60;
  });
  const openRounds = world.open_rounds || [];
  const notApplied = openRounds.filter((r) => !r.applied);
  const rfps = world.eligible_rfps || [];
  const unbid = rfps.filter((r) => !r.my_bid || r.my_bid.status === 'draft');

  const deadlines = rfps
    .map((r) => r.bid_deadline)
    .filter(Boolean)
    .sort();

  return (
    <Page
      title={`Welcome, ${world.org.legal_name}`}
      lede="Your qualifications, open supply rounds and live solicitations."
    >
      <KeyFigures
        figures={[
          {
            label: 'Live qualifications',
            value: live.length,
            hint: expiringSoon.length
              ? `${expiringSoon.length} expiring within 60 days`
              : null,
          },
          {
            label: 'Open EOI rounds',
            value: openRounds.length,
            hint: notApplied.length
              ? `${notApplied.length} not yet applied to`
              : 'all applied to',
          },
          {
            label: 'Solicitations open to you',
            value: rfps.length,
            hint: unbid.length
              ? `${unbid.length} without a submitted bid`
              : null,
          },
          {
            label: 'Next bid deadline',
            value: deadlines.length ? formatDate(deadlines[0]) : '—',
          },
        ]}
      />

      <div className="grid-2">
        <Card
          title="Your qualifications"
          subtitle="Granted through EOI review; solicitations are gated on these."
        >
          {live.length ? (
            <DataTable
              rows={live}
              rowKey={(q) => q.id}
              columns={[
                {
                  key: 'category',
                  label: 'Category',
                  value: (q) => categoryLabel(q.category),
                },
                {
                  key: 'granted',
                  label: 'Granted',
                  value: (q) => q.granted_at,
                  render: (q) => formatDate(q.granted_at),
                },
                {
                  key: 'expires',
                  label: 'Expires',
                  value: (q) => q.expires_at,
                  render: (q) => <ExpiryChip iso={q.expires_at} />,
                },
              ]}
            />
          ) : (
            <EmptyState
              title="No qualifications yet"
              hint="Apply to an open expression-of-interest round to join the supplier registry."
            />
          )}
        </Card>

        <Card title="Open expression-of-interest rounds">
          {openRounds.length ? (
            <DataTable
              rows={openRounds}
              rowKey={(r) => r.id}
              columns={[
                { key: 'title', label: 'Round', value: (r) => r.title },
                {
                  key: 'cats',
                  label: 'Categories',
                  sortable: false,
                  value: (r) => '',
                  render: (r) => <CategoryPills categories={r.categories} />,
                },
                {
                  key: 'closes',
                  label: 'Closes',
                  value: (r) => r.closes_at,
                  render: (r) => formatDate(r.closes_at),
                },
                {
                  key: 'applied',
                  label: 'Status',
                  value: (r) => (r.applied ? 1 : 0),
                  render: (r) =>
                    r.applied ? (
                      <Badge tone="good">Applied</Badge>
                    ) : (
                      <Badge tone="warn">Not applied</Badge>
                    ),
                },
              ]}
            />
          ) : (
            <EmptyState title="No rounds are open right now." />
          )}
        </Card>
      </div>

      <Card title="Solicitations open to you">
        {rfps.length ? (
          <DataTable
            rows={rfps}
            rowKey={(r) => r.id}
            columns={[
              { key: 'title', label: 'Solicitation', value: (r) => r.title },
              { key: 'lots', label: 'Lots', value: (r) => r.lots.length },
              {
                key: 'countries',
                label: 'Countries',
                sortable: false,
                value: () => '',
                render: (r) => r.countries.map(countryLabel).join(', '),
              },
              {
                key: 'deadline',
                label: 'Bid deadline',
                value: (r) => r.bid_deadline,
                render: (r) => formatDate(r.bid_deadline),
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
            ]}
          />
        ) : (
          <EmptyState
            title="No solicitations are open to you"
            hint="Solicitations are visible once your organisation holds a live qualification in a matching category."
          />
        )}
      </Card>
    </Page>
  );
}
