// @vitest-environment happy-dom
//
// The optional "Audit photos" button of the generic indicator reports
// (config.photo_audit), MOUNTED on the same fixture snapshot as
// indicator_render_guards.test.js: off without the config, and with it one
// createAudit call per click carrying exactly one worker, a date window ending on
// their last visit, the configured photo types only, and this run.
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
// The fixture's first worker: opportunity 10097, last visit 2026-09-29.
const WORKER = PAYLOAD.byFLW[0];
const USERNAME = WORKER.key.split('::')[1];

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
let fetched = [];
afterEach(async () => {
  if (root)
    await React.act(async () => {
      root.unmount();
    });
  if (host) host.remove();
  root = null;
  host = null;
  fetched = [];
});

async function mount(file, { url, props, routes }) {
  window.happyDOM.setURL('https://labs.example' + url);
  window.LabsReport = LabsReport;
  globalThis.fetch = window.fetch = async function (u) {
    fetched.push(String(u));
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

async function click(el) {
  expect(el, 'element to click').toBeTruthy();
  await React.act(async () => {
    el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  });
  await settle();
}

async function choose(select, value) {
  expect(select, 'select to change').toBeTruthy();
  await React.act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLSelectElement.prototype,
      'value',
    ).set;
    setter.call(select, String(value));
    select.dispatchEvent(new window.Event('change', { bubbles: true }));
  });
  await settle();
}

function buttonsNamed(el, text) {
  return Array.from(el.querySelectorAll('button')).filter(
    (b) => b.textContent.trim() === text,
  );
}

const PHOTO_AUDIT = {
  image_paths: ['group_one/photo_a', '/form/group_two/photo_b'],
  recent_days: 30,
  window_options: [30, 60, 90],
  title: 'Photo check',
};

// createAudit records each payload. `stream` (optional) answers
// streamAuditProgress: {complete: result} | {error: message} | 'hold' (never
// answers; its cleanup is counted).
function stubActions(result, stream) {
  const calls = [];
  const actions = {
    calls,
    stopped: 0,
    createAudit: (payload) => {
      calls.push(JSON.parse(JSON.stringify(payload)));
      return result && result.then ? result : Promise.resolve(result);
    },
  };
  if (stream)
    actions.streamAuditProgress = (taskId, onProgress, onComplete, onError) => {
      expect(taskId).toBe('task-fake-1');
      if (stream.complete) setTimeout(() => onComplete(stream.complete), 0);
      if (stream.error) setTimeout(() => onError(stream.error), 0);
      return () => {
        actions.stopped += 1;
      };
    };
  return actions;
}

const OK = { success: true, task_id: 'task-fake-1' };
// run_audit_creation's final result, one session with photos / none.
const FOUND = {
  success: true,
  sessions: [{ id: 501, title: 't', visits: 4, images: 12 }],
  total_visits: 4,
  total_images: 12,
};
const NONE_FOUND = {
  success: true,
  sessions: [],
  total_visits: 0,
  total_images: 0,
};

function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

// The fixture with the first worker's own last visit moved earlier than their
// case's last visit (a case can be visited by another worker).
function payloadWithOwnLast(day) {
  const p = JSON.parse(JSON.stringify(PAYLOAD));
  p.byFLW[0].lastVisit = day;
  return p;
}

function expectPayload(call, { start, end, days, run }) {
  expect(call.opportunities).toEqual([{ id: 10097, name: expect.any(String) }]);
  expect(call.criteria).toEqual({
    audit_type: 'date_range',
    granularity: 'per_flw',
    start_date: start,
    end_date: end,
    selected_flw_user_ids: [USERNAME],
    tag: expect.stringMatching(/^photo-audit:[0-9a-z]+-[0-9a-z]+$/),
    title: 'Photo check (' + days + ' days to ' + end + ')',
  });
  // Exact question paths, no 'form/' prefix or leading slash; no reviewers.
  expect(call.image_audits).toEqual([
    { image_path: 'group_one/photo_a', reviewers: [] },
    { image_path: 'group_two/photo_b', reviewers: [] },
  ]);
  expect(call.workflow_run_id).toBe(run);
  expect(call.ai_agent_id).toBeUndefined();
  expect(call.criteria.count_per_flw).toBeUndefined();
}

// ── The opportunity report (indicator_report_render.js) ─────────────────────

function oppProps(config, actions, payload) {
  return {
    definition: {
      id: 1,
      config: Object.assign(
        {
          templateType: 'indicator_opp_report',
          multi_opp: false,
          stale_after_days: 14,
        },
        config,
      ),
    },
    instance: { id: 12, opportunity_id: 10097 },
    workers: [],
    pipelines: {},
    links: {},
    actions,
    onUpdateState: () => {},
    view: { isCompleted: true, state: { snapshot: payload || PAYLOAD } },
  };
}

function mountReport(config, actions, payload) {
  return mount('indicator_report_render.js', {
    url: '/labs/workflow/1/run/?opportunity_id=10097&run_id=12',
    props: oppProps(config, actions, payload),
    routes: {},
  });
}

function rowButton(el, key) {
  return el.querySelector(
    'button[data-photo-audit="' + (key || WORKER.key) + '"]',
  );
}
function rowNote(el, key) {
  return el.querySelector(
    '[data-photo-audit-note="' + (key || WORKER.key) + '"]',
  );
}

describe('the indicator report photo audit', () => {
  test('is off without config.photo_audit: no button, no extra fetch', async () => {
    const actions = stubActions(OK);
    const el = await mountReport({}, actions);
    expect(el.textContent).toContain(WORKER.name);
    expect(buttonsNamed(el, 'Audit photos')).toEqual([]);
    expect(el.querySelector('[data-photo-audit-window]')).toBeNull();
    expect(fetched.filter((u) => u.indexOf('/audit/') >= 0)).toEqual([]);
  });

  test('is off when photo_audit names no image paths', async () => {
    const el = await mountReport(
      { photo_audit: { image_paths: [], recent_days: 30 } },
      stubActions(OK),
    );
    expect(buttonsNamed(el, 'Audit photos')).toEqual([]);
  });

  test('one click creates one audit of that worker over the default window', async () => {
    const actions = stubActions(OK);
    const el = await mountReport({ photo_audit: PHOTO_AUDIT }, actions);
    await click(rowButton(el));
    expect(actions.calls.length).toBe(1);
    expectPayload(actions.calls[0], {
      start: '2026-08-31',
      end: '2026-09-29',
      days: 30,
      run: 12,
    });
    expect(rowButton(el).textContent).toBe('Photo audit queued');
    expect(rowButton(el).disabled).toBe(true);
    const note = el.querySelector(
      '[data-photo-audit-note="' + WORKER.key + '"]',
    );
    expect(note.textContent).toContain('Photo audit queued for ' + WORKER.name);
    expect(note.textContent).toContain('Reviewers will find it under Audits');
    expect(note.querySelector('a').getAttribute('href')).toBe(
      '/audit/?opportunity_id=10097',
    );
    // The click did not open the worker's case list as well.
    expect(rowButton(el).closest('tr').className).not.toContain('bg-indigo-50');
  });

  test('a longer window moves the start back', async () => {
    const actions = stubActions(OK);
    const el = await mountReport({ photo_audit: PHOTO_AUDIT }, actions);
    const select = el.querySelector('[data-photo-audit-window]');
    expect(
      Array.from(select.querySelectorAll('option')).map((o) => o.value),
    ).toEqual(['30', '60', '90']);
    await choose(select, 90);
    await click(rowButton(el));
    expect(actions.calls.length).toBe(1);
    expectPayload(actions.calls[0], {
      start: '2026-07-02',
      end: '2026-09-29',
      days: 90,
      run: 12,
    });
  });

  test('a failed creation shows the error and allows another try', async () => {
    const actions = stubActions({ success: false, error: 'Not authenticated' });
    const el = await mountReport({ photo_audit: PHOTO_AUDIT }, actions);
    await click(rowButton(el));
    const note = el.querySelector(
      '[data-photo-audit-note="' + WORKER.key + '"]',
    );
    expect(note.textContent).toContain(
      'Could not create the photo audit: Not authenticated',
    );
    expect(rowButton(el).disabled).toBe(false);
  });

  test('follows the task: photos found', async () => {
    const actions = stubActions(OK, { complete: FOUND });
    const el = await mountReport({ photo_audit: PHOTO_AUDIT }, actions);
    await click(rowButton(el));
    expect(rowNote(el).textContent).toContain(
      'Photo audit ready: 12 photos for ' + WORKER.name,
    );
    expect(rowNote(el).querySelector('a').getAttribute('href')).toBe(
      '/audit/?opportunity_id=10097',
    );
    expect(rowButton(el).textContent).toBe('Photo audit ready');
    expect(rowButton(el).disabled).toBe(true);
  });

  test('follows the task: no photo in the window', async () => {
    const actions = stubActions(OK, { complete: NONE_FOUND });
    const el = await mountReport({ photo_audit: PHOTO_AUDIT }, actions);
    await click(rowButton(el));
    expect(rowNote(el).textContent).toContain(
      'No photos found for ' + WORKER.name + ' in this window',
    );
    expect(rowButton(el).textContent).toBe('Audit photos');
    expect(rowButton(el).disabled).toBe(false);
  });

  test('follows the task: the task fails', async () => {
    const actions = stubActions(OK, { error: 'Task failed' });
    const el = await mountReport({ photo_audit: PHOTO_AUDIT }, actions);
    await click(rowButton(el));
    expect(rowNote(el).textContent).toContain(
      'Could not create the photo audit: Task failed',
    );
    expect(rowButton(el).disabled).toBe(false);
  });

  test('a task still building is shown, and stopped on unmount', async () => {
    const actions = stubActions(OK, 'hold');
    const el = await mountReport({ photo_audit: PHOTO_AUDIT }, actions);
    await click(rowButton(el));
    expect(rowButton(el).textContent).toBe('Building…');
    expect(rowButton(el).disabled).toBe(true);
    expect(rowNote(el).textContent).toContain('Building the photo audit');
    await React.act(async () => {
      root.unmount();
    });
    root = null;
    expect(actions.stopped).toBe(1);
  });

  test("one worker's pending audit does not touch another's row", async () => {
    const pending = deferred();
    const actions = stubActions(pending.promise);
    const el = await mountReport({ photo_audit: PHOTO_AUDIT }, actions);
    const other = PAYLOAD.byFLW[1].key;
    await click(rowButton(el));
    expect(rowButton(el).textContent).toBe('Creating…');
    expect(rowButton(el, other).textContent).toBe('Audit photos');
    await click(rowButton(el, other));
    expect(actions.calls.length).toBe(2);
    // Each worker's own resume key.
    expect(actions.calls[0].criteria.tag).not.toBe(
      actions.calls[1].criteria.tag,
    );
    expect(actions.calls[1].criteria.selected_flw_user_ids).toEqual([
      other.split('::')[1],
    ]);
    await React.act(async () => {
      pending.resolve(OK);
    });
    await settle();
    expect(rowNote(el).textContent).toContain(
      'Photo audit queued for ' + WORKER.name,
    );
    expect(rowNote(el, other).textContent).toContain(
      'Photo audit queued for ' + PAYLOAD.byFLW[1].name,
    );
  });

  test('the tag follows the photo paths, not their order', async () => {
    const tagFor = async (paths) => {
      const actions = stubActions(OK);
      const el = await mountReport(
        { photo_audit: Object.assign({}, PHOTO_AUDIT, { image_paths: paths }) },
        actions,
      );
      await click(rowButton(el));
      await React.act(async () => {
        root.unmount();
      });
      root = null;
      host.remove();
      host = null;
      return actions.calls[0].criteria.tag;
    };
    const ab = await tagFor(['group_one/photo_a', 'group_two/photo_b']);
    const ba = await tagFor(['group_two/photo_b', 'group_one/photo_a']);
    const a = await tagFor(['group_one/photo_a']);
    expect(ab).toBe(ba);
    expect(a).not.toBe(ab);
  });

  test("the window ends on the worker's own last visit, not a case's", async () => {
    const actions = stubActions(OK);
    const el = await mountReport(
      { photo_audit: PHOTO_AUDIT },
      actions,
      payloadWithOwnLast('2026-09-20'),
    );
    await click(rowButton(el));
    expect(actions.calls[0].criteria.end_date).toBe('2026-09-20');
    expect(actions.calls[0].criteria.start_date).toBe('2026-08-22');
  });

  test('a window is never 0 days when the config arrives after mount', async () => {
    const actions = stubActions(OK);
    const el = await mountReport({}, actions);
    await React.act(async () => {
      root.render(
        React.createElement(
          loadRender('indicator_report_render.js'),
          oppProps({ photo_audit: PHOTO_AUDIT }, actions),
        ),
      );
    });
    await settle();
    expect(el.querySelector('[data-photo-audit-window]').value).toBe('30');
    await click(rowButton(el));
    expect(actions.calls[0].criteria.start_date).toBe('2026-08-31');
    expect(actions.calls[0].criteria.title).toBe(
      'Photo check (30 days to 2026-09-29)',
    );
  });
});

// ── The worker review (indicator_worker_review_render.js) ───────────────────

function workerProps(config, actions) {
  return {
    definition: {
      id: 3,
      config: Object.assign(
        {
          templateType: 'indicator_worker_review',
          multi_opp: true,
          source_workflow_id: 1,
        },
        config,
      ),
    },
    instance: { id: 13, program_id: 1 },
    workers: [],
    pipelines: {},
    links: {},
    actions,
    onUpdateState: () => {},
  };
}

function mountWorker(config, actions, payload) {
  return mount('indicator_worker_review_render.js', {
    url:
      '/labs/workflow/3/run/?program_id=1&flw=' +
      encodeURIComponent(WORKER.key) +
      '&source_run=11',
    props: workerProps(config, actions),
    routes: {
      '/snapshot/preview/': {
        snapshot: { state: { snapshot: payload || PAYLOAD } },
        source: 'stored',
      },
      '/pipeline-rows/': { rows: [] },
      '/visit-images/': { visit_images: {} },
    },
  });
}

describe('the worker review photo audit', () => {
  test('is off without config.photo_audit: no button, no extra fetch', async () => {
    const el = await mountWorker({}, stubActions(OK));
    expect(el.textContent).toContain(WORKER.name);
    expect(buttonsNamed(el, 'Audit photos')).toEqual([]);
    expect(fetched.filter((u) => u.indexOf('/audit/') >= 0)).toEqual([]);
  });

  test('one click creates one audit of this worker over the default window', async () => {
    const actions = stubActions(OK);
    const el = await mountWorker({ photo_audit: PHOTO_AUDIT }, actions);
    await click(buttonsNamed(el, 'Audit photos')[0]);
    expect(actions.calls.length).toBe(1);
    expectPayload(actions.calls[0], {
      start: '2026-08-31',
      end: '2026-09-29',
      days: 30,
      run: 13,
    });
    const note = el.querySelector('[data-photo-audit-note]');
    expect(note.textContent).toContain('Photo audit queued for ' + WORKER.name);
    expect(note.querySelector('a').getAttribute('href')).toBe(
      '/audit/?opportunity_id=10097',
    );
    expect(buttonsNamed(el, 'Photo audit queued')[0].disabled).toBe(true);
  });

  test('a longer window moves the start back', async () => {
    const actions = stubActions(OK);
    const el = await mountWorker({ photo_audit: PHOTO_AUDIT }, actions);
    await choose(el.querySelector('[data-photo-audit-window]'), 60);
    await click(buttonsNamed(el, 'Audit photos')[0]);
    expect(actions.calls.length).toBe(1);
    expectPayload(actions.calls[0], {
      start: '2026-08-01',
      end: '2026-09-29',
      days: 60,
      run: 13,
    });
  });

  test('a failed creation shows the error', async () => {
    const actions = stubActions({ success: false, error: 'Not authenticated' });
    const el = await mountWorker({ photo_audit: PHOTO_AUDIT }, actions);
    await click(buttonsNamed(el, 'Audit photos')[0]);
    expect(el.querySelector('[data-photo-audit-note]').textContent).toContain(
      'Could not create the photo audit: Not authenticated',
    );
    expect(buttonsNamed(el, 'Audit photos')[0].disabled).toBe(false);
  });

  test('follows the task: photos found', async () => {
    const actions = stubActions(OK, { complete: FOUND });
    const el = await mountWorker({ photo_audit: PHOTO_AUDIT }, actions);
    await click(buttonsNamed(el, 'Audit photos')[0]);
    expect(el.querySelector('[data-photo-audit-note]').textContent).toContain(
      'Photo audit ready: 12 photos for ' + WORKER.name,
    );
    expect(buttonsNamed(el, 'Photo audit ready')[0].disabled).toBe(true);
  });

  test('follows the task: no photo in the window', async () => {
    const actions = stubActions(OK, { complete: NONE_FOUND });
    const el = await mountWorker({ photo_audit: PHOTO_AUDIT }, actions);
    await click(buttonsNamed(el, 'Audit photos')[0]);
    expect(el.querySelector('[data-photo-audit-note]').textContent).toContain(
      'No photos found for ' + WORKER.name + ' in this window',
    );
    expect(buttonsNamed(el, 'Audit photos')[0].disabled).toBe(false);
  });

  test('follows the task: the task fails', async () => {
    const actions = stubActions(OK, { error: 'Task failed' });
    const el = await mountWorker({ photo_audit: PHOTO_AUDIT }, actions);
    await click(buttonsNamed(el, 'Audit photos')[0]);
    expect(el.querySelector('[data-photo-audit-note]').textContent).toContain(
      'Could not create the photo audit: Task failed',
    );
  });

  test('an answer arriving after the page is gone is dropped, not followed', async () => {
    const pending = deferred();
    const actions = stubActions(pending.promise, 'hold');
    const streamed = [];
    const stream = actions.streamAuditProgress;
    actions.streamAuditProgress = function () {
      streamed.push(1);
      return stream.apply(null, arguments);
    };
    const el = await mountWorker({ photo_audit: PHOTO_AUDIT }, actions);
    await click(buttonsNamed(el, 'Audit photos')[0]);
    await React.act(async () => {
      root.unmount();
    });
    root = null;
    await React.act(async () => {
      pending.resolve(OK);
    });
    await settle();
    // The page went away before createAudit answered: nothing is followed.
    expect(streamed).toEqual([]);
  });

  test('a building audit is stopped when the page goes', async () => {
    const actions = stubActions(OK, 'hold');
    const el = await mountWorker({ photo_audit: PHOTO_AUDIT }, actions);
    await click(buttonsNamed(el, 'Audit photos')[0]);
    expect(buttonsNamed(el, 'Building…')[0].disabled).toBe(true);
    await React.act(async () => {
      root.unmount();
    });
    root = null;
    expect(actions.stopped).toBe(1);
  });

  test("the window ends on the worker's own last visit, not a case's", async () => {
    const actions = stubActions(OK);
    const el = await mountWorker(
      { photo_audit: PHOTO_AUDIT },
      actions,
      payloadWithOwnLast('2026-09-20'),
    );
    await click(buttonsNamed(el, 'Audit photos')[0]);
    expect(actions.calls[0].criteria.end_date).toBe('2026-09-20');
  });
});

// The two renders parse the config the same way: the helper is one block,
// copied verbatim.
test('both renders carry the same photo_audit helper', () => {
  const block = (file) => {
    const src = readFileSync(join(HERE, '..', file), 'utf8');
    const a = src.indexOf('  // ══ Photo audit (optional: config.photo_audit)');
    const b = src.indexOf('  // What happened to a worker');
    expect(a).toBeGreaterThan(0);
    expect(b).toBeGreaterThan(a);
    return src.slice(
      a,
      src.indexOf('\n  }\n', src.indexOf('function photoAuditNote')),
    );
  };
  expect(block('indicator_worker_review_render.js')).toBe(
    block('indicator_report_render.js'),
  );
});
