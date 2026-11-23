/* Builds the engine parity cases: generated data run through the browser engine (engine_reference.js).
 *
 *   node connect_labs/audit_reviews/tests/parity/make_cases.js
 *
 * Writes cases.json next to this file. Every figure, name and username is generated from a seed:
 * no real data (this repo is public). test_engine_parity.py replays each case through engine.py
 * and expects the same world.
 *
 * Staff decisions are made by a fixed policy as the runs are replayed (confirm most proposals,
 * set some aside, close some reviews as suspended or other) and recorded in the case, so the
 * Python side replays the recorded decisions, not the policy.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const AR = require('./engine_reference.js');

function rng(seed) {
  let s = seed >>> 0;
  return function () {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const METRICS = {
  // two main rates, each with a count indicator that pools it across a worker's opportunities
  m_on_time_28d: {
    title: 'On time (last 28 days)',
    unit: '%',
    direction: 'higher',
    bands: [50, 30],
    min_den: 10,
  },
  m_visits_28d: {
    title: 'Visits per case (last 28 days)',
    unit: 'n',
    direction: 'higher',
    bands: [4.95, 3.05],
    min_den: 10,
  },
  // details
  m_missed_28d: {
    title: 'Missed (last 28 days)',
    unit: '%',
    direction: 'lower',
    bands: [10, 25],
    min_den: 10,
  },
  m_drop_28d: {
    title: 'Dropped (last 28 days)',
    unit: '%',
    direction: 'lower',
    bands: [10, 20],
    min_den: 10,
  },
  // a banded metric the set-up does not know: arrives as a detail
  m_new_28d: {
    title: 'New metric (last 28 days)',
    unit: '%',
    direction: 'higher',
    bands: [60, 40],
    min_den: 5,
  },
  // counts behind the rates (unbanded)
  c_on_time_28d: {
    title: 'On time (count)',
    unit: 'n',
    direction: 'none',
    bands: [],
    min_den: 1,
  },
  c_visits_28d: {
    title: 'Visits (count)',
    unit: 'n',
    direction: 'none',
    bands: [],
    min_den: 1,
  },
  c_missed_28d: {
    title: 'Missed (count)',
    unit: 'n',
    direction: 'none',
    bands: [],
    min_den: 1,
  },
};
const SETUP = {
  pool: {
    m_on_time_28d: 'c_on_time_28d',
    m_visits_28d: 'c_visits_28d',
    m_missed_28d: 'c_missed_28d',
  },
  parents: { m_missed_28d: 'm_on_time_28d', m_drop_28d: 'm_visits_28d' },
  supporting: ['m_missed_28d', 'm_drop_28d'],
  known: ['m_on_time_28d', 'm_visits_28d', 'm_missed_28d', 'm_drop_28d'],
};

function gradeCell(id, value, n, r) {
  const m = METRICS[id];
  // a few cells carry KC Pulse's non-colour bands as published
  const roll = r();
  if (roll < 0.03) return { band: 'unrecorded', value: null, n: n };
  if (roll < 0.05) return { band: 'notcredible', value: value, n: n };
  if (!n) return { band: 'nodata' };
  if (n < m.min_den) return { band: 'insufficient', n: n, value: value };
  if (!m.bands.length) return { band: 'unbanded', value: value, n: n };
  const v = m.unit === '%' ? value * 100 : value;
  const [g, y] = m.bands;
  const band =
    m.direction === 'lower'
      ? v <= g
        ? 'green'
        : v <= y
          ? 'yellow'
          : 'red'
      : v >= g
        ? 'green'
        : v >= y
          ? 'yellow'
          : 'red';
  return { band: band, value: value, n: n };
}

/* One entity's cells for one week, drifting from its previous quality. */
function makeCells(r, q, size) {
  const cells = {};
  const n = Math.max(0, Math.round(size * (0.5 + r())));
  const onTime = Math.round(
    n * Math.min(1, Math.max(0, q.onTime + (r() - 0.5) * 0.2)),
  );
  const missed = Math.round(
    n * Math.min(1, Math.max(0, q.missed + (r() - 0.5) * 0.1)),
  );
  const vpcN = Math.max(0, Math.round(n * 0.6));
  const visits = Math.round(vpcN * Math.max(0, q.visits + (r() - 0.5)));
  cells.m_on_time_28d = gradeCell('m_on_time_28d', n ? onTime / n : null, n, r);
  cells.c_on_time_28d = { band: 'unbanded', value: onTime, n: n };
  cells.m_missed_28d = gradeCell('m_missed_28d', n ? missed / n : null, n, r);
  cells.c_missed_28d = { band: 'unbanded', value: missed, n: n };
  cells.m_visits_28d = gradeCell(
    'm_visits_28d',
    vpcN ? visits / vpcN : null,
    vpcN,
    r,
  );
  cells.c_visits_28d = { band: 'unbanded', value: visits, n: vpcN };
  const drop = Math.min(1, Math.max(0, q.drop + (r() - 0.5) * 0.1));
  cells.m_drop_28d = gradeCell('m_drop_28d', drop, vpcN, r);
  if (q.hasNew)
    cells.m_new_28d = gradeCell(
      'm_new_28d',
      Math.min(1, Math.max(0, q.onTime + 0.1)),
      n,
      r,
    );
  return cells;
}

function drift(r, q) {
  return {
    onTime: Math.min(0.95, Math.max(0.05, q.onTime + (r() - 0.5) * 0.25)),
    missed: Math.min(0.6, Math.max(0, q.missed + (r() - 0.5) * 0.12)),
    visits: Math.min(7, Math.max(1, q.visits + (r() - 0.5) * 1.6)),
    drop: Math.min(0.5, Math.max(0, q.drop + (r() - 0.5) * 0.1)),
    hasNew: q.hasNew,
  };
}

function makeData(seed, nRuns) {
  const r = rng(seed);
  const llos = ['ALPHA', 'BRAVO', 'CHARLIE'];
  const opps = [
    { id: 10301, llo: 'ALPHA' },
    { id: 10302, llo: 'ALPHA' },
    { id: 10303, llo: 'BRAVO' },
    { id: 10304, llo: 'CHARLIE' },
    { id: 10305, llo: 'CHARLIE' },
  ];
  const workers = [];
  for (let i = 0; i < 14; i++) {
    const o = opps[Math.floor(r() * opps.length)];
    const homes = [o];
    // some workers deliver on two opportunities of the same LLO
    if (r() < 0.25) {
      const sib = opps.filter(function (x) {
        return x.llo === o.llo && x.id !== o.id;
      })[0];
      if (sib) homes.push(sib);
    }
    workers.push({
      username: 'worker' + String(i + 1).padStart(2, '0'),
      name: 'Worker ' + (i + 1),
      homes: homes,
      size: 4 + Math.floor(r() * 20),
      joins: Math.floor(r() * 4),
      leaves: r() < 0.15 ? 5 + Math.floor(r() * nRuns) : 99,
    });
  }
  const q0 = function () {
    return {
      onTime: 0.3 + r() * 0.5,
      missed: r() * 0.3,
      visits: 2.5 + r() * 3,
      drop: r() * 0.25,
      hasNew: false,
    };
  };
  const Q = new Map();
  const runs = [];
  for (let w = 0; w < nRuns; w++) {
    const hasNew = w >= nRuns - 3; // a metric appears late
    const end = new Date(Date.UTC(2026, 6, 12) + w * 7 * 864e5);
    const start = new Date(end.getTime() - 6 * 864e5);
    const run = {
      run_id: 50000 + w * 3 + (w % 2),
      period_start: start.toISOString().slice(0, 10),
      period_end: end.toISOString().slice(0, 10),
      llo: {},
      opportunity: {},
      worker: [],
    };
    function q(key) {
      const cur = Q.get(key) || q0();
      const nx = drift(r, cur);
      nx.hasNew = hasNew;
      Q.set(key, nx);
      return nx;
    }
    run.programme = makeCells(r, q('program'), 400);
    llos.forEach(function (l) {
      if (!(l === 'CHARLIE' && w === 4))
        run.llo[l] = makeCells(r, q('llo:' + l), 120);
    });
    opps.forEach(function (o) {
      if (!(o.id === 10305 && w < 3))
        run.opportunity[String(o.id)] = makeCells(r, q('opp:' + o.id), 60);
    });
    workers.forEach(function (wk) {
      if (
        w < wk.joins ||
        w >= wk.leaves ||
        (wk.username === 'worker07' && w === 6)
      )
        return;
      wk.homes.forEach(function (o) {
        run.worker.push({
          key: o.id + '::' + wk.username,
          username: wk.username,
          name: wk.name,
          opp: String(o.id),
          llo: o.llo,
          cells: makeCells(r, q('flw:' + wk.username + '@' + o.id), wk.size),
        });
      });
    });
    runs.push(run);
  }
  return {
    source: {
      program_name: 'Generated program',
      opportunities: opps.map(function (o) {
        return { id: o.id, name: 'Opportunity ' + o.id, llo: o.llo };
      }),
    },
    metrics: METRICS,
    pool: SETUP.pool,
    parents: SETUP.parents,
    supporting: SETUP.supporting,
    knownMetrics: SETUP.known,
    runs: runs,
  };
}

/* Replays run by run, deciding as staff might, and returns the decisions it made. */
function decide(D, CFG, opts, seed) {
  const r = rng(seed ^ 0x9e3779b9);
  const dec = { opens: {}, closes: {}, dismissed: {} };
  let num = 1001;
  for (let upto = 1; upto <= D.runs.length; upto++) {
    const sub = Object.assign({}, D, { runs: D.runs.slice(0, upto) });
    const W = AR.replay(sub, CFG, dec, opts);
    if (W.latestRi < W.goLiveIdx) continue;
    const run = sub.runs[upto - 1];
    const rid = String(run.run_id);
    const t0 = Date.parse(run.period_end + 'T00:00:00Z') + 864e5 + 9 * 3600e3;
    (W.proposalsByRun[W.latestRi] || []).forEach(function (p, i) {
      const at = t0 + i * 60e3;
      if (p.cls === 'open') {
        const x = r();
        if (x < 0.7)
          (dec.opens[rid] = dec.opens[rid] || {})[p.ent] = {
            num: num++,
            by: 'staff' + (1 + (i % 3)),
            at: at,
          };
        else if (x < 0.85)
          (dec.dismissed[rid] = dec.dismissed[rid] || {})[p.ent] = {
            by: 'staff1',
            at: at,
          };
      } else if (p.cls === 'close') {
        if (r() < 0.6)
          (dec.closes[rid] = dec.closes[rid] || {})[p.ent] = {
            status: 'closed_improved',
            by: 'staff2',
            at: at,
          };
      } else if (p.kind === 'update' && r() < 0.05) {
        (dec.closes[rid] = dec.closes[rid] || {})[p.ent] = {
          status: r() < 0.5 ? 'closed_suspended' : 'closed_other',
          by: 'staff3',
          at: at,
        };
      }
    });
  }
  return dec;
}

/* The world in a comparable form: Maps become objects, functions and per-run entity maps are dropped. */
function serialize(W) {
  const mapObj = function (m) {
    const o = {};
    m.forEach(function (v, k) {
      o[k] = v;
    });
    return o;
  };
  return JSON.parse(
    JSON.stringify({
      goLiveIdx: W.goLiveIdx,
      latestRi: W.latestRi,
      pf: W.pf,
      entityOrder: W.ents.map(function (m) {
        return Array.from(m.keys());
      }),
      entityInfo: mapObj(W.entityInfo),
      history: mapObj(W.history),
      reviews: W.reviews,
      openBy: Array.from(W.openBy.keys()),
      proposalsByRun: W.proposalsByRun,
    }),
  );
}

const CASES = [
  { name: 'go_live_mid_history', seed: 11, runs: 12, goLiveAt: 5, saved: null },
  { name: 'go_live_first_run', seed: 23, runs: 10, goLiveAt: 0, saved: null },
  {
    name: 'stricter_settings',
    seed: 37,
    runs: 12,
    goLiveAt: 4,
    saved: {
      closeAfter: 3,
      levelActions: { opportunity: ['investigate'] },
      metrics: {
        m_visits_28d: { persistence: 2 },
        m_on_time_28d: { levels: ['llo', 'flw'] },
      },
    },
  },
  {
    // Saved empty lists are settings, not gaps (an empty list is truthy in JS):
    // no actions at FLW and LLO level, and only one metric known (the rest arrive as details).
    name: 'saved_empty_lists',
    seed: 53,
    runs: 10,
    goLiveAt: 3,
    saved: {
      levelActions: { flw: [], llo: [] },
      knownMetrics: ['m_visits_28d'],
    },
  },
  {
    name: 'preview_no_go_live',
    seed: 41,
    runs: 8,
    goLiveAt: null,
    saved: null,
  },
];

const out = CASES.map(function (c) {
  const D = makeData(c.seed, c.runs);
  const CFG = AR.defaultConfig(D, c.saved);
  const opts =
    c.goLiveAt == null
      ? { goLiveRunId: 'none' }
      : { goLiveRunId: D.runs[c.goLiveAt].run_id };
  const decisions = decide(D, CFG, opts, c.seed);
  const W = AR.replay(D, CFG, decisions, opts);
  return {
    name: c.name,
    D: D,
    saved: c.saved,
    opts: opts,
    decisions: decisions,
    config: CFG,
    world: serialize(W),
  };
});
fs.writeFileSync(
  path.join(__dirname, 'cases.json'),
  JSON.stringify(out) + String.fromCharCode(10),
);
out.forEach(function (c) {
  const w = c.world;
  console.log(
    c.name +
      ': ' +
      w.reviews.length +
      ' reviews, ' +
      w.proposalsByRun.reduce(function (a, p) {
        return a + (p ? p.length : 0);
      }, 0) +
      ' proposals, ' +
      Object.keys(w.history).length +
      ' entities',
  );
});
