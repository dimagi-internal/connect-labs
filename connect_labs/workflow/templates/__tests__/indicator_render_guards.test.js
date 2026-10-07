// @vitest-environment happy-dom
//
// The generic indicator reports (indicator_report_render.js: the programme and
// opportunity reports; indicator_worker_review_render.js), MOUNTED on a fixture
// snapshot and held to the checks in render_guards.js.
//
// Why a mounted render and not the static checks in kmc_render.test.mjs: every
// defect class here is a property of what the page DRAWS for a payload -- a
// colour on a background, a column whose cells all match, a band that covers a
// point -- so it only exists after the render has run against data, clicked
// into a case, and fetched its visits. happy-dom is the smallest DOM that lets
// React run its effects and a click land; the library (window.LabsReport) is
// the real one from components/workflow/report, and the render is transpiled
// exactly as the run page does it (Babel, React classic runtime).
//
// The fixture (fixtures/indicator_payload.json) is a semantic_snapshot payload
// from a synthetic programme, trimmed to nine workers and renamed; a few cells
// are set to Watch and Off target so the band states draw.
import { afterEach, describe, expect, test } from 'vitest';
import React from 'react';
import { createRoot } from 'react-dom/client';
import { transformSync } from '@babel/core';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { LabsReport } from '../../../../components/workflow/report/index';
import {
  constantColumns,
  contrastFailures,
  gapsOverVisits,
  unstatedBandRules,
} from './render_guards.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const PAYLOAD = JSON.parse(
  readFileSync(join(HERE, 'fixtures', 'indicator_payload.json'), 'utf8'),
);

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function loadRender(file) {
  const src = readFileSync(join(HERE, '..', file), 'utf8');
  const { code } = transformSync(src, {
    filename: file.replace(/\.js$/, '.jsx'),
    presets: [['@babel/preset-react', { runtime: 'classic' }]],
    configFile: false,
    babelrc: false,
  });
  // eslint-disable-next-line no-new-func
  return new Function('React', code + '\nreturn WorkflowUI;')(React);
}

const flush = () => new Promise((r) => setTimeout(r, 0));

let root = null;
let host = null;
afterEach(async () => {
  if (root)
    await React.act(async () => {
      root.unmount();
    });
  if (host) host.remove();
  root = null;
  host = null;
});

/** Mount a render with `fetch` answered from `routes` ({substring: body}). */
async function mount(file, { url, props, routes }) {
  window.happyDOM.setURL('https://labs.example' + url);
  window.LabsReport = LabsReport;
  globalThis.fetch = window.fetch = async function (u) {
    const key = Object.keys(routes).find((k) => String(u).indexOf(k) >= 0);
    const body = key ? routes[key] : { error: 'no fixture for ' + u };
    return {
      ok: !!key,
      status: key ? 200 : 404,
      json: async () => JSON.parse(JSON.stringify(body)),
    };
  };
  const WorkflowUI = loadRender(file);
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await React.act(async () => {
    root.render(React.createElement(WorkflowUI, props));
  });
  await settle();
  return host;
}

async function settle() {
  for (let i = 0; i < 4; i++)
    await React.act(async () => {
      await flush();
    });
}

async function click(el) {
  expect(el, 'element to click').toBeTruthy();
  await React.act(async () => {
    el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  });
  await settle();
}

function byText(container, selector, text) {
  return Array.from(container.querySelectorAll(selector)).find(
    (e) => e.textContent.replace(/\s+/g, ' ').trim() === text,
  );
}

function allGuards(el) {
  const g = {
    contrast: contrastFailures(el),
    constantColumns: constantColumns(el),
    unstatedBandRules: unstatedBandRules(el),
    gapsOverVisits: gapsOverVisits(el),
  };
  if (process.env.GUARD_DEBUG) {
    const seen = {};
    g.contrast.forEach((c) => {
      const k = c.fg + ' on ' + c.bg + ' ' + c.ratio;
      (seen[k] = seen[k] || []).push(c.text);
    });
    console.log(
      JSON.stringify(
        {
          ...g,
          contrast: Object.entries(seen).map(
            ([k, v]) => k + ' :: ' + [...new Set(v)].slice(0, 8).join(' | '),
          ),
        },
        null,
        1,
      ),
    );
  }
  return g;
}

const NONE = {
  contrast: [],
  constantColumns: [],
  unstatedBandRules: [],
  gapsOverVisits: [],
};

// ── The programme report ────────────────────────────────────────────────────

const completed = {
  isCompleted: true,
  state: { snapshot: PAYLOAD },
};

function programmeProps() {
  return {
    definition: {
      id: 1,
      config: {
        templateType: 'indicator_programme_report',
        multi_opp: true,
        stale_after_days: 14,
      },
    },
    instance: { id: 11, program_id: 1 },
    workers: [],
    pipelines: {},
    links: {},
    actions: {},
    onUpdateState: () => {},
    view: completed,
  };
}

function oppProps() {
  const p = programmeProps();
  p.definition.config = {
    templateType: 'indicator_opp_report',
    multi_opp: false,
    stale_after_days: 14,
  };
  p.instance = { id: 12, opportunity_id: 10097 };
  return p;
}

// The anonymous benchmark for opportunity 10097: its organisation against the
// other two, from the fixture's own organisation figures.
function benchmarkPayload() {
  const series = {};
  PAYLOAD.cMeasures.forEach(function (m) {
    const fig = (llo) => {
      const e = (PAYLOAD.byLLO.find((l) => l.llo === llo) || {}).ind || {};
      return e[m.indicator] || null;
    };
    series[m.series] = series[m.series] || {};
    series[m.series][m.indicator] = {
      organisations: {
        own: fig('Partner A'),
        others: [fig('Partner B'), fig('Partner C')].filter(Boolean),
      },
    };
  });
  return {
    as_of: PAYLOAD.meta.as_of,
    cohorts: { c1: { as_of: PAYLOAD.meta.as_of } },
    indicators: { c1: series },
  };
}

describe('the programme report', () => {
  test('passes every guard at the top of the drill', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/1/run/?program_id=1&run_id=11',
      props: programmeProps(),
      routes: {},
    });
    expect(el.textContent).toContain('Meeting regularity');
    expect(allGuards(el)).toEqual(NONE);
  });

  test('passes every guard drilled to one organisation', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/1/run/?program_id=1&run_id=11',
      props: programmeProps(),
      routes: {},
    });
    await click(byText(el, 'div', 'Partner A').closest('tr'));
    expect(allGuards(el)).toEqual(NONE);
  });
});

describe('the opportunity report', () => {
  test('passes every guard on its report tab', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/2/run/?opportunity_id=10097&run_id=12',
      props: oppProps(),
      routes: {},
    });
    expect(allGuards(el)).toEqual(NONE);
  });

  test('passes every guard on its benchmarks tab, a row expanded', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/2/run/?opportunity_id=10097&run_id=12',
      props: oppProps(),
      routes: { '/labs/benchmarks/api/': benchmarkPayload() },
    });
    await click(byText(el, 'button', 'Benchmarks'));
    const row = byText(el, 'div', 'Meeting regularity');
    await click(row.closest('button'));
    // The row really opened: its bars carry the labelled target line.
    expect(el.textContent).toContain('target 80.0%');
    expect(allGuards(el)).toEqual(NONE);
  });

  test('a coverage column that reads the same on every row is said once', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/2/run/?opportunity_id=10097&run_id=12',
      props: oppProps(),
      routes: { '/labs/benchmarks/api/': benchmarkPayload() },
    });
    await click(byText(el, 'button', 'Benchmarks'));
    expect(byText(el, 'div', 'Coverage')).toBeUndefined();
    expect(el.textContent).toMatch(/all 3 \w+ covered/);
  });
});

describe('what the ported fixes do', () => {
  test('a size column equal on every row moves into the title', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/2/run/?opportunity_id=10097&run_id=12',
      props: oppProps(),
      routes: {},
    });
    // Every fixture worker holds one community.
    expect(el.textContent).toContain('1 community each');
    expect(byText(el, 'th', 'Communities↕')).toBeUndefined();
  });

  test('the legend states the rule behind Watch and Off target', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/1/run/?program_id=1&run_id=11',
      props: programmeProps(),
      routes: {},
    });
    const watch = byText(el, 'span', 'Watch');
    expect(watch.getAttribute('title')).toContain(
      'Meeting regularity 60%–80% (target ≥ 80%)',
    );
    expect(byText(el, 'span', 'Off target').getAttribute('title')).toContain(
      'Meeting regularity < 60%',
    );
  });
});

// ── The worker review ───────────────────────────────────────────────────────

const WORKER = PAYLOAD.byFLW.find((f) => f.yellows || f.reds);

// A case's visits: weekly, then a three-week gap, weekly again, then a
// two-week gap. The second gap starts at a visit that ends the first stretch
// -- the shape where one band drawn from first gap to last ran through a real
// visit.
function visitRows(caseId) {
  const dates = [
    '2026-07-06',
    '2026-07-13',
    '2026-07-20',
    '2026-08-10',
    '2026-08-17',
    '2026-08-31',
    '2026-09-07',
  ];
  return dates.map(function (d, i) {
    return {
      id: 'v' + i,
      visit_date: d,
      status: 'approved',
      entity_id: caseId,
      female_participants: 10 + i,
      male_attendance: 12,
      female_attendance: i === 4 ? 17 : 14 + i, // visit 4 repeats visit 3
      repeat_counts_flag: i === 4 ? 1 : 0,
      gps_missing_flag: 0,
      far_from_enrolment_flag: 0,
    };
  });
}

function workerProps() {
  return {
    definition: {
      id: 3,
      config: {
        templateType: 'indicator_worker_review',
        multi_opp: true,
        source_workflow_id: 1,
      },
    },
    instance: { id: 13, program_id: 1 },
    workers: [],
    pipelines: {},
    links: {},
    actions: {},
    onUpdateState: () => {},
  };
}

async function mountWorker() {
  const firstCase = PAYLOAD.cases[WORKER.rows[0]];
  const el = await mount('indicator_worker_review_render.js', {
    url:
      '/labs/workflow/3/run/?program_id=1&flw=' +
      encodeURIComponent(WORKER.key) +
      '&source_run=11',
    props: workerProps(),
    routes: {
      '/snapshot/preview/': {
        snapshot: { state: { snapshot: PAYLOAD } },
        source: 'stored',
      },
      '/pipeline-rows/': { rows: visitRows(firstCase.entity_id) },
      '/visit-images/': { visit_images: {} },
    },
  });
  return { el, firstCase };
}

describe('the worker review', () => {
  test('passes every guard with a case open', async () => {
    const { el, firstCase } = await mountWorker();
    expect(el.textContent).toContain(WORKER.name);
    const caseCell = byText(el, 'td', firstCase.entity_id);
    await click(caseCell.closest('tr'));
    expect(el.querySelectorAll('svg circle').length).toBe(7);
    expect(allGuards(el)).toEqual(NONE);
  });

  test('a status or field equal on every visit is said once, not as a column', async () => {
    const { el, firstCase } = await mountWorker();
    await click(byText(el, 'td', firstCase.entity_id).closest('tr'));
    expect(el.textContent).toContain('status: all approved');
    expect(byText(el, 'th', 'Status')).toBeUndefined();
    expect(el.textContent).toContain('Men attending: 12 at every visit');
    expect(byText(el, 'th', 'Men attending')).toBeUndefined();
    expect(byText(el, 'th', 'Women attending')).toBeTruthy();
  });

  test('the worker legend states the rule for the bands it shows', async () => {
    const { el } = await mountWorker();
    const watch = byText(el, 'span', 'Watch');
    expect(watch.getAttribute('title')).toMatch(/^Watch: .*\d+%–\d+%/);
  });

  test('a flagged visit marks the registry-named cells that repeat the last visit', async () => {
    const { el, firstCase } = await mountWorker();
    await click(byText(el, 'td', firstCase.entity_id).closest('tr'));
    const marked = Array.from(el.querySelectorAll('td[data-repeat]'));
    expect(marked.map((td) => td.textContent)).toEqual(['17']);
    expect(byText(el, 'th', 'Flags ⓘ').getAttribute('title')).toBe(
      'Repeat count: Attendance counts exactly repeat the previous visit',
    );
  });

  test('shades each gap separately, between its two visits', async () => {
    const { el, firstCase } = await mountWorker();
    await click(byText(el, 'td', firstCase.entity_id).closest('tr'));
    const gaps = Array.from(
      el.querySelectorAll('rect[data-gap]:not([data-gap-open])'),
    );
    // 20 Jul -> 10 Aug (21 days) and 17 Aug -> 31 Aug (14 days).
    expect(gaps.map((g) => g.getAttribute('data-gap'))).toEqual(['21', '14']);
    expect(gapsOverVisits(el)).toEqual([]);
  });
});

// ── What the second promotion adds (templates 7712 / 7715 / 7718) ───────────

describe('the programme report, second promotion', () => {
  test('a partner with one opportunity names it instead of a one-row table', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/1/run/?program_id=1&run_id=11',
      props: programmeProps(),
      routes: {},
    });
    await click(byText(el, 'div', 'Partner A').closest('tr'));
    expect(el.textContent).toContain('1 opportunity: Partner A');
    expect(
      Array.from(el.querySelectorAll('h2, h3, div')).some(
        (e) => e.textContent.trim() === 'Opportunities' && !e.children.length,
      ),
    ).toBe(false);
    expect(allGuards(el)).toEqual(NONE);
  });

  test('a row subline the same on every row is dropped; the header has no "Report of" chip', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/1/run/?program_id=1&run_id=11',
      props: programmeProps(),
      routes: {},
    });
    // Every fixture partner runs one opportunity with three workers.
    expect(el.textContent).not.toMatch(/1 opportunity · 3 \w+/);
    expect(el.textContent).not.toContain('Report of');
  });

  test('a rate cell states the counts behind it', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/1/run/?program_id=1&run_id=11',
      props: programmeProps(),
      routes: {},
    });
    const cell = byText(el, 'td', '99.2%');
    // Partner B, meeting regularity: 0.9919 of 124.
    expect(cell.getAttribute('title')).toBe('123 of 124');
  });

  test('headline tiles: a value bar with a target tick, and the direction chip', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/1/run/?program_id=1&run_id=11',
      props: programmeProps(),
      routes: {},
    });
    // Two targeted rate tiles (meeting regularity 80%, the other 75%).
    expect(el.querySelectorAll('[data-target-tick]').length).toBe(2);
    expect(el.textContent).toContain('lower is better');
    // A tile's label opens the indicator's definition.
    const before = el.querySelectorAll('[role="dialog"]').length;
    const label = byText(el, 'button', 'Meeting regularity');
    expect(label).toBeTruthy();
    await click(label);
    expect(el.querySelectorAll('[role="dialog"]').length).toBeGreaterThan(
      before,
    );
  });
});

describe('the opportunity report benchmarks, second promotion', () => {
  test('yours carries its signed gap to target, in points', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/2/run/?opportunity_id=10097&run_id=12',
      props: oppProps(),
      routes: { '/labs/benchmarks/api/': benchmarkPayload() },
    });
    await click(byText(el, 'button', 'Benchmarks'));
    const gaps = Array.from(el.querySelectorAll('[data-target-gap]')).map(
      (e) => e.textContent,
    );
    // Partner A's meeting regularity, 72.0% against a target of 80%.
    expect(gaps).toContain('−8.0 points to target');
    expect(el.textContent).not.toContain('Report of');
    expect(allGuards(el)).toEqual(NONE);
  });
});

describe('the worker review, second promotion', () => {
  test('the silence since the last visit is a hatched open gap, counted in the header', async () => {
    const { el, firstCase } = await mountWorker();
    await click(byText(el, 'td', firstCase.entity_id).closest('tr'));
    // Last visit 7 Sep, report as of 4 Oct: 27 days and still open.
    const open = el.querySelector('rect[data-gap-open]');
    expect(open.getAttribute('data-gap')).toBe('27');
    expect(el.textContent).toContain('still open (no visit since)');
    expect(el.textContent).toContain('no visit for over 7 days');
    expect(el.textContent).toContain(
      '7 visits · 1 flagged · 3 gaps over 7 days',
    );
    expect(el.textContent).toContain('as of 4 Oct 2026');
    expect(gapsOverVisits(el)).toEqual([]);
  });

  test('every gap bound is labelled on the date axis', async () => {
    const { el, firstCase } = await mountWorker();
    await click(byText(el, 'td', firstCase.entity_id).closest('tr'));
    const labels = Array.from(el.querySelectorAll('svg text')).map(
      (t) => t.textContent,
    );
    ['6 Jul', '20 Jul', '10 Aug', '17 Aug', '31 Aug', '7 Sep'].forEach(
      function (d) {
        expect(labels).toContain(d);
      },
    );
  });

  test('days since the previous visit, the over-threshold ones marked', async () => {
    const { el, firstCase } = await mountWorker();
    await click(byText(el, 'td', firstCase.entity_id).closest('tr'));
    expect(byText(el, 'th', 'Days since previous')).toBeTruthy();
    const over = Array.from(el.querySelectorAll('[data-over-gap]')).map(
      (e) => e.textContent,
    );
    expect(over).toEqual(['21', '14']);
  });

  test('the gap threshold follows config.stale_after_days when the review has one', async () => {
    const firstCase = PAYLOAD.cases[WORKER.rows[0]];
    const props = workerProps();
    props.definition.config.stale_after_days = 14;
    const el = await mount('indicator_worker_review_render.js', {
      url:
        '/labs/workflow/3/run/?program_id=1&flw=' +
        encodeURIComponent(WORKER.key) +
        '&source_run=11',
      props,
      routes: {
        '/snapshot/preview/': {
          snapshot: { state: { snapshot: PAYLOAD } },
          source: 'stored',
        },
        '/pipeline-rows/': { rows: visitRows(firstCase.entity_id) },
        '/visit-images/': { visit_images: {} },
      },
    });
    await click(byText(el, 'td', firstCase.entity_id).closest('tr'));
    const closed = Array.from(
      el.querySelectorAll('rect[data-gap]:not([data-gap-open])'),
    ).map((g) => g.getAttribute('data-gap'));
    // Only the 21-day gap is over 14 days; the 14-day one is not.
    expect(closed).toEqual(['21']);
    expect(el.textContent).toContain('2 gaps over 14 days');
  });

  test('indicators sit under category rows with the direction said once', async () => {
    const { el } = await mountWorker();
    // A category whose indicators all point one way carries one chip.
    const chips = Array.from(el.querySelectorAll('td span')).filter(
      (s) => s.textContent === 'lower is better',
    );
    expect(chips.length).toBeGreaterThan(0);
    expect(chips[0].closest('td').getAttribute('colspan')).toBeTruthy();
  });

  test('an indicator name opens its registry definition', async () => {
    const { el } = await mountWorker();
    window.fetch = globalThis.fetch;
    const seen = [];
    const orig = window.fetch;
    globalThis.fetch = window.fetch = async function (u) {
      seen.push(String(u));
      if (String(u).indexOf('/indicator-definitions/') >= 0)
        return {
          ok: true,
          status: 200,
          json: async () => ({
            indicators: [{ id: 'SF_P1', title: 'Meeting regularity' }],
          }),
        };
      return orig(u);
    };
    await click(byText(el, 'button', 'Meeting regularity'));
    expect(
      seen.some(
        (u) =>
          u.indexOf('/labs/workflow/api/1/indicator-definitions/') >= 0 &&
          u.indexOf('scope=flw') >= 0,
      ),
    ).toBe(true);
  });

  test('the header names the partner, and drops a caseload band every worker shares', async () => {
    const { el } = await mountWorker();
    expect(el.textContent).toContain('Partner A · ');
    expect(el.textContent).not.toContain('opportunity 10097');
    // Every fixture worker is in the same caseload band.
    expect(el.querySelector('[data-caseload-tip]')).toBeNull();
    expect(el.textContent).toContain('figures as of 4 Oct 2026');
  });
});

// ── A finished case is not stale (display.entity.done_property) ─────────────
// A community that completed its final step has no more meetings by design;
// its facilitator's old last-visit date is the end of the work, not a lapse.

/** The fixture with a done property declared, and two workers' only cases
 * last visited long before the as-of date: A01's finished, A02's not. */
function finishedPayload({ declare = true } = {}) {
  const p = JSON.parse(JSON.stringify(PAYLOAD));
  if (declare)
    p.display.entity = { ...p.display.entity, done_property: 'step7_done' };
  p.cases.forEach((c) => {
    c.step7_done = false;
  });
  const a01 = p.cases[0]; // flw_a01's only case
  const a02 = p.cases[1]; // flw_a02's only case
  a01.last_visit_date = '2026-08-01';
  a01.step7_done = true;
  a02.last_visit_date = '2026-08-01';
  return p;
}

function oppPropsWith(payload) {
  const props = oppProps();
  props.view = { isCompleted: true, state: { snapshot: payload } };
  return props;
}

function workerRow(el, name) {
  return byText(el, 'span', name).closest('tr');
}

function lastVisitCell(row) {
  // The trailing pair is Last visit, then Attention.
  const cells = row.querySelectorAll('td');
  return cells[cells.length - 2];
}

describe('a finished case is not stale', () => {
  test('a worker whose cases are all finished is not red; one with an open case still is', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/2/run/?opportunity_id=10097&run_id=12',
      props: oppPropsWith(finishedPayload()),
      routes: {},
    });
    const done = lastVisitCell(workerRow(el, 'Worker A01'));
    expect(done.className).not.toContain('text-red-700');
    expect(done.getAttribute('data-finished')).toBe('1');
    expect(done.textContent).toContain('finished');
    expect(done.getAttribute('title')).toContain('finished');

    const open = lastVisitCell(workerRow(el, 'Worker A02'));
    expect(open.className).toContain('text-red-700');
    expect(open.getAttribute('data-finished')).toBeNull();
    expect(open.getAttribute('title')).toMatch(/^No visits for \d+ days$/);

    // The legend explains both marks, and the page still passes every guard.
    expect(el.textContent).toContain('no visit > 14 days');
    expect(el.textContent).toContain('every community done, no visit due');
    expect(allGuards(el)).toEqual(NONE);
  });

  test('an opened worker lists the finished case as finished', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/2/run/?opportunity_id=10097&run_id=12',
      props: oppPropsWith(finishedPayload()),
      routes: {},
    });
    await click(workerRow(el, 'Worker A01'));
    // The case list's row carries the tag (the Last-visit cell is a td).
    const tags = Array.from(el.querySelectorAll('span[data-finished]'));
    expect(tags.map((t) => t.textContent)).toEqual(['finished']);
    expect(tags[0].closest('tr').textContent).toContain('case-0001');
  });

  test('without a done property, the same old date is red whatever the case says', async () => {
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/2/run/?opportunity_id=10097&run_id=12',
      props: oppPropsWith(finishedPayload({ declare: false })),
      routes: {},
    });
    const cell = lastVisitCell(workerRow(el, 'Worker A01'));
    expect(cell.className).toContain('text-red-700');
    expect(el.querySelector('[data-finished]')).toBeNull();
    expect(el.textContent).not.toContain('no visit due');
  });

  test('the worker review draws no open gap after a finished case, and still does after an open one', async () => {
    async function openCase(payload) {
      const firstCase = payload.cases[WORKER.rows[0]];
      const el = await mount('indicator_worker_review_render.js', {
        url:
          '/labs/workflow/3/run/?program_id=1&flw=' +
          encodeURIComponent(WORKER.key) +
          '&source_run=11',
        props: workerProps(),
        routes: {
          '/snapshot/preview/': {
            snapshot: { state: { snapshot: payload } },
            source: 'stored',
          },
          '/pipeline-rows/': { rows: visitRows(firstCase.entity_id) },
          '/visit-images/': { visit_images: {} },
        },
      });
      // A finished case's cell reads "<id> finished".
      const cell = Array.from(el.querySelectorAll('td')).find(
        (td) => td.textContent.trim().indexOf(firstCase.entity_id) === 0,
      );
      await click(cell.closest('tr'));
      return el;
    }
    const done = finishedPayload();
    done.cases[WORKER.rows[0]].step7_done = true;
    let el = await openCase(done);
    expect(el.querySelector('rect[data-gap-open]')).toBeNull();
    expect(el.textContent).not.toContain('still open (no visit since)');
    // the gaps BETWEEN its visits are still gaps
    expect(el.textContent).toContain('2 gaps over 7 days');
    expect(el.querySelectorAll('[data-finished]').length).toBe(2); // row + heading
    expect(allGuards(el)).toEqual(NONE);
    await React.act(async () => {
      root.unmount();
    });
    host.remove();
    root = null;

    el = await openCase(finishedPayload()); // WORKER's case not finished
    expect(el.querySelector('rect[data-gap-open]')).toBeTruthy();
    expect(el.textContent).toContain('3 gaps over 7 days');
    expect(el.querySelector('[data-finished]')).toBeNull();
  });
});

// ── The guards catch what they are for ──────────────────────────────────────
// Each guard against the defect it exists to catch, so a guard that silently
// stops matching (a renamed class, a moved attribute) fails here rather than
// passing every page.

describe('the guards themselves', () => {
  function html(s) {
    const d = document.createElement('div');
    d.innerHTML = s;
    return d;
  }
  test('contrast: the judges’ secondary grey fails, gray-600 passes', () => {
    expect(
      contrastFailures(html('<span class="text-gray-400">2 visits</span>')),
    ).toHaveLength(1);
    expect(
      contrastFailures(html('<span class="text-gray-600">2 visits</span>')),
    ).toEqual([]);
    expect(
      contrastFailures(
        html('<svg><text fill="#d1d5db" font-size="11">↑</text></svg>'),
      ),
    ).toHaveLength(1);
  });
  test('constant column: a size column of all 1s is reported', () => {
    const rows = [1, 2, 3]
      .map((i) => '<tr><td>w' + i + '</td><td>1</td></tr>')
      .join('');
    expect(
      constantColumns(
        html(
          '<table><thead><tr><th>Worker</th><th>Cases</th></tr></thead><tbody>' +
            rows +
            '</tbody></table>',
        ),
      ),
    ).toEqual([{ column: 'Cases', value: '1' }]);
  });
  test('band rule: a Watch cell with no rule is reported, a titled legend clears it', () => {
    const cell = '<span class="bg-amber-50 text-amber-800">70.0%</span>';
    expect(unstatedBandRules(html(cell))).toEqual([{ band: 'Watch' }]);
    expect(
      unstatedBandRules(
        html(cell + '<span title="Watch: Regularity 60%–80%">Watch</span>'),
      ),
    ).toEqual([]);
  });
  test('gap shading: a band across a visit is reported', () => {
    expect(
      gapsOverVisits(
        html(
          '<svg><rect data-gap="9" x="10" width="100"></rect><circle cx="50"></circle></svg>',
        ),
      ),
    ).toEqual([{ gap: [10, 110], visitAt: 50 }]);
  });
});

describe('starting coaching', () => {
  function coachingProps(calls) {
    const p = oppProps();
    p.view = {
      ...completed,
      workflowActions: [
        {
          key: 'initiate_ai_coach',
          type: 'start_ocs_outreach',
          label: 'Start coaching',
          description: 'Coach them',
        },
      ],
    };
    p.actions = {
      runAction: (key, args) => {
        calls.push({ key, args });
        return Promise.resolve(null);
      },
    };
    return p;
  }

  test('is offered only to workers with something red or yellow to coach', async () => {
    const calls = [];
    const el = await mount('indicator_report_render.js', {
      url: '/labs/workflow/2/run/?opportunity_id=10097&run_id=12',
      props: coachingProps(calls),
      routes: {},
    });
    const buttons = Array.from(el.querySelectorAll('button'));
    const rowButtons = buttons.filter(
      (b) => b.textContent.trim() === 'Start coaching',
    );
    // Four of the fixture's nine workers have a red or yellow per-worker
    // indicator; the other five have nothing off target and get no button.
    expect(rowButtons.length).toBe(4);
    const bulk = buttons.find((b) =>
      b.textContent.startsWith('Start coaching ·'),
    );
    expect(bulk.textContent).toBe(
      'Start coaching · 4 facilitators off target or on watch',
    );
    await click(bulk);
    expect(calls).toHaveLength(1);
    expect(calls[0].args.workers.map((w) => w.key).sort()).toEqual([
      '10097::flw_a02',
      '10097::flw_a03',
      '10099::flw_c02',
      '10099::flw_c03',
    ]);
  });
});
