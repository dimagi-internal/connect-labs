/*
 * Pure-logic tests for window.Microplans (shared.js).
 * Run: node connect_labs/static/microplans/shared.test.mjs
 * Stubs window/document/mapboxgl/fetch so the classic-script IIFE loads
 * under node. No DOM env needed — every assertion is on pure helpers.
 */
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// ---- minimal globals the IIFE + helpers touch -----------------------------
class LngLatBounds {
  constructor() {
    this.pts = [];
  }
  extend(c) {
    this.pts.push(c);
    return this;
  }
}
global.window = {};
global.document = { cookie: 'csrftoken=tok123; sessionid=zzz' };
global.mapboxgl = { LngLatBounds };

const here = dirname(fileURLToPath(import.meta.url));
await import('file://' + join(here, 'shared.js'));
const M = window.Microplans;

let fails = 0;
const ok = (c, m) => {
  if (!c) {
    console.log('FAIL:', m);
    fails++;
  } else console.log('ok:', m);
};

// ---- esc ------------------------------------------------------------------
ok(M.esc('<a>&"\'') === '&lt;a&gt;&amp;&quot;&#39;', 'esc escapes all 5 chars');
ok(M.esc(null) === '' && M.esc(undefined) === '', 'esc null/undefined → ""');
ok(M.esc(42) === '42', 'esc coerces non-strings');

// ---- CSRF -----------------------------------------------------------------
ok(M.getCookie('csrftoken') === 'tok123', 'getCookie reads the cookie');
ok(M.getCookie('missing') === '', 'getCookie missing → ""');
ok(M.getCsrf() === 'tok123', 'getCsrf falls back to the cookie');
M.setCsrf('bound-token');
ok(M.getCsrf() === 'bound-token', 'setCsrf overrides the cookie');
M.setCsrf('');
ok(M.getCsrf() === 'tok123', 'clearing the bound token falls back again');

// ---- colorFor -------------------------------------------------------------
ok(M.colorFor('') === '#cbd5e1', 'colorFor empty → neutral');
ok(M.colorFor('group-a') === M.colorFor('group-a'), 'colorFor is stable');
ok(/^hsl\(/.test(M.colorFor('group-a')), 'colorFor returns an hsl()');

// ---- oppColorFor (wraparound incl. negatives) -----------------------------
ok(M.oppColorFor(0) === M.OPP_COLORS[0], 'oppColorFor(0) → first');
ok(M.oppColorFor(M.OPP_COLORS.length) === M.OPP_COLORS[0], 'wraps at length');
ok(
  M.oppColorFor(-1) === M.OPP_COLORS[M.OPP_COLORS.length - 1],
  'negative wraps to last',
);

// ---- walkCoords (leaf [lng,lat] extraction) -------------------------------
let leaves = 0;
M.walkCoords(
  [
    [
      [0, 0],
      [1, 1],
      [2, 2],
    ],
  ],
  () => leaves++,
);
ok(leaves === 3, 'walkCoords visits each leaf pair once');
leaves = 0;
M.walkCoords([0, 0], () => leaves++);
ok(leaves === 1, 'walkCoords handles a bare [lng,lat]');
M.walkCoords(null, () => leaves++);
ok(leaves === 1, 'walkCoords ignores non-arrays');

// ---- boundsOf -------------------------------------------------------------
const poly = {
  type: 'Feature',
  geometry: {
    type: 'Polygon',
    coordinates: [
      [
        [0, 0],
        [0, 1],
        [1, 1],
        [0, 0],
      ],
    ],
  },
};
ok(M.boundsOf(poly).pts.length === 4, 'boundsOf extends over a Feature');
const fc = { type: 'FeatureCollection', features: [poly, poly] };
ok(M.boundsOf(fc).pts.length === 8, 'boundsOf walks a FeatureCollection');
ok(M.boundsOf([poly, poly]).pts.length === 8, 'boundsOf accepts an array');
ok(M.boundsOf(null) === null, 'boundsOf(null) → null');
ok(
  M.boundsOf({ type: 'FeatureCollection', features: [] }) === null,
  'empty FC → null',
);

// ---- fitTo ----------------------------------------------------------------
let fit = null;
const fitMap = { fitBounds: (b, o) => (fit = { b, o }) };
M.fitTo(fitMap, poly, { maxZoom: 15 });
ok(
  fit && fit.b.pts.length === 4 && fit.o.maxZoom === 15 && fit.o.padding === 40,
  'fitTo merges opts + defaults',
);
fit = null;
M.fitTo(fitMap, { type: 'FeatureCollection', features: [] });
ok(fit === null, 'fitTo no-ops on empty geometry');
M.fitTo(null, poly); // must not throw
ok(true, 'fitTo tolerates a null map');

// ---- upsertSource (add then setData) --------------------------------------
const sources = {};
const srcMap = {
  getSource: (id) => sources[id],
  addSource: (id, cfg) =>
    (sources[id] = {
      cfg,
      data: cfg.data,
      setData(d) {
        this.data = d;
      },
    }),
};
M.upsertSource(srcMap, 's1', { a: 1 });
ok(
  sources.s1 && sources.s1.cfg.type === 'geojson',
  'upsertSource creates a geojson source',
);
M.upsertSource(srcMap, 's1', { a: 2 });
ok(sources.s1.data.a === 2, 'upsertSource setData on an existing source');

// ---- removeSourceAndLayers ------------------------------------------------
const present = new Set(['lyr-a', 'lyr-b', 'src-x']);
const rmMap = {
  getLayer: (id) => present.has(id),
  removeLayer: (id) => present.delete(id),
  getSource: (id) => present.has(id),
  removeSource: (id) => present.delete(id),
};
M.removeSourceAndLayers(rmMap, 'src-x', ['lyr-a', 'lyr-b', 'absent']);
ok(
  !present.has('lyr-a') && !present.has('lyr-b') && !present.has('src-x'),
  'removeSourceAndLayers frees layers + source',
);

// ---- chip -----------------------------------------------------------------
const c = M.chip('Buildings', '1,024');
ok(
  c.includes('Buildings') && c.includes('1,024') && c.includes('<b>'),
  'chip renders label + bold value',
);
ok(M.chip('<x>', '<y>').includes('&lt;x&gt;'), 'chip escapes its label');

// ---- debounce (trailing edge) ---------------------------------------------
let calls = 0;
const d = M.debounce(() => calls++, 20);
d();
d();
d();
ok(calls === 0, 'debounce does not fire synchronously');
await new Promise((r) => setTimeout(r, 40));
ok(calls === 1, 'debounce fires once on the trailing edge');

// ---- apiCall / apiGet (status + parse handling) ---------------------------
function stubFetch(impl) {
  global.fetch = impl;
}
stubFetch(async () => ({
  ok: true,
  status: 200,
  json: async () => ({ status: 'ok', value: 7 }),
}));
ok(
  (await M.apiCall('/u', {})).value === 7,
  'apiCall resolves parsed JSON on 2xx',
);

stubFetch(async () => ({
  ok: false,
  status: 500,
  json: async () => ({ detail: 'kaboom' }),
}));
let err = null;
try {
  await M.apiCall('/u', {});
} catch (e) {
  err = e;
}
ok(
  err && err.message === 'kaboom',
  'apiCall rejects with the server detail on non-2xx',
);

stubFetch(async () => ({
  ok: true,
  status: 200,
  json: async () => ({ status: 'error', error: 'bad input' }),
}));
err = null;
try {
  await M.apiCall('/u', {});
} catch (e) {
  err = e;
}
ok(
  err && err.message === 'bad input',
  'apiCall treats {status:"error"} as a failure',
);

stubFetch(async () => {
  const e = new Error('x');
  e.name = 'AbortError';
  throw e;
});
err = null;
try {
  await M.apiGet('/g', { signal: {} });
} catch (e) {
  err = e;
}
ok(err && err.aborted === true, 'apiGet surfaces aborts as {aborted:true}');

stubFetch(async () => {
  throw new Error('socket hang up');
});
err = null;
try {
  await M.apiGet('/g');
} catch (e) {
  err = e;
}
ok(
  err && /Network error/.test(err.message),
  'apiGet maps network errors to a friendly message',
);

// ---- enqueueAndPoll (202 enqueue → poll to completion) --------------------
// Routes POST (enqueue) vs GET (poll) on the same fetch stub; poll responses
// are drained from pollSeq in order.
let pollSeq = [];
function routeEnqueuePoll(enqueueResp) {
  global.fetch = async (url, init) => {
    const method = (init && init.method) || 'GET';
    if (method === 'POST')
      return { ok: true, status: 202, json: async () => enqueueResp };
    return { ok: true, status: 200, json: async () => pollSeq.shift() };
  };
}

pollSeq = [
  { state: 'running', message: 'Fetching building footprints…' },
  { state: 'completed', result: { status: 'ok', count: 3 } },
];
routeEnqueuePoll({ task_id: 't1', poll_url: '/poll/t1' });
const progress = [];
const done = await M.enqueueAndPoll(
  '/enqueue',
  {},
  { interval: 5, onProgress: (m) => progress.push(m) },
);
ok(done.count === 3, 'enqueueAndPoll resolves the completed result envelope');
ok(
  progress.length === 1 && progress[0] === 'Fetching building footprints…',
  'enqueueAndPoll forwards progress messages',
);

routeEnqueuePoll({ task_id: 't2' }); // no poll_url
err = null;
try {
  await M.enqueueAndPoll('/enqueue', {}, { interval: 5 });
} catch (e) {
  err = e;
}
ok(
  err && /poll URL/.test(err.message),
  'enqueueAndPoll throws when the server omits poll_url',
);

pollSeq = [{ state: 'failed', detail: 'area too big' }];
routeEnqueuePoll({ task_id: 't3', poll_url: '/poll/t3' });
err = null;
try {
  await M.enqueueAndPoll('/enqueue', {}, { interval: 5 });
} catch (e) {
  err = e;
}
ok(
  err && err.message === 'area too big',
  'enqueueAndPoll rejects a failed task with its detail',
);

console.log(fails === 0 ? '\nALL PASS' : `\n${fails} FAILED`);
process.exit(fails ? 1 : 0);
