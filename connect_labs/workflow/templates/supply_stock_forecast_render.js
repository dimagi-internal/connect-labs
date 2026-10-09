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
  // ══ Stock forecast (workflow/templates/supply_stock_forecast.py) ══
  //
  // Draws ONE supply source, `supply.forecast` (stock_forecast): its single row
  // is the whole forecast, computed on the server -- per worker, per store and
  // for the programme, week by week from `anchor`. Nothing is totalled here.
  // Assumptions are chips. Classes are only those supply_stock_review already
  // ships; colours in the charts are inline. ES5 dialect (Babel transpiles JSX only).
  var cfg = (definition && definition.config) || {};
  var src = (view && view.supply) || supply || {};
  var loaded = src.forecast;

  var sOverride = React.useState(null);
  var override = sOverride[0];
  var setOverride = sOverride[1];
  var sScenario = React.useState(1);
  var scenario = sScenario[0];
  var setScenario = sScenario[1];
  var sBusy = React.useState(false);
  var busy = sBusy[0];
  var setBusy = sBusy[1];
  var sOpen = React.useState(null);
  var open = sOpen[0];
  var setOpen = sOpen[1];
  // The chart is drawn in the card's own pixel width, so its labels stay 12px:
  // a fixed viewBox left half the card empty at 1440 and shrank the text to
  // ~5px on a phone.
  var sChartW = React.useState(0);
  var chartW = sChartW[0];
  var setChartW = sChartW[1];
  var chartBox = React.useRef(null);
  React.useEffect(
    function () {
      setOverride(null);
      setScenario(1);
    },
    [loaded],
  );
  React.useEffect(
    function () {
      var el = chartBox.current;
      if (!el) return undefined;
      function measure() {
        setChartW(el.clientWidth);
      }
      measure();
      if (window.ResizeObserver) {
        var ro = new window.ResizeObserver(measure);
        ro.observe(el);
        return function () {
          ro.disconnect();
        };
      }
      window.addEventListener('resize', measure);
      return function () {
        window.removeEventListener('resize', measure);
      };
    },
    [loaded],
  );

  var COMMITTED = '#4f46e5';
  var NEW = '#f59e0b';
  var PAST = '#9ca3af';
  var STOCK = '#0d9488';
  var DRY = '#dc2626';

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var x = typeof v === 'object' ? v.amount : v;
    if (x === null || x === undefined || x === '') return null;
    var n = Number(x);
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

  var source = override || loaded;
  if (!source) {
    return <div className="p-6 text-sm text-gray-600">Loading forecast…</div>;
  }
  var f = source.rows && source.rows[0];
  var perOpp = (source.metadata && source.metadata.per_opp) || {};
  var errors = Object.keys(perOpp).filter(function (k) {
    return perOpp[k] && perOpp[k].error;
  });
  if (!f) {
    return (
      <div className="p-6 text-sm text-gray-600">
        {errors.length
          ? errors
              .map(function (k) {
                return 'Opportunity ' + k + ': ' + perOpp[k].error;
              })
              .join(' · ')
          : 'No forecast for these opportunities.'}
      </div>
    );
  }

  var unit = f.unit || 'sachet';
  var units = unit + 's';
  var weeks = f.weeks || [];
  var H = weeks.length;
  var programme = f.programme || {};
  var basis = f.basis || {};
  var course = basis.course || {};

  function runScenario(value) {
    setScenario(value);
    if (!actions || !actions.querySupply) return;
    setBusy(true);
    actions
      .querySupply('forecast', {
        args: { scenario: value },
        as_of: f.as_of || undefined,
      })
      .then(function (res) {
        setOverride(res);
        setBusy(false);
      })
      .catch(function () {
        setBusy(false);
      });
  }

  // ---- chips: every assumption, where it came from ----
  var chips = [];
  // A basis in words: "measured, steady" said where the number came from
  // only to whoever wrote the server.
  var BASIS_WORDS = {
    measured: 'from visits',
    steady: 'then held at the last measured week',
    protocol: 'protocol',
    default: 'default',
  };
  if (!f.cases_configured) {
    chips.push('no case rule · each worker’s own pace');
  } else {
    chips.push(
      course.size !== null && course.size !== undefined
        ? 'course ' +
            fmt(num(course.size)) +
            ' ' +
            units +
            ' · ' +
            (BASIS_WORDS[course.basis] || course.basis)
        : 'course unknown',
    );
    var profileBases = [];
    (basis.profile || []).forEach(function (p) {
      var w = BASIS_WORDS[p.basis] || p.basis;
      if (profileBases.indexOf(w) < 0) profileBases.push(w);
    });
    chips.push('weekly ration · ' + profileBases.join(', '));
    chips.push(
      programme.enrolled_per_week !== null &&
        programme.enrolled_per_week !== undefined
        ? 'enrolling ' +
            num(programme.enrolled_per_week).toFixed(1) +
            ' a week · last ' +
            (basis.enrolment_weeks || 3) +
            ' weeks'
        : 'enrolment · not enough history',
    );
    if (programme.workers_without_enrolment_rate)
      chips.push(
        programme.workers_without_enrolment_rate +
          ' workers too new to project',
      );
    var carried = (f.cohorts || []).reduce(function (n, c) {
      return n + (c.carry_over || 0);
    }, 0);
    if (carried)
      chips.push(
        carried +
          (carried === 1 ? ' child' : ' children') +
          ' enrolled before the visits start',
      );
    if ((basis.lost_after_days || []).length)
      chips.push(
        'not seen for ' +
          basis.lost_after_days.join('/') +
          ' days = out of treatment',
      );
  }
  if (f.data_to && f.anchor && (!f.as_of || f.data_to < f.as_of)) {
    chips.push('data to ' + day(f.data_to));
  } else {
    chips.push('from ' + day(f.anchor));
  }
  chips.push(H + ' weeks');

  // ---- programme line: past given out, then committed + new, and stock left ----
  var past = (f.history || []).map(function (h) {
    return num(h.given_out) || 0;
  });
  var future = (programme.weeks || []).map(function (w) {
    return { committed: num(w.committed) || 0, fresh: num(w.new) || 0 };
  });
  var startLeft = num(programme.top_on_hand) || 0;
  var left = (programme.left_at_top || []).map(function (v) {
    return num(v) || 0;
  });
  var top = 1;
  past.forEach(function (v) {
    top = Math.max(top, v);
  });
  future.forEach(function (w) {
    top = Math.max(top, w.committed + w.fresh);
  });
  var stockTop = Math.max(startLeft, 1);
  left.forEach(function (v) {
    stockTop = Math.max(stockTop, v);
  });

  function Chart() {
    var n = past.length + future.length;
    if (!n) return null;
    // Room either side for the scale labels, so no bar runs under them.
    var W = Math.max(chartW || 480, 280),
      Hh = 220,
      P = 44,
      PR = 56,
      B = 20;
    var slot = (W - P - PR) / n;
    var bw = Math.max(Math.min(slot * 0.6, 48), 2);
    function y(v) {
      return Hh - B - (v / top) * (Hh - B - 24);
    }
    function ys(v) {
      return Hh - B - (Math.max(v, 0) / stockTop) * (Hh - B - 24);
    }
    var ticks = [0, top / 2, top];
    var path = '';
    var x0 = P + past.length * slot;
    path += 'M' + x0 + ',' + ys(startLeft);
    left.forEach(function (v, i) {
      path += 'L' + (x0 + (i + 1) * slot) + ',' + ys(v);
    });
    return (
      <svg viewBox={'0 0 ' + W + ' ' + Hh} width={W} height={Hh}>
        {ticks.map(function (t, i) {
          return (
            <g key={'t' + i}>
              <line x1={P} x2={W - PR} y1={y(t)} y2={y(t)} stroke="#f3f4f6" />
              <text x={2} y={y(t) + 3} fontSize="12" fill="#9ca3af">
                {fmt(t)}
              </text>
              <text
                x={W - 2}
                y={y(t) + 3}
                fontSize="12"
                fill={STOCK}
                textAnchor="end"
              >
                {fmt((t / top) * stockTop)}
              </text>
            </g>
          );
        })}
        {past.map(function (v, i) {
          return (
            <rect
              key={'p' + i}
              x={P + i * slot + (slot - bw) / 2}
              y={y(v)}
              width={bw}
              height={Hh - B - y(v)}
              fill={PAST}
            >
              <title>{fmt(v) + ' ' + units + ' given out'}</title>
            </rect>
          );
        })}
        {future.map(function (w, i) {
          var x = P + (past.length + i) * slot + (slot - bw) / 2;
          return (
            <g key={'f' + i}>
              <rect
                x={x}
                y={y(w.committed)}
                width={bw}
                height={Hh - B - y(w.committed)}
                fill={COMMITTED}
              >
                <title>{fmt(w.committed) + ' to children in treatment'}</title>
              </rect>
              <rect
                x={x}
                y={y(w.committed + w.fresh)}
                width={bw}
                height={y(w.committed) - y(w.committed + w.fresh)}
                fill={NEW}
              >
                <title>{fmt(w.fresh) + ' to new children'}</title>
              </rect>
            </g>
          );
        })}
        <line
          x1={x0}
          x2={x0}
          y1={8}
          y2={Hh - B}
          stroke="#d1d5db"
          strokeDasharray="3 3"
        />
        <path d={path} fill="none" stroke={STOCK} strokeWidth="2" />
        <text x={x0 + 4} y={12} fontSize="12" fill="#6b7280">
          {day(f.anchor)}
        </text>
        {(f.history || []).length > 0 && (
          <text x={P} y={Hh - 4} fontSize="12" fill="#6b7280">
            {day(f.history[0].start)}
          </text>
        )}
        {H > 0 && (
          <text
            x={W - PR}
            y={Hh - 4}
            fontSize="12"
            fill="#6b7280"
            textAnchor="end"
          >
            {day(weeks[H - 1].end)}
          </text>
        )}
      </svg>
    );
  }

  function Legend() {
    var items = [
      [PAST, 'given out'],
      [COMMITTED, 'children in treatment'],
      [NEW, 'new children'],
      [STOCK, 'left at ' + topName + ' (right scale)'],
    ];
    return (
      <div className="flex flex-wrap items-center gap-2 mt-1">
        {items.map(function (it) {
          return (
            <span
              key={it[1]}
              className="inline-flex items-center gap-1.5 text-xs text-gray-600"
            >
              <span
                className="inline-block w-2 h-2 rounded-full"
                style={{ background: it[0] }}
              ></span>
              {it[1]}
            </span>
          );
        })}
      </div>
    );
  }

  function WorkerBars(props) {
    var w = props.w;
    var onHand = num(w.on_hand) || 0;
    var needs = (w.weeks || []).map(function (k) {
      return num(k.need) || 0;
    });
    var cum = 0;
    var Wd = 640,
      Hd = 90,
      P = 6;
    var max = Math.max(onHand, 1);
    var running = needs.map(function (v) {
      cum += v;
      max = Math.max(max, cum);
      return cum;
    });
    var slot = (Wd - 2 * P) / Math.max(needs.length, 1);
    function y(v) {
      return Hd - 14 - (v / max) * (Hd - 24);
    }
    return (
      <svg viewBox={'0 0 ' + Wd + ' ' + Hd} className="w-full max-w-2xl h-32">
        {running.map(function (v, i) {
          return (
            <rect
              key={i}
              x={P + i * slot + slot * 0.2}
              y={y(v)}
              width={slot * 0.6}
              height={Hd - 14 - y(v)}
              fill={v > onHand ? DRY : COMMITTED}
            >
              <title>
                {'by ' +
                  day(weeks[i] && weeks[i].end) +
                  ': ' +
                  fmt(v) +
                  ' needed'}
              </title>
            </rect>
          );
        })}
        <line
          x1={P}
          x2={Wd - P}
          y1={y(onHand)}
          y2={y(onHand)}
          stroke={STOCK}
          strokeWidth="2"
        />
        <text x={P} y={y(onHand) - 3} fontSize="10" fill={STOCK}>
          {fmt(onHand) + ' held'}
        </text>
        <text x={P} y={Hd - 2} fontSize="10" fill="#6b7280">
          {day(weeks[0] && weeks[0].start)}
        </text>
        <text
          x={Wd - P}
          y={Hd - 2}
          fontSize="10"
          fill="#6b7280"
          textAnchor="end"
        >
          {day(weeks[H - 1] && weeks[H - 1].end)}
        </text>
      </svg>
    );
  }

  var SCENARIOS = [0.5, 0.75, 1, 1.25, 1.5];
  var cohorts = f.cohorts || [];
  var cohortTop = 1;
  cohorts.forEach(function (c) {
    cohortTop = Math.max(cohortTop, num(c.owed) || 0);
  });
  var stores = f.stores || [];
  var storeById = {};
  stores.forEach(function (s) {
    storeById[s.supply_point_id] = s;
  });
  // The stock line is `left_at_top`: the top store(s) only, not every store.
  var tops = stores.filter(function (s) {
    return !s.parent_supply_point_id;
  });
  var topName = tops.length === 1 ? tops[0].name : 'the top stores';
  // Who a store's demand comes from: its own workers, and the shortfall of
  // any store below it -- "asked for by 20 workers" on the top store was the
  // Partner store's gap passed up, not twenty workers asking.
  function askedBy(s) {
    var direct = (f.workers || []).filter(function (w) {
      return w.parent_supply_point_id === s.supply_point_id;
    }).length;
    var below = stores
      .filter(function (c) {
        return c.parent_supply_point_id === s.supply_point_id;
      })
      .map(function (c) {
        return c.name;
      });
    var parts = [];
    if (below.length) parts.push(below.join(', '));
    if (direct)
      parts.push('its ' + direct + (direct === 1 ? ' worker' : ' workers'));
    return parts.length ? parts.join(' and ') : s.workers + ' workers';
  }
  function shortText(s) {
    var short = num(s.shortfall_by_end);
    if (!short) return 'covered';
    var parent = storeById[s.parent_supply_point_id];
    if (parent && !num(parent.shortfall_by_end))
      return fmt(short) + ' short · ' + parent.name + ' covers it';
    return fmt(short) + ' short';
  }
  var multi =
    ((source.metadata && source.metadata.opportunity_ids) || []).length > 1;

  return (
    <div className="space-y-6">
      {errors.length > 0 && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800">
          {errors.map(function (k) {
            return (
              <div key={k}>{'Opportunity ' + k + ': ' + perOpp[k].error}</div>
            );
          })}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {chips.map(function (c) {
          return (
            <span
              key={c}
              className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs border-gray-300 bg-white text-gray-700"
            >
              {c}
            </span>
          );
        })}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-gray-500">
            In treatment
          </div>
          <div className="text-2xl font-semibold text-gray-900">
            {f.cases_configured ? programme.open_cases || 0 : '—'}
          </div>
          <div className="text-xs text-gray-500">children</div>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-gray-500">
            Still owed to them
          </div>
          <div className="text-2xl font-semibold text-gray-900">
            {fmt(num(programme.owed))}{' '}
            <span className="text-sm font-normal text-gray-500">{units}</span>
          </div>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-gray-500">
            {'Needed, next ' + H + ' weeks'}
          </div>
          <div className="text-2xl font-semibold text-gray-900">
            {fmt(num(programme.need_total))}{' '}
            <span className="text-sm font-normal text-gray-500">{units}</span>
          </div>
          <div className="text-xs text-gray-500">
            {fmt(num(programme.on_hand)) + ' in the network'}
          </div>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-gray-500">
            Network runs dry
          </div>
          <div
            className={
              'text-2xl font-semibold ' +
              (programme.runs_dry_on ? 'text-red-700' : 'text-gray-900')
            }
          >
            {programme.runs_dry_on
              ? day(programme.runs_dry_on)
              : 'not in ' + H + ' weeks'}
          </div>
          <div className="text-xs text-gray-500">
            {num(programme.shortfall_by_end)
              ? fmt(num(programme.shortfall_by_end)) + ' short by the end'
              : 'covered'}
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-gray-200 bg-white p-3">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <span className="text-sm font-semibold text-gray-900">
            Week by week
          </span>
          {f.cases_configured && (
            <span className="inline-flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-gray-500 mr-1">new children</span>
              {SCENARIOS.map(function (s) {
                var on = Math.abs(scenario - s) < 0.001;
                return (
                  <button
                    key={s}
                    type="button"
                    disabled={busy}
                    onClick={function () {
                      runScenario(s);
                    }}
                    className={
                      'inline-flex items-center rounded-full border px-2.5 py-1 text-xs ' +
                      (on
                        ? 'border-gray-900 bg-gray-900 text-white'
                        : 'border-gray-300 bg-white text-gray-700')
                    }
                  >
                    {Math.round(s * 100) + '%'}
                  </button>
                );
              })}
            </span>
          )}
        </div>
        <div ref={chartBox} className="w-full">
          <Chart />
        </div>
        <Legend />
        {(programme.inbound || []).length > 0 && (
          <div className="mt-1 text-xs text-gray-600">
            {'Arriving, and counted in the line: ' +
              programme.inbound
                .filter(function (o) {
                  return o.counted !== false;
                })
                .map(function (o) {
                  var at = storeById[o.supply_point_id];
                  return (
                    o.reference +
                    ' · ' +
                    fmt(num(o.quantity)) +
                    ' ' +
                    units +
                    (at ? ' to ' + at.name : '') +
                    ' · ' +
                    (o.overdue ? 'overdue since ' : 'due ') +
                    day(o.expected_on)
                  );
                })
                .join('; ')}
          </div>
        )}
      </div>

      {f.cases_configured && cohorts.length > 0 && (
        <div>
          <div className="text-sm font-semibold text-gray-900 mb-2">
            Children in treatment, by the week they enrolled
          </div>
          <div className="rounded-lg border border-gray-200 bg-white divide-y divide-gray-100">
            {cohorts.map(function (c) {
              var owed = num(c.owed);
              return (
                <div key={c.week_of} className="px-4 py-2">
                  <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
                    <div className="sm:w-40 shrink-0 text-sm text-gray-900">
                      {'week of ' + day(c.week_of)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="h-3 rounded bg-gray-100 overflow-hidden">
                        <div
                          className="h-3"
                          style={{
                            width: ((owed || 0) / cohortTop) * 100 + '%',
                            background: COMMITTED,
                          }}
                        ></div>
                      </div>
                    </div>
                    <div className="sm:w-48 shrink-0 sm:text-right text-xs text-gray-600 tabular-nums">
                      {c.children +
                        (c.children === 1 ? ' child' : ' children') +
                        (c.carry_over
                          ? ' (' + c.carry_over + ' from before)'
                          : '') +
                        ' · ' +
                        fmt(owed) +
                        ' owed'}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div>
        <div className="text-sm font-semibold text-gray-900 mb-2">
          Workers, soonest dry first{' '}
          <span className="font-normal text-xs text-gray-500">
            on what each holds now, before any resupply
          </span>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white divide-y divide-gray-100">
          {(f.workers || []).map(function (w) {
            var key = w.opportunity_id + ':' + w.supply_point_id;
            var dry = w.runs_dry_on;
            return (
              <div key={key}>
                <button
                  type="button"
                  onClick={function () {
                    setOpen(open === key ? null : key);
                  }}
                  className="w-full text-left px-4 py-2.5 hover:bg-gray-50"
                >
                  <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
                    <div className="sm:w-40 shrink-0">
                      <div className="text-sm font-medium text-gray-900">
                        {w.name}
                      </div>
                      {multi && (
                        <div className="text-xs text-gray-500">
                          {'opportunity ' + w.opportunity_id}
                        </div>
                      )}
                    </div>
                    <div
                      className={
                        'sm:w-40 shrink-0 text-sm font-medium ' +
                        (dry ? 'text-red-700' : 'text-gray-900')
                      }
                    >
                      {!w.stock_known
                        ? 'stock unknown'
                        : dry
                          ? 'dry ' + day(dry)
                          : 'not in ' + H + ' weeks'}
                    </div>
                    <div className="flex-1 min-w-0 text-xs text-gray-600 tabular-nums">
                      {fmt(num(w.on_hand)) +
                        ' held · ' +
                        fmt(num(w.need_total)) +
                        ' needed' +
                        (w.basis === 'cases'
                          ? ' · ' +
                            w.open_cases +
                            ' in treatment · ' +
                            fmt(num(w.owed)) +
                            ' owed · ' +
                            (w.enrolled_per_week !== null
                              ? num(w.enrolled_per_week).toFixed(1) +
                                ' new a week'
                              : 'enrolment unknown')
                          : ' · own pace')}
                    </div>
                  </div>
                </button>
                {open === key && (
                  <div className="px-4 pb-3 bg-gray-50">
                    <WorkerBars w={w} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {stores.length > 0 && (
        <div>
          <div className="text-sm font-semibold text-gray-900 mb-2">
            Stores{' '}
            <span className="font-normal text-xs text-gray-500">
              covering what their workers, or the store below, cannot
            </span>
          </div>
          <div className="rounded-lg border border-gray-200 bg-white divide-y divide-gray-100">
            {stores.map(function (s) {
              var demand = s.demand_from_below || [];
              return (
                <div
                  key={s.supply_point_id}
                  className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3 px-4 py-2 text-sm"
                >
                  <div className="sm:w-40 shrink-0 text-gray-900">{s.name}</div>
                  <div
                    className={
                      'sm:w-40 shrink-0 font-medium ' +
                      (s.runs_dry_on ? 'text-red-700' : 'text-gray-900')
                    }
                  >
                    {s.runs_dry_on
                      ? 'dry ' + day(s.runs_dry_on)
                      : 'not in ' + H + ' weeks'}
                  </div>
                  <div className="flex-1 min-w-0 text-xs text-gray-600 tabular-nums">
                    {fmt(num(s.own_on_hand)) +
                      ' held · ' +
                      fmt(num(demand[demand.length - 1])) +
                      ' asked for by ' +
                      askedBy(s) +
                      ' · ' +
                      shortText(s)}
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
