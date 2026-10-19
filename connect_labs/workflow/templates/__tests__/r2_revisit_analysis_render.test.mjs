// The R2 Revisit Analysis render (r2_revisit_analysis_render.js) is a JSX string
// Babel transpiles IN THE BROWSER, so nothing in the Python suite executes it.
// This compiles it as the runner does, checks the pure rr* functions on a small
// synthetic ward, and renders both tabs through a minimal fake React.

import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import { transformSync } from '@babel/core';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(HERE, '..', 'r2_revisit_analysis_render.js'),
  'utf8',
);

function makeReact(stateOverrides = {}) {
  let n = 0;
  return {
    createElement(type, props, ...children) {
      return { type, props: Object.assign({}, props, { children }) };
    },
    useState(init) {
      const i = n++;
      const v = i in stateOverrides ? stateOverrides[i] : init;
      return [typeof v === 'function' ? v() : v, () => {}];
    },
    useMemo(fn) {
      return fn();
    },
    useRef(v) {
      return { current: v };
    },
    useEffect() {},
    Fragment: 'Fragment',
  };
}

function load(React) {
  const code = transformSync(SRC, {
    presets: [['@babel/preset-react', { runtime: 'classic' }]],
  }).code;
  const ctx = {
    React,
    location: { pathname: '/labs/workflow/1/run/' },
    window: {},
    console,
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return ctx;
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

const COLS = [
  'main',
  'r1',
  'algo_add',
  'susp',
  'susp_not_r1',
  'inacc_not_r1',
  'rejected',
  'rejected_not_r1',
  'total_r2',
].map((k) => [k, k, k]);

const cen = (x) => JSON.stringify({ type: 'Point', coordinates: [x, 9.5] });
const wa = (c, ward, status, owner, x, evc, bld) => ({
  wa_case_id: c,
  ward,
  wa_status: status,
  owner,
  centroid: cen(x),
  evc,
  bld,
});

// Ward A: c1 kept by the algorithm (2 of 10 HSD), c2 out (8/10 > 40%), c3 out
// (MUAC on 3/3), c4 out (NCF, 1 building), c5 kept (NCF, 3 buildings).
// Ward_B: c6 suspended + inaccessible (EVC 0, so not the algorithm), c7 in R1,
// c8 rejected (9/10 HSD, so not the algorithm). c9 is Excluded: left out.
function fixture(ctx) {
  const main = [
    wa('c1', 'A', 'VISITED', 'u1', 1, 10, 5),
    wa('c2', 'A', 'VISITED', 'u2', 2, 10, 5),
    wa('c3', 'A', 'VISITED', 'u2', 3, 10, 5),
    wa('c4', 'A', 'VISITED', 'u2', 4, 10, 1),
    wa('c5', 'A', 'VISITED', 'u2', 5, 10, 3),
    wa('c6', 'Ward_B', 'INACCESSIBLE', 'u1', 6, 0, 2),
    wa('c7', 'Ward_B', 'VISITED', 'u2', 7, 10, 5),
    wa('c8', 'Ward_B', 'VISITED', 'u2', 8, 10, 5),
    wa('c9', 'Ward_B', 'EXCLUDED', 'u2', 9, 10, 5),
  ];
  const r1 = [
    wa('', 'Ward B', 'VISITED', 'x', 7, 10, 5),
    wa('', 'C', 'VISITED', 'x', 99, 10, 5),
  ];
  const visits = [
    ['c1', 2, 0, 0, 0],
    ['c2', 8, 0, 0, 0],
    ['c3', 3, 3, 0, 0],
    ['c4', 0, 0, 0, 1],
    ['c5', 0, 0, 0, 1],
    ['c7', 1, 0, 0, 0],
    ['c8', 9, 0, 0, 0],
  ].map(([c, hsd, muac, dw, ncf]) => ({ wa_case_id: c, hsd, muac, dw, ncf }));
  return {
    wa_main: ctx.rrCompact('wa_main', main),
    wa_r1: ctx.rrCompact('wa_r1', r1),
    workers: ctx.rrCompact('workers', [
      { uname: 'u1', suspended: 'true' },
      { uname: 'u2', suspended: 'false' },
    ]),
    rejected: ctx.rrCompact('rejected', [{ wa_case_id: 'c8' }]),
    visits: ctx.rrCompact('visits', visits),
  };
}

test('rrClassifyOrg counts every column per ward', () => {
  const ctx = load(makeReact());
  const out = ctx.rrClassifyOrg(fixture(ctx), COLS);
  const byWard = Object.fromEntries(out.wards.map((w) => [w.ward, w]));
  const row = (w) => COLS.map((c) => byWard[w][c[0]]).join('|');
  // main|r1|algo|susp|susp_not_r1|inacc_not_r1|rejected|rejected_not_r1|total
  assert.strictEqual(row('A'), '5|0|2|1|1|0|0|0|2');
  assert.strictEqual(row('Ward B'), '3|1|0|1|1|1|1|1|2');
  assert.strictEqual(row('C'), '0|1|0|0|0|0|0|0|0');
  assert.deepStrictEqual(Object.keys(byWard['Ward B'].raws), ['Ward_B']);
});

test('each work area gets the first category that applies', () => {
  const ctx = load(makeReact());
  const was = ctx.rrClassifyOrg(fixture(ctx), COLS).was;
  const cats = Object.fromEntries(
    Object.entries(was).map(([k, v]) => [k, v.cat]),
  );
  assert.deepStrictEqual(cats, {
    c1: 'susp',
    c2: 'none',
    c3: 'none',
    c4: 'none',
    c5: 'algo',
    c6: 'susp',
    c7: 'r1',
    c8: 'rej',
  });
});

test('rrWorkAreaFeatures keeps classified shapes with a geometry', () => {
  const ctx = load(makeReact());
  const was = ctx.rrClassifyOrg(fixture(ctx), COLS).was;
  const poly = JSON.stringify({
    type: 'Polygon',
    coordinates: [
      [
        [0, 0],
        [1, 0],
        [1, 1],
        [0, 0],
      ],
    ],
  });
  const fc = ctx.rrWorkAreaFeatures(
    [
      { wa_case_id: 'c5', slug: 'S5', boundary: poly },
      { wa_case_id: 'c9', slug: 'S9', boundary: poly }, // Excluded
      { wa_case_id: 'c1', slug: 'S1', boundary: null },
    ],
    was,
  );
  assert.strictEqual(fc.features.length, 1);
  assert.strictEqual(fc.features[0].properties.cat, 'algo');
  assert.strictEqual(fc.features[0].properties.color, '#3b82f6');
  assert.match(ctx.rrPopupHtml(fc.features[0].properties), /S5/);
});

test('rrWardFeature matches ward names loosely', () => {
  const ctx = load(makeReact());
  const rows = [
    {
      name: 'Birin Fulani West',
      boundary: '{"type":"Point","coordinates":[1,2]}',
    },
  ];
  assert.ok(ctx.rrWardFeature(rows, 'birin_fulani  west'));
  assert.strictEqual(ctx.rrWardFeature(rows, 'Kamo'), null);
});

function props() {
  return {
    definition: {
      name: 'R2',
      config: {
        orgs: [{ org: 'X', main: 1, r1: 2 }],
        sources: [['workers', 'workers', 'Workers', 0]],
        columns: COLS,
        text: { notes: ['n'] },
        program_id: 1,
      },
    },
    instance: {},
    actions: {},
  };
}

test('the page renders the Table tab', () => {
  const ctx = load(makeReact());
  assert.ok(renderTree(ctx.WorkflowUI(props())) > 10);
});

test('the page renders the Map tab without a ward or a map library', () => {
  // useState #5 is the tab.
  const ctx = load(makeReact({ 5: 'map' }));
  const tree = ctx.WorkflowUI(props());
  assert.ok(renderTree(tree) > 5);
  assert.match(JSON.stringify(tree), /Pick an organisation and a ward/);
});
