// kmc_image_audit audits the WORKFLOW'S OWN opportunities (opportunity_ids), never
// ones its config only describes (config.opp_meta). The sandbox binds a frame's reads
// to the workflow's opportunities (components/workflow/sandbox/routes.ts), and an
// author-written config list must not widen that, so the render may not fetch for an
// opportunity it does not cover. This runs the real RENDER_CODE (read out of the
// template module) through a minimal React and records every request it makes.

import { describe, expect, test } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { transformSync } from '@babel/core';

const HERE = dirname(fileURLToPath(import.meta.url));

/** RENDER_CODE as Python sees it: the triple-quoted literal, its `\\` escapes undone. */
function renderCode() {
  const py = readFileSync(join(HERE, '..', 'kmc_image_audit.py'), 'utf8');
  const start = py.indexOf('RENDER_CODE = """') + 'RENDER_CODE = """'.length;
  const end = py.indexOf('\n"""', start);
  return py.slice(start, end).replace(/\\\\/g, '\\');
}

function makeReact() {
  return {
    createElement(type, props, ...children) {
      return { type, props: Object.assign({}, props, { children }) };
    },
    useState(init) {
      return [typeof init === 'function' ? init() : init, () => {}];
    },
    useMemo: (fn) => fn(),
    useCallback: (fn) => fn,
    useRef: (v) => ({ current: v }),
    // Effects run at once: the requests a first paint makes are the ones under test.
    useEffect: (fn) => {
      fn();
    },
    Fragment: 'Fragment',
  };
}

/** Every node a tree draws, so the test can read its text. */
function walk(node, out, depth = 0) {
  if (depth > 400 || node === null || node === undefined) return out;
  if (typeof node === 'boolean') return out;
  if (Array.isArray(node)) {
    node.forEach((c) => walk(c, out, depth + 1));
    return out;
  }
  if (typeof node !== 'object') {
    out.text.push(String(node));
    return out;
  }
  if (typeof node.type === 'function') {
    return walk(node.type(node.props), out, depth + 1);
  }
  out.nodes.push(node);
  return walk(node.props && node.props.children, out, depth + 1);
}

function draw({ oppMeta, opportunityIds, selected }) {
  const code = transformSync(renderCode(), {
    presets: [['@babel/preset-react', { runtime: 'classic' }]],
    babelrc: false,
    configFile: false,
  }).code;
  const urls = [];
  const fetch = (url) => {
    urls.push(String(url));
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ success: true, sessions: [], agents: [] }),
    });
  };
  const sandbox = {
    React: makeReact(),
    console,
    fetch,
    window: { location: { search: '', pathname: '/labs/workflow/1/run/' } },
    document: { getElementById: () => null },
    setInterval: () => 0,
    clearInterval: () => {},
    setTimeout: () => 0,
    clearTimeout: () => {},
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  const tree = sandbox.WorkflowUI({
    definition: { config: { opp_meta: oppMeta } },
    instance: {
      id: 77,
      opportunity_id: opportunityIds[0],
      opportunity_ids: opportunityIds,
      state: selected ? { selected_opps: selected } : {},
    },
    actions: {},
    onUpdateState: () => Promise.resolve(),
    view: null,
    workers: [],
  });
  return { urls, drawn: walk(tree, { nodes: [], text: [] }) };
}

const META = {
  523: { llo: 'NAMA', country: 'Uganda', version: 'V0', scale: 'dial' },
  524: { llo: 'PIPN', country: 'Uganda', version: 'V0', scale: 'digital' },
  675: { llo: 'GHI-KE', country: 'Kenya', version: 'V1', scale: 'digital' },
};

function sessionOpps(urls) {
  return urls
    .filter((u) => u.includes('/sessions/'))
    .map((u) =>
      Number(
        new URL(u, 'https://labs.test').searchParams.get('opportunity_id'),
      ),
    );
}

describe('kmc_image_audit reads only its own opportunities', () => {
  test('opportunities only the config names are never fetched', () => {
    const { urls } = draw({ oppMeta: META, opportunityIds: [524] });
    expect(sessionOpps(urls)).toEqual([524]);
  });

  test('a saved selection naming an opportunity it does not cover is dropped', () => {
    const { urls } = draw({
      oppMeta: META,
      opportunityIds: [524, 675],
      selected: [523, 675],
    });
    expect(sessionOpps(urls)).toEqual([675]);
  });

  test('every opportunity both name is offered', () => {
    const { urls } = draw({ oppMeta: META, opportunityIds: [523, 524, 675] });
    expect(sessionOpps(urls).sort()).toEqual([523, 524, 675]);
  });

  test('the ones it cannot offer are named, with how to add them', () => {
    const { drawn } = draw({ oppMeta: META, opportunityIds: [524] });
    const note = drawn.nodes.find(
      (n) => n.props && n.props['data-testid'] === 'kmc-undeclared-opps',
    );
    expect(note).toBeTruthy();
    const text = walk(note.props.children, { nodes: [], text: [] }).text.join(
      '',
    );
    expect(text).toContain('#523');
    expect(text).toContain('#675');
    expect(text).not.toContain('#524');
  });
});
