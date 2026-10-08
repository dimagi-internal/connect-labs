// MUAC/Age Plausibility -- render code (transpiled by Babel in the browser).
//
// Layout of this file:
//   1. Pure calculation functions (mp*) -- no React, no DOM. Every number and
//      colour on the page comes from these; __tests__/muac_plausibility_render.test.mjs
//      runs them on fixtures.
//   2. Small presentational components, declared at the TOP LEVEL so React does
//      not remount them (and drop their state) on every render.
//   3. WorkflowUI: reads the newest saved run, applies the filters, renders.
//
// Data (see muac_plausibility.py): view.state.muac_plausibility.cells is a
// {columns, rows} table of counts per (opportunity, ward, FLW, week, age band).
// The readings were classified on the server (workflow/muac_plausibility_compute.py);
// this file only adds counts up and tests them.

// ===========================================================================
// 1. Calculation
// ===========================================================================

var MP_DEFAULTS = {
  minN: 20,
  baselineMode: 'observed', // or 'fixed'
  baselineFixed: 0.003,
  fixedPctBands: [0.01, 0.05],
};

var MP_Z_ONE_SIDED_95 = 1.645;

var MP_COUNTERS = [
  'records',
  'missing_age',
  'missing_muac',
  'out_of_age_range',
  'valid',
  'unit_error',
  'tier_a_low',
  'tier_a_high',
  'tier_a_high_flat',
  'tier_b_assessed',
  'tier_b',
];

var MP_AGE_BANDS = ['6-11', '12-23', '24-35', '36-47', '48-59'];

function mpConfig(definition) {
  var c = (definition && definition.config) || {};
  return {
    minN: Number(c.min_n) > 0 ? Number(c.min_n) : MP_DEFAULTS.minN,
    baselineMode:
      c.baseline_mode === 'fixed' ? 'fixed' : MP_DEFAULTS.baselineMode,
    baselineFixed:
      Number(c.baseline_fixed) > 0
        ? Number(c.baseline_fixed)
        : MP_DEFAULTS.baselineFixed,
    fixedPctBands:
      Array.isArray(c.fixed_pct_bands) && c.fixed_pct_bands.length === 2
        ? c.fixed_pct_bands.map(Number)
        : MP_DEFAULTS.fixedPctBands,
    lloByOpportunity: c.llo_by_opportunity || {},
  };
}

// The {columns, rows} table as objects.
function mpCells(state) {
  var table = state && state.cells;
  if (!table || !Array.isArray(table.columns) || !Array.isArray(table.rows))
    return [];
  var cols = table.columns;
  return table.rows.map(function (r) {
    var o = {};
    for (var i = 0; i < cols.length; i++) o[cols[i]] = r[i];
    return o;
  });
}

function mpLlo(cfg, oppId) {
  var name = cfg.lloByOpportunity[String(oppId)];
  return name || 'Opportunity ' + oppId;
}

function mpZero() {
  var o = {};
  MP_COUNTERS.forEach(function (k) {
    o[k] = 0;
  });
  return o;
}

function mpAdd(into, cell) {
  MP_COUNTERS.forEach(function (k) {
    into[k] += Number(cell[k]) || 0;
  });
  return into;
}

function mpSum(cells) {
  return cells.reduce(mpAdd, mpZero());
}

// Readings above the chosen ceiling. The floor is the same in both modes.
function mpTierAHigh(counts, flat) {
  return flat ? counts.tier_a_high_flat : counts.tier_a_high;
}

// Tier A count: below the floor plus above the chosen ceiling.
function mpTierA(counts, flat) {
  return counts.tier_a_low + mpTierAHigh(counts, flat);
}

function mpRate(num, den) {
  return den > 0 ? num / den : null;
}

// One-sided 95% upper bound on a unit's rate if its true rate were p.
function mpUpperBound(p, n) {
  if (!(n > 0)) return null;
  return p + MP_Z_ONE_SIDED_95 * Math.sqrt((p * (1 - p)) / n);
}

// 'grey' | 'green' | 'yellow' | 'red', and whether the unit is statistically
// elevated. method 'stat' is the audit framework's test; 'fixed' the flat bands.
function mpColour(num, n, p, cfg, method) {
  var rate = mpRate(num, n);
  var ub = mpUpperBound(p, n);
  var elevated = rate !== null && ub !== null && n >= cfg.minN && rate > ub;
  if (rate === null || n < cfg.minN)
    return { colour: 'grey', elevated: false, rate: rate, ub: ub };
  var colour;
  if (method === 'fixed') {
    colour =
      rate <= cfg.fixedPctBands[0]
        ? 'green'
        : rate <= cfg.fixedPctBands[1]
          ? 'yellow'
          : 'red';
  } else {
    colour = rate <= p ? 'green' : elevated ? 'red' : 'yellow';
  }
  return { colour: colour, elevated: elevated, rate: rate, ub: ub };
}

// Weeks, LLO, ward and age band. Any filter left '' does not filter.
function mpFilter(cells, f, cfg) {
  return cells.filter(function (c) {
    if (f.weekFrom && c.week < f.weekFrom) return false;
    if (f.weekTo && c.week > f.weekTo) return false;
    if (f.ageBand && c.age_band !== f.ageBand) return false;
    if (f.llo && mpLlo(cfg, c.opportunity_id) !== f.llo) return false;
    if (f.ward && (c.ward || '') !== f.ward) return false;
    return true;
  });
}

// The baseline p: the program-wide Tier A rate over the selected weeks and ages
// -- NOT narrowed by LLO or ward, or a unit would be compared with itself.
function mpBaseline(cells, f, cfg, flat) {
  if (cfg.baselineMode === 'fixed') return cfg.baselineFixed;
  var scope = mpFilter(
    cells,
    { weekFrom: f.weekFrom, weekTo: f.weekTo, ageBand: f.ageBand },
    cfg,
  );
  var t = mpSum(scope);
  var r = mpRate(mpTierA(t, flat), t.valid);
  return r === null ? cfg.baselineFixed : r;
}

// Units at one level: 'llo', 'ward' or 'flw'. An FLW is one person within an
// LLO, added up across wards and the LLO's opportunities.
function mpUnits(cells, level, cfg, names) {
  var byKey = {};
  cells.forEach(function (c) {
    var llo = mpLlo(cfg, c.opportunity_id);
    var key, label, parent;
    if (level === 'llo') {
      key = llo;
      label = llo;
      parent = '';
    } else if (level === 'ward') {
      key = llo + '|' + (c.ward || '');
      label = c.ward || '(no ward)';
      parent = llo;
    } else {
      key = llo + '|' + c.username;
      var byOpp = (names && names[String(c.opportunity_id)]) || {};
      label = byOpp[c.username] || c.username;
      parent = llo;
    }
    var u = byKey[key];
    if (!u) {
      u = byKey[key] = {
        key: key,
        label: label,
        parent: parent,
        llo: llo,
        ward: level === 'ward' ? c.ward || '' : '',
        username: level === 'flw' ? c.username : '',
        wards: {},
        counts: mpZero(),
      };
    }
    if (c.ward) u.wards[c.ward] = true;
    mpAdd(u.counts, c);
  });
  return Object.keys(byKey).map(function (k) {
    return byKey[k];
  });
}

// Score and sort. sortBy 'flagged' (default): statistically elevated first, then
// highest Tier A rate. 'total': most implausible readings first, whatever the rate.
function mpScore(units, p, cfg, method, flat, sortBy) {
  var scored = units.map(function (u) {
    var num = mpTierA(u.counts, flat);
    var res = mpColour(num, u.counts.valid, p, cfg, method);
    return Object.assign({}, u, {
      tierA: num,
      tooLow: u.counts.tier_a_low,
      tooHigh: mpTierAHigh(u.counts, flat),
      n: u.counts.valid,
      rate: res.rate,
      ub: res.ub,
      colour: res.colour,
      elevated: res.elevated,
    });
  });
  scored.sort(function (a, b) {
    if (sortBy === 'total' && b.tierA !== a.tierA) return b.tierA - a.tierA;
    if (a.elevated !== b.elevated) return a.elevated ? -1 : 1;
    var ra = a.rate === null ? -1 : a.rate;
    var rb = b.rate === null ? -1 : b.rate;
    if (rb !== ra) return rb - ra;
    return b.n - a.n;
  });
  return scored;
}

function mpWeekly(cells, flat) {
  var byWeek = {};
  cells.forEach(function (c) {
    byWeek[c.week] = mpAdd(byWeek[c.week] || mpZero(), c);
  });
  return Object.keys(byWeek)
    .sort()
    .map(function (w) {
      var t = byWeek[w];
      var num = mpTierA(t, flat);
      return {
        week: w,
        num: num,
        low: t.tier_a_low,
        high: mpTierAHigh(t, flat),
        n: t.valid,
        rate: mpRate(num, t.valid),
      };
    });
}

function mpUnique(values) {
  var seen = {};
  var out = [];
  values.forEach(function (v) {
    if (v !== '' && v !== null && v !== undefined && !seen[v]) {
      seen[v] = true;
      out.push(v);
    }
  });
  return out.sort();
}

function mpPct(rate, digits) {
  if (rate === null || rate === undefined) return '—';
  return (rate * 100).toFixed(digits === undefined ? 1 : digits) + '%';
}

// ===========================================================================
// 2. Components
// ===========================================================================

var MP_COLOURS = {
  green: { bg: '#dcfce7', fg: '#166534', label: 'At or below baseline' },
  yellow: {
    bg: '#fef9c3',
    fg: '#854d0e',
    label: 'Above baseline, within noise',
  },
  red: { bg: '#fee2e2', fg: '#991b1b', label: 'Statistically elevated' },
  grey: { bg: '#f3f4f6', fg: '#4b5563', label: 'Too few readings to test' },
};

function MpChip(props) {
  var c = MP_COLOURS[props.colour] || MP_COLOURS.grey;
  return (
    <span
      className="inline-block rounded px-2 py-0.5 text-xs font-medium"
      style={{ background: c.bg, color: c.fg }}
    >
      {props.children}
    </span>
  );
}

function MpTile(props) {
  return (
    <div className="border rounded-lg p-3 bg-white">
      <div className="text-xs text-gray-500">{props.label}</div>
      <div className="text-2xl font-semibold mt-1">{props.value}</div>
      <div className="text-xs text-gray-500 mt-1">{props.sub}</div>
    </div>
  );
}

function MpSelect(props) {
  return (
    <label className="text-xs text-gray-600 flex flex-col gap-1">
      {props.label}
      <select
        className="border rounded px-2 py-1 text-sm bg-white"
        value={props.value}
        onChange={function (e) {
          props.onChange(e.target.value);
        }}
      >
        {props.allLabel !== undefined && (
          <option value="">{props.allLabel}</option>
        )}
        {props.options.map(function (o) {
          var value = typeof o === 'object' ? o.value : o;
          var label = typeof o === 'object' ? o.label : o;
          return (
            <option key={value} value={value}>
              {label}
            </option>
          );
        })}
      </select>
    </label>
  );
}

function MpToggle(props) {
  return (
    <div className="text-xs text-gray-600 flex flex-col gap-1">
      {props.label}
      <div className="inline-flex border rounded overflow-hidden">
        {props.options.map(function (o) {
          var on = o.value === props.value;
          return (
            <button
              key={o.value}
              type="button"
              className="px-2 py-1 text-sm"
              style={{
                background: on ? '#1f2937' : '#ffffff',
                color: on ? '#ffffff' : '#374151',
              }}
              onClick={function () {
                props.onChange(o.value);
              }}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function MpUnitTable(props) {
  var level = props.level;
  var showParent = level !== 'llo';
  return (
    <div className="overflow-x-auto border rounded-lg">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-3 py-2 text-left font-semibold">
              {level === 'llo' ? 'LLO' : level === 'ward' ? 'Ward' : 'FLW'}
            </th>
            {showParent && (
              <th className="px-3 py-2 text-left font-semibold">LLO</th>
            )}
            {level === 'flw' && (
              <th className="px-3 py-2 text-left font-semibold">Ward(s)</th>
            )}
            <th
              className="px-3 py-2 text-right font-semibold"
              title={'Below the ' + props.floorCm + ' cm floor'}
            >
              Too low
            </th>
            <th
              className="px-3 py-2 text-right font-semibold"
              title="Above the ceiling for the child's age and sex"
            >
              Too high
            </th>
            <th className="px-3 py-2 text-right font-semibold">
              Implausible (total)
            </th>
            <th className="px-3 py-2 text-right font-semibold">
              Valid readings
            </th>
            <th className="px-3 py-2 text-right font-semibold">
              % implausible
            </th>
            <th className="px-3 py-2 text-right font-semibold">Upper bound</th>
            <th className="px-3 py-2 text-left font-semibold">Elevated?</th>
            <th className="px-3 py-2 text-right font-semibold text-gray-500">
              Below WHO -2SD
            </th>
            <th className="px-3 py-2 text-right font-semibold text-gray-500">
              mm/cm mix-ups
            </th>
            <th className="px-3 py-2 text-right font-semibold text-gray-500">
              Excluded
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {props.units.map(function (u) {
            var c = u.counts;
            var excluded = c.missing_age + c.missing_muac;
            return (
              <tr
                key={u.key}
                className={
                  props.onPick ? 'hover:bg-gray-50 cursor-pointer' : ''
                }
                onClick={function () {
                  if (props.onPick) props.onPick(u);
                }}
              >
                <td className="px-3 py-2">{u.label}</td>
                {showParent && <td className="px-3 py-2">{u.parent}</td>}
                {level === 'flw' && (
                  <td className="px-3 py-2 text-xs text-gray-600">
                    {Object.keys(u.wards).sort().join(', ')}
                  </td>
                )}
                <td className="px-3 py-2 text-right">{u.tooLow}</td>
                <td className="px-3 py-2 text-right">{u.tooHigh}</td>
                <td className="px-3 py-2 text-right font-semibold">
                  {u.tierA}
                </td>
                <td className="px-3 py-2 text-right">{u.n}</td>
                <td className="px-3 py-2 text-right">
                  <MpChip colour={u.colour}>{mpPct(u.rate)}</MpChip>
                </td>
                <td className="px-3 py-2 text-right text-gray-600">
                  {u.n >= props.minN ? mpPct(u.ub) : '—'}
                </td>
                <td className="px-3 py-2">
                  {u.n < props.minN ? (
                    <span className="text-xs text-gray-500">
                      n &lt; {props.minN}
                    </span>
                  ) : u.elevated ? (
                    <span
                      className="text-xs font-semibold"
                      style={{ color: '#991b1b' }}
                    >
                      Yes
                    </span>
                  ) : (
                    <span className="text-xs text-gray-500">No</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right text-gray-600">
                  {c.tier_b}/{c.tier_b_assessed} (
                  {mpPct(mpRate(c.tier_b, c.tier_b_assessed))})
                </td>
                <td className="px-3 py-2 text-right text-gray-600">
                  {c.unit_error}
                </td>
                <td className="px-3 py-2 text-right text-gray-600">
                  {excluded}/{c.records - c.out_of_age_range}
                </td>
              </tr>
            );
          })}
          {props.units.length === 0 && (
            <tr>
              <td className="px-3 py-4 text-gray-500" colSpan={13}>
                No readings match these filters.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function MpTrend(props) {
  var weeks = props.weeks;
  var max = weeks.reduce(function (m, w) {
    return Math.max(m, w.rate || 0);
  }, 0);
  var scale = Math.max(max, props.p * 2, 0.005);
  return (
    <div className="border rounded-lg p-3 bg-white">
      <div className="flex items-end gap-2" style={{ height: '140px' }}>
        {weeks.map(function (w) {
          var res = mpColour(w.num, w.n, props.p, props.cfg, props.method);
          var c = MP_COLOURS[res.colour];
          var h = w.rate === null ? 0 : Math.max(2, (w.rate / scale) * 110);
          return (
            <div
              key={w.week}
              className="flex flex-col items-center justify-end"
              style={{ flex: '1 1 0', minWidth: '36px', height: '100%' }}
              title={
                w.week +
                ': ' +
                w.num +
                '/' +
                w.n +
                ' (' +
                w.low +
                ' too low, ' +
                w.high +
                ' too high)'
              }
            >
              <div className="text-xs text-gray-600">{mpPct(w.rate)}</div>
              <div
                style={{
                  height: h + 'px',
                  width: '70%',
                  background: c.fg,
                  opacity: 0.75,
                  borderRadius: '2px',
                }}
              />
            </div>
          );
        })}
      </div>
      <div className="flex gap-2 mt-1">
        {weeks.map(function (w) {
          return (
            <div
              key={w.week}
              className="text-center text-gray-500"
              style={{ flex: '1 1 0', minWidth: '36px', fontSize: '10px' }}
            >
              {w.week.slice(5)}
              <br />
              {w.num}/{w.n}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MpMethod(props) {
  var t = props.thresholds || {};
  var bands = t.age_bands || [];
  var ceilings = t.ceilings || {};
  return (
    <details className="border rounded-lg p-3 bg-white text-sm">
      <summary className="cursor-pointer font-medium">
        How readings are classified
      </summary>
      <div className="mt-2 space-y-2 text-gray-700">
        <p>
          Only Connect-approved Health Service Delivery visits for children aged
          6–59 months count. A reading in the{' '}
          {(t.unit_error_range || []).join('–')} range is counted as a mm/cm
          mix-up and kept out of the implausible rate.
        </p>
        <p>
          <b>Implausible (headline)</b>: below {t.floor_cm} cm, or above the
          ceiling for the child's age and sex. These are near-certain entry
          errors, not malnutrition.
        </p>
        <table className="text-xs border">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-2 py-1 text-left">Age</th>
              <th className="px-2 py-1 text-right">Ceiling, boys</th>
              <th className="px-2 py-1 text-right">Ceiling, girls</th>
            </tr>
          </thead>
          <tbody>
            {bands.map(function (b) {
              var c = ceilings[b.key];
              return (
                <tr key={b.key}>
                  <td className="px-2 py-1">{b.label}</td>
                  <td className="px-2 py-1 text-right">
                    {c
                      ? c.male + ' cm'
                      : t.flat_ceiling_cm + ' cm (provisional)'}
                  </td>
                  <td className="px-2 py-1 text-right">
                    {c
                      ? c.female + ' cm'
                      : t.flat_ceiling_cm + ' cm (provisional)'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p>
          The 6–11 month ceiling is provisional (the flat {t.flat_ceiling_cm}{' '}
          cm) until the WHO 6–11 month reference rows are added; that band is
          not assessed for "below WHO -2SD".
        </p>
        <p>
          <b>Below WHO -2SD</b> (secondary): low for age and sex but above the
          floor. Real moderate or severe malnutrition lives here, so it is never
          counted as implausible.
        </p>
        <p>
          <b>Colours</b>: p is {props.baselineLabel}. A unit is red when its
          rate is above p + 1.645·√(p(1−p)/n) — a one-sided 95% test, so a small
          FLW with one slip is not flagged by chance — yellow when above p but
          within that bound, green at or below p, and grey below {props.minN}{' '}
          valid readings.
        </p>
        <p className="text-xs text-gray-500">
          Reference: de Onis, Yip &amp; Mei, Bull WHO 1997;75(1):11–18.
        </p>
      </div>
    </details>
  );
}

// ===========================================================================
// 3. Page
// ===========================================================================

function WorkflowUI(props) {
  var definition = props.definition;
  var view = props.view || {};
  var state = (view.state && view.state.muac_plausibility) || null;
  var cfg = mpConfig(definition);

  var cells = React.useMemo(
    function () {
      return mpCells(state);
    },
    [state],
  );
  var allWeeks = React.useMemo(
    function () {
      return mpUnique(
        cells.map(function (c) {
          return c.week;
        }),
      );
    },
    [cells],
  );

  var _f = React.useState({
    weekFrom: '',
    weekTo: '',
    llo: '',
    ward: '',
    ageBand: '',
  });
  var f = _f[0];
  var setF = _f[1];
  var _level = React.useState('llo');
  var level = _level[0];
  var setLevel = _level[1];
  var _flat = React.useState(false);
  var flat = _flat[0];
  var setFlat = _flat[1];
  var _method = React.useState('stat');
  var method = _method[0];
  var setMethod = _method[1];
  var _sortBy = React.useState('flagged');
  var sortBy = _sortBy[0];
  var setSortBy = _sortBy[1];

  if (!state) {
    return (
      <div className="p-6 text-gray-600">
        <p className="font-medium">No saved report yet.</p>
        <p className="text-sm mt-1">
          This report is computed by a scheduled run, not in the browser.
          Schedule it from the workflow list, or ask for a run to be started.
        </p>
      </div>
    );
  }

  function set(patch) {
    setF(Object.assign({}, f, patch));
  }

  var llos = mpUnique(
    cells.map(function (c) {
      return mpLlo(cfg, c.opportunity_id);
    }),
  );
  var wards = mpUnique(
    cells
      .filter(function (c) {
        return !f.llo || mpLlo(cfg, c.opportunity_id) === f.llo;
      })
      .map(function (c) {
        return c.ward;
      }),
  );

  var selected = mpFilter(cells, f, cfg);
  var p = mpBaseline(cells, f, cfg, flat);
  var totals = mpSum(selected);
  var tierA = mpTierA(totals, flat);
  var headline = mpColour(tierA, totals.valid, p, cfg, method);
  var excluded = totals.missing_age + totals.missing_muac;
  var inScope = totals.records - totals.out_of_age_range;
  var units = mpScore(
    mpUnits(selected, level, cfg, state.flw_names),
    p,
    cfg,
    method,
    flat,
    sortBy,
  );
  var weekly = mpWeekly(selected, flat);
  var baselineLabel =
    cfg.baselineMode === 'fixed'
      ? 'a fixed ' + mpPct(cfg.baselineFixed, 2) + ' (program assumption)'
      : 'the observed program-wide rate over the selected weeks and ages, ' +
        mpPct(p, 2) +
        ' (empirical)';

  function pick(u) {
    if (level === 'llo') {
      set({ llo: u.llo, ward: '' });
      setLevel('ward');
    } else if (level === 'ward') {
      set({ llo: u.llo, ward: u.ward });
      setLevel('flw');
    }
  }

  return (
    <div className="space-y-4 p-4">
      <div>
        <h1 className="text-xl font-bold">{definition.name}</h1>
        <p className="text-sm text-gray-500">
          Computed{' '}
          {String(state.generated_at || '')
            .slice(0, 16)
            .replace('T', ' ')}{' '}
          UTC · Connect-approved Health Service Delivery visits
        </p>
      </div>

      {state.errors && state.errors.length > 0 && (
        <div
          className="border rounded-lg p-3 text-sm"
          style={{ background: '#fffbeb' }}
        >
          <b>Some data could not be read for this run:</b>
          <ul className="list-disc ml-5">
            {state.errors.map(function (e, i) {
              return <li key={i}>{e}</li>;
            })}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-3 items-end">
        <MpSelect
          label="From week"
          value={f.weekFrom}
          allLabel="First"
          options={allWeeks}
          onChange={function (v) {
            set({ weekFrom: v });
          }}
        />
        <MpSelect
          label="To week"
          value={f.weekTo}
          allLabel="Latest"
          options={allWeeks}
          onChange={function (v) {
            set({ weekTo: v });
          }}
        />
        <MpSelect
          label="LLO"
          value={f.llo}
          allLabel="All LLOs"
          options={llos}
          onChange={function (v) {
            set({ llo: v, ward: '' });
          }}
        />
        <MpSelect
          label="Ward"
          value={f.ward}
          allLabel="All wards"
          options={wards}
          onChange={function (v) {
            set({ ward: v });
          }}
        />
        <MpSelect
          label="Age"
          value={f.ageBand}
          allLabel="6–59 months"
          options={MP_AGE_BANDS.map(function (b) {
            return { value: b, label: b + ' months' };
          })}
          onChange={function (v) {
            set({ ageBand: v });
          }}
        />
        <MpToggle
          label="Ceiling"
          value={flat ? 'flat' : 'banded'}
          options={[
            { value: 'banded', label: 'Age-banded' },
            { value: 'flat', label: 'Flat 9–20 cm' },
          ]}
          onChange={function (v) {
            setFlat(v === 'flat');
          }}
        />
        <MpToggle
          label="Colours from"
          value={method}
          options={[
            { value: 'stat', label: '95% test' },
            { value: 'fixed', label: 'Fixed %' },
          ]}
          onChange={setMethod}
        />
      </div>

      <div
        className="grid gap-3"
        style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' }}
      >
        <MpTile
          label={'Implausible MUAC (' + (flat ? 'flat' : 'age-banded') + ')'}
          value={
            <MpChip colour={headline.colour}>{mpPct(headline.rate, 2)}</MpChip>
          }
          sub={
            tierA +
            ' of ' +
            totals.valid +
            ' valid readings: ' +
            totals.tier_a_low +
            ' too low, ' +
            mpTierAHigh(totals, flat) +
            ' too high'
          }
        />
        <MpTile
          label="Below WHO -2SD (context, not an error)"
          value={mpPct(mpRate(totals.tier_b, totals.tier_b_assessed))}
          sub={
            totals.tier_b +
            ' of ' +
            totals.tier_b_assessed +
            ' readings aged 12–59 months'
          }
        />
        <MpTile
          label="mm/cm mix-ups"
          value={totals.unit_error}
          sub={
            mpPct(mpRate(totals.unit_error, totals.valid), 2) +
            ' of valid readings'
          }
        />
        <MpTile
          label="Excluded (no age or no MUAC)"
          value={excluded}
          sub={
            totals.missing_age +
            ' no age · ' +
            totals.missing_muac +
            ' no MUAC · ' +
            mpPct(mpRate(excluded, inScope)) +
            ' of visits'
          }
        />
        <MpTile
          label="Baseline p"
          value={mpPct(p, 2)}
          sub={
            cfg.baselineMode === 'fixed'
              ? 'fixed program assumption'
              : 'observed, program-wide'
          }
        />
      </div>

      <p className="text-xs text-gray-600">
        Colours:{' '}
        {method === 'fixed'
          ? 'fixed bands — green ≤ ' +
            mpPct(cfg.fixedPctBands[0], 0) +
            ', yellow ≤ ' +
            mpPct(cfg.fixedPctBands[1], 0) +
            ', red above (ignores sample size)'
          : 'one-sided 95% test against p = ' +
            mpPct(p, 2) +
            ' — red means statistically elevated'}
        ; grey below {cfg.minN} valid readings.
      </p>

      <div className="flex gap-2 items-center flex-wrap">
        {[
          { value: 'llo', label: 'By LLO' },
          { value: 'ward', label: 'By ward' },
          { value: 'flw', label: 'By FLW' },
        ].map(function (t) {
          var on = t.value === level;
          return (
            <button
              key={t.value}
              type="button"
              className="px-3 py-1 rounded text-sm border"
              style={{
                background: on ? '#1f2937' : '#ffffff',
                color: on ? '#ffffff' : '#374151',
              }}
              onClick={function () {
                setLevel(t.value);
              }}
            >
              {t.label}
            </button>
          );
        })}
        {(f.llo || f.ward) && (
          <button
            type="button"
            className="px-2 py-1 text-xs text-gray-600 underline"
            onClick={function () {
              set({ llo: '', ward: '' });
            }}
          >
            Clear LLO/ward
          </button>
        )}
        <span className="text-xs text-gray-500">
          {level !== 'flw' ? 'Click a row to drill down.' : ''}
        </span>
        <span className="text-xs text-gray-600 ml-auto">Sort by</span>
        <MpToggle
          label=""
          value={sortBy}
          options={[
            { value: 'flagged', label: 'Flagged first' },
            { value: 'total', label: 'Most implausible' },
          ]}
          onChange={setSortBy}
        />
      </div>

      <MpUnitTable
        level={level}
        units={units}
        minN={cfg.minN}
        floorCm={(state.thresholds || {}).floor_cm || 9}
        onPick={level === 'flw' ? null : pick}
      />

      <div>
        <h2 className="text-sm font-semibold mb-2">
          Weekly implausible rate (current selection)
        </h2>
        <MpTrend weeks={weekly} p={p} cfg={cfg} method={method} />
      </div>

      <MpMethod
        thresholds={state.thresholds}
        minN={cfg.minN}
        baselineLabel={baselineLabel}
      />
    </div>
  );
}
