function ConsoleHome({ ctx }) {
  const { world } = ctx;
  const registry = world.registry || [];
  const queue = world.review_queue || [];
  const rounds = world.rounds || [];
  const rfps = world.rfps || [];

  const byCategory = {};
  registry.forEach((row) => {
    row.qualifications.forEach((q) => {
      byCategory[q.category] = (byCategory[q.category] || 0) + 1;
    });
  });

  const expiringSoon = registry.reduce(
    (n, row) =>
      n +
      row.qualifications.filter((q) => {
        const d = daysUntil(q.expires_at);
        return d !== null && d <= 60;
      }).length,
    0,
  );
  const openRounds = rounds.filter((r) => r.status === 'open');
  const liveRfps = rfps.filter((r) => r.status === 'published');
  const awaitingAward = liveRfps.filter((r) =>
    r.lots.some((l) => !l.awarded_org),
  );

  return (
    <Page
      title="Procurement dashboard"
      lede="Registry health, applications awaiting review, and solicitations in flight."
    >
      <KeyFigures
        figures={[
          {
            label: 'Qualified suppliers',
            value: registry.length,
            hint: expiringSoon
              ? `${expiringSoon} qualifications expiring within 60 days`
              : 'no near-term expiries',
          },
          {
            label: 'Applications to review',
            value: queue.length,
            hint: queue.length ? 'awaiting a decision' : 'queue clear',
          },
          {
            label: 'Open EOI rounds',
            value: openRounds.length,
            hint: `${rounds.length} total`,
          },
          {
            label: 'Live solicitations',
            value: liveRfps.length,
            hint: `${awaitingAward.length} with unawarded lots`,
          },
        ]}
      />

      <div className="grid-2">
        <Card title="Registry coverage by category">
          {Object.keys(byCategory).length ? (
            <div className="bar-list">
              {SUPPLY_CATEGORIES.map((c) => {
                const n = byCategory[c.key] || 0;
                const max = Math.max(...Object.values(byCategory), 1);
                return (
                  <div className="bar-row" key={c.key}>
                    <div className="bar-label">{c.label}</div>
                    <div className="bar-track">
                      <div
                        className="bar-fill"
                        style={{ width: `${(n / max) * 100}%` }}
                      />
                    </div>
                    <div className="bar-value">{n}</div>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState
              title="No suppliers qualified yet."
              hint="Review applications to populate the registry."
            />
          )}
        </Card>

        <Card title="Applications awaiting review">
          <DataTable
            rows={queue}
            rowKey={(s) => s.id}
            empty="The review queue is clear."
            columns={[
              { key: 'org', label: 'Supplier', value: (s) => s.org_name },
              { key: 'round', label: 'Round', value: (s) => s.round_title },
              {
                key: 'cats',
                label: 'Categories',
                sortable: false,
                value: () => '',
                render: (s) => <CategoryPills categories={s.categories} />,
              },
              {
                key: 'submitted',
                label: 'Waiting since',
                value: (s) => s.submitted_at,
                render: (s) => formatDate(s.submitted_at),
              },
            ]}
          />
        </Card>
      </div>

      {rfps.length ? (
        <Card title="Solicitations">
          <DataTable
            rows={rfps}
            rowKey={(r) => r.id}
            columns={[
              { key: 'title', label: 'Solicitation', value: (r) => r.title },
              {
                key: 'status',
                label: 'Status',
                value: (r) => r.status,
                render: (r) => <StatusChip status={r.status} />,
              },
              { key: 'lots', label: 'Lots', value: (r) => r.lots.length },
              {
                key: 'awarded',
                label: 'Awarded',
                value: (r) => r.lots.filter((l) => l.awarded_org).length,
                render: (r) =>
                  `${r.lots.filter((l) => l.awarded_org).length} / ${
                    r.lots.length
                  }`,
              },
              {
                key: 'deadline',
                label: 'Bid deadline',
                value: (r) => r.bid_deadline,
                render: (r) => formatDate(r.bid_deadline),
              },
            ]}
          />
        </Card>
      ) : null}
    </Page>
  );
}
