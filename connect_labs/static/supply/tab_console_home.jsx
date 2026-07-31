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
  // The oldest thing waiting on a decision. A queue length says how much
  // there is; the date says whether anyone is keeping up with it.
  const waitingDates = queue
    .map((q) => q.submitted_at || q.created_at)
    .filter(Boolean)
    .sort();
  const oldestWaiting = waitingDates.length ? waitingDates[0] : null;

  const openRounds = rounds.filter((r) => r.status === 'open');
  const liveRfps = rfps.filter((r) => r.status === 'published');
  const awaitingAward = liveRfps.filter((r) =>
    r.lots.some((l) => !l.awarded_org),
  );
  // Lots, not solicitations. "1 live solicitation" understates four separate
  // award decisions sitting on one tender.
  const unawardedLots = liveRfps.reduce(
    (n, r) => n + r.lots.filter((l) => !l.awarded_org).length,
    0,
  );

  return (
    <Page
      title="Procurement dashboard"
      lede="Registry health, applications awaiting review, and solicitations in flight."
    >
      {/* The queue leads: it is the only figure here that is somebody's work
          rather than a description of the world. Registry size is context. */}
      <KeyFigures
        figures={[
          {
            label: 'Applications to review',
            value: queue.length,
            lead: true,
            tone: queue.length ? 'at-risk' : 'ok',
            hint: queue.length
              ? `oldest waiting since ${
                  oldestWaiting ? formatDate(oldestWaiting) : '—'
                }`
              : 'queue clear',
          },
          {
            label: 'Qualified suppliers',
            value: registry.length,
            tone: expiringSoon ? 'at-risk' : undefined,
            hint: expiringSoon
              ? `${expiringSoon} qualification${
                  expiringSoon === 1 ? '' : 's'
                } expiring within 60 days`
              : 'no near-term expiries',
            method:
              'The registry IS the set of live qualifications — there is no list anyone maintains. A lapsed certification drops a supplier out of it without anybody remembering to remove them, which is why the expiry count beside it is the number worth watching.',
          },
          {
            label: 'Lots awaiting award',
            value: unawardedLots,
            tone: unawardedLots ? 'at-risk' : undefined,
            hint: `across ${liveRfps.length} live solicitation${
              liveRfps.length === 1 ? '' : 's'
            }`,
          },
          // One fact, not two. "0 open" over a hint reading "1 total" read as
          // two unrelated numbers a reader had to subtract to understand; the
          // interesting content is the disposition of every round there is.
          {
            label: 'EOI rounds',
            value: openRounds.length
              ? `${openRounds.length} open`
              : 'none open',
            hint:
              rounds.length - openRounds.length
                ? `${rounds.length - openRounds.length} closed · ${
                    rounds.length
                  } in total`
                : `${rounds.length} in total`,
          },
        ]}
      />

      <div className="grid-2-wide">
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

        {/* A clear queue states itself in one line, not in a 150px card.
            Empty, this was the largest panel on the establishing screen — two
            thirds of the width holding the single sentence "The review queue is
            clear." — which pushed the Solicitations table, and with it the only
            live tender, below the fold on the frame that is supposed to answer
            "what is in flight today". */}
        {queue.length ? (
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
        ) : (
          <p className="muted small queue-clear">
            No applications awaiting review.
          </p>
        )}
      </div>

      {rfps.length ? (
        <Card
          title="Solicitations"
          subtitle="Soonest bid deadline first — the tender whose window closes next is the one with a decision attached."
        >
          <DataTable
            rows={rfps}
            rowKey={(r) => r.id}
            defaultSort={{ key: 'deadline', dir: 1 }}
            columns={[
              { key: 'title', label: 'Solicitation', value: (r) => r.title },
              {
                key: 'status',
                label: 'Status',
                value: (r) => r.status,
                render: (r) => <SolicitationStatusChip rfp={r} />,
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
