// @vitest-environment happy-dom
// @vitest-environment-options {"settings": {"disableIframePageLoading": true}}
// Phase 2 of the sandbox: a WORKFLOW's render code in the frame. What it can make
// labs do as the viewer (routes.ts), how the host answers it (host.ts), and the
// shims that turn the run page's direct calls into bridge requests (frameShims.ts).
// Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Phase 2".
import { describe, it, expect, vi } from 'vitest';
import { checkFetch, cleanHeaders, ROUTES } from './routes';
import {
  checkNavigate,
  createDispatcher,
  createSandboxHost,
  proxyFetch,
} from './host';
import { UPDATE, WORKFLOW_METHODS, PHASE1_METHODS } from './protocol';
import { RUN_ONLY_METHODS } from './host';
import { workflowActions } from './frame';
import {
  downloadBytes,
  labsUrl,
  makeSandboxFetch,
  proxyLabsImages,
  trackObjectUrls,
} from './frameShims';

const LABS = 'https://labs.connect.dimagi.com';
const CTX = {
  labsOrigin: LABS,
  definitionId: 42,
  runId: 900,
  opportunityIds: [10, 11],
  programIds: [7],
  organizationKey: 'acme',
  // The report a companion reads (config.source_workflow_id), declared.
  readableDefinitionIds: [42, 31],
  jobIds: ['job-of-this-run'],
  defaultScope: { name: 'opportunity_id', value: '10' },
};

describe('routes: what a workflow can reach', () => {
  it.each([
    ['/labs/mcp/tokens/create/', 'POST'],
    ['/labs/mcp/tokens/create/', 'GET'],
    ['/o/authorize/?client_id=x', 'GET'],
    ['/accounts/logout/', 'POST'],
    ['/admin/', 'GET'],
    ['/labs/workflow/api/42/render-code/', 'POST'],
    ['/labs/workflow/api/42/delete/', 'POST'],
    ['/labs/workflow/api/42/share/', 'POST'],
    ['/labs/workflow/api/42/sync-template/', 'POST'],
    ['/labs/workflow/api/run/900/actions/coach/run/', 'POST'],
    ['/labs/workflow/api/run/900/actions/coach/preview/', 'POST'],
    ['/labs/workflow/api/pipeline/5/schema/', 'POST'],
    ['/labs/sandbox/render/', 'GET'],
    ['/export/labs_record/', 'GET'],
  ])('refuses %s %s', (url, method) => {
    expect(() => checkFetch(url, method, CTX)).toThrow(/cannot be/);
  });

  it.each([
    'https://evil.example/labs/workflow/api/run/900/',
    '//evil.example/labs/workflow/api/run/900/',
    'https://labs.connect.dimagi.com.evil.example/labs/workflow/api/run/900/',
    'https://u:p@labs.connect.dimagi.com/labs/workflow/api/run/900/',
    'http://labs.connect.dimagi.com/labs/workflow/api/run/900/',
    '/\\evil.example/x',
    '\t//evil.example/x',
  ])('refuses anything not on labs: %s', (url) => {
    expect(() => checkFetch(url, 'GET', CTX)).toThrow();
  });

  it('reads pass, as reads, with the parsed absolute URL', () => {
    const c = checkFetch('/audit/api/ai-agents/', 'get', CTX);
    expect(c).toMatchObject({
      href: `${LABS}/audit/api/ai-agents/`,
      method: 'GET',
      gate: 'read',
    });
  });

  it('a read naming an opportunity is bound to the workflow’s opportunities', () => {
    expect(
      checkFetch('/labs/workflow/api/image/10/abc123/', 'GET', CTX).gate,
    ).toBe('read');
    expect(() =>
      checkFetch('/labs/workflow/api/image/99/abc123/', 'GET', CTX),
    ).toThrow(/opportunity this workflow does not cover/);
    expect(() =>
      checkFetch('/audit/api/opportunity/99/image-questions/', 'GET', CTX),
    ).toThrow(/does not cover/);
  });

  it('this workflow’s own pipeline rows only', () => {
    expect(
      checkFetch('/labs/workflow/api/42/pipeline-rows/?alias=v', 'GET', CTX)
        .gate,
    ).toBe('read');
    expect(() =>
      checkFetch('/labs/workflow/api/43/pipeline-rows/?alias=v', 'GET', CTX),
    ).toThrow(/a workflow this workflow does not cover/);
  });

  it('own writes are bound to this run and definition', () => {
    expect(
      checkFetch('/labs/workflow/api/run/900/state/', 'POST', CTX).gate,
    ).toBe('own');
    expect(() =>
      checkFetch('/labs/workflow/api/run/901/state/', 'POST', CTX),
    ).toThrow(/a run this workflow does not cover/);
    expect(() =>
      checkFetch('/labs/workflow/api/41/audit-batch-config/', 'POST', CTX),
    ).toThrow(/does not cover/);
    // In edit mode there is no run (id 0): nothing is written to "run 0".
    expect(() =>
      checkFetch('/labs/workflow/api/run/0/state/', 'POST', {
        ...CTX,
        runId: 0,
      }),
    ).toThrow(/does not cover/);
  });

  it('a write may not name another scope in its query', () => {
    expect(() =>
      checkFetch(
        '/labs/workflow/api/run/900/state/?opportunity_id=99',
        'POST',
        CTX,
      ),
    ).toThrow(/opportunity_id=99/);
    expect(
      checkFetch(
        '/labs/workflow/api/run/900/state/?opportunity_id=10',
        'POST',
        CTX,
      ).gate,
    ).toBe('own');
  });

  it('writes past the run are effects, with labs’ own label', () => {
    const c = checkFetch('/tasks/123/ai/initiate/', 'POST', CTX);
    expect(c.gate).toBe('effect');
    expect(c.label).toMatch(/coaching conversation with the worker/);
    expect(checkFetch('/tasks/api/single-create/', 'POST', CTX).gate).toBe(
      'effect',
    );
    expect(
      checkFetch('/labs/workflow/api/run/900/job/start/', 'POST', CTX).gate,
    ).toBe('effect');
    expect(() =>
      checkFetch('/labs/workflow/api/run/901/job/start/', 'POST', CTX),
    ).toThrow(/does not cover/);
  });

  it('a method a route does not take is refused', () => {
    expect(() =>
      checkFetch('/labs/workflow/api/run/900/state/', 'GET', CTX),
    ).toThrow(/GET .* cannot be made/);
    expect(() => checkFetch('/audit/api/ai-agents/', 'DELETE', CTX)).toThrow(
      /cannot be made/,
    );
  });

  it('every effect route names what it does', () => {
    for (const r of ROUTES.filter((x) => x.gate === 'effect')) {
      expect(r.label, r.path.source).toBeTruthy();
    }
  });

  it('headers: only content-type and accept pass; the CSRF token is the host’s', () => {
    expect(
      cleanHeaders({
        'Content-Type': 'application/json',
        'X-CSRFToken': 'frame-made-this-up',
        Cookie: 'sessionid=x',
        Authorization: 'Bearer x',
        Accept: 'application/json',
      }),
    ).toEqual({
      'content-type': 'application/json',
      accept: 'application/json',
    });
  });
});

function workflowHandlers(overrides = {}) {
  return {
    fetch: vi.fn(async (checked) => ({
      status: 200,
      statusText: 'OK',
      headers: { 'content-type': 'application/json' },
      body: new TextEncoder().encode(JSON.stringify({ ok: checked.href }))
        .buffer,
    })),
    queryPipelineRows: vi.fn(async () => ({ rows: [], total: 0 })),
    updateState: vi.fn(async () => undefined),
    runAction: vi.fn(async () => ({ id: 1, status: 'done' })),
    completeRun: vi.fn(async () => true),
    setLocation: vi.fn(),
    notify: vi.fn(),
    ask: vi.fn(async () => true),
    actionKeys: () => ['coach'],
    ...overrides,
  };
}

function pageHandlers() {
  return {
    querySupply: vi.fn(async () => ({})),
    queryWorkflow: vi.fn(async () => ({})),
    navigate: vi.fn(),
    shareSelection: vi.fn(),
    resize: vi.fn(),
    copyText: vi.fn(async () => undefined),
    confirm: vi.fn(async () => true),
    download: vi.fn(),
    reportError: vi.fn(),
  };
}

function wfDispatcher({
  w = workflowHandlers(),
  h = pageHandlers(),
  activation = true,
  now,
} = {}) {
  return {
    w,
    h,
    d: createDispatcher(h, {
      labsOrigin: LABS,
      hasActivation: () => activation,
      mode: 'workflow',
      routeContext: CTX,
      workflow: w,
      now,
    }),
  };
}

describe('the host answers a workflow', () => {
  it('a read is made without asking', async () => {
    const { d, w } = wfDispatcher();
    const out = await d({
      id: 1,
      method: 'fetch',
      params: { url: '/audit/api/ai-agents/', method: 'GET' },
    });
    expect(out.error).toBeUndefined();
    expect(w.ask).not.toHaveBeenCalled();
    expect(w.fetch.mock.calls[0][0].href).toBe(`${LABS}/audit/api/ai-agents/`);
  });

  it('a refused route never reaches fetch', async () => {
    const { d, w } = wfDispatcher();
    const out = await d({
      id: 1,
      method: 'fetch',
      params: { url: '/labs/mcp/tokens/create/', method: 'POST', body: '{}' },
    });
    expect(out.error).toMatch(/cannot be reached from here/);
    expect(w.fetch).not.toHaveBeenCalled();
  });

  it('an effect asks first, in labs’ words, and a no stops it', async () => {
    const { d, w } = wfDispatcher({
      w: workflowHandlers({ ask: vi.fn(async () => false) }),
    });
    const out = await d({
      id: 1,
      method: 'fetch',
      params: { url: '/tasks/api/single-create/', method: 'POST', body: '{}' },
    });
    expect(w.ask).toHaveBeenCalledWith(
      'This workflow wants to create tasks, as you.',
    );
    expect(out.error).toMatch(/said no/);
    expect(w.fetch).not.toHaveBeenCalled();
  });

  it('a yes holds for that kind of write a while, then is asked again', async () => {
    let t = 1_000;
    const { d, w } = wfDispatcher({ now: () => t });
    const req = {
      method: 'fetch',
      params: { url: '/tasks/api/single-create/', method: 'POST', body: '{}' },
    };
    await d({ id: 1, ...req });
    await d({ id: 2, ...req });
    expect(w.ask).toHaveBeenCalledTimes(1);
    expect(w.fetch).toHaveBeenCalledTimes(2);
    // Another kind of effect is its own question.
    await d({
      id: 3,
      method: 'fetch',
      params: {
        url: '/audit/api/audit/create-async/',
        method: 'POST',
        body: '{}',
      },
    });
    expect(w.ask).toHaveBeenCalledTimes(2);
    t += 3 * 60 * 1000;
    await d({ id: 4, ...req });
    expect(w.ask).toHaveBeenCalledTimes(3);
  });

  it('an own write is made without asking', async () => {
    const { d, w } = wfDispatcher();
    const out = await d({
      id: 1,
      method: 'fetch',
      params: {
        url: '/labs/workflow/api/run/900/flags/',
        method: 'POST',
        body: '{}',
      },
    });
    expect(out.error).toBeUndefined();
    expect(w.ask).not.toHaveBeenCalled();
  });

  it('runAction only for an action this workflow declares', async () => {
    const { d, w } = wfDispatcher();
    const bad = await d({
      id: 1,
      method: 'runAction',
      params: { key: 'delete_everything', args: {} },
    });
    expect(bad.error).toMatch(/no action/);
    const ok = await d({
      id: 2,
      method: 'runAction',
      params: { key: 'coach', args: { workers: [] }, options: { qaOnly: 1 } },
    });
    expect(ok.result).toEqual({ id: 1, status: 'done' });
    expect(w.runAction).toHaveBeenCalledWith(
      'coach',
      { workers: [] },
      { qaOnly: false },
    );
  });

  it('completeRun goes to the host, with the page’s text as detail only', async () => {
    const { d, w } = wfDispatcher();
    const out = await d({
      id: 1,
      method: 'completeRun',
      params: { confirm: 'Done reviewing?' },
    });
    expect(out.result).toBe(true);
    expect(w.completeRun).toHaveBeenCalledWith('Done reviewing?');
  });

  it('updateState takes a plain object', async () => {
    const { d, w } = wfDispatcher();
    expect(
      (await d({ id: 1, method: 'updateState', params: { state: 'x' } })).error,
    ).toMatch(/plain object/);
    await d({ id: 2, method: 'updateState', params: { state: { a: 1 } } });
    expect(w.updateState).toHaveBeenCalledWith({ a: 1 });
  });

  it('setLocation changes query and hash only, never sync/template', async () => {
    const { d, w } = wfDispatcher();
    await d({
      id: 1,
      method: 'setLocation',
      params: { search: '?tab=2', hash: '#x', push: true },
    });
    expect(w.setLocation).toHaveBeenCalledWith('?tab=2', '#x', true);
    for (const search of ['?sync=true&template=x', 'tab=2', '//evil']) {
      const out = await d({ id: 2, method: 'setLocation', params: { search } });
      expect(out.error, search).toBeTruthy();
    }
  });

  it('navigation without a click is asked, showing where it leads', async () => {
    const ask = vi.fn(async () => true);
    const { d, h } = wfDispatcher({
      w: workflowHandlers({ ask }),
      activation: false,
    });
    await d({
      id: 1,
      method: 'navigate',
      params: { url: '/audit/5/bulk/?opportunity_id=10' },
    });
    expect(ask).toHaveBeenCalledWith(
      'This workflow wants to open /audit/5/bulk/.',
    );
    expect(h.navigate).toHaveBeenCalledWith(
      `${LABS}/audit/5/bulk/?opportunity_id=10`,
      false,
    );
  });

  it('a workflow may link to audits and tasks; a page may not; neither to APIs', () => {
    expect(checkNavigate('/audit/5/bulk/', LABS, 'workflow')).toBe(
      `${LABS}/audit/5/bulk/`,
    );
    expect(() => checkNavigate('/audit/5/bulk/', LABS, 'page')).toThrow();
    expect(() =>
      checkNavigate('/audit/api/5/delete/', LABS, 'workflow'),
    ).toThrow();
    expect(() =>
      checkNavigate('/labs/workflow/api/42/delete/', LABS, 'workflow'),
    ).toThrow();
    expect(() =>
      checkNavigate('/labs/mcp/tokens/', LABS, 'workflow'),
    ).toThrow();
  });

  it('the phase-2 methods are not answered for a page', async () => {
    const d = createDispatcher(pageHandlers(), {
      labsOrigin: LABS,
      hasActivation: () => true,
    });
    for (const method of ['fetch', 'updateState', 'runAction', 'completeRun']) {
      const out = await d({ id: 1, method, params: {} });
      expect(out.error).toBe(`${method} is not available on a page`);
    }
  });
});

describe('proxyFetch: the host makes the request as the viewer', () => {
  it('adds its own CSRF token to a write, never follows a redirect', async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response('{"a":1}', {
          status: 201,
          headers: { 'content-type': 'application/json', 'set-cookie': 'x=y' },
        }),
    );
    const checked = checkFetch(
      '/labs/workflow/api/run/900/state/',
      'POST',
      CTX,
    );
    const out = await proxyFetch(
      checked,
      {
        headers: { 'X-CSRFToken': 'fake', 'content-type': 'application/json' },
        body: '{"state":{}}',
      },
      'real-token',
      fetchImpl,
    );
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(
      `${LABS}/labs/workflow/api/run/900/state/?opportunity_id=10`,
    );
    expect(init.headers).toEqual({
      'content-type': 'application/json',
      'x-csrftoken': 'real-token',
      'x-labs-sandbox-for': '42',
    });
    expect(init.redirect).toBe('manual');
    expect(init.credentials).toBe('same-origin');
    expect(out.status).toBe(201);
    expect(out.headers).toEqual({ 'content-type': 'application/json' });
    expect(new TextDecoder().decode(out.body)).toBe('{"a":1}');
  });

  it('a GET carries no body and no token', async () => {
    const fetchImpl = vi.fn(async () => new Response('x'));
    await proxyFetch(
      checkFetch('/audit/api/ai-agents/', 'GET', CTX),
      { body: 'ignored' },
      'real-token',
      fetchImpl,
    );
    const init = fetchImpl.mock.calls[0][1];
    expect(init.body).toBeUndefined();
    expect(init.headers['x-csrftoken']).toBeUndefined();
  });
});

describe('reads of other workflows are bound to the ones this one declares', () => {
  it.each([
    '/labs/workflow/api/77/runs/history/?keys=snapshot.meta',
    '/labs/workflow/api/77/indicator-definitions/?format=json',
    '/labs/workflow/api/77/semantic/',
    '/labs/workflow/api/flw-audit-report-history/?definition_id=77',
    '/labs/workflow/api/flw-daily-summary-history/?definition_id=77',
  ])('refuses an undeclared workflow: %s', (url) => {
    expect(() => checkFetch(url, 'GET', CTX)).toThrow(/does not cover/);
  });

  it.each([
    '/labs/workflow/api/31/runs/history/?keys=snapshot.meta',
    '/labs/workflow/api/42/runs/history/?keys=snapshot.meta',
    '/labs/workflow/api/31/indicator-definitions/',
    '/labs/workflow/api/flw-audit-report-history/?definition_id=31',
  ])('allows a declared one (or its own): %s', (url) => {
    expect(() => checkFetch(url, 'GET', CTX)).not.toThrow();
  });

  it('a run is read by id, so every request says whose it is', async () => {
    // The host cannot tell whose run 5555 is; the server can, from the header.
    const checked = checkFetch('/labs/workflow/api/run/5555/', 'GET', CTX);
    expect(checked.sandboxFor).toBe('42');
    const fetchImpl = vi.fn(async () => new Response('{}'));
    await proxyFetch(
      checked,
      // The frame cannot set it, or blank it, itself.
      { headers: { 'X-Labs-Sandbox-For': '31', 'x-labs-sandbox-for': '' } },
      't',
      fetchImpl,
    );
    expect(fetchImpl.mock.calls[0][1].headers['x-labs-sandbox-for']).toBe('42');
  });

  it('a frame that names no definition reads no other workflow', () => {
    const ctx = { ...CTX, definitionId: null, readableDefinitionIds: [] };
    expect(
      checkFetch('/labs/workflow/api/run/5555/', 'GET', ctx).sandboxFor,
    ).toBe('none');
    expect(() =>
      checkFetch('/labs/workflow/api/31/runs/history/', 'GET', ctx),
    ).toThrow();
  });
});

describe('frame shims', () => {
  const base = () => `${LABS}/labs/sandbox/render/?run_id=900`;

  it('labsUrl: a labs path, never another host', () => {
    expect(labsUrl('/audit/x/', base())?.pathname).toBe('/audit/x/');
    expect(labsUrl('https://api.mapbox.com/x', base())).toBeNull();
    expect(labsUrl('//evil.example/x', base())).toBeNull();
  });

  it('fetch to labs goes over the bridge; anything else to the browser', async () => {
    const bridge = {
      request: vi.fn(async () => ({
        status: 200,
        statusText: 'OK',
        headers: { 'content-type': 'application/json' },
        body: new TextEncoder().encode('{"ok":true}').buffer,
      })),
    };
    const native = vi.fn(async () => new Response('native'));
    const f = makeSandboxFetch(bridge, base, native);
    const r = await f('/audit/api/ai-agents/?x=1', {
      method: 'post',
      headers: { 'X-CSRFToken': 'abc', 'Content-Type': 'application/json' },
      body: '{}',
    });
    expect(await r.json()).toEqual({ ok: true });
    expect(bridge.request).toHaveBeenCalledWith('fetch', {
      url: '/audit/api/ai-agents/?x=1',
      method: 'POST',
      headers: { 'x-csrftoken': 'abc', 'content-type': 'application/json' },
      body: '{}',
    });
    await f('https://api.mapbox.com/styles/v1/mapbox/x');
    expect(native).toHaveBeenCalledTimes(1);
  });

  it('a 204 answer has no body', async () => {
    const bridge = {
      request: vi.fn(async () => ({
        status: 204,
        statusText: '',
        headers: {},
        body: new ArrayBuffer(0),
      })),
    };
    const r = await makeSandboxFetch(
      bridge,
      base,
      fetch,
    )('/audit/api/ai-agents/');
    expect(r.status).toBe(204);
  });

  it('labs images are fetched over the bridge and shown as blob: URLs', async () => {
    const sandboxFetch = vi.fn(async () => new Response(new Blob(['png'])));
    const stop = proxyLabsImages(document, sandboxFetch, base);
    const img = document.createElement('img');
    img.setAttribute('src', '/labs/workflow/api/image/10/abc/');
    document.body.appendChild(img);
    const other = document.createElement('img');
    other.setAttribute('src', 'https://api.mapbox.com/x.png');
    document.body.appendChild(other);
    await new Promise((r) => setTimeout(r, 20));
    expect(sandboxFetch).toHaveBeenCalledWith(
      '/labs/workflow/api/image/10/abc/',
    );
    expect(img.getAttribute('src')).toMatch(/^blob:/);
    expect(other.getAttribute('src')).toBe('https://api.mapbox.com/x.png');
    stop();
  });

  it('a download link’s blob is read back for the bridge', async () => {
    const blobs = trackObjectUrls(window);
    const url = URL.createObjectURL(new Blob(['a,b\n1,2']));
    const bytes = await downloadBytes(url, blobs);
    expect(new TextDecoder().decode(bytes)).toBe('a,b\n1,2');
    const data = await downloadBytes('data:text/csv;base64,YSxi', blobs);
    expect(new TextDecoder().decode(data)).toBe('a,b');
    expect(await downloadBytes('/audit/x.csv', blobs)).toBeNull();
  });
});

// Every endpoint a seed template's render code (or a shared library it uses)
// fetches today, as that code builds it, for this workflow (definition 42, run 900,
// opportunities 10 and 11, program 7). If one of these stops passing, a template
// breaks in the sandbox: add the route on purpose, or move the template off it.
describe('routes cover what the seed templates fetch', () => {
  it.each([
    ['GET', '/audit/api/workflow/900/sessions/'],
    ['GET', '/audit/api/workflow/901/sessions/?opportunity_id=10'],
    ['GET', '/audit/api/ai-agents/'],
    ['GET', '/audit/api/opportunity/10/image-questions/'],
    ['GET', '/audit/api/opportunity/11/field-questions/'],
    ['GET', '/audit/api/opportunity/10/deliver-unit-types/'],
    ['GET', '/audit/api/opportunity/10/sessions-summary/'],
    ['GET', '/audit/api/program/7/sessions-summary/'],
    ['GET', '/audit/api/scope-context/'],
    ['GET', '/audit/api/55/bulk-data/'],
    ['POST', '/audit/api/audit/preview/'],
    ['GET', '/audit/image/10/abc%20def.jpg/'],
    ['GET', '/labs/workflow/api/image/10/blob-1.jpg/'],
    ['GET', '/labs/workflow/api/10/visit-images/?visit_ids=1,2'],
    ['GET', '/labs/workflow/api/run/900/snapshot/preview/?program_id=7'],
    ['GET', '/labs/workflow/api/run/31/'],
    ['GET', '/labs/workflow/api/31/runs/history/?limit=12'],
    ['GET', '/labs/workflow/api/31/indicator-definitions/?format=json'],
    ['GET', '/labs/workflow/api/42/semantic/'],
    ['GET', '/labs/workflow/api/42/pipeline-rows/?alias=visits'],
    ['GET', '/labs/workflow/api/42/pipeline-rows/stream/?alias=visits'],
    ['GET', '/labs/workflow/api/42/case-states/'],
    ['POST', '/labs/workflow/api/42/pipeline-query/'],
    ['POST', '/labs/workflow/api/42/supply-query/'],
    ['POST', '/labs/workflow/api/42/workflow-query/'],
    ['GET', '/labs/workflow/api/flw-audit-report-history/?definition_id=31'],
    ['GET', '/labs/workflow/api/flw-daily-indicator-history/?definition_id=31'],
    ['GET', '/labs/workflow/api/open-tasks/?opportunity_id=10'],
    ['GET', '/labs/workflow/api/prev-categories/'],
    ['GET', '/labs/workflow/api/open-run-state/'],
    ['POST', '/labs/workflow/api/opportunity-flws/'],
    ['GET', '/labs/workflow/api/auth-status/?next=/x'],
    ['GET', '/labs/workflow/api/ocs/status/'],
    ['GET', '/labs/workflow/api/job/abc-123/status.json?run_id=900'],
    ['GET', '/labs/benchmarks/api/10/'],
    ['GET', '/microplans/boundaries/viewport/?bbox=1,2,3,4'],
    ['GET', '/tasks/api/12/'],
    ['GET', '/tasks/12/ai/transcript/'],
    ['POST', '/labs/workflow/api/run/900/state/'],
    ['POST', '/labs/workflow/api/run/900/flags/'],
    ['POST', '/labs/workflow/api/run/900/worker-result/'],
    ['POST', '/labs/workflow/api/run/900/rename/'],
    ['POST', '/labs/workflow/api/run/900/reconcile-generation/'],
    ['POST', '/labs/workflow/api/42/audit-batch-config/'],
    ['POST', '/labs/workflow/api/run/900/job/start/'],
    ['POST', '/labs/workflow/api/run/900/complete/'],
    ['POST', '/labs/workflow/api/run/900/manager-audit/'],
    ['POST', '/tasks/api/single-create/'],
    ['POST', '/tasks/api/12/update/'],
    ['POST', '/tasks/12/ai/initiate/'],
    ['POST', '/audit/api/audit/create-async/'],
    ['POST', '/audit/api/55/delete/'],
  ])('%s %s', (method, url) => {
    expect(() => checkFetch(url, method, CTX)).not.toThrow();
  });

  it('an opportunity named in a body is bound too', () => {
    const url = '/labs/workflow/api/opportunity-flws/';
    expect(
      checkFetch(url, 'POST', CTX, JSON.stringify({ opportunities: [10, 11] }))
        .gate,
    ).toBe('read');
    expect(() =>
      checkFetch(url, 'POST', CTX, JSON.stringify({ opportunities: [10, 99] })),
    ).toThrow(/opportunity 99/);
  });

  it('a compute-only job is the run’s own; any other job is an effect', () => {
    const url = '/labs/workflow/api/run/900/job/start/';
    const body = (t) => JSON.stringify({ job_config: { job_type: t } });
    expect(checkFetch(url, 'POST', CTX, body('audit_par_rollup')).gate).toBe(
      'own',
    );
    expect(
      checkFetch(url, 'POST', CTX, body('program_audit_generate')).gate,
    ).toBe('effect');
    expect(checkFetch(url, 'POST', CTX).gate).toBe('effect');
    // A body the host cannot read as a JSON object is refused, not guessed at.
    expect(() => checkFetch(url, 'POST', CTX, 'not json')).toThrow(/JSON/);
  });
});

describe('the workflow frame on the host page', () => {
  function drawn(options = {}) {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const host = createSandboxHost({
      container,
      labsOrigin: LABS,
      payload: { a: 1, big: { rows: [1, 2, 3] } },
      handlers: pageHandlers(),
      onLeave: () => {},
      hasActivation: () => true,
      ...options,
    });
    return host;
  }

  function connect(host) {
    const frameWindow = { postMessage: vi.fn() };
    Object.defineProperty(host.iframe, 'contentWindow', {
      value: frameWindow,
      configurable: true,
    });
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'labs-sandbox:hello' },
        origin: 'null',
        source: frameWindow,
      }),
    );
    return frameWindow.postMessage.mock.calls[0][2][0];
  }

  it('fills the window below it and does not grow to its content', async () => {
    const fills = drawn({
      mode: 'workflow',
      fill: true,
      routeContext: CTX,
      workflow: workflowHandlers(),
    });
    const grows = drawn();
    const before = fills.iframe.style.height;
    for (const host of [fills, grows]) {
      const port = connect(host);
      // Content sized by 100vh reports the frame's own height back, bigger each time.
      port.postMessage({ id: 1, method: 'resize', params: { height: 4321 } });
    }
    await new Promise((r) => setTimeout(r, 30));
    expect(fills.iframe.style.height).toBe(before);
    expect(grows.iframe.style.height).toBe('4321px');
    fills.destroy();
    grows.destroy();
  });

  it('carries the page query to the shell, and nothing else', () => {
    const ok = drawn({ search: '?run_id=5&tab=2' });
    expect(ok.iframe.getAttribute('src')).toBe(
      '/labs/sandbox/render/?run_id=5&tab=2',
    );
    ok.destroy();
    for (const bad of ['//evil.example/', '?a=1#frag', 'javascript:x']) {
      const h = drawn({ search: bad });
      expect(h.iframe.getAttribute('src')).toBe('/labs/sandbox/render/');
      h.destroy();
    }
  });

  it('sends only what changed', async () => {
    const big = { rows: [1, 2, 3] };
    const host = drawn({ payload: { a: 1, big } });
    // The frame says hello: the host answers with INIT and one port.
    const port = connect(host);
    const received = [];
    port.onmessage = (e) => received.push(e.data);
    host.update({ a: 2, big });
    host.update({ a: 2, big });
    await new Promise((r) => setTimeout(r, 20));
    // `big` is the same object: it is not cloned again. An unchanged payload sends nothing.
    expect(received).toEqual([{ type: UPDATE, payload: { a: 2 } }]);
    host.destroy();
  });
});

// Jonathan, 2026-10-12: "pages should be completely definable like workflows are".
// One capability set, gated only by whether there is a run.
describe('a page can do everything a workflow can, except what needs a run', () => {
  const RUN_ONLY = ['updateState', 'completeRun', 'runAction'];
  const PAGE_CTX = { ...CTX, runId: null };

  function pageDispatcher(w = workflowHandlers(), h = pageHandlers()) {
    return {
      w,
      h,
      d: createDispatcher(h, {
        labsOrigin: LABS,
        hasActivation: () => true,
        mode: 'page',
        routeContext: PAGE_CTX,
        workflow: w,
      }),
    };
  }

  it('the run-only list is exactly the run-only methods', () => {
    expect([...RUN_ONLY_METHODS].sort()).toEqual([...RUN_ONLY].sort());
  });

  it.each(
    [...WORKFLOW_METHODS, ...PHASE1_METHODS].filter(
      (m) => !RUN_ONLY.includes(m),
    ),
  )('%s is answered for a page as for a workflow', async (method) => {
    const params = {
      fetch: { url: '/audit/api/ai-agents/', method: 'GET' },
      queryPipelineRows: { alias: 'visits', query: {} },
      setLocation: { search: '?a=1' },
      notify: { message: 'hi' },
      querySupply: { alias: 'stock' },
      queryWorkflow: { alias: 'report' },
      navigate: { url: '/audit/5/bulk/' },
      shareSelection: { visible_ids: ['a'] },
      resize: { height: 300 },
      copyText: { text: 'x' },
      confirm: { text: 'ok?' },
      download: { filename: 'a.csv', bytes: 'a,b' },
      reportError: { message: 'boom' },
    }[method];
    const run = async (mode, ctx) =>
      createDispatcher(pageHandlers(), {
        labsOrigin: LABS,
        hasActivation: () => true,
        mode,
        routeContext: ctx,
        workflow: workflowHandlers(),
      })({ id: 1, method, params });
    const page = await run('page', PAGE_CTX);
    const wf = await run('workflow', CTX);
    expect(page.error).toBeUndefined();
    expect(wf.error).toBeUndefined();
  });

  it.each(RUN_ONLY)(
    '%s is refused for a page: it has no run',
    async (method) => {
      const { d, w } = pageDispatcher();
      const out = await d({
        id: 1,
        method,
        params: { key: 'coach', state: {} },
      });
      expect(out.error).toBe(`${method} needs a run, and a page has none`);
      expect(w.updateState).not.toHaveBeenCalled();
      expect(w.completeRun).not.toHaveBeenCalled();
      expect(w.runAction).not.toHaveBeenCalled();
    },
  );

  it('a page reaches what a workflow reaches, except a run’s own routes', () => {
    expect(
      checkFetch('/labs/workflow/api/42/pipeline-rows/', 'GET', PAGE_CTX).gate,
    ).toBe('read');
    expect(checkFetch('/tasks/api/single-create/', 'POST', PAGE_CTX).gate).toBe(
      'effect',
    );
    for (const url of [
      '/labs/workflow/api/run/900/state/',
      '/labs/workflow/api/run/900/job/start/',
      '/labs/workflow/api/run/900/complete/',
      '/labs/workflow/api/run/900/worker-result/',
      '/labs/workflow/api/run/900/rename/',
    ]) {
      expect(() => checkFetch(url, 'POST', PAGE_CTX), url).toThrow(/a run/);
    }
    expect(() =>
      checkFetch('/labs/workflow/api/run/900/snapshot/', 'GET', {
        ...PAGE_CTX,
      }),
    ).not.toThrow(); // another run's saved figures are a read, as on a workflow
  });

  it('a page’s actions are a workflow’s actions', () => {
    const bridge = { request: vi.fn(async () => ({})) };
    const actions = workflowActions(bridge, () => LABS + '/labs/p/org/acme/');
    for (const name of [
      'createTask',
      'createAudit',
      'queryPipelineRows',
      'navigate',
      'download',
    ]) {
      expect(typeof actions[name], name).toBe('function');
    }
  });
});

// The adversarial review's findings, each pinned (spec, "Review fixes (phase 2)").
describe('review fixes', () => {
  it('1: a checked body must be JSON text, and the host sends what it checked', () => {
    const url = '/labs/workflow/api/opportunity-flws/';
    const bytes = new TextEncoder().encode('{"opportunities":[99]}');
    expect(() => checkFetch(url, 'POST', CTX, bytes)).toThrow(/as text/);
    expect(() =>
      checkFetch(url, 'POST', CTX, '\uFEFF{"opportunities":[99]}'),
    ).toThrow(/JSON/);
    const ok = checkFetch(url, 'POST', CTX, ' { "opportunities" : [10] } ');
    expect(ok.body).toBe('{"opportunities":[10]}');
  });

  it('1: proxyFetch sends the checked body, not the frame’s', async () => {
    const fetchImpl = vi.fn(async () => new Response('{}'));
    const checked = checkFetch(
      '/labs/workflow/api/opportunity-flws/',
      'POST',
      CTX,
      '{"opportunities":[10]}',
    );
    await proxyFetch(
      checked,
      { body: '{"opportunities":[99]}' },
      't',
      fetchImpl,
    );
    expect(fetchImpl.mock.calls[0][1].body).toBe('{"opportunities":[10]}');
  });

  it('2: a read may not name another scope in its query either', () => {
    for (const q of [
      'opportunity_id=99',
      'program_id=8',
      'organization_id=other',
    ]) {
      expect(
        () => checkFetch('/labs/workflow/api/workers/?' + q, 'GET', CTX),
        q,
      ).toThrow(/does not cover/);
    }
    expect(
      checkFetch('/labs/workflow/api/workers/?opportunity_id=10', 'GET', CTX)
        .gate,
    ).toBe('read');
  });

  it('2: a write that names no scope gets this workflow’s', () => {
    const c = checkFetch('/tasks/api/single-create/', 'POST', CTX, '{}');
    expect(new URL(c.href).searchParams.get('opportunity_id')).toBe('10');
    const r = checkFetch('/audit/api/ai-agents/', 'GET', CTX);
    expect(new URL(r.href).search).toBe('');
  });

  it('3: audit preview and creation bind the opportunities in the body', () => {
    for (const url of [
      '/audit/api/audit/preview/',
      '/audit/api/audit/create-async/',
    ]) {
      expect(() =>
        checkFetch(
          url,
          'POST',
          CTX,
          JSON.stringify({ opportunities: [{ id: 99 }] }),
        ),
      ).toThrow(/opportunity 99/);
      expect(() =>
        checkFetch(
          url,
          'POST',
          CTX,
          JSON.stringify({ opportunities: [{ id: 10 }, 11] }),
        ),
      ).not.toThrow();
    }
  });

  it('4: an effect on one record is asked every time and names it', async () => {
    const { d, w } = wfDispatcher();
    for (const id of [55, 56]) {
      await d({
        id,
        method: 'fetch',
        params: { url: `/audit/api/${id}/delete/`, method: 'POST' },
      });
    }
    expect(w.ask.mock.calls.map((c) => c[0])).toEqual([
      'This workflow wants to delete audit 55, as you.',
      'This workflow wants to delete audit 56, as you.',
    ]);
  });

  it('4: a yes to a creating kind covers a bounded number of writes', async () => {
    const { d, w } = wfDispatcher();
    const req = {
      method: 'fetch',
      params: { url: '/tasks/api/single-create/', method: 'POST', body: '{}' },
    };
    for (let i = 0; i < 201; i++) await d({ id: i, ...req });
    expect(w.ask).toHaveBeenCalledTimes(2);
  });

  it('5: only this run’s jobs can be cancelled, and only on this run', () => {
    expect(() =>
      checkFetch(
        '/labs/workflow/api/job/someone-elses/cancel/',
        'POST',
        CTX,
        '{}',
      ),
    ).toThrow(/a job/);
    expect(() =>
      checkFetch(
        '/labs/workflow/api/job/job-of-this-run/cancel/',
        'POST',
        CTX,
        '{"run_id": 901}',
      ),
    ).toThrow(/a run/);
    expect(
      checkFetch(
        '/labs/workflow/api/job/job-of-this-run/cancel/',
        'POST',
        CTX,
        '{"run_id": 900}',
      ).gate,
    ).toBe('own');
  });

  it('5: a job this host starts may then be cancelled', async () => {
    const ctx = { ...CTX, jobIds: [] };
    const w = workflowHandlers({
      fetch: vi.fn(async () => ({
        status: 200,
        statusText: 'OK',
        headers: {},
        body: new TextEncoder().encode(
          '{"success": true, "task_id": "new-job"}',
        ).buffer,
      })),
    });
    const d = createDispatcher(pageHandlers(), {
      labsOrigin: LABS,
      hasActivation: () => true,
      mode: 'workflow',
      routeContext: ctx,
      workflow: w,
    });
    await d({
      id: 1,
      method: 'fetch',
      params: {
        url: '/labs/workflow/api/run/900/job/start/',
        method: 'POST',
        body: JSON.stringify({ job_config: { job_type: 'audit_par_rollup' } }),
      },
    });
    expect(ctx.jobIds).toEqual(['new-job']);
  });

  it('7: a link may not carry a second hop', () => {
    for (const u of [
      '/labs/commcare/initiate/?next=/o/authorize/?client_id=x',
      '/labs/ocs/initiate/?redirect_uri=/labs/mcp/tokens/',
      '/audit/5/bulk/?return_to=/admin/',
    ]) {
      expect(() => checkNavigate(u, LABS, 'workflow'), u).toThrow();
    }
    expect(() =>
      checkNavigate('/labs/commcare/logout/', LABS, 'workflow'),
    ).toThrow();
    expect(checkNavigate('/labs/commcare/initiate/', LABS, 'workflow')).toBe(
      `${LABS}/labs/commcare/initiate/`,
    );
  });

  it('9: setLocation keeps the scope and run as they are', async () => {
    const w = workflowHandlers();
    const d = createDispatcher(pageHandlers(), {
      labsOrigin: LABS,
      hasActivation: () => true,
      mode: 'workflow',
      routeContext: CTX,
      workflow: w,
      currentSearch: () => '?opportunity_id=10&run_id=900',
    });
    const ok = await d({
      id: 1,
      method: 'setLocation',
      params: { search: '?opportunity_id=10&run_id=900&case=c1' },
    });
    expect(ok.error).toBeUndefined();
    for (const search of [
      '?opportunity_id=99&run_id=900',
      '?opportunity_id=10',
      '?opportunity_id=10&run_id=900&edit=true',
    ]) {
      const out = await d({ id: 2, method: 'setLocation', params: { search } });
      expect(out.error, search).toMatch(/cannot be changed/);
    }
  });

  it('10: a job’s own scope ids are bound', () => {
    const body = (cfg) =>
      JSON.stringify({ job_config: { job_type: 'audit_par_rollup', ...cfg } });
    const url = '/labs/workflow/api/run/900/job/start/';
    expect(() =>
      checkFetch(url, 'POST', CTX, body({ opportunity_id: 99 })),
    ).toThrow(/opportunity 99/);
    expect(() => checkFetch(url, 'POST', CTX, body({ program_id: 8 }))).toThrow(
      /program/,
    );
    expect(
      checkFetch(url, 'POST', CTX, body({ opportunity_id: 10 })).gate,
    ).toBe('own');
  });

  it('11: audit batch config names only this workflow’s opportunities', () => {
    const url = '/labs/workflow/api/42/audit-batch-config/';
    expect(() =>
      checkFetch(url, 'POST', CTX, JSON.stringify({ per_opp: { 99: {} } })),
    ).toThrow(/opportunity 99/);
    expect(
      checkFetch(url, 'POST', CTX, JSON.stringify({ per_opp: { 10: {} } }))
        .gate,
    ).toBe('own');
  });
});
