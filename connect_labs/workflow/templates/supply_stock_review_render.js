function WorkflowUI({
  definition,
  instance,
  workers,
  pipelines,
  supply,
  links,
  actions,
  onUpdateState,
  view,
}) {
  // ══ Stock in field workers' hands (workflow/templates/supply_stock_review.py) ══
  //
  // Reads supply sources (workflow/supply_sources.py), never computes a total of
  // its own: `supply.stock.rows` is one row per worker (issued, given out,
  // counted, on hand, pace, days left), `supply.stock.rollup` the server's totals
  // (counts summed, rates recomputed), `supply.stores.rows` the stores. Sachets
  // lead. Workers are listed soonest-to-run-out first: a date, not a verdict.
  // ES5 dialect (Babel transpiles JSX only).
  var cfg = (definition && definition.config) || {};
  var src = (view && view.supply) || supply || {};
  var stock = src.stock;
  var stores = src.stores;
  var courseSize = Number(cfg.course_size) || 0;
  var courseLabel = cfg.course_label || 'courses';

  var sFilter = React.useState(null);
  var filter = sFilter[0];
  var setFilter = sFilter[1];
  var sOpen = React.useState(null);
  var open = sOpen[0];
  var setOpen = sOpen[1];
  var sLine = React.useState({});
  var lines = sLine[0];
  var setLines = sLine[1];

  function amount(cell) {
    if (cell === null || cell === undefined || cell === '') return null;
    var v = typeof cell === 'object' ? cell.amount : cell;
    if (v === null || v === undefined || v === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }
  function fmt(n) {
    if (n === null || n === undefined) return '—';
    return Math.round(n).toLocaleString();
  }
  function day(iso) {
    if (!iso) return '';
    var d = new Date(String(iso).slice(0, 10) + 'T00:00:00');
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }
  // A run-out date counts from the day being viewed: on a past date, days
  // left were measured then, so adding them to today moved every date forward.
  var asOf = (stock && stock.metadata && stock.metadata.as_of) || null;
  function addDays(n) {
    var d = asOf
      ? new Date(String(asOf).slice(0, 10) + 'T00:00:00')
      : new Date();
    d.setDate(d.getDate() + Math.floor(n));
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }
  // Whole days still in hand, never rounded up: the count Workers, a worker's
  // page and Stock show (days_text). Rounded here, 9.85 read "10 days left"
  // beside "9 days" there, and 6.6 read "7 days" under "Under a week".
  function daysLeft(n) {
    if (n < 1) return 'under a day left';
    var whole = Math.floor(n);
    return whole + (whole === 1 ? ' day left' : ' days left');
  }

  if (!stock) {
    var err = src && src.stock === undefined;
    return (
      <div className="p-6 text-sm text-gray-600">
        {err ? 'Loading stock…' : 'No stock source on this workflow.'}
      </div>
    );
  }

  var unit = (stock.rows[0] && stock.rows[0].unit) || 'sachet';
  var units = unit + 's';
  var rollup = stock.rollup || {};
  var totals = (rollup.by_unit || {})[unit] || {};
  var perOpp = (stock.metadata && stock.metadata.per_opp) || {};
  var oppErrors = Object.keys(perOpp).filter(function (k) {
    return perOpp[k] && perOpp[k].error;
  });
  var multi =
    ((stock.metadata && stock.metadata.opportunity_ids) || []).length > 1;

  // ---- per worker, read once ----
  var BANDS = [
    ['out', 'Out of stock', 'bg-red-600'],
    ['under_week', 'Under a week', 'bg-orange-500'],
    ['one_to_three_weeks', '1–3 weeks', 'bg-amber-400'],
    ['three_to_six_weeks', '3–6 weeks', 'bg-teal-500'],
    ['over_six_weeks', 'Over 6 weeks', 'bg-sky-500'],
    // No days left to show: too few days given out to measure a rate (a new
    // worker), or none given out at all. Each row says which.
    ['not_yet', 'No run-out date yet', 'bg-gray-300'],
  ];
  function bandOf(r) {
    var onHand = amount(r.on_hand);
    if (onHand !== null && onHand <= 0) return 'out';
    var d = amount(r.days_to_stockout);
    if (d === null) return 'not_yet';
    if (d < 7) return 'under_week';
    if (d < 21) return 'one_to_three_weeks';
    if (d < 42) return 'three_to_six_weeks';
    return 'over_six_weeks';
  }
  var rows = stock.rows.map(function (r) {
    var amc = amount(r.amc);
    return {
      raw: r,
      key: r.opportunity_id + ':' + r.supply_point_id,
      name: r.name,
      opp: r.opportunity_id,
      onHand: amount(r.on_hand),
      issued: amount(r.issued),
      given: amount(r.dispensed),
      unapproved: amount(r.unapproved),
      counted: amount(r.reported),
      countedOn: r.reported_on,
      ledgerOnCount: amount(r.ledger_on_count_day),
      gap: amount(r.variance_reported_minus_ledger),
      noAnswer: r.no_answer_visits || 0,
      perDay: amc !== null ? amc / 30 : amount(r.rate_per_day_so_far),
      rateDays: r.rate_days,
      rateWindow: r.rate_window_days,
      days: amount(r.days_to_stockout),
      band: bandOf(r),
    };
  });
  rows.sort(function (a, b) {
    var da = a.band === 'out' ? -1 : a.days === null ? 1e9 : a.days;
    var db = b.band === 'out' ? -1 : b.days === null ? 1e9 : b.days;
    return da - db || String(a.name).localeCompare(String(b.name));
  });
  var shown = filter
    ? rows.filter(function (r) {
        return r.band === filter;
      })
    : rows;
  var perDayTotal = rows.reduce(function (s, r) {
    return s + (r.perDay || 0);
  }, 0);
  // The window every worker's pace is averaged over (resupply.window_for), as the
  // supply pages say it: one pace per worker, everywhere (#2342).
  var paceWindow =
    rows.reduce(function (w, r) {
      return Math.max(w, r.rateWindow || 0);
    }, 0) || 14;
  var maxDays = 60;

  function openWorker(r) {
    if (open === r.key) return setOpen(null);
    setOpen(r.key);
    if (lines[r.key] || !actions || !actions.querySupply) return;
    actions
      .querySupply('worker', {
        args: { supply_point_id: r.raw.supply_point_id },
        opportunity_id: r.opp,
      })
      .then(function (res) {
        var one = res.rows && res.rows[0];
        setLines(function (prev) {
          var next = Object.assign({}, prev);
          next[r.key] = (one && one.timeline) || { days: [], counts: [] };
          return next;
        });
      })
      .catch(function (e) {
        setLines(function (prev) {
          var next = Object.assign({}, prev);
          next[r.key] = { error: String(e.message || e) };
          return next;
        });
      });
  }

  function Spark(props) {
    var line = props.line;
    if (!line)
      return <div className="text-xs text-gray-500 py-2">Loading…</div>;
    if (line.error)
      return <div className="text-xs text-red-700 py-2">{line.error}</div>;
    var days = line.days || [];
    if (!days.length)
      return (
        <div className="text-xs text-gray-500 py-2">Nothing recorded yet.</div>
      );
    var W = 640,
      H = 120,
      P = 6;
    var t0 = new Date(days[0].on).getTime();
    var t1 = new Date(days[days.length - 1].on).getTime();
    (line.counts || []).forEach(function (c) {
      var t = new Date(c.on).getTime();
      if (t > t1) t1 = t;
    });
    var span = Math.max(t1 - t0, 86400000);
    var top = 1;
    days.forEach(function (d) {
      top = Math.max(top, Number(d.balance), Number(d.before));
    });
    (line.counts || []).forEach(function (c) {
      top = Math.max(top, Number(c.quantity));
    });
    function x(iso) {
      return P + ((new Date(iso).getTime() - t0) / span) * (W - 2 * P);
    }
    function y(v) {
      return H - P - (Number(v) / top) * (H - 2 * P);
    }
    var path = '';
    days.forEach(function (d, i) {
      path +=
        (i ? 'L' : 'M') +
        x(d.on) +
        ',' +
        y(d.before) +
        'L' +
        x(d.on) +
        ',' +
        y(d.balance);
    });
    return (
      <svg
        viewBox={'0 0 ' + W + ' ' + (H + 14)}
        className="w-full max-w-2xl h-32"
      >
        <text x={P} y={10} fontSize="10" fill="#6b7280">
          {fmt(top) + ' ' + units}
        </text>
        <text x={P} y={H + 12} fontSize="10" fill="#6b7280">
          {day(days[0].on)}
        </text>
        <text
          x={W - P}
          y={H + 12}
          fontSize="10"
          fill="#6b7280"
          textAnchor="end"
        >
          {day(new Date(t1).toISOString())}
        </text>
        <line x1={P} x2={W - P} y1={H - P} y2={H - P} stroke="#e5e7eb" />
        <path d={path} fill="none" stroke="#4f46e5" strokeWidth="2" />
        {(line.counts || []).map(function (c, i) {
          return (
            <circle
              key={i}
              cx={x(c.on)}
              cy={y(c.quantity)}
              r="4"
              fill="#f59e0b"
            >
              <title>
                {'Counted ' + fmt(Number(c.quantity)) + ' on ' + day(c.on)}
              </title>
            </circle>
          );
        })}
      </svg>
    );
  }

  var storeRows = ((stores && stores.rows) || []).filter(function (s) {
    return s.kind !== 'user_held';
  });

  return (
    <div className="space-y-6">
      {oppErrors.length > 0 && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800">
          {oppErrors.map(function (k) {
            return (
              <div key={k}>{'Opportunity ' + k + ': ' + perOpp[k].error}</div>
            );
          })}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-gray-500">
            With workers
          </div>
          <div className="text-2xl font-semibold text-gray-900">
            {fmt(totals.on_hand)}{' '}
            <span className="text-sm font-normal text-gray-500">{units}</span>
          </div>
          {courseSize > 0 && totals.on_hand !== undefined && (
            <div className="text-xs text-gray-500">
              {'≈ ' + fmt(totals.on_hand / courseSize) + ' ' + courseLabel}
            </div>
          )}
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-gray-500">
            Given out at visits
          </div>
          <div className="text-2xl font-semibold text-gray-900">
            {fmt(totals.dispensed)}{' '}
            <span className="text-sm font-normal text-gray-500">{units}</span>
          </div>
          {totals.unapproved_share !== null &&
            totals.unapproved_share !== undefined && (
              <div className="text-xs text-gray-500">
                {(totals.unapproved_share > 0 && totals.unapproved_share < 0.5
                  ? 'under 1'
                  : Math.round(totals.unapproved_share)) +
                  '% on visits not yet approved'}
              </div>
            )}
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-gray-500">
            Going out, a day
          </div>
          <div className="text-2xl font-semibold text-gray-900">
            {fmt(perDayTotal)}{' '}
            <span className="text-sm font-normal text-gray-500">{units}</span>
          </div>
          <div className="text-xs text-gray-500">
            {"each worker's last " + paceWindow + ' days, added'}
          </div>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-gray-500">
            Workers
          </div>
          <div className="text-2xl font-semibold text-gray-900">
            {rollup.workers || 0}
          </div>
          <div className="text-xs text-gray-500">
            {(rollup.never_counted || 0) +
              ' never counted · ' +
              (rollup.no_answer_visits || 0) +
              ' visits did not say'}
          </div>
        </div>
      </div>

      <div>
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <span className="text-sm font-semibold text-gray-900 mr-1">
            How long it lasts
          </span>
          {BANDS.map(function (b) {
            var n = (rollup.runway || {})[b[0]] || 0;
            if (!n) return null;
            var on = filter === b[0];
            return (
              <button
                key={b[0]}
                type="button"
                onClick={function () {
                  setFilter(on ? null : b[0]);
                }}
                className={
                  'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ' +
                  (on
                    ? 'border-gray-900 bg-gray-900 text-white'
                    : 'border-gray-300 bg-white text-gray-700')
                }
              >
                <span
                  className={'inline-block w-2 h-2 rounded-full ' + b[2]}
                ></span>
                {b[1] + ' · ' + n}
              </button>
            );
          })}
          {filter && (
            <button
              type="button"
              className="text-xs text-brand-indigo underline"
              onClick={function () {
                setFilter(null);
              }}
            >
              show all
            </button>
          )}
        </div>
        <div className="rounded-lg border border-gray-200 bg-white divide-y divide-gray-100">
          {shown.map(function (r) {
            var band = BANDS.filter(function (b) {
              return b[0] === r.band;
            })[0];
            var width =
              r.band === 'out'
                ? 0
                : r.days === null
                  ? 0
                  : (Math.min(r.days, maxDays) / maxDays) * 100;
            var early =
              r.rateDays !== null && r.rateDays !== undefined && r.rateDays < 7;
            return (
              <div key={r.key}>
                <button
                  type="button"
                  onClick={function () {
                    openWorker(r);
                  }}
                  className="w-full text-left px-4 py-2.5 hover:bg-gray-50"
                >
                  <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
                    <div className="sm:w-40 shrink-0">
                      <div className="text-sm font-medium text-gray-900">
                        {r.name}
                      </div>
                      {multi && (
                        <div className="text-xs text-gray-500">
                          {'opportunity ' + r.opp}
                        </div>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="h-3 rounded bg-gray-100 overflow-hidden">
                        <div
                          className={'h-3 ' + band[2]}
                          style={{ width: width + '%' }}
                        ></div>
                      </div>
                      <div className="mt-1 text-xs text-gray-600">
                        {fmt(r.onHand) + ' ' + units + ' held'}
                        {r.band === 'out'
                          ? ' · out of stock'
                          : r.days !== null
                            ? ' · ' +
                              daysLeft(r.days) +
                              ', runs out ' +
                              addDays(r.days)
                            : r.rateDays
                              ? ' · given out on ' +
                                r.rateDays +
                                (r.rateDays === 1 ? ' day' : ' days') +
                                ' so far, ' +
                                (r.raw.rate_days_needed || 7) +
                                ' needed for a run-out date'
                              : ' · nothing given out lately, so no run-out date'}
                        {r.perDay !== null && (
                          <span className={early ? 'text-gray-400' : ''}>
                            {' · ' +
                              (r.perDay < 1
                                ? r.perDay.toFixed(1)
                                : fmt(r.perDay)) +
                              ' a day' +
                              (r.rateDays
                                ? ' (over ' + r.rateDays + ' days)'
                                : ' so far')}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="sm:w-48 shrink-0 sm:text-right text-xs text-gray-600">
                      {r.counted !== null ? (
                        <span>
                          {'counted ' +
                            fmt(r.counted) +
                            ' on ' +
                            day(r.countedOn)}
                          {r.gap !== null && r.gap !== 0 && (
                            <span
                              className={
                                r.gap < 0
                                  ? 'text-red-700 font-medium'
                                  : 'text-gray-700 font-medium'
                              }
                            >
                              {' (' + (r.gap > 0 ? '+' : '') + fmt(r.gap) + ')'}
                            </span>
                          )}
                        </span>
                      ) : (
                        <span className="text-gray-400">never counted</span>
                      )}
                    </div>
                  </div>
                </button>
                {open === r.key && (
                  <div className="px-4 pb-3 bg-gray-50">
                    <div className="text-xs text-gray-600 pt-2">
                      Stock held, day by day (deliveries step it up, visits step
                      it down); orange dots are their counts.
                    </div>
                    <Spark line={lines[r.key]} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <div className="text-sm font-semibold text-gray-900 mb-2">
          Does it add up?
        </div>
        {/* On a phone, one card per worker with the count and its gap first:
            the table scrolled sideways and hid both off the right edge (#2350
            made the supply pages' own tables cards; this one is the review's). */}
        {/* lg, not sm: render code is never scanned by Tailwind, and the built
            stylesheet has lg:hidden / lg:block but no sm:hidden -- with it the
            cards showed beside the table on a desktop. */}
        <div className="lg:hidden rounded-lg border border-gray-200 bg-white divide-y divide-gray-100">
          {rows
            .slice()
            .sort(function (a, b) {
              return String(a.name).localeCompare(String(b.name));
            })
            .map(function (r) {
              return (
                <div key={r.key} className="px-3 py-2 text-sm">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium text-gray-900">{r.name}</span>
                    <span
                      className={
                        'tabular-nums ' +
                        (r.gap
                          ? r.gap < 0
                            ? 'text-red-700 font-medium'
                            : 'text-gray-900 font-medium'
                          : 'text-gray-500')
                      }
                    >
                      {r.gap !== null
                        ? 'gap ' + (r.gap > 0 ? '+' : '') + fmt(r.gap)
                        : 'never counted'}
                    </span>
                  </div>
                  {r.counted !== null && (
                    <div className="text-xs text-gray-700 tabular-nums">
                      {'counted ' +
                        fmt(r.counted) +
                        (r.countedOn ? ' on ' + day(r.countedOn) : '') +
                        ' · ledger that day ' +
                        fmt(r.ledgerOnCount)}
                    </div>
                  )}
                  <div className="text-xs text-gray-500 tabular-nums">
                    {'issued ' +
                      fmt(r.issued) +
                      ' · given out ' +
                      fmt(r.given) +
                      ' · held ' +
                      fmt(r.onHand)}
                    {r.noAnswer
                      ? ' · ' + r.noAnswer + " visits didn't say"
                      : ''}
                    {r.given && r.unapproved
                      ? ' · ' +
                        Math.round(((r.unapproved || 0) / r.given) * 100) +
                        '% on unapproved visits'
                      : ''}
                  </div>
                </div>
              );
            })}
        </div>
        <div className="hidden lg:block overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="text-left px-3 py-2">Worker</th>
                <th className="text-right px-3 py-2">Issued</th>
                <th className="text-right px-3 py-2">Given out</th>
                <th className="text-right px-3 py-2">Ledger says held</th>
                <th className="text-right px-3 py-2">They counted</th>
                <th className="text-right px-3 py-2">Ledger that day</th>
                <th className="text-right px-3 py-2">Gap</th>
                <th className="text-right px-3 py-2">Visits that didn't say</th>
                <th className="text-right px-3 py-2">On unapproved visits</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows
                .slice()
                .sort(function (a, b) {
                  return String(a.name).localeCompare(String(b.name));
                })
                .map(function (r) {
                  return (
                    <tr key={r.key}>
                      <td className="px-3 py-1.5 text-gray-900">{r.name}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {fmt(r.issued)}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {fmt(r.given)}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {fmt(r.onHand)}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {r.counted !== null ? fmt(r.counted) : '—'}
                        {r.counted !== null && r.countedOn && (
                          <div className="text-xs text-gray-500">
                            {day(r.countedOn)}
                          </div>
                        )}
                      </td>
                      {/* The gap is against the ledger on the count day, not
                          the held figure beside it: a delivery after the
                          count (28 Sep, counted 27 Sep) read 269 held · 97
                          counted · gap 0 -- a row that did not add up. */}
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {r.ledgerOnCount !== null ? fmt(r.ledgerOnCount) : '—'}
                      </td>
                      <td
                        className={
                          'px-3 py-1.5 text-right tabular-nums ' +
                          (r.gap
                            ? r.gap < 0
                              ? 'text-red-700'
                              : 'text-gray-900'
                            : 'text-gray-400')
                        }
                      >
                        {r.gap !== null
                          ? (r.gap > 0 ? '+' : '') + fmt(r.gap)
                          : '—'}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {r.noAnswer || '—'}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {r.given
                          ? Math.round(((r.unapproved || 0) / r.given) * 100) +
                            '%'
                          : '—'}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
        <div className="text-xs text-gray-500 mt-1">
          Gap is what they counted minus the ledger that day: negative means
          fewer in the bag than the records say.
        </div>
      </div>

      {storeRows.length > 0 && (
        <div>
          <div className="text-sm font-semibold text-gray-900 mb-2">
            Behind them: the stores
          </div>
          <div className="rounded-lg border border-gray-200 bg-white divide-y divide-gray-100">
            {storeRows.map(function (s) {
              var base = amount(s.on_hand_in_base);
              var packs = amount(s.on_hand);
              var packUnit = s.on_hand && s.on_hand.unit;
              return (
                <div
                  key={s.program_id + ':' + s.supply_point_id}
                  className="flex items-center justify-between px-4 py-2 text-sm"
                >
                  <div className="text-gray-900">
                    {s.name}
                    <span className="text-xs text-gray-500">
                      {' · ' + String(s.kind || '').replace(/_/g, ' ')}
                    </span>
                  </div>
                  <div className="text-gray-700 tabular-nums">
                    {base !== null
                      ? fmt(base) + ' ' + units
                      : fmt(packs) + ' ' + (packUnit || '')}
                    {base !== null &&
                    packs !== null &&
                    packUnit &&
                    packUnit !== unit ? (
                      <span className="text-xs text-gray-500">
                        {' · ' + fmt(packs) + ' ' + packUnit + 's'}
                      </span>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
