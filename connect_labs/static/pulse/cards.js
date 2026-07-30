/**
 * Pulse card library.
 *
 * A card is a small object: `{ key, label, mount(el, store) }`. It subscribes
 * to the shared PulseStore and renders; it never fetches, never knows about
 * Connect, and never owns a clock. That is what makes layouts cheap to try —
 * rearranging cards is data, not code.
 *
 * Cards must never throw into the store's emit loop. On a wall display nobody
 * is reading a console, so a broken card should degrade to a stale card, not
 * a blank screen.
 */
(function (global) {
  'use strict';

  const nf = new Intl.NumberFormat('en-US');
  const usd = (v, d = 2) =>
    '$' +
    Number(v || 0).toLocaleString('en-US', {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    });
  const usdCompact = (v) => {
    const n = Number(v || 0);
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return '$' + Math.round(n / 1e3) + 'k';
    return usd(n);
  };

  const el = (tag, cls, html) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  };

  /* Nearest town, so a row reads as a place rather than a coordinate.
     Town scale only — household GPS is never surfaced as text. */
  const TOWNS = [
    ['Kano', 'NG', 12.0, 8.52],
    ['Zaria', 'NG', 11.07, 7.72],
    ['Kaduna', 'NG', 10.52, 7.44],
    ['Katsina', 'NG', 12.99, 7.6],
    ['Sokoto', 'NG', 13.06, 5.24],
    ['Azare', 'NG', 11.68, 10.19],
    ['Bauchi', 'NG', 10.31, 9.84],
    ['Gombe', 'NG', 10.29, 11.17],
    ['Yola', 'NG', 9.2, 12.48],
    ['Jos', 'NG', 9.9, 8.89],
    ['Maiduguri', 'NG', 11.83, 13.15],
    ['Abuja', 'NG', 9.06, 7.49],
    ['Lagos', 'NG', 6.52, 3.38],
    ['Dutse', 'NG', 11.76, 9.34],
    ['Potiskum', 'NG', 11.71, 11.08],
    ['Kampala', 'UG', 0.31, 32.58],
    ['Gulu', 'UG', 2.77, 32.3],
    ['Mbale', 'UG', 1.08, 34.18],
    ['Jinja', 'UG', 0.42, 33.2],
    ['Mbarara', 'UG', -0.61, 30.65],
    ['Lira', 'UG', 2.25, 32.9],
    ['Nairobi', 'KE', -1.29, 36.82],
    ['Kisumu', 'KE', -0.09, 34.77],
    ['Mombasa', 'KE', -4.04, 39.67],
    ['Eldoret', 'KE', 0.51, 35.27],
    ['Nakuru', 'KE', -0.3, 36.08],
    ['Kakamega', 'KE', 0.28, 34.75],
    ['Lodwar', 'KE', 3.12, 35.6],
    ['Kinshasa', 'CD', -4.44, 15.27],
    ['Lubumbashi', 'CD', -11.66, 27.48],
    ['Goma', 'CD', -1.68, 29.22],
    ['Monrovia', 'LR', 6.3, -10.8],
    ['Freetown', 'SL', 8.48, -13.23],
    ['Bo', 'SL', 7.96, -11.74],
    ['Dar es Salaam', 'TZ', -6.79, 39.21],
    ['Mwanza', 'TZ', -2.52, 32.9],
    ['Bhopal', 'IN', 23.26, 77.41],
    ['Jaipur', 'IN', 26.91, 75.79],
    ['Ranchi', 'IN', 23.34, 85.31],
    ['Udaipur', 'IN', 24.58, 73.71],
    ['Indore', 'IN', 22.72, 75.86],
  ];
  function nearestTown(lat, lon) {
    if (lat == null || lon == null) return { t: '—', c: '' };
    let best = null,
      bd = Infinity;
    for (const t of TOWNS) {
      const d = (t[2] - lat) ** 2 + (t[3] - lon) ** 2;
      if (d < bd) {
        bd = d;
        best = t;
      }
    }
    return { t: best[0], c: best[1] };
  }

  const CARDS = {};
  const define = (key, label, mount) => {
    CARDS[key] = { key, label, mount };
  };

  /**
   * Subscribe to an event AND immediately paint from what the store already
   * holds. Acts mount lazily, long after `summary` first fired, so a card that
   * only listened would sit empty forever showing em-dashes.
   */
  function bind(store, event, fn, current) {
    store.on(event, fn);
    const now = typeof current === 'function' ? current() : current;
    if (now) {
      try {
        fn(now, store);
      } catch (err) {
        console.error('[pulse] initial paint failed', err);
      }
    }
  }

  /* ── KPI rail ─────────────────────────────────────────────────── */
  define('kpis', 'Headline figures', (root, store) => {
    const cells = [
      {
        key: 'services',
        label: 'Services delivered',
        sub: 'all-time, every programme',
        gold: true,
      },
      {
        key: 'paid',
        label: 'Paid to frontline workers',
        sub: 'all-time, measured from approved work',
      },
      {
        key: 'live',
        label: 'Verified in this window',
        sub: 'approved and payable',
      },
      {
        key: 'cps',
        label: 'Cost per verified service',
        sub: 'to the worker · blended',
      },
    ];
    root.innerHTML = '';
    const nodes = {};
    for (const c of cells) {
      const cell = el('div', 'kpi-cell');
      cell.appendChild(el('span', 'pulse-lbl', c.label));
      const val = el('div', 'kpi-val num' + (c.gold ? ' gold' : ''), '—');
      cell.appendChild(val);
      cell.appendChild(el('div', 'kpi-sub', c.sub));
      root.appendChild(cell);
      nodes[c.key] = val;
    }

    // The all-time total is a *running* figure: it starts from the last synced
    // scope count and then climbs as each new service arrives. A headline that
    // freezes the moment the page loads reads as a screenshot, not a feed --
    // and the whole claim of this screen is that the number is still moving.
    let baseline = 0; // last figure the server reported
    let delivered = 0; // services seen live since that figure
    let paidBaseline = 0;
    let paidLive = 0;

    const renderTotals = () => {
      nodes.services.textContent = nf.format(baseline + delivered);
      nodes.paid.innerHTML =
        '<small>$</small>' + nf.format(Math.round(paidBaseline + paidLive));
    };

    const paint = (s) => {
      if (!s) return;
      const scope = s.scope || {};
      const money = s.money || {};
      const nextBaseline = scope.lifetime_visits || 0;
      const first = baseline === 0;
      baseline = nextBaseline;
      paidBaseline = money.to_workers || 0;
      // A fresh server figure already includes what we counted locally.
      delivered = 0;
      paidLive = 0;
      nodes.cps.innerHTML =
        '<small>$</small>' + (money.usd_per_approved_work || 0).toFixed(2);
      if (first) countUp(nodes.services, baseline, renderTotals);
      else renderTotals();
      nodes.paid.innerHTML =
        '<small>$</small>' + nf.format(Math.round(paidBaseline));
    };
    store.on('summary', paint);
    paint(store.summary);

    store.on('event', (ev) => {
      delivered += 1;
      if (ev.status === 'approved' && ev.usd) paidLive += ev.usd;
      renderTotals();
    });
    store.on('backfill', () => {
      delivered = 0;
      paidLive = 0;
      renderTotals();
    });

    store.on('counts', (c) => {
      nodes.live.textContent = nf.format(c.verified);
    });
  });

  function countUp(node, end, onDone) {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
      node.textContent = nf.format(end);
      if (onDone) onDone();
      return;
    }
    const dur = 1800,
      t0 = performance.now();
    const run = (t) => {
      const p = Math.min((t - t0) / dur, 1);
      node.textContent = nf.format(Math.round(end * (1 - Math.pow(1 - p, 3))));
      if (p < 1) requestAnimationFrame(run);
      else if (onDone) onDone();
    };
    requestAnimationFrame(run);
  }

  /* ── event ticker ─────────────────────────────────────────────── */
  define('ticker', 'Live feed', (root, store) => {
    const MAX = 5;
    const services = () =>
      (store.summary &&
        store.summary.labels &&
        store.summary.labels.services) ||
      {};
    const oppName = (id) => {
      const o = (store.summary?.opportunities || []).find((x) => x.id === id);
      return o ? o.name : '';
    };

    function row(ev) {
      const town = nearestTown(ev.lat, ev.lon);
      const state = ev.flag_type ? 'flagged' : ev.status;
      const flags = store.summary?.labels?.flags || {};
      const stateTxt = ev.flag_type
        ? flags[ev.flag_type] || 'flagged'
        : (ev.status || '').replace('_', ' ');
      const svc =
        services()[ev.service_slug] ||
        oppName(ev.opportunity_id).split(/[-·(]/)[0].trim() ||
        'Service';

      const node = el('div', 'trow');
      node.innerHTML =
        `<span class="t-time">${new Date(ev.field_ts * 1000)
          .toISOString()
          .slice(5, 16)
          .replace('T', ' ')}</span>` +
        `<span class="t-place">${town.t}<em>${town.c}</em></span>` +
        `<span class="t-svc">${svc}</span>` +
        `<span><span class="chip" data-s="${state}">${stateTxt}</span></span>` +
        `<span class="t-worker">${(ev.worker || '').slice(0, 6)}…</span>` +
        `<span class="t-usd">${ev.usd ? usd(ev.usd) : '—'}</span>`;
      return node;
    }

    store.on('event', (ev) => {
      root.prepend(row(ev));
      while (root.children.length > MAX) root.lastChild.remove();
    });
    store.on('backfill', () => {
      root.innerHTML = '';
      for (const ev of store.recent.slice(0, MAX)) root.appendChild(row(ev));
    });
  });

  /* ── act 1 · verification ─────────────────────────────────────── */
  define('verification', 'Verification', (root, store) => {
    root.innerHTML = `
      <p class="act-lede">Connect does not take delivery on trust. Automated checks stop
        <b data-x="flagrate">—</b> of submissions for a human to look at, and
        <b data-x="notpaid">—</b> never become payable at all.</p>
      <div class="sect">
        <span class="pulse-lbl">What happened to every submission</span>
        <div class="partbar" data-x="partbar"></div>
        <div class="partkey" data-x="partkey"></div>
      </div>
      <div class="sect">
        <span class="pulse-lbl">Why work gets flagged</span>
        <div class="rank" data-x="flags"></div>
      </div>`;
    const $ = (n) => root.querySelector(`[data-x="${n}"]`);

    bind(
      store,
      'summary',
      (s) => {
        const st = s.by_status || {};
        const total = Object.values(st).reduce((a, b) => a + b, 0) || 1;
        const flagged = s.stored?.flagged || 0;
        const notPayable = total - (st.approved || 0);

        $('flagrate').textContent = ((flagged / total) * 100).toFixed(1) + '%';
        $('notpaid').textContent =
          ((notPayable / total) * 100).toFixed(1) + '%';

        // status is a partition; `flagged` is orthogonal and shown separately —
        // a flagged visit can still be approved, so they are not funnel stages.
        const OUT = [
          ['approved', 'Approved — worker paid', 'var(--ok)'],
          ['over_limit', 'Over the budget cap', 'var(--idle)'],
          ['pending', 'Awaiting review', 'var(--c-2)'],
          ['rejected', 'Rejected', 'var(--crit)'],
          ['duplicate', 'Duplicate beneficiary', '#8c4640'],
        ].filter(([k]) => (st[k] || 0) > 0);

        $('partbar').innerHTML = OUT.map(
          ([k, , c]) => `<i style="flex:${st[k]} 0 0;background:${c}"></i>`,
        ).join('');
        $('partkey').innerHTML = OUT.map(
          ([k, name, c]) => `
        <div class="pkrow"><i style="background:${c}"></i>
          <span class="pkn">${name}</span>
          <span class="pkv">${nf.format(st[k])}</span>
          <span class="pkp">${((st[k] / total) * 100).toFixed(
            1,
          )}%</span></div>`,
        ).join('');

        const labels = s.labels?.flags || {};
        const fl = Object.entries(s.by_flag || {}).sort((a, b) => b[1] - a[1]);
        const max = fl.length ? fl[0][1] : 1;
        $('flags').innerHTML =
          fl
            .map(
              ([k, n]) => `
        <div class="rrow"><span class="rn">${labels[k] || k}</span>
          <span class="rv">${nf.format(n)}</span>
          <span class="rb"><i style="width:${((n / max) * 100).toFixed(
            1,
          )}%"></i></span></div>`,
            )
            .join('') || '<p class="act-lede">No flags in this window.</p>';
      },
      () => store.summary,
    );
  });

  /* ── act 2 · money ────────────────────────────────────────────── */
  define('money', 'Money', (root, store) => {
    root.innerHTML = `
      <p class="act-lede">The money reaches the person who did the work — no sub-grantee chain,
        no per-diem. <b data-x="approved">—</b> units of approved work have paid out so far.</p>
      <div class="sect">
        <span class="pulse-lbl">Where the money went</span>
        <div class="flow" data-x="flow"></div>
      </div>
      <div class="sect">
        <span class="pulse-lbl">Paid to workers, by country</span>
        <div class="rank" data-x="bycountry"></div>
      </div>
      <div class="sect">
        <span class="pulse-lbl">Paid to workers, by service</span>
        <div class="rank" data-x="byservice"></div>
      </div>`;
    const $ = (n) => root.querySelector(`[data-x="${n}"]`);

    bind(
      store,
      'summary',
      (s) => {
        const m = s.money || {};
        $('approved').textContent = nf.format(m.approved_works || 0);

        // Accrued vs paid: the gap is the float between a worker earning and a
        // worker being paid, which is the number a funder actually asks about.
        const toWorkers = m.to_workers || 0;
        const toOrgs = m.to_orgs || 0;
        const steps = [
          [
            'Earned by frontline workers',
            toWorkers,
            'var(--light)',
            `${nf.format(m.approved_works || 0)} approved units of work`,
          ],
          [
            'Accrued to delivery organisations',
            toOrgs,
            'var(--c-2)',
            "the org's share for running the programme",
          ],
        ];
        const max = Math.max(toWorkers, toOrgs, 1);
        $('flow').innerHTML = steps
          .map(
            ([name, v, c, note]) => `
        <div class="flow-step">
          <div class="flow-top"><span class="flow-name">${name}</span><span class="flow-val">${usdCompact(
            v,
          )}</span></div>
          <div class="flow-bar"><i style="width:${((v / max) * 100).toFixed(
            1,
          )}%;background:${c}"></i></div>
          <div class="flow-note">${note}</div>
        </div>`,
          )
          .join('');

        const renderRank = (node, rows, keyName) => {
          if (!rows || !rows.length) {
            node.innerHTML = '<p class="act-lede">No data yet.</p>';
            return;
          }
          const mx = rows[0].usd || 1;
          node.innerHTML = rows
            .map(
              (r) => `
          <div class="rrow"><span class="rn">${r[keyName]}</span>
            <span class="rv">${usdCompact(r.usd)}</span>
            <span class="rb"><i style="width:${((r.usd / mx) * 100).toFixed(
              1,
            )}%;background:var(--light-dim)"></i></span></div>`,
            )
            .join('');
        };
        renderRank($('bycountry'), m.by_country, 'name');
        /* by_service reconciles to to_workers; by_country does not, because
           Connect leaves country blank on most opportunities. Say what the
           breakdown covers rather than letting three full-width bars imply
           they are the whole portfolio. */
        const cov = m.by_country_unattributed;
        if (cov && cov.usd > 0 && cov.usd_share < 0.98) {
          $('bycountry').insertAdjacentHTML(
            'beforeend',
            `<div class="flow-note">Covers ${usdCompact(
              m.to_workers - cov.usd,
            )} of ${usdCompact(
              m.to_workers,
            )} — country not recorded for the rest.</div>`,
          );
        }
        renderRank($('byservice'), m.by_service, 'name');
      },
      () => store.summary,
    );
  });

  /* ── act 3 · reach ────────────────────────────────────────────── */
  define('reach', 'Reach', (root, store) => {
    root.innerHTML = `
      <p class="act-lede">One platform, <b data-x="opps">—</b> opportunities across
        <b data-x="ncountries">—</b> countries — run by <b data-x="orgs">—</b> local
        organisations, not by us.</p>
      <div class="sect">
        <span class="pulse-lbl">Where the work is</span>
        <div class="rank" data-x="countries"></div>
      </div>
      <div class="sect">
        <span class="pulse-lbl">Last 72 hours of delivery</span>
        <svg class="spark" data-x="spark" viewBox="0 0 300 62" preserveAspectRatio="none"
             role="img" aria-label="Services delivered per hour, last 72 hours"></svg>
        <div class="axis"><span>72h ago</span><span>36h</span><span>now</span></div>
      </div>
      <div class="sect">
        <div class="pairs">
          <div><span class="pulse-lbl">Programmes</span><div class="pv num" data-x="progs">—</div></div>
          <div><span class="pulse-lbl">Grid cells mapped</span><div class="pv num" data-x="cells">—</div></div>
        </div>
      </div>`;
    const $ = (n) => root.querySelector(`[data-x="${n}"]`);

    bind(
      store,
      'summary',
      (s) => {
        const scope = s.scope || {};
        const byCountry = s.money?.by_country || [];
        $('opps').textContent = nf.format(scope.opportunities || 0);
        $('orgs').textContent = nf.format(scope.orgs || 0);
        $('progs').textContent = nf.format(scope.programs || 0);
        $('ncountries').textContent = byCountry.length || '—';

        if (byCountry.length) {
          const mx = byCountry[0].works || 1;
          const PAL = ['var(--c-1)', 'var(--c-2)', 'var(--c-3)', 'var(--c-4)'];
          $('countries').innerHTML = byCountry
            .map(
              (c, i) => `
          <div class="rrow"><span class="rn">${c.name}</span>
            <span class="rv">${nf.format(c.works)}</span>
            <span class="rb"><i style="width:${((c.works / mx) * 100).toFixed(
              1,
            )}%;background:${PAL[i % 4]}"></i></span></div>`,
            )
            .join('');
        }

        const hourly = s.hourly || [];
        if (hourly.length > 1) {
          const max = Math.max(...hourly.map((h) => h.n), 1);
          const pts = hourly.map((h, i) => [
            (i / (hourly.length - 1)) * 300,
            60 - (h.n / max) * 54,
          ]);
          const line = pts
            .map(
              (p, i) =>
                (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1),
            )
            .join(' ');
          $(
            'spark',
          ).innerHTML = `<defs><linearGradient id="pulse-sg" x1="0" y1="0" x2="0" y2="1">
             <stop offset="0" stop-color="#bd8407" stop-opacity=".42"/>
             <stop offset="1" stop-color="#bd8407" stop-opacity="0"/></linearGradient></defs>
           <path d="${line} L300 62 L0 62 Z" fill="url(#pulse-sg)"/>
           <path d="${line}" fill="none" stroke="#d99a1c" stroke-width="2" stroke-linejoin="round"/>
           <circle cx="300" cy="${pts[pts.length - 1][1].toFixed(
             1,
           )}" r="3" fill="var(--light)"/>`;
        }
      },
      () => store.summary,
    );

    bind(
      store,
      'grid',
      (g) => {
        $('cells').textContent = nf.format((g && g.cells.length) || 0);
      },
      () => store.lastGrid,
    );
  });

  /* ── unit economics ───────────────────────────────────────────── */
  define('unitecon', 'Unit economics', (root, store) => {
    root.innerHTML = `
      <p class="act-lede">The blended figure is <b data-x="blended">—</b> per verified service.
        But a blended figure hides the thing a funder should actually see: these are
        different jobs, priced differently.</p>
      <div class="sect">
        <div class="pairs">
          <div><span class="pulse-lbl">Cheapest service</span><div class="pv num ok" data-x="lo">—</div><div class="kpi-sub" data-x="loname">—</div></div>
          <div><span class="pulse-lbl">Most intensive</span><div class="pv num gold" data-x="hi">—</div><div class="kpi-sub" data-x="hiname">—</div></div>
        </div>
      </div>
      <div class="sect">
        <span class="pulse-lbl">Paid to the worker, per service</span>
        <div class="rank" data-x="rates"></div>
      </div>
      <div class="sect">
        <p class="act-lede" style="margin:0">These are measured payouts, not budgets. They
          cross-check against each programme's budgeted rate at market FX to within a few
          cents — two independent fields agreeing.</p>
      </div>`;
    const $ = (n) => root.querySelector(`[data-x="${n}"]`);

    bind(
      store,
      'summary',
      (s) => {
        const m = s.money || {};
        $('blended').textContent = usd(m.usd_per_approved_work || 0);

        // Volume-weighted, straight from money accrued over approved work --
        // NOT the mean of each opportunity's rate. Averaging per opportunity
        // lets a two-row test opp count as much as a 106,719-work programme,
        // which reported "Malaria rapid test" at $17.03 against a real $1.08.
        const rows = (m.by_service || [])
          .filter((r) => r.rate != null && r.approved >= 20)
          .map((r) => ({ name: r.name, rate: r.rate, n: r.approved }))
          .sort((a, b) => b.rate - a.rate);

        if (!rows.length) {
          $('rates').innerHTML =
            '<p class="act-lede">No measured rates yet.</p>';
          return;
        }
        const mx = rows[0].rate || 1;
        $('rates').innerHTML = rows
          .map(
            (r) => `
        <div class="rrow"><span class="rn">${r.name}</span>
          <span class="rv">${usd(
            r.rate,
          )} <span style="color:var(--ink-3)">· ${nf.format(r.n)}</span></span>
          <span class="rb"><i style="width:${((r.rate / mx) * 100).toFixed(
            1,
          )}%;background:var(--light-dim)"></i></span></div>`,
          )
          .join('');

        // Read the extremes off the same list the chart draws, so prose and
        // chart cannot drift apart.
        $('hi').textContent = usd(rows[0].rate);
        $('hiname').textContent = rows[0].name;
        $('lo').textContent = usd(rows[rows.length - 1].rate);
        $('loname').textContent = rows[rows.length - 1].name;
      },
      () => store.summary,
    );
  });

  /* ── act 4 · offline ──────────────────────────────────────────── */
  define('offline', 'Offline', (root, store) => {
    root.innerHTML = `
      <p class="act-lede">These are not people at desks. Work happens where there is no
        signal, and syncs when there is.</p>
      <div class="sect">
        <span class="pulse-lbl">Field time → server time</span>
        <div class="lagchart" data-x="lag"></div>
        <div class="axis"><span>&lt;5m</span><span>1h</span><span>6h</span><span>1d</span><span>3d+</span></div>
      </div>
      <div class="sect">
        <div class="pairs">
          <div><span class="pulse-lbl">Median sync</span><div class="pv num ok" data-x="median">—</div></div>
          <div><span class="pulse-lbl">Slowest seen</span><div class="pv num" data-x="max">—</div></div>
        </div>
      </div>
      <div class="sect">
        <p class="act-lede" style="margin:0">This is why the feed replays on <b>field time</b>,
          not arrival time. A visit delivered at 09:14 belongs at 09:14 — even if the phone
          only found signal at 13:32.</p>
      </div>`;
    const $ = (n) => root.querySelector(`[data-x="${n}"]`);

    const recompute = () => {
      const evs = store.events.length ? store.events : store.recent;
      if (!evs.length) return;
      const EDGES = [5, 30, 60, 360, 1440, 4320, Infinity];
      const B = new Array(EDGES.length).fill(0);
      const lags = [];
      for (const e of evs) {
        const m = (e.sync_ts - e.field_ts) / 60;
        if (m < 0) continue;
        lags.push(m);
        for (let i = 0; i < EDGES.length; i++)
          if (m < EDGES[i]) {
            B[i]++;
            break;
          }
      }
      if (!lags.length) return;
      lags.sort((a, b) => a - b);
      const max = Math.max(...B, 1);
      $('lag').innerHTML = B.map(
        (v, i) =>
          `<div class="${i >= 3 ? 'slow' : ''}" style="height:${(
            (v / max) *
            100
          ).toFixed(1)}%" title="${nf.format(v)} submissions"></div>`,
      ).join('');
      const median = lags[Math.floor(lags.length / 2)];
      $('median').textContent =
        median < 60
          ? Math.round(median) + ' min'
          : (median / 60).toFixed(1) + ' h';
      const worst = lags[lags.length - 1];
      $('max').textContent =
        worst > 1440
          ? (worst / 1440).toFixed(1) + ' days'
          : (worst / 60).toFixed(1) + ' h';
    };
    store.on('window', recompute);
    store.on('backfill', recompute);
    recompute();
  });

  global.PulseCards = {
    CARDS,
    define,
    helpers: { nf, usd, usdCompact, el, nearestTown },
  };
})(window);
