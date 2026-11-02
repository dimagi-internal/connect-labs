// @vitest-environment happy-dom
//
// The optional worker filters of the generic indicator reports (the registry's
// display.worker_filters), MOUNTED on the same fixture snapshot as
// indicator_render_guards.test.js: no checkbox without the declaration; with it,
// a ticked box hides the workers whose flag indicator is not above 0 (or has no
// value), the table's count follows, ticked filters AND together, and the
// headline tiles do not move.
import { afterEach, describe, expect, test } from 'vitest';
import React from 'react';
import { createRoot } from 'react-dom/client';
import { transformSync } from '@babel/core';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { LabsReport } from '../../../../components/workflow/report/index';

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
async function settle() {
  for (let i = 0; i < 4; i++)
    await React.act(async () => {
      await flush();
    });
}

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

async function mountReport(payload) {
  window.happyDOM.setURL(
    'https://labs.example/labs/workflow/1/run/?opportunity_id=10097&run_id=12',
  );
  window.LabsReport = LabsReport;
  globalThis.fetch = window.fetch = async function () {
    return { ok: false, status: 404, json: async () => ({ error: 'none' }) };
  };
  const WorkflowUI = loadRender('indicator_report_render.js');
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await React.act(async () => {
    root.render(
      React.createElement(WorkflowUI, {
        definition: {
          id: 1,
          config: {
            templateType: 'indicator_opp_report',
            multi_opp: false,
            stale_after_days: 14,
          },
        },
        instance: { id: 12, opportunity_id: 10097 },
        workers: [],
        pipelines: {},
        links: {},
        actions: {},
        onUpdateState: () => {},
        view: { isCompleted: true, state: { snapshot: payload } },
      }),
    );
  });
  await settle();
  return host;
}

async function toggle(box) {
  expect(box, 'checkbox to toggle').toBeTruthy();
  await React.act(async () => {
    box.click();
  });
  await settle();
}

// Two flag indicators on opportunity 10097's three workers. A01 carries both,
// A02 only the first, A03 neither (no value at all for the second). The
// fixture's six other workers carry neither, so a ticked filter hides them too.
function payloadWithFlags(filters) {
  const p = JSON.parse(JSON.stringify(PAYLOAD));
  const flags = {
    '10097::flw_a01': [1, 2],
    '10097::flw_a02': [1, 0],
    '10097::flw_a03': [0, null],
  };
  p.byFLW.forEach((f) => {
    const v = flags[f.key];
    if (!v) return;
    f.ind.GRP_A = { id: 'GRP_A', n: 1, value: v[0], band: 'unbanded' };
    if (v[1] !== null)
      f.ind.GRP_B = { id: 'GRP_B', n: 1, value: v[1], band: 'unbanded' };
  });
  if (filters) p.display.worker_filters = filters;
  return p;
}

function workersShown(el) {
  return ['Worker A01', 'Worker A02', 'Worker A03'].filter((name) =>
    Array.from(el.querySelectorAll('tr')).some(
      (tr) => tr.textContent.indexOf(name) >= 0,
    ),
  );
}
function countText(el) {
  const span = Array.from(el.querySelectorAll('span')).find(
    (s) => / · compare with$/.test(s.textContent) && !s.children.length,
  );
  return span ? span.textContent : null;
}
function tiles(el) {
  return Array.from(el.querySelectorAll('[data-tile-head]')).map(
    (h) => h.parentElement.textContent,
  );
}
const box = (el, ind) =>
  el.querySelector('input[type="checkbox"][data-worker-filter="' + ind + '"]');

describe('the indicator report worker filters', () => {
  test('are off without display.worker_filters: no checkbox, every worker', async () => {
    const el = await mountReport(payloadWithFlags(null));
    expect(el.querySelector('[data-worker-filter]')).toBeNull();
    expect(workersShown(el)).toEqual([
      'Worker A01',
      'Worker A02',
      'Worker A03',
    ]);
    expect(countText(el)).toBe('9 facilitators · compare with');
  });

  test('a ticked filter hides the unflagged workers and updates the count, not the tiles', async () => {
    const el = await mountReport(
      payloadWithFlags([{ indicator: 'GRP_A', label: 'Group A only' }]),
    );
    const before = tiles(el);
    expect(before.length).toBeGreaterThan(0);
    const cb = box(el, 'GRP_A');
    expect(cb.checked).toBe(false);
    expect(cb.parentElement.textContent).toBe('Group A only');
    expect(workersShown(el)).toEqual([
      'Worker A01',
      'Worker A02',
      'Worker A03',
    ]);

    await toggle(cb);
    expect(box(el, 'GRP_A').checked).toBe(true);
    expect(workersShown(el)).toEqual(['Worker A01', 'Worker A02']);
    expect(countText(el)).toBe('2 facilitators · compare with');
    expect(tiles(el)).toEqual(before);

    await toggle(box(el, 'GRP_A'));
    expect(workersShown(el)).toEqual([
      'Worker A01',
      'Worker A02',
      'Worker A03',
    ]);
    expect(countText(el)).toBe('9 facilitators · compare with');
  });

  test('a default-on filter starts ticked, and ticked filters AND together', async () => {
    const el = await mountReport(
      payloadWithFlags([
        { indicator: 'GRP_A', label: 'Group A only', default: true },
        { indicator: 'GRP_B', label: 'Group B only' },
      ]),
    );
    expect(box(el, 'GRP_A').checked).toBe(true);
    expect(box(el, 'GRP_B').checked).toBe(false);
    expect(workersShown(el)).toEqual(['Worker A01', 'Worker A02']);

    // A02's 0 and A03's missing value both fail the second filter.
    await toggle(box(el, 'GRP_B'));
    expect(workersShown(el)).toEqual(['Worker A01']);
    expect(countText(el)).toBe('1 facilitator · compare with');
  });
});
