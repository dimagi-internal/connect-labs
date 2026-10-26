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
    // Another run's state is not this run's own write: it is an effect the host asks
    // about, and the server binds it to a declared workflow (declared_reads.py).
    const other = checkFetch('/labs/workflow/api/run/901/state/', 'POST', CTX);
    expect(other.gate).toBe('effect');
    expect(other.label).toMatch(/saved state of run/);
    expect(() =>
      checkFetch('/labs/workflow/api/run/901/flags/', 'POST', CTX),
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
    stream: vi.fn(async () => undefined),
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
    scroll: vi.fn(),
    reveal: vi.fn(),
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
      'x-labs-sandbox-scope': 'opportunity_id=10',
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

  it('a job acts only on this run, and a job status is only this run’s', () => {
    const url = '/labs/workflow/api/run/900/job/start/';
    const body = (cfg) => JSON.stringify({ job_config: cfg });
    expect(
      checkFetch(
        url,
        'POST',
        CTX,
        body({ job_type: 'audit_par_rollup', run_id: 900 }),
      ).gate,
    ).toBe('own');
    expect(() =>
      checkFetch(
        url,
        'POST',
        CTX,
        body({ job_type: 'audit_par_rollup', run_id: 901 }),
      ),
    ).toThrow(/run this workflow does not cover/);
    expect(() =>
      checkFetch(
        '/labs/workflow/api/job/abc/status.json?run_id=901',
        'GET',
        CTX,
      ),
    ).toThrow(/run_id=901/);
  });

  it('every request says where its definition lives', async () => {
    const fetchImpl = vi.fn(async () => new Response('{}'));
    await proxyFetch(
      checkFetch('/labs/workflow/api/run/5555/', 'GET', CTX),
      { headers: { 'x-labs-sandbox-scope': 'program_id=999' } },
      't',
      fetchImpl,
    );
    expect(fetchImpl.mock.calls[0][1].headers['x-labs-sandbox-scope']).toBe(
      'opportunity_id=10',
    );
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
  // Saving a run's state and completing a run: the only things a page cannot do.
  // Its declared actions run within its scope (workflow/actions.py PageScope).
  const RUN_ONLY = ['updateState', 'completeRun'];
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
      overlay: { open: false },
      scroll: { top: 40 },
      reveal: { top: 10, bottom: 20 },
      copyText: { text: 'x' },
      confirm: { text: 'ok?' },
      download: { filename: 'a.csv', bytes: 'a,b' },
      reportError: { message: 'boom' },
      runAction: { key: 'coach', args: { workers: [] } },
      streamOpen: { url: '/audit/api/ai-agents/' },
      streamClose: { stream: 's1' },
    }[method];
    const run = async (mode, ctx) =>
      createDispatcher(pageHandlers(), {
        labsOrigin: LABS,
        hasActivation: () => true,
        mode,
        routeContext: ctx,
        workflow: workflowHandlers(),
        push: () => undefined,
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
    },
  );

  it('a page runs its declared actions through the host, as a workflow does', async () => {
    const { d, w } = pageDispatcher();
    const ok = await d({
      id: 1,
      method: 'runAction',
      params: { key: 'coach', args: { workers: [{ key: '10::w1' }] } },
    });
    expect(ok.error).toBeUndefined();
    expect(w.runAction).toHaveBeenCalledWith(
      'coach',
      { workers: [{ key: '10::w1' }] },
      { qaOnly: false },
    );
    const undeclared = await d({
      id: 2,
      method: 'runAction',
      params: { key: 'not_declared', args: {} },
    });
    expect(undeclared.error).toMatch(/no action/);
  });

  it('an action key that is not a slug is refused even if declared', async () => {
    const key = '../../1/delete/?x=';
    const { d, w } = pageDispatcher(
      workflowHandlers({ actionKeys: () => [key] }),
    );
    const out = await d({
      id: 3,
      method: 'runAction',
      params: { key, args: {} },
    });
    expect(out.error).toMatch(/no action/);
    expect(w.runAction).not.toHaveBeenCalled();
  });

  it('a page reaches what a workflow reaches, except a run’s own routes', () => {
    expect(
      checkFetch('/labs/workflow/api/42/pipeline-rows/', 'GET', PAGE_CTX).gate,
    ).toBe('read');
    expect(checkFetch('/tasks/api/single-create/', 'POST', PAGE_CTX).gate).toBe(
      'effect',
    );
    // A declared workflow's run's state: an effect, asked, for a page as for a workflow.
    expect(
      checkFetch('/labs/workflow/api/run/900/state/', 'POST', PAGE_CTX).gate,
    ).toBe('effect');
    for (const url of [
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

// ---------------------------------------------------------------------------
// Every live copy runs sandboxed (2026-10-13): what the live scan found missing
// ---------------------------------------------------------------------------

describe("a workflow's own data sources (attach / detach a pipeline)", () => {
  const ctx = () => ({ ...CTX, pipelineIds: [6619], jobIds: [] });

  it('reads the pipelines on offer and only those, or its own', () => {
    const c = ctx();
    expect(
      checkFetch('/labs/workflow/api/available-pipelines/', 'GET', c)
        .remembersPipelines,
    ).toBe(true);
    expect(checkFetch('/labs/workflow/api/pipeline/6619/', 'GET', c).gate).toBe(
      'read',
    );
    expect(() =>
      checkFetch('/labs/workflow/api/pipeline/7000/', 'GET', c),
    ).toThrow(/a pipeline this workflow does not cover/);
    // ...and never the pipeline's other endpoints (schema writes, sharing, SQL).
    for (const url of [
      '/labs/workflow/api/pipeline/6619/schema/',
      '/labs/workflow/api/pipeline/6619/share/',
      '/labs/workflow/api/pipeline/6619/sql/',
    ]) {
      expect(() => checkFetch(url, 'POST', c, '{}'), url).toThrow(
        /cannot be reached/,
      );
    }
  });

  it('attaching or detaching is asked every time, naming the change', () => {
    const c = ctx();
    const add = checkFetch(
      '/labs/workflow/api/42/pipeline-sources/add/',
      'POST',
      c,
      JSON.stringify({ pipeline_id: 6619, alias: 'hsd' }),
    );
    expect(add.gate).toBe('effect');
    expect(add.perRecord).toBe(true);
    expect(add.question).toBe(
      'change its own data sources: attach pipeline #6619 as "hsd"',
    );
    const remove = checkFetch(
      '/labs/workflow/api/42/pipeline-sources/remove/',
      'POST',
      c,
      JSON.stringify({ alias: 'hsd' }),
    );
    expect(remove.question).toBe(
      'change its own data sources: detach the pipeline "hsd"',
    );
    // Another workflow's sources, a pipeline it was not offered, a stray field.
    for (const [url, b] of [
      [
        '/labs/workflow/api/41/pipeline-sources/add/',
        { pipeline_id: 6619, alias: 'x' },
      ],
      [
        '/labs/workflow/api/42/pipeline-sources/add/',
        { pipeline_id: 7000, alias: 'x' },
      ],
      [
        '/labs/workflow/api/42/pipeline-sources/add/',
        { pipeline_id: 6619, alias: '"><img>' },
      ],
      [
        '/labs/workflow/api/42/pipeline-sources/add/',
        { pipeline_id: 6619, alias: 'x', definition_id: 41 },
      ],
      [
        '/labs/workflow/api/42/pipeline-sources/remove/',
        { alias: 'x', pipeline_id: 1 },
      ],
    ]) {
      expect(
        () => checkFetch(url, 'POST', c, JSON.stringify(b)),
        JSON.stringify(b),
      ).toThrow();
    }
  });

  it('the host remembers what it offered, and asks in labs’ words', async () => {
    const c = ctx();
    const w = workflowHandlers({
      fetch: vi.fn(async (checked) => ({
        status: 200,
        statusText: 'OK',
        headers: { 'content-type': 'application/json' },
        body: new TextEncoder().encode(
          JSON.stringify(
            checked.href.endsWith('/available-pipelines/')
              ? { pipelines: [{ id: 7000, name: 'Other' }, { id: 'x' }] }
              : { success: true },
          ),
        ).buffer,
      })),
    });
    const d = createDispatcher(pageHandlers(), {
      labsOrigin: LABS,
      hasActivation: () => true,
      mode: 'workflow',
      routeContext: c,
      workflow: w,
    });
    const attach = {
      method: 'fetch',
      params: {
        url: '/labs/workflow/api/42/pipeline-sources/add/',
        method: 'POST',
        body: JSON.stringify({ pipeline_id: 7000, alias: 'hsd' }),
      },
    };
    expect((await d({ id: 1, ...attach })).error).toMatch(/not offered/);
    await d({
      id: 2,
      method: 'fetch',
      params: { url: '/labs/workflow/api/available-pipelines/' },
    });
    expect(c.pipelineIds).toEqual([6619, 7000]);
    const out = await d({ id: 3, ...attach });
    expect(out.error).toBeUndefined();
    expect(w.ask).toHaveBeenCalledWith(
      'This workflow wants to change its own data sources: attach pipeline #7000 "Other" as "hsd", as you.',
    );
    // Asked again for the next one: a change to the definition is never remembered.
    await d({ id: 4, ...attach });
    expect(w.ask).toHaveBeenCalledTimes(2);
  });
});

describe('a control room that marks the runs it watches', () => {
  it('reads the run links of what it declares, bound to its scope', () => {
    expect(
      checkFetch('/labs/workflow/?opportunity_id=10', 'GET', CTX).gate,
    ).toBe('read');
    expect(() =>
      checkFetch('/labs/workflow/?opportunity_id=99', 'GET', CTX),
    ).toThrow(/does not cover/);
    // The list page's other addresses are not reads it may make.
    expect(() => checkFetch('/labs/workflow/create/', 'GET', CTX)).toThrow();
  });

  it('a change to another run is asked naming it, and a yes holds for that run only', async () => {
    const t = 1_000;
    const { d, w } = wfDispatcher({ now: () => t });
    const write = (i, run) =>
      d({
        id: i,
        method: 'fetch',
        params: {
          url: `/labs/workflow/api/run/${run}/state/`,
          method: 'POST',
          body: JSON.stringify({ state: { baseline_excluded: true } }),
        },
      });
    for (const [i, run] of [39811, 39812, 39811, 39811].entries()) {
      expect((await write(i, run)).error).toBeUndefined();
    }
    expect(w.ask.mock.calls.map((c) => c[0])).toEqual([
      'This workflow wants to change the saved state of run 39811, as you.',
      'This workflow wants to change the saved state of run 39812, as you.',
    ]);
    // The request carries who it is for: the server takes it only for a declared one.
    expect(w.fetch.mock.calls[0][0].sandboxFor).toBe('42');
    // This run's own state is still asked nothing.
    await d({
      id: 9,
      method: 'fetch',
      params: {
        url: '/labs/workflow/api/run/900/state/',
        method: 'POST',
        body: '{"state":{}}',
      },
    });
    expect(w.ask).toHaveBeenCalledTimes(2);
  });

  it('the only paths two routes share are a run’s state (own here, an effect elsewhere)', () => {
    // A request falls through to a later route only on a binding miss: list where that
    // can happen, so a new overlap is a decision, not an accident.
    function expand(src) {
      const start = src.indexOf('(');
      if (start === -1) return [src.replace(/\\(.)/g, '$1')];
      let depth = 0;
      let end = start;
      for (; end < src.length; end++) {
        if (src[end] === '(' && src[end - 1] !== '\\') depth++;
        if (src[end] === ')' && src[end - 1] !== '\\') depth--;
        if (depth === 0) break;
      }
      const optional = src[end + 1] === '?';
      const inner = src.slice(start + 1, end).replace(/^\?<\w+>/, '');
      let options;
      if (inner === '\\d+') options = ['7'];
      else if (/^\[.*\]\+$/.test(inner)) options = ['x'];
      else {
        const parts = [];
        let d = 0;
        let last = 0;
        for (let k = 0; k < inner.length; k++) {
          if (inner[k] === '(') d++;
          if (inner[k] === ')') d--;
          if (inner[k] === '|' && d === 0) {
            parts.push(inner.slice(last, k));
            last = k + 1;
          }
        }
        parts.push(inner.slice(last));
        options = parts.flatMap(expand);
      }
      if (optional) options = [...options, ''];
      const rest = expand(src.slice(end + (optional ? 2 : 1)));
      const head = src.slice(0, start).replace(/\\(.)/g, '$1');
      return options.flatMap((o) => rest.map((r) => head + o + r));
    }
    const overlapping = new Set();
    ROUTES.forEach((r, i) => {
      for (const probe of expand(r.path.source.slice(1, -1))) {
        ROUTES.forEach((other, j) => {
          if (j <= i || !r.methods.some((m) => other.methods.includes(m)))
            return;
          if (other.path.test(probe)) overlapping.add(probe);
        });
      }
    });
    expect([...overlapping]).toEqual(['/labs/workflow/api/run/7/state/']);
  });
});

describe('a sign-in start comes back to where the person was', () => {
  it('its next is allowed when it is itself a labs page a link could open', () => {
    const ok = checkNavigate(
      '/labs/commcare/initiate/?next=' +
        encodeURIComponent('/labs/workflow/42/run/?run_id=900'),
      LABS,
      'full',
    );
    expect(new URL(ok).pathname).toBe('/labs/commcare/initiate/');
    for (const next of [
      '/o/authorize/?client_id=x',
      '//evil.example/',
      'https://evil.example/',
      '/labs/ocs/initiate/?next=/o/authorize/',
      '/labs/workflow/42/run/?next=/o/authorize/',
      '/labs/mcp/tokens/',
    ]) {
      expect(
        () =>
          checkNavigate(
            '/labs/ocs/initiate/?next=' + encodeURIComponent(next),
            LABS,
            'full',
          ),
        next,
      ).toThrow();
    }
    // Two nexts, a redirect param, or the page set (no sign-in starts there): refused.
    expect(() =>
      checkNavigate(
        '/labs/ocs/initiate/?next=/labs/p/x/&next=/o/',
        LABS,
        'full',
      ),
    ).toThrow();
    expect(() =>
      checkNavigate('/labs/ocs/initiate/?redirect=/labs/p/x/', LABS, 'full'),
    ).toThrow();
    expect(() =>
      checkNavigate('/labs/workflow/42/run/?next=/labs/p/x/', LABS, 'full'),
    ).toThrow();
  });
});

// ---------------------------------------------------------------------------
// The final audit before switching the sandbox on (2026-10-23)
// ---------------------------------------------------------------------------

describe('final audit fixes', () => {
  it('a coaching conversation is asked naming its recipient, bot and opening', async () => {
    const { d, w } = wfDispatcher();
    await d({
      id: 1,
      method: 'fetch',
      params: {
        url: '/tasks/77/ai/initiate/',
        method: 'POST',
        body: JSON.stringify({
          identifier: 'flw_001',
          experiment: 'bot-123',
          prompt_text: 'Hello "there"\n  friend',
        }),
      },
    });
    expect(w.ask).toHaveBeenCalledWith(
      'This workflow wants to start an AI coaching conversation with flw_001' +
        ' on bot "bot-123", opening with: "Hello \'there\' friend", as you.',
    );
  });

  it('a long opening is quoted cut short', () => {
    const c = checkFetch(
      '/tasks/77/ai/initiate/',
      'POST',
      CTX,
      JSON.stringify({
        identifier: 'a',
        experiment: 'b',
        prompt_text: 'x'.repeat(500),
      }),
    );
    expect(c.question).toMatch(/x{300}\.\.\."$/);
  });

  it('a job start names the job, and every opportunity its config names is bound', () => {
    const url = '/labs/workflow/api/run/900/job/start/';
    const body = (cfg) =>
      JSON.stringify({
        job_config: {
          job_type: 'muac_picture_audit_create',
          run_id: 900,
          ...cfg,
        },
      });
    expect(checkFetch(url, 'POST', CTX, body({})).question).toBe(
      'start a background job "muac_picture_audit_create" that creates audits or other records',
    );
    expect(() =>
      checkFetch(url, 'POST', CTX, body({ opportunities: [{ id: 99 }] })),
    ).toThrow(/opportunity 99/);
    expect(() =>
      checkFetch(
        url,
        'POST',
        CTX,
        body({ flw_opportunity_ids: { flw_1: 99 } }),
      ),
    ).toThrow(/opportunity 99/);
    expect(() =>
      checkFetch(url, 'POST', CTX, body({ only_opportunity_id: 99 })),
    ).toThrow(/opportunity 99/);
    expect(
      checkFetch(
        url,
        'POST',
        CTX,
        body({
          opportunities: [{ id: 10 }, 11],
          flw_opportunity_ids: { a: 10 },
        }),
      ).gate,
    ).toBe('effect');
    // A job type that is not a slug is not quoted.
    expect(
      checkFetch(url, 'POST', CTX, body({ job_type: '"><b>' })).question,
    ).toBe('start a background job that creates audits or other records');
  });

  it('audit creation binds the per-worker opportunity map too', () => {
    expect(() =>
      checkFetch(
        '/audit/api/audit/create-async/',
        'POST',
        CTX,
        JSON.stringify({ opportunities: [10], flw_opportunity_ids: { f: 99 } }),
      ),
    ).toThrow(/opportunity 99/);
  });

  it('owning_program_id and storage_opportunity_id are bound like any scope', () => {
    const ctx = { ...CTX, owningProgramIds: [25] };
    // In the query of a read.
    expect(() =>
      checkFetch('/labs/workflow/api/run/5/?owning_program_id=99', 'GET', ctx),
    ).toThrow(/owning_program_id=99/);
    expect(
      checkFetch('/labs/workflow/api/run/5/?owning_program_id=25', 'GET', ctx)
        .gate,
    ).toBe('read');
    expect(
      checkFetch('/labs/workflow/api/run/5/?owning_program_id=7', 'GET', ctx)
        .gate,
    ).toBe('read');
    expect(() =>
      checkFetch(
        '/audit/api/5/bulk-data/?storage_opportunity_id=99',
        'GET',
        ctx,
      ),
    ).toThrow(/storage_opportunity_id=99/);
    // In the body of a state write, this run's or another's.
    for (const url of [
      '/labs/workflow/api/run/900/state/',
      '/labs/workflow/api/run/5/state/',
    ]) {
      expect(() =>
        checkFetch(
          url,
          'POST',
          ctx,
          JSON.stringify({ state: {}, owning_program_id: 99 }),
        ),
      ).toThrow(/owning_program_id=99/);
      expect(() =>
        checkFetch(
          url,
          'POST',
          ctx,
          JSON.stringify({ state: {}, owning_program_id: 25 }),
        ),
      ).not.toThrow();
    }
  });

  it('after a no, labs asks nothing for a moment', async () => {
    let t = 1_000;
    const { d, w } = wfDispatcher({
      now: () => t,
      w: workflowHandlers({ ask: vi.fn(async () => false) }),
    });
    const req = (id) => ({
      id,
      method: 'fetch',
      params: { url: '/tasks/api/single-create/', method: 'POST', body: '{}' },
    });
    expect((await d(req(1))).error).toMatch(/said no/);
    expect((await d(req(2))).error).toMatch(/wait a moment/);
    expect(w.ask).toHaveBeenCalledTimes(1);
    t += 6_000;
    await d(req(3));
    expect(w.ask).toHaveBeenCalledTimes(2);
  });

  it('labs asks at most a bounded number of questions a minute', async () => {
    let t = 1_000;
    const { d, w } = wfDispatcher({ now: () => t });
    let refused = '';
    for (let i = 0; i < 25; i += 1) {
      // Each its own record, so none is covered by an earlier yes.
      const out = await d({
        id: i,
        method: 'fetch',
        params: {
          url: `/tasks/api/${i + 1}/update/`,
          method: 'POST',
          body: '{}',
        },
      });
      if (out.error) {
        refused = out.error;
        break;
      }
    }
    expect(refused).toMatch(/too many questions/);
    expect(w.ask).toHaveBeenCalledTimes(20);
  });

  it('workflow jobs have a budget of their own', async () => {
    const { d } = wfDispatcher({ now: () => 5_000 });
    const start = (id) =>
      d({
        id,
        method: 'fetch',
        params: {
          url: '/labs/workflow/api/run/900/job/start/',
          method: 'POST',
          body: JSON.stringify({
            job_config: { job_type: 'audit_par_rollup' },
          }),
        },
      });
    for (let i = 0; i < 6; i += 1)
      expect((await start(i)).error).toBeUndefined();
    expect((await start(7)).error).toMatch(/too many background jobs/);
  });

  it('reads that run a pipeline are few at once', async () => {
    const pending = [];
    const w = workflowHandlers({
      fetch: vi.fn(
        () =>
          new Promise((resolve) =>
            pending.push(() =>
              resolve({
                status: 200,
                statusText: 'OK',
                headers: {},
                body: new ArrayBuffer(0),
              }),
            ),
          ),
      ),
    });
    const { d } = wfDispatcher({ w });
    const read = (id) =>
      d({
        id,
        method: 'fetch',
        params: { url: '/labs/workflow/api/42/pipeline-rows/', method: 'GET' },
      });
    const inFlight = [0, 1, 2, 3, 4, 5].map(read);
    expect((await read(6)).error).toMatch(/too many data reads/);
    // Other reads are not held behind them.
    const other = d({
      id: 7,
      method: 'fetch',
      params: { url: '/audit/api/ai-agents/', method: 'GET' },
    });
    await Promise.resolve();
    pending.forEach((f) => f());
    await Promise.all([...inFlight, other]);
  });
});

describe('proxyFetch reads an answer with a cap', () => {
  it('refuses one too large as it arrives, not after', async () => {
    const { readCapped } = await import('./host');
    const big = new Response(new Uint8Array(2000));
    await expect(readCapped(big, 1000)).rejects.toThrow(/too large/);
    const declared = new Response('x', {
      headers: { 'content-length': '5000' },
    });
    await expect(readCapped(declared, 1000)).rejects.toThrow(/too large/);
    const ok = await readCapped(new Response('hello'), 1000);
    expect(new TextDecoder().decode(ok)).toBe('hello');
  });
});
