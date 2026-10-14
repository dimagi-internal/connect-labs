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

// ---------------------------------------------------------------- MUAC index
//
// Neal's six MUAC distribution checks (MLFeatureAggregationReport.py), ported
// line for line and checked against that file's own output on 400 histograms
// (__tests__/fixtures/muac_index_parity.json). They read the twelve 1 cm bins
// [9.5, 10.5) ... [20.5, 21.5) every run counts. A real FLW's readings form one
// smooth hump; fabricated ones tend to pile into one bin, skip bins, or plateau.

var MP_BINS = [
  'muac_bin_0',
  'muac_bin_1',
  'muac_bin_2',
  'muac_bin_3',
  'muac_bin_4',
  'muac_bin_5',
  'muac_bin_6',
  'muac_bin_7',
  'muac_bin_8',
  'muac_bin_9',
  'muac_bin_10',
  'muac_bin_11',
];
MP_COUNTERS = MP_COUNTERS.concat(MP_BINS);

var MP_INDEX_MIN_READINGS = 20; // below this the checks are not run
var MP_INDEX_PEAK_MAX = 0.42; // peak_reasonable threshold
var MP_INDEX_PLATEAU_MIN = 3; // python file: 3+ similar high bins is a plateau

var MP_INDEX_CHECKS = [
  ['increasing', 'Rises to its peak'],
  ['decreasing', 'Falls after its peak'],
  ['no_skipped', 'No empty bins in the middle'],
  ['no_plateau', 'No flat top'],
  ['bins_sufficient', 'Spread over 5+ bins'],
  ['peak_reasonable', 'Peak holds ≤ 42%'],
];

function mpIdxIncreasing(bins, nz, peak, wiggle) {
  if (peak === 0) return false;
  if (
    !nz.some(function (i) {
      return i < peak;
    })
  )
    return false;
  var pos = nz.indexOf(peak);
  if (pos <= 0) return false;
  var inc = 0;
  var bigDrops = 0;
  for (var i = 0; i < pos; i++) {
    var step = bins[nz[i + 1]] - bins[nz[i]];
    if (step > 0) inc++;
    if (step < -wiggle) bigDrops++;
  }
  var enough = false;
  for (var j = 0; j < peak; j++)
    if (bins[j] >= bins[peak] * 0.25) enough = true;
  return inc >= 1 && bigDrops === 0 && enough;
}

function mpIdxDecreasing(bins, nz, peak, wiggle) {
  var pos = nz.indexOf(peak);
  if (peak === bins.length - 1 || pos < 0) return false;
  if (pos === nz.length - 1) return false;
  var dec = 0;
  var bigRises = 0;
  for (var i = pos; i < nz.length - 1; i++) {
    var step = bins[nz[i + 1]] - bins[nz[i]];
    if (step < 0) dec++;
    if (step > wiggle) bigRises++;
  }
  return dec >= 1 && bigRises === 0;
}

function mpIdxNoSkipped(bins, total) {
  if (total <= 0) return true;
  var sig = [];
  bins.forEach(function (c, i) {
    if (c >= total * 0.02) sig.push(i);
  });
  if (sig.length <= 1) return true;
  for (var i = sig[0]; i <= sig[sig.length - 1]; i++)
    if (bins[i] === 0) return false;
  return true;
}

function mpIdxNoPlateau(bins, maxCount) {
  var total = bins.reduce(function (a, b) {
    return a + b;
  }, 0);
  if (total === 0 || maxCount === 0) return true;
  var high = [];
  bins.forEach(function (c, i) {
    if (c >= 0.5 * maxCount) high.push([i, c]);
  });
  if (high.length < 2) return true;
  var tolerance = total * 0.04;
  var longest = 1;
  var start = 0;
  for (var i = 1; i < high.length; i++) {
    if (high[i][0] === high[i - 1][0] + 1) {
      var seg = high.slice(start, i + 1).map(function (h) {
        return h[1];
      });
      if (Math.max.apply(null, seg) - Math.min.apply(null, seg) <= tolerance) {
        longest = Math.max(longest, seg.length);
      } else {
        start = i - 1;
      }
    } else {
      start = i - 1;
    }
  }
  return longest < MP_INDEX_PLATEAU_MIN;
}

// The six checks for one set of counts, or null below 20 readings in 9.5-21.5 cm.
function mpIndex(counts) {
  var bins = MP_BINS.map(function (k) {
    return Number(counts[k]) || 0;
  });
  var total = bins.reduce(function (a, b) {
    return a + b;
  }, 0);
  if (total < MP_INDEX_MIN_READINGS) return null;
  var nz = [];
  bins.forEach(function (c, i) {
    if (c > 0) nz.push(i);
  });
  var maxCount = Math.max.apply(null, bins);
  var peak = bins.indexOf(maxCount);
  var wiggle = total * 0.02;
  var checks = {
    increasing: mpIdxIncreasing(bins, nz, peak, wiggle),
    decreasing: mpIdxDecreasing(bins, nz, peak, wiggle),
    no_skipped: mpIdxNoSkipped(bins, total),
    no_plateau: mpIdxNoPlateau(bins, maxCount),
    bins_sufficient: nz.length >= 5,
    peak_reasonable: maxCount / total <= MP_INDEX_PEAK_MAX,
  };
  var passed = MP_INDEX_CHECKS.filter(function (c) {
    return checks[c[0]];
  }).length;
  return {
    bins: bins,
    total: total,
    peak: peak,
    peakShare: maxCount / total,
    checks: checks,
    passed: passed,
  };
}

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
    opportunityLabels: c.opportunity_labels || {},
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

// An opportunity's display name: config.opportunity_labels, else "<LLO> · <id>".
function mpOppLabel(cfg, oppId) {
  return (
    cfg.opportunityLabels[String(oppId)] || mpLlo(cfg, oppId) + ' · ' + oppId
  );
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
    if (f.opp && String(c.opportunity_id) !== String(f.opp)) return false;
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

// Counts per age band, for one unit or the whole selection.
function mpByBand(cells) {
  var out = {};
  cells.forEach(function (c) {
    out[c.age_band] = mpAdd(out[c.age_band] || mpZero(), c);
  });
  return out;
}

// Units at one level: 'llo', 'opp', 'ward' or 'flw'. An FLW is one person
// within an LLO, added up across wards and the LLO's opportunities. Each unit
// also carries its counts per age band (byBand) for its age breakdown.
function mpUnits(cells, level, cfg, names) {
  var byKey = {};
  cells.forEach(function (c) {
    var llo = mpLlo(cfg, c.opportunity_id);
    var key, label, parent;
    if (level === 'llo') {
      key = llo;
      label = llo;
      parent = '';
    } else if (level === 'opp') {
      key = String(c.opportunity_id);
      label = mpOppLabel(cfg, c.opportunity_id);
      parent = llo;
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
        opp: level === 'opp' ? String(c.opportunity_id) : '',
        wards: {},
        counts: mpZero(),
        byBand: {},
      };
    }
    if (c.ward) u.wards[c.ward] = true;
    mpAdd(u.counts, c);
    u.byBand[c.age_band] = mpAdd(u.byBand[c.age_band] || mpZero(), c);
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
      index: mpIndex(u.counts),
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
    if (sortBy === 'index') {
      var ia = a.index ? a.index.passed : 99;
      var ib = b.index ? b.index.passed : 99;
      if (ia !== ib) return ia - ib;
    }
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

// ------------------------------------------------------------- definitions
//
// One or two sentences per column and tile, shown on hover over its title (the
// (i) marks which titles have one). Written for programme staff, not analysts.

function mpDefs(t) {
  t = t || {};
  var floorCm = t.floor_cm || 9;
  var monthly = t.method === 'who2007_monthly';
  var tooHigh = monthly
    ? "Readings above WHO's +3 SD for the child's exact month of age and sex (WHO 2007 MUAC-for-age). About 1 healthy child in 750 measures this high, so these are very likely entry errors."
    : "Readings above the fixed ceiling for the child's age band and sex (this run predates the WHO-by-month ceilings).";
  return {
    unit: 'The LLO, opportunity, ward or field worker these figures are for. Click a row to drill down; ▸ opens it by age range and shows its MUAC index.',
    llo: 'The local organisation running the opportunity. R1 opportunities count under their LLO.',
    wards:
      'Wards this worker delivered approved visits in; one worker can appear under several wards.',
    too_low:
      'Readings below ' +
      floorCm +
      ' cm. That is 4-5 SD below the WHO median at every age from 6 to 59 months, so these are near-certain entry errors, not malnutrition. % is of the valid readings.',
    too_high: tooHigh + ' % is of the valid readings.',
    implausible:
      'Too low plus too high: the readings that are almost certainly data errors.',
    valid:
      'Approved Health Service Delivery visits for a child aged 6-59 months with a MUAC value recorded. The denominator for every percentage in the row.',
    pct: 'Implausible ÷ valid readings. The colour compares it with the programme-wide rate (see Upper bound).',
    upper_bound:
      'The highest rate this many readings would plausibly show by chance if the true rate were the programme baseline p: p + 1.645·√(p(1−p)/n), a one-sided 95% test. A rate above it is coloured red.',
    elevated:
      'Yes when the implausible rate is above the upper bound: higher than chance explains at 95% confidence. Shown only with at least the minimum number of valid readings.',
    tier_b: monthly
      ? "Readings below WHO's −2 SD for the child's month and sex but above " +
        floorCm +
        ' cm, out of the valid readings with a recorded sex. Real moderate or severe malnutrition sits here, so it is context, never an error.'
      : 'Readings below WHO −2 SD for age and sex, out of the readings it could be assessed for. Context, never an error.',
    unit_error:
      'Readings between 90 and 200: almost certainly millimetres typed into the centimetre field (145 for 14.5). Counted on their own, not as implausible.',
    excluded:
      'Visits with no age, or a child aged 6-59 months with no MUAC value, out of all visits for children in range or of unknown age. A high share is itself a data problem.',
    index:
      "Neal's MUAC distribution index: how many of six checks this row's MUAC values pass (6/6 = looks like real measuring). It looks at the shape of all values from 9.5 to 21.5 cm in 1 cm bins, needs at least 20 of them, and is designed for one field worker. ▸ shows which checks failed. Green 5–6, yellow 4, red 3 or fewer.",
    age: 'Age band in completed months at the visit.',
    too_high_if: monthly
      ? "WHO +3 SD for this band's months: the lowest and highest value over the band, by sex. Each child is checked against the value for their own month."
      : 'The fixed ceiling this run used for the band.',
    tile_implausible:
      'Share of valid readings below ' +
      floorCm +
      " cm or above WHO +3 SD for the child's month and sex. The headline data-quality figure.",
    tile_tier_b:
      'Share of readings below WHO −2 SD for month and sex (but above ' +
      floorCm +
      ' cm). Possible real malnutrition: context, not an error.',
    tile_unit:
      'Readings between 90 and 200, almost certainly millimetres entered as centimetres.',
    tile_excluded:
      'Visits that could not be checked: no age, or no MUAC for a child of 6-59 months.',
    tile_p:
      'The rate each row is compared with: the programme-wide implausible rate over the selected weeks and ages (not narrowed by LLO, opportunity or ward), or a fixed assumption if configured.',
    check_increasing:
      'Counts build up towards the peak bin: at least one rise, no drop bigger than 2% of the readings, and a bin before the peak with at least a quarter of its count.',
    check_decreasing:
      'Counts tail off after the peak: at least one fall, no rise bigger than 2% of the readings, and the peak is not the last bin.',
    check_no_skipped:
      'No empty 1 cm bin between the first and last bins holding 2% or more of the readings.',
    check_no_plateau:
      'No run of ' +
      MP_INDEX_PLATEAU_MIN +
      '+ neighbouring high bins (each ≥ half the peak) with near-identical counts (within 4% of the readings).',
    check_bins_sufficient:
      'Readings fall in at least 5 of the 12 one-centimetre bins.',
    check_peak_reasonable:
      'The fullest 1 cm bin holds no more than 42% of the readings.',
  };
}

// A column title with its definition on hover, marked with an (i).
function MpTh(props) {
  return (
    <th
      className={props.className}
      title={props.def}
      style={{ cursor: props.def ? 'help' : undefined }}
    >
      {props.children}
      {props.def ? <span className="text-gray-400 font-normal"> ⓘ</span> : null}
    </th>
  );
}
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
      <div
        className="text-xs text-gray-500"
        title={props.def}
        style={{ cursor: props.def ? 'help' : undefined }}
      >
        {props.label}
        {props.def ? <span className="text-gray-400"> ⓘ</span> : null}
      </div>
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

var MP_LEVEL_LABELS = {
  llo: 'LLO',
  opp: 'Opportunity',
  ward: 'Ward',
  flw: 'FLW',
};

// "7 (0.02%)": a count with its share of the valid readings.
function MpCountPct(props) {
  return (
    <span>
      {props.n}
      <span className="text-xs text-gray-500">
        {' '}
        ({mpPct(mpRate(props.n, props.of), 2)})
      </span>
    </span>
  );
}

// The ceiling a band is held to, as text: one value, or boys / girls.
function mpCeilingText(thresholds, band, flat) {
  var t = thresholds || {};
  var flatCm = t.flat_ceiling_cm || 20;
  if (flat) return '> ' + flatCm + ' cm';
  var r = (t.ceiling_range || {})[band];
  if (r) {
    var span = function (v) {
      return v[0] === v[1] ? v[0] + '' : v[0] + '–' + v[1];
    };
    return '> ' + span(r.male) + ' cm boys, > ' + span(r.female) + ' cm girls';
  }
  // A run saved before the WHO-by-month ceilings: fixed per-band values.
  var c = (t.ceilings || {})[band];
  if (!c) return '> ' + flatCm + ' cm (provisional)';
  if (c.male === c.female) return '> ' + c.male + ' cm';
  return '> ' + c.male + ' cm boys, > ' + c.female + ' cm girls';
}

// Too low / too high / total, per age band, with each band's cut-offs. byBand is
// {band: counts}; used for the whole selection and inside an expanded row.
function MpAgeTable(props) {
  var byBand = props.byBand || {};
  var floorCm = (props.thresholds || {}).floor_cm || 9;
  var D = mpDefs(props.thresholds);
  var flat = props.flat;
  var total = mpZero();
  var rows = MP_AGE_BANDS.filter(function (b) {
    return byBand[b] && byBand[b].valid > 0;
  }).map(function (b) {
    mpAdd(total, byBand[b]);
    return { band: b, c: byBand[b] };
  });
  function cells(c) {
    return [
      <td key="lo" className="px-3 py-1.5 text-right">
        <MpCountPct n={c.tier_a_low} of={c.valid} />
      </td>,
      <td key="hi" className="px-3 py-1.5 text-right">
        <MpCountPct n={mpTierAHigh(c, flat)} of={c.valid} />
      </td>,
      <td key="all" className="px-3 py-1.5 text-right font-semibold">
        <MpCountPct n={mpTierA(c, flat)} of={c.valid} />
      </td>,
      <td key="n" className="px-3 py-1.5 text-right">
        {c.valid}
      </td>,
    ];
  }
  if (rows.length === 0)
    return <p className="text-sm text-gray-500 p-2">No valid readings.</p>;
  return (
    <div className="overflow-x-auto border rounded-lg bg-white">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50">
          <tr>
            <MpTh className="px-3 py-1.5 text-left font-semibold" def={D.age}>
              Age
            </MpTh>
            <MpTh
              className="px-3 py-1.5 text-left font-semibold"
              def={D.too_high_if}
            >
              Too high if
            </MpTh>
            <MpTh
              className="px-3 py-1.5 text-right font-semibold"
              def={D.too_low}
            >
              Too low (&lt; {floorCm} cm)
            </MpTh>
            <MpTh
              className="px-3 py-1.5 text-right font-semibold"
              def={D.too_high}
            >
              Too high
            </MpTh>
            <MpTh
              className="px-3 py-1.5 text-right font-semibold"
              def={D.implausible}
            >
              Implausible (total)
            </MpTh>
            <MpTh
              className="px-3 py-1.5 text-right font-semibold"
              def={D.valid}
            >
              Valid readings
            </MpTh>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map(function (r) {
            return (
              <tr key={r.band}>
                <td className="px-3 py-1.5">{r.band} months</td>
                <td className="px-3 py-1.5 text-xs text-gray-600">
                  {mpCeilingText(props.thresholds, r.band, flat)}
                </td>
                {cells(r.c)}
              </tr>
            );
          })}
          <tr className="bg-gray-50 font-medium">
            <td className="px-3 py-1.5">All ages</td>
            <td className="px-3 py-1.5" />
            {cells(total)}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// "5/6", tinted by how many checks fail; "—" below 20 readings.
function MpIndexBadge(props) {
  var ix = props.index;
  if (!ix) return <span className="text-xs text-gray-400">—</span>;
  var colour = ix.passed >= 5 ? 'green' : ix.passed === 4 ? 'yellow' : 'red';
  return <MpChip colour={colour}>{ix.passed}/6</MpChip>;
}

// The 1 cm histogram the index reads, and each check passed or failed.
function MpIndexPanel(props) {
  var ix = props.index;
  var D = props.defs;
  if (!ix)
    return (
      <p className="text-xs text-gray-500">
        MUAC index: fewer than {MP_INDEX_MIN_READINGS} readings between 9.5 and
        21.5 cm, so the checks are not run.
      </p>
    );
  var max = Math.max.apply(null, ix.bins) || 1;
  return (
    <div className="border rounded-lg p-3 bg-white">
      <div
        className="text-sm font-medium mb-2"
        title={D.index}
        style={{ cursor: 'help' }}
      >
        MUAC index: {ix.passed}/6 checks passed ({ix.total} readings)
        <span className="text-gray-400 font-normal"> ⓘ</span>
        {props.level !== 'flw' && (
          <span className="text-xs text-gray-500 font-normal">
            {' '}
            — pooled over many workers; the checks are designed for one worker
          </span>
        )}
      </div>
      <div className="flex gap-4 flex-wrap">
        <div>
          <div className="flex items-end gap-1" style={{ height: '80px' }}>
            {ix.bins.map(function (c, i) {
              return (
                <div
                  key={i}
                  title={
                    10 + i + ' cm: ' + c + ' (' + mpPct(c / ix.total) + ')'
                  }
                  style={{
                    width: '14px',
                    height: Math.max(c > 0 ? 2 : 0, (c / max) * 78) + 'px',
                    background: i === ix.peak ? '#1f2937' : '#9ca3af',
                    borderRadius: '2px',
                  }}
                />
              );
            })}
          </div>
          <div className="flex gap-1 text-gray-500" style={{ fontSize: '9px' }}>
            {ix.bins.map(function (_c, i) {
              return (
                <div key={i} style={{ width: '14px', textAlign: 'center' }}>
                  {10 + i}
                </div>
              );
            })}
          </div>
          <div className="text-xs text-gray-500">MUAC (cm), 1 cm bins</div>
        </div>
        <ul className="text-sm space-y-0.5">
          {MP_INDEX_CHECKS.map(function (chk) {
            var ok = ix.checks[chk[0]];
            return (
              <li
                key={chk[0]}
                title={D['check_' + chk[0]]}
                style={{ cursor: 'help' }}
              >
                <span style={{ color: ok ? '#166534' : '#991b1b' }}>
                  {ok ? '✓' : '✗'}
                </span>{' '}
                {chk[1]}
                {chk[0] === 'peak_reasonable'
                  ? ' (' + mpPct(ix.peakShare, 0) + ')'
                  : ''}
                <span className="text-gray-400"> ⓘ</span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function MpUnitTable(props) {
  var level = props.level;
  var showParent = level !== 'llo';
  var _open = React.useState({});
  var open = _open[0];
  var setOpen = _open[1];
  var colCount = 13 + (showParent ? 1 : 0) + (level === 'flw' ? 1 : 0);
  var D = mpDefs(props.thresholds);

  function toggle(key, e) {
    if (e && e.stopPropagation) e.stopPropagation();
    var next = Object.assign({}, open);
    next[key] = !open[key];
    setOpen(next);
  }

  return (
    <div className="overflow-x-auto border rounded-lg">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50">
          <tr>
            <th
              className="px-2 py-2"
              title="Show this row by age range and its MUAC index"
            />
            <MpTh className="px-3 py-2 text-left font-semibold" def={D.unit}>
              {MP_LEVEL_LABELS[level]}
            </MpTh>
            {showParent && (
              <MpTh className="px-3 py-2 text-left font-semibold" def={D.llo}>
                LLO
              </MpTh>
            )}
            {level === 'flw' && (
              <MpTh className="px-3 py-2 text-left font-semibold" def={D.wards}>
                Ward(s)
              </MpTh>
            )}
            <MpTh
              className="px-3 py-2 text-right font-semibold"
              def={D.too_low}
            >
              Too low
            </MpTh>
            <MpTh
              className="px-3 py-2 text-right font-semibold"
              def={D.too_high}
            >
              Too high
            </MpTh>
            <MpTh
              className="px-3 py-2 text-right font-semibold"
              def={D.implausible}
            >
              Implausible (total)
            </MpTh>
            <MpTh className="px-3 py-2 text-right font-semibold" def={D.valid}>
              Valid readings
            </MpTh>
            <MpTh className="px-3 py-2 text-right font-semibold" def={D.pct}>
              % implausible
            </MpTh>
            <MpTh
              className="px-3 py-2 text-right font-semibold"
              def={D.upper_bound}
            >
              Upper bound
            </MpTh>
            <MpTh
              className="px-3 py-2 text-left font-semibold"
              def={D.elevated}
            >
              Elevated?
            </MpTh>
            <MpTh className="px-3 py-2 text-right font-semibold" def={D.index}>
              MUAC index
            </MpTh>
            <MpTh
              className="px-3 py-2 text-right font-semibold text-gray-500"
              def={D.tier_b}
            >
              Below WHO -2SD
            </MpTh>
            <MpTh
              className="px-3 py-2 text-right font-semibold text-gray-500"
              def={D.unit_error}
            >
              mm/cm mix-ups
            </MpTh>
            <MpTh
              className="px-3 py-2 text-right font-semibold text-gray-500"
              def={D.excluded}
            >
              Excluded
            </MpTh>
          </tr>
        </thead>
        <tbody className="divide-y">
          {props.units.map(function (u) {
            var c = u.counts;
            var excluded = c.missing_age + c.missing_muac;
            var isOpen = !!open[u.key];
            var row = (
              <tr
                key={u.key}
                className={
                  props.onPick ? 'hover:bg-gray-50 cursor-pointer' : ''
                }
                onClick={function () {
                  if (props.onPick) props.onPick(u);
                }}
              >
                <td className="px-2 py-2">
                  <button
                    type="button"
                    className="text-xs text-gray-600 px-1"
                    title="Show by age range"
                    onClick={function (e) {
                      toggle(u.key, e);
                    }}
                  >
                    {isOpen ? '▾' : '▸'}
                  </button>
                </td>
                <td className="px-3 py-2">{u.label}</td>
                {showParent && <td className="px-3 py-2">{u.parent}</td>}
                {level === 'flw' && (
                  <td className="px-3 py-2 text-xs text-gray-600">
                    {Object.keys(u.wards).sort().join(', ')}
                  </td>
                )}
                <td className="px-3 py-2 text-right">
                  <MpCountPct n={u.tooLow} of={u.n} />
                </td>
                <td className="px-3 py-2 text-right">
                  <MpCountPct n={u.tooHigh} of={u.n} />
                </td>
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
                <td className="px-3 py-2 text-right">
                  <MpIndexBadge index={u.index} />
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
            if (!isOpen) return row;
            return [
              row,
              <tr key={u.key + ':age'} className="bg-gray-50">
                <td />
                <td className="px-3 py-2" colSpan={colCount - 1}>
                  <div className="space-y-3">
                    <MpAgeTable
                      byBand={u.byBand}
                      thresholds={props.thresholds}
                      flat={props.flat}
                    />
                    <MpIndexPanel index={u.index} defs={D} level={level} />
                  </div>
                </td>
              </tr>,
            ];
          })}
          {props.units.length === 0 && (
            <tr>
              <td className="px-3 py-4 text-gray-500" colSpan={colCount}>
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
  var monthly = t.method === 'who2007_monthly';
  var range = function (r) {
    return r ? (r[0] === r[1] ? r[0] : r[0] + '–' + r[1]) + ' cm' : '—';
  };
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
        {monthly ? (
          <p>
            <b>Implausible (headline)</b>: below {t.floor_cm} cm, or above WHO's
            +3 SD for the child's own completed month of age and sex (WHO 2007
            MUAC-for-age standard). About 1 healthy child in 750 measures above
            +3 SD, and {t.floor_cm} cm is 4–5 SD below the median at every age,
            so these are very likely entry errors, not malnutrition. Neither is
            impossible: a red row says "much more often than chance", not that
            every reading is wrong.
          </p>
        ) : (
          <p>
            <b>Implausible (headline)</b>: below {t.floor_cm} cm, or above the
            fixed ceiling for the child's age band and sex. This run predates
            the WHO-by-month ceilings.
          </p>
        )}
        <table className="text-xs border">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-2 py-1 text-left">Age</th>
              <th className="px-2 py-1 text-right">Too high, boys</th>
              <th className="px-2 py-1 text-right">Too high, girls</th>
              {monthly && (
                <th className="px-2 py-1 text-right">Below −2 SD, boys</th>
              )}
              {monthly && (
                <th className="px-2 py-1 text-right">Below −2 SD, girls</th>
              )}
            </tr>
          </thead>
          <tbody>
            {bands.map(function (b) {
              if (monthly) {
                var hi = (t.ceiling_range || {})[b.key] || {};
                var lo = (t.minus_2sd_range || {})[b.key] || {};
                return (
                  <tr key={b.key}>
                    <td className="px-2 py-1">{b.label}</td>
                    <td className="px-2 py-1 text-right">
                      &gt; {range(hi.male)}
                    </td>
                    <td className="px-2 py-1 text-right">
                      &gt; {range(hi.female)}
                    </td>
                    <td className="px-2 py-1 text-right">
                      &lt; {range(lo.male)}
                    </td>
                    <td className="px-2 py-1 text-right">
                      &lt; {range(lo.female)}
                    </td>
                  </tr>
                );
              }
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
        {monthly && (
          <p className="text-xs text-gray-500">
            Each band shows the range over its months; every child is checked
            against the exact value for their own month, which rises with age.
            The "Flat 9–20 cm" toggle compares with a single 20 cm ceiling for
            all ages.
          </p>
        )}
        <p>
          <b>Below WHO -2SD</b> (secondary): low for age and sex but above the
          floor. Real moderate or severe malnutrition lives here, so it is never
          counted as implausible.
        </p>
        <p>
          <b>Colours</b>: p is {props.baselineLabel}. A row is red when its rate
          is above p + 1.645·√(p(1−p)/n) — a one-sided 95% test, so a small FLW
          with one slip is not flagged by chance — yellow when above p but
          within that bound, green at or below p, and grey below {props.minN}{' '}
          valid readings. Testing many rows at 95% means about 1 in 20 ordinary
          rows turns red by chance.
        </p>
        <p>
          <b>MUAC index</b>: Neal's six checks on the shape of a worker's MUAC
          values in 1 cm bins (9.5–21.5 cm, any age, at least 20 values). Real
          measuring gives one smooth hump; repeated or invented values pile into
          one bin, skip bins or plateau. Green 5–6, yellow 4, red 3 or fewer.
        </p>
        <p className="text-xs text-gray-500">
          {monthly
            ? 'Reference: WHO Child Growth Standards, arm circumference-for-age, 3–60 months (2007).'
            : 'Reference: de Onis, Yip & Mei, Bull WHO 1997;75(1):11–18.'}
        </p>
      </div>
    </details>
  );
}

// A run created by hand starts empty: the figures are classified on the server.
// This starts that job (job_handlers/muac_plausibility.py) for THIS run and
// reloads when it is done. A completed run with no figures cannot be filled.
function MpEmpty(props) {
  var instance = props.instance || {};
  var actions = props.actions || {};
  var _status = React.useState('idle'); // idle | running | error
  var status = _status[0];
  var setStatus = _status[1];
  var _message = React.useState(null);
  var message = _message[0];
  var setMessage = _message[1];
  var canCompute = !props.isCompleted && instance.id && actions.startJob;

  function compute() {
    if (!canCompute || status === 'running') return;
    setStatus('running');
    setMessage('Starting…');
    actions
      .startJob(instance.id, {
        job_type: 'muac_plausibility_compute',
        run_id: instance.id,
        program_id: instance.program_id,
        opportunity_id: instance.program_id ? null : instance.opportunity_id,
      })
      .then(function (resp) {
        if (!resp || !resp.success || !resp.task_id) {
          setStatus('error');
          setMessage(
            (resp && resp.error) || 'Could not start the computation.',
          );
          return;
        }
        setMessage(
          'Reading and classifying approved visits — this takes a few minutes…',
        );
        actions.streamJobProgress(
          resp.task_id,
          function (data) {
            if (data && data.message) setMessage(data.message);
          },
          null,
          function () {
            setMessage('Done — loading the report…');
            if (actions && actions.reload) actions.reload();
            else window.location.reload();
          },
          function (err) {
            setStatus('error');
            setMessage(err || 'The computation failed.');
          },
        );
      })
      .catch(function () {
        setStatus('error');
        setMessage('Could not start the computation.');
      });
  }

  return (
    <div className="p-6 text-gray-700 space-y-3">
      <p className="font-medium">This run has no figures yet.</p>
      {canCompute ? (
        <div className="space-y-2">
          <p className="text-sm">
            The figures are worked out on the server from every approved visit.
            Compute them for this run now, or open the latest scheduled run from
            the workflow list.
          </p>
          <button
            type="button"
            className="px-3 py-1.5 rounded text-sm"
            style={{
              background: status === 'running' ? '#9ca3af' : '#1f2937',
              color: '#ffffff',
            }}
            disabled={status === 'running'}
            onClick={compute}
          >
            {status === 'running' ? 'Computing…' : 'Compute this report'}
          </button>
          {message && (
            <p
              className="text-sm"
              style={{ color: status === 'error' ? '#991b1b' : '#4b5563' }}
            >
              {message}
            </p>
          )}
        </div>
      ) : (
        <p className="text-sm">
          This run was completed without figures. Open the latest scheduled run
          from the workflow list, or create a new run and compute it.
        </p>
      )}
    </div>
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
    opp: '',
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
      <MpEmpty
        instance={props.instance}
        actions={props.actions}
        isCompleted={!!view.isCompleted}
      />
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
  var opps = mpUnique(
    cells
      .filter(function (c) {
        return !f.llo || mpLlo(cfg, c.opportunity_id) === f.llo;
      })
      .map(function (c) {
        return String(c.opportunity_id);
      }),
  ).map(function (id) {
    return { value: id, label: mpOppLabel(cfg, id) };
  });
  var wards = mpUnique(
    cells
      .filter(function (c) {
        if (f.llo && mpLlo(cfg, c.opportunity_id) !== f.llo) return false;
        return !f.opp || String(c.opportunity_id) === String(f.opp);
      })
      .map(function (c) {
        return c.ward;
      }),
  );

  var selected = mpFilter(cells, f, cfg);
  var D = mpDefs(state.thresholds);
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
  var selectionByBand = mpByBand(selected);
  var selectionLabel =
    [
      f.ward,
      f.opp ? mpOppLabel(cfg, f.opp) : f.llo,
      f.ageBand ? f.ageBand + ' months' : '',
    ]
      .filter(Boolean)
      .join(' · ') || 'all LLOs';
  var baselineLabel =
    cfg.baselineMode === 'fixed'
      ? 'a fixed ' + mpPct(cfg.baselineFixed, 2) + ' (program assumption)'
      : 'the observed program-wide rate over the selected weeks and ages, ' +
        mpPct(p, 2) +
        ' (empirical)';

  function pick(u) {
    if (level === 'llo') {
      set({ llo: u.llo, opp: '', ward: '' });
      setLevel('ward');
    } else if (level === 'opp') {
      set({ llo: u.llo, opp: u.opp, ward: '' });
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
            set({ llo: v, opp: '', ward: '' });
          }}
        />
        <MpSelect
          label="Opportunity"
          value={f.opp}
          allLabel="All opportunities"
          options={opps}
          onChange={function (v) {
            set({ opp: v, ward: '' });
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
            { value: 'banded', label: 'WHO by month' },
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
          label={
            'Implausible MUAC (' +
            (flat ? 'flat 9–20 cm' : 'WHO by month') +
            ')'
          }
          def={D.tile_implausible}
          value={
            <MpChip colour={headline.colour}>{mpPct(headline.rate, 2)}</MpChip>
          }
          sub={
            tierA +
            ' of ' +
            totals.valid +
            ' valid readings: ' +
            totals.tier_a_low +
            ' too low (' +
            mpPct(mpRate(totals.tier_a_low, totals.valid), 2) +
            '), ' +
            mpTierAHigh(totals, flat) +
            ' too high (' +
            mpPct(mpRate(mpTierAHigh(totals, flat), totals.valid), 2) +
            ')'
          }
        />
        <MpTile
          label="Below WHO -2SD (context, not an error)"
          def={D.tile_tier_b}
          value={mpPct(mpRate(totals.tier_b, totals.tier_b_assessed))}
          sub={
            totals.tier_b +
            ' of ' +
            totals.tier_b_assessed +
            ' readings with a recorded sex'
          }
        />
        <MpTile
          label="mm/cm mix-ups"
          def={D.tile_unit}
          value={totals.unit_error}
          sub={
            mpPct(mpRate(totals.unit_error, totals.valid), 2) +
            ' of valid readings'
          }
        />
        <MpTile
          label="Excluded (no age or no MUAC)"
          def={D.tile_excluded}
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
          def={D.tile_p}
          value={mpPct(p, 2)}
          sub={
            cfg.baselineMode === 'fixed'
              ? 'fixed program assumption'
              : 'observed, program-wide'
          }
        />
      </div>

      <div>
        <h2 className="text-sm font-semibold mb-2">
          By age range ({selectionLabel})
        </h2>
        <MpAgeTable
          byBand={selectionByBand}
          thresholds={state.thresholds}
          flat={flat}
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
          { value: 'opp', label: 'By opportunity' },
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
        {(f.llo || f.opp || f.ward) && (
          <button
            type="button"
            className="px-2 py-1 text-xs text-gray-600 underline"
            onClick={function () {
              set({ llo: '', opp: '', ward: '' });
            }}
          >
            Clear LLO/opportunity/ward
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
            { value: 'index', label: 'Lowest MUAC index' },
          ]}
          onChange={setSortBy}
        />
      </div>

      <MpUnitTable
        level={level}
        units={units}
        minN={cfg.minN}
        floorCm={(state.thresholds || {}).floor_cm || 9}
        thresholds={state.thresholds}
        flat={flat}
        onPick={level === 'flw' ? null : pick}
      />
      <p className="text-xs text-gray-500">
        ▸ on a row shows that row by age range. Percentages are of the row's
        valid readings.
      </p>

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
