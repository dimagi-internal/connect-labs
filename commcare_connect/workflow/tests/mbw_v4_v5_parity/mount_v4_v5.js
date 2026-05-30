#!/usr/bin/env node
/* Mount v4 and v5 workflow templates via the same JSX → React path the
 * browser uses (Babel.transform with the react preset, then eval), and
 * confirm their server-rendered HTML is byte-identical for the initial
 * pre-analysis state.
 *
 * This catches:
 *   - Any prop-shape mismatch (v5 was edited to destructure `view`)
 *   - React mount errors (hook order, undefined access, etc.)
 *   - JSX structural drift between the two templates
 *
 * Companion to test_parity.py which verifies the *compute* layer. Together
 * they cover: same SQL inputs → same data → same DOM.
 *
 * Usage:
 *   node mount_v4_v5.js <v4-render> <v5-render> <fixture-json>
 *
 * Exit code 0 = HTML byte-identical. Exit code 1 = diff (printed to stderr).
 */

const fs = require('fs');
const Babel = require('@babel/standalone');
const React = require('react');
const ReactDOMServer = require('react-dom/server');

if (process.argv.length < 5) {
  console.error(
    'Usage: node mount_v4_v5.js <v4-render> <v5-render> <fixture-json>',
  );
  process.exit(2);
}
const [, , v4Path, v5Path, fixturePath] = process.argv;
const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));

function makeView() {
  return {
    workers: [],
    pipelines: {
      visits: { rows: fixture.visits },
      visits_agg: { rows: fixture.visits_agg },
      registrations: { rows: fixture.registrations },
      gs_forms: { rows: fixture.gs_forms },
    },
    state: {
      selected_workers: fixture.active_usernames,
      worker_results: {},
      task_states: {},
      audit_statuses: {},
      previous_metrics: {},
      previous_categories: {},
    },
    isCompleted: false,
    asOf: null,
    complete: async () => true,
  };
}

const mockInstance = {
  id: 999,
  opportunity_id: 12345,
  definition_id: 678,
  status: 'in_progress',
  state: {
    selected_workers: fixture.active_usernames,
    worker_results: {},
    task_states: {},
    audit_statuses: {},
    previous_metrics: {},
    previous_categories: {},
  },
};
const mockWorkers = fixture.active_usernames.map((u) => ({
  username: u,
  name: fixture.flw_names[u] || u,
  opportunity_id: 12345,
  last_active: '2025-05-20T00:00:00Z',
}));
const noop = () => {};
const asyncNoop = async () => ({ success: true });
const links = { auditUrl: () => '#', taskUrl: () => '#' };
const actions = {
  startJob: asyncNoop,
  streamJobProgress: () => () => {},
  completeRun: asyncNoop,
  saveWorkerResult: asyncNoop,
  openTaskCreator: noop,
  getTaskDetail: asyncNoop,
  getAISessions: asyncNoop,
  getAITranscript: asyncNoop,
};
const onUpdateState = async () => true;

global.window = global.window || {};
global.document = global.document || {
  querySelector: () => null,
  cookie: '',
  createElement: () => ({}),
  head: { appendChild: () => {} },
};
global.fetch = () =>
  Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
global.alert = () => {};

function loadComponent(renderPath) {
  const code = fs.readFileSync(renderPath, 'utf8');
  const wrapped = `(function(React) { ${code}\nreturn WorkflowUI; })`;
  const transpiled = Babel.transform(wrapped, { presets: ['react'] }).code;
  // eslint-disable-next-line no-eval
  return eval(transpiled)(React);
}

function renderOnce(label, Component) {
  const view = makeView();
  const props = {
    definition: {
      name: 'shared',
      statuses: [],
      config: {},
      pipeline_sources: [],
    },
    instance: mockInstance,
    workers: mockWorkers,
    pipelines: view.pipelines,
    view: view,
    links: links,
    actions: actions,
    onUpdateState: onUpdateState,
  };
  try {
    const html = ReactDOMServer.renderToString(
      React.createElement(Component, props),
    );
    return { ok: true, html: html };
  } catch (e) {
    return { ok: false, error: e.message, stack: e.stack };
  }
}

const v4 = loadComponent(v4Path);
const v5 = loadComponent(v5Path);

const v4Result = renderOnce('v4', v4);
const v5Result = renderOnce('v5', v5);

if (!v4Result.ok) {
  console.error('v4 mount FAIL:', v4Result.stack);
  process.exit(1);
}
if (!v5Result.ok) {
  console.error('v5 mount FAIL:', v5Result.stack);
  process.exit(1);
}

if (v4Result.html === v5Result.html) {
  console.log(
    `OK: v4 and v5 mount to byte-identical HTML (${v4Result.html.length} bytes)`,
  );
  process.exit(0);
} else {
  console.error('FAIL: v4 and v5 mount to different HTML');
  console.error('--- v4 ---');
  console.error(v4Result.html);
  console.error('--- v5 ---');
  console.error(v5Result.html);
  process.exit(1);
}
