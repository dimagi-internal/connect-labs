// The MUAC plausibility render (muac_plausibility_render.js) is a JSX string that
// Babel transpiles IN THE BROWSER, so nothing in the Python suite executes it.
// This file compiles it as the runner does, checks the pure mp* functions on
// synthetic counts, and renders the page through a minimal fake React so a crash
// in any level fails here, not on the page.

import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import { transformSync } from '@babel/core';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(HERE, '..', 'muac_plausibility_render.js'),
  'utf8',
);

function makeReact() {
  return {
    createElement(type, props, ...children) {
      return { type, props: Object.assign({}, props, { children }) };
    },
    useState(init) {
      return [typeof init === 'function' ? init() : init, () => {}];
    },
    useMemo(fn) {
      return fn();
    },
    Fragment: 'Fragment',
  };
}

function renderTree(node, depth = 0) {
  if (depth > 200) throw new Error('render too deep');
  if (node === null || node === undefined || typeof node === 'boolean')
    return 0;
  if (Array.isArray(node))
    return node.reduce((n, c) => n + renderTree(c, depth + 1), 0);
  if (typeof node !== 'object') return 1;
  if (typeof node.type === 'function')
    return renderTree(node.type(node.props), depth + 1);
  return 1 + renderTree(node.props && node.props.children, depth + 1);
}

function load() {
  const code = transformSync(SRC, {
    presets: [['@babel/preset-react', { runtime: 'classic' }]],
    babelrc: false,
    configFile: false,
  }).code;
  const sandbox = { React: makeReact(), console };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox;
}

const M = load();

const COUNTERS = [
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
const COLUMNS = [
  'opportunity_id',
  'ward',
  'username',
  'week',
  'age_band',
  ...COUNTERS,
];

function cell(opp, ward, user, week, band, counts) {
  return [opp, ward, user, week, band, ...COUNTERS.map((k) => counts[k] || 0)];
}

// Two LLOs; LLO "A" spans two opportunities (main + R1).
const STATE = {
  generated_at: '2026-10-08T01:00:00+00:00',
  cells: {
    columns: COLUMNS,
    rows: [
      cell(1, 'Medu', 'u1', '2026-09-07', '12-23', {
        records: 200,
        valid: 200,
        tier_a_low: 1,
        tier_a_high: 1,
        tier_a_high_flat: 0,
        tier_b_assessed: 198,
        tier_b: 10,
      }),
      cell(2, 'Medu', 'u1', '2026-09-14', '12-23', {
        records: 100,
        valid: 100,
        tier_b_assessed: 100,
        tier_b: 5,
      }),
      cell(3, 'Yola', 'u2', '2026-09-07', '24-35', {
        records: 110,
        valid: 100,
        missing_muac: 10,
        tier_a_high: 12,
        tier_a_high_flat: 3,
        tier_b_assessed: 88,
      }),
      cell(3, 'Yola', 'u3', '2026-09-14', '24-35', {
        records: 5,
        valid: 5,
        tier_a_high: 2,
        tier_a_high_flat: 2,
      }),
    ],
  },
  flw_names: { 1: { u1: 'Synthetic Worker One' } },
  thresholds: {
    floor_cm: 9,
    flat_ceiling_cm: 20,
    unit_error_range: [90, 200],
    age_bands: [{ key: '6-11', label: '6-11 months', low: 6, high: 11 }],
    ceilings: { '6-11': null },
  },
  errors: [],
};
const DEFINITION = {
  name: 'MUAC/Age Plausibility',
  config: { min_n: 20, llo_by_opportunity: { 1: 'A', 2: 'A', 3: 'B' } },
};
const cfg = M.mpConfig(DEFINITION);
const cells = M.mpCells(STATE);
const ALL = { weekFrom: '', weekTo: '', llo: '', ward: '', ageBand: '' };

test('upper bound is the one-sided 95% bound', () => {
  const ub = M.mpUpperBound(0.01, 100);
  assert.ok(Math.abs(ub - (0.01 + 1.645 * Math.sqrt(0.0099 / 100))) < 1e-12);
  assert.strictEqual(M.mpUpperBound(0.01, 0), null);
});

test('colours: grey below min n, red only above the bound', () => {
  assert.strictEqual(M.mpColour(1, 5, 0.01, cfg, 'stat').colour, 'grey');
  assert.strictEqual(M.mpColour(0, 100, 0.01, cfg, 'stat').colour, 'green');
  assert.strictEqual(M.mpColour(2, 100, 0.01, cfg, 'stat').colour, 'yellow'); // ub ~2.6%
  const red = M.mpColour(5, 100, 0.01, cfg, 'stat');
  assert.strictEqual(red.colour, 'red');
  assert.strictEqual(red.elevated, true);
  // The fixed-% fallback ignores sample size above min n.
  assert.strictEqual(M.mpColour(3, 100, 0.5, cfg, 'fixed').colour, 'yellow');
});

test('LLO rolls its opportunities together; flat ceiling changes the count', () => {
  const units = M.mpUnits(cells, 'llo', cfg, STATE.flw_names);
  const a = units.find((u) => u.key === 'A');
  assert.strictEqual(a.counts.valid, 300);
  assert.strictEqual(M.mpTierA(a.counts, false), 2);
  assert.strictEqual(M.mpTierA(a.counts, true), 1);
});

test('too low and too high are shown apart and add up to implausible', () => {
  const p = M.mpBaseline(cells, ALL, cfg, false);
  for (const flat of [false, true]) {
    const units = M.mpScore(
      M.mpUnits(cells, 'ward', cfg, STATE.flw_names),
      p,
      cfg,
      'stat',
      flat,
    );
    for (const u of units) assert.strictEqual(u.tooLow + u.tooHigh, u.tierA);
  }
  const medu = M.mpScore(
    M.mpUnits(cells, 'ward', cfg, STATE.flw_names),
    p,
    cfg,
    'stat',
    false,
  ).find((u) => u.ward === 'Medu');
  assert.strictEqual(medu.tooLow, 1);
  assert.strictEqual(medu.tooHigh, 1);
  const week = M.mpWeekly(cells, true)[0];
  assert.strictEqual(week.low + week.high, week.num);
});

test('"most implausible" sorts by total count, not by rate', () => {
  const p = M.mpBaseline(cells, ALL, cfg, false);
  const units = M.mpUnits(cells, 'flw', cfg, STATE.flw_names);
  const byTotal = M.mpScore(units, p, cfg, 'stat', false, 'total');
  for (let i = 1; i < byTotal.length; i++)
    assert.ok(byTotal[i - 1].tierA >= byTotal[i].tierA);
  // u3 has the highest RATE (2/5) but is grey; u2 has the most readings flagged.
  assert.strictEqual(byTotal[0].username, 'u2');
});

test('baseline is program-wide, not narrowed by the LLO filter', () => {
  const p = M.mpBaseline(
    cells,
    Object.assign({}, ALL, { llo: 'A' }),
    cfg,
    false,
  );
  assert.ok(Math.abs(p - 16 / 405) < 1e-12);
  const fixed = M.mpConfig({
    config: { baseline_mode: 'fixed', baseline_fixed: 0.003 },
  });
  assert.strictEqual(M.mpBaseline(cells, ALL, fixed, false), 0.003);
});

test('elevated units sort first; FLW names come from the run', () => {
  const p = M.mpBaseline(cells, ALL, cfg, false);
  const flws = M.mpScore(
    M.mpUnits(cells, 'flw', cfg, STATE.flw_names),
    p,
    cfg,
    'stat',
    false,
  );
  assert.strictEqual(flws[0].username, 'u2');
  assert.strictEqual(flws[0].elevated, true);
  assert.ok(flws.some((u) => u.label === 'Synthetic Worker One'));
  assert.strictEqual(flws.find((u) => u.username === 'u3').colour, 'grey');
});

test('filters by week, ward and age band', () => {
  assert.strictEqual(
    M.mpFilter(cells, Object.assign({}, ALL, { weekFrom: '2026-09-14' }), cfg)
      .length,
    2,
  );
  assert.strictEqual(
    M.mpFilter(cells, Object.assign({}, ALL, { ward: 'Yola' }), cfg).length,
    2,
  );
  assert.strictEqual(
    M.mpFilter(cells, Object.assign({}, ALL, { ageBand: '12-23' }), cfg).length,
    2,
  );
  const weeks = M.mpWeekly(cells, false);
  assert.strictEqual(
    weeks.map((w) => w.week).join(','),
    '2026-09-07,2026-09-14',
  );
  assert.strictEqual(weeks[0].num, 14);
});

test('the page renders with a run, and without one', () => {
  assert.ok(
    renderTree(
      M.WorkflowUI({
        definition: DEFINITION,
        view: { state: { muac_plausibility: STATE } },
      }),
    ) > 50,
  );
  assert.ok(
    renderTree(M.WorkflowUI({ definition: DEFINITION, view: { state: {} } })) >
      0,
  );
});
