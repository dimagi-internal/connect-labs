// @vitest-environment happy-dom
// The host's side of what the compile-time rewrites and the run-time shims send it:
// streams (EventSource), the routes old live copies use, an audit save's per-record
// yes, a search cut to this workflow's opportunities, and forms.
// Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Compile-time rewrites".
import { describe, it, expect, vi } from 'vitest';
import { checkFetch } from './routes';
import {
  createDispatcher,
  createSseParser,
  proxyFetch,
  proxyStream,
} from './host';
import { STREAM } from './protocol';
import { makeSandboxFetch } from './frameShims';

const LABS = 'https://labs.connect.dimagi.com';
const ctx = () => ({
  labsOrigin: LABS,
  definitionId: 42,
  runId: 900,
  opportunityIds: [10, 11],
  programIds: [7],
  organizationKey: 'acme',
  readableDefinitionIds: [42],
  jobIds: [],
  defaultScope: { name: 'opportunity_id', value: '10' },
});

function json(obj, status = 200) {
  return {
    status,
    statusText: 'OK',
    headers: { 'content-type': 'application/json' },
    body: new TextEncoder().encode(JSON.stringify(obj)).buffer,
  };
}

function setup({
  fetchAnswer = json({ ok: true }),
  stream,
  routeContext,
} = {}) {
  const pushed = [];
  const w = {
    fetch: vi.fn(async () => fetchAnswer),
    queryPipelineRows: vi.fn(),
    updateState: vi.fn(),
    runAction: vi.fn(),
    completeRun: vi.fn(),
    setLocation: vi.fn(),
    notify: vi.fn(),
    ask: vi.fn(async () => true),
    actionKeys: () => [],
    stream,
  };
  const rc = routeContext || ctx();
  const d = createDispatcher(
    {
      querySupply: vi.fn(),
      queryWorkflow: vi.fn(),
      navigate: vi.fn(),
      shareSelection: vi.fn(),
      resize: vi.fn(),
      copyText: vi.fn(),
      confirm: vi.fn(),
      download: vi.fn(),
      reportError: vi.fn(),
    },
    {
      labsOrigin: LABS,
      hasActivation: () => true,
      mode: 'workflow',
      routeContext: rc,
      workflow: w,
      push: (m) => pushed.push(m),
    },
  );
  return { d, w, pushed, rc };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

describe('streams: an EventSource the host reads', () => {
  it('a read route is streamed, its events pushed with the stream id', async () => {
    const stream = vi.fn(async (checked, onEvent, onOpen) => {
      onOpen();
      onEvent({
        name: 'message',
        data: '{"message":"Loading"}',
        lastEventId: '',
      });
      onEvent({
        name: 'message',
        data: '{"data":{"rows":[]}}',
        lastEventId: '',
      });
    });
    const { d, pushed } = setup({ stream });
    const out = await d({
      id: 1,
      method: 'streamOpen',
      params: { url: '/labs/workflow/api/42/pipeline-rows/stream/?alias=v' },
    });
    expect(out.result).toEqual({ stream: 's1' });
    await tick();
    expect(stream.mock.calls[0][0].href).toBe(
      `${LABS}/labs/workflow/api/42/pipeline-rows/stream/?alias=v`,
    );
    expect(stream.mock.calls[0][0].sandboxFor).toBe('42');
    expect(pushed.map((m) => [m.type, m.stream, m.kind])).toEqual([
      [STREAM, 's1', 'open'],
      [STREAM, 's1', 'event'],
      [STREAM, 's1', 'event'],
      [STREAM, 's1', 'end'],
    ]);
  });

  it('streams are bound like fetches: another workflow’s pipeline is refused', async () => {
    const stream = vi.fn();
    const { d } = setup({ stream });
    const out = await d({
      id: 1,
      method: 'streamOpen',
      params: { url: '/labs/workflow/api/43/pipeline-rows/stream/' },
    });
    expect(out.error).toMatch(/does not cover/);
    expect(stream).not.toHaveBeenCalled();
  });

  it('only a read can be streamed, and nothing off the table', async () => {
    const stream = vi.fn();
    const { d } = setup({ stream });
    for (const url of [
      '/labs/workflow/api/run/900/state/',
      '/custom_analysis/mbw_monitoring/api/stream/',
      '/labs/mcp/tokens/create/',
    ]) {
      const out = await d({ id: 1, method: 'streamOpen', params: { url } });
      expect(out.error).toBeTruthy();
    }
    expect(stream).not.toHaveBeenCalled();
  });

  it('close aborts the host’s read, and no message follows', async () => {
    let signal;
    const stream = vi.fn(
      (checked, onEvent, onOpen, s) =>
        new Promise((resolve, reject) => {
          signal = s;
          s.addEventListener('abort', () => reject(new Error('aborted')));
        }),
    );
    const { d, pushed } = setup({ stream });
    await d({
      id: 1,
      method: 'streamOpen',
      params: { url: '/labs/workflow/api/job/abc/status/' },
    });
    await tick();
    await d({ id: 2, method: 'streamClose', params: { stream: 's1' } });
    await tick();
    expect(signal.aborted).toBe(true);
    expect(pushed).toEqual([]);
  });

  it('a capped number at once; stopping the host aborts them all', async () => {
    const signals = [];
    const stream = vi.fn(
      (c, e, o, s) =>
        new Promise(() => {
          signals.push(s);
        }),
    );
    const { d } = setup({ stream });
    const answers = [];
    for (let i = 0; i < 7; i++) {
      answers.push(
        await d({
          id: i,
          method: 'streamOpen',
          params: { url: '/labs/workflow/api/job/abc/status/' },
        }),
      );
    }
    expect(answers.filter((a) => a.result).length).toBe(6);
    expect(answers[6].error).toMatch(/too many streams/);
    await tick();
    d.closeStreams();
    expect(signals.every((s) => s.aborted)).toBe(true);
  });

  it('a failed read is pushed as an error', async () => {
    const stream = vi.fn(async () => {
      throw new Error('stream: HTTP 500');
    });
    const { d, pushed } = setup({ stream });
    await d({
      id: 1,
      method: 'streamOpen',
      params: { url: '/labs/workflow/api/job/abc/status/' },
    });
    await tick();
    await tick();
    expect(pushed).toEqual([
      {
        type: STREAM,
        stream: 's1',
        kind: 'error',
        message: 'stream: HTTP 500',
      },
    ]);
  });
});

describe('createSseParser', () => {
  it('reads events across chunk boundaries, names, ids and multi-line data', () => {
    const got = [];
    const p = createSseParser((e) => got.push(e));
    p.feed('﻿: comment\ndata: one\n\nevent: prog');
    p.feed('ress\nid: 7\ndata: a\r\ndata: b\r');
    p.feed('\n\r\ndata: {"x":1}\n\ndata: unfinished');
    p.end();
    expect(got).toEqual([
      { name: 'message', data: 'one', lastEventId: '' },
      { name: 'progress', data: 'a\nb', lastEventId: '7' },
      { name: 'message', data: '{"x":1}', lastEventId: '7' },
    ]);
  });
});

describe('createSseParser on a long line in many chunks', () => {
  it('reads it once, correctly, with a CRLF split across chunks', () => {
    const got = [];
    const p = createSseParser((e) => got.push(e.data));
    const big = 'x'.repeat(200_000);
    p.feed('data: ');
    for (let i = 0; i < big.length; i += 1000) p.feed(big.slice(i, i + 1000));
    p.feed('\r');
    p.feed('\n\r\n');
    expect(got).toEqual([big]);
  });
});

describe('proxyStream: as the viewer, with the sandbox headers', () => {
  function sseResponse(text, init = {}) {
    return new Response(text, {
      status: 200,
      headers: { 'content-type': 'text/event-stream' },
      ...init,
    });
  }

  it('stamps whose request it is and reads events', async () => {
    const checked = checkFetch(
      '/labs/workflow/api/42/pipeline-rows/stream/',
      'GET',
      ctx(),
    );
    const fetchImpl = vi.fn(async () => sseResponse('data: hi\n\n'));
    const events = [];
    const open = vi.fn();
    await proxyStream(
      checked,
      (e) => events.push(e.data),
      open,
      new AbortController().signal,
      fetchImpl,
    );
    const [, init] = fetchImpl.mock.calls[0];
    expect(init.headers['x-labs-sandbox-for']).toBe('42');
    expect(init.headers['x-labs-sandbox-scope']).toBe('opportunity_id=10');
    expect(init.credentials).toBe('same-origin');
    expect(open).toHaveBeenCalled();
    expect(events).toEqual(['hi']);
  });

  it('refuses an answer that is not a stream, or an error status', async () => {
    const checked = checkFetch(
      '/labs/workflow/api/job/a/status/',
      'GET',
      ctx(),
    );
    await expect(
      proxyStream(
        checked,
        () => {},
        () => {},
        new AbortController().signal,
        async () =>
          new Response('<html>', {
            status: 200,
            headers: { 'content-type': 'text/html' },
          }),
      ),
    ).rejects.toThrow(/does not answer as a stream/);
    await expect(
      proxyStream(
        checked,
        () => {},
        () => {},
        new AbortController().signal,
        async () => new Response('', { status: 403 }),
      ),
    ).rejects.toThrow(/HTTP 403/);
  });

  it('stops a stream past its size cap', async () => {
    const checked = checkFetch(
      '/labs/workflow/api/job/a/status/',
      'GET',
      ctx(),
    );
    await expect(
      proxyStream(
        checked,
        () => {},
        () => {},
        new AbortController().signal,
        async () => sseResponse('data: ' + 'x'.repeat(100) + '\n\n'),
        undefined,
        50,
      ),
    ).rejects.toThrow(/too large/);
  });
});

describe('routes the live copies use', () => {
  it('a workflow’s own schedule and pipeline stream are reads of it', () => {
    for (const url of [
      '/labs/workflow/api/42/schedule/?opportunity_id=10',
      '/labs/workflow/api/42/pipeline-data/stream/?opportunity_id=10',
    ]) {
      expect(checkFetch(url, 'GET', ctx()).gate).toBe('read');
    }
    expect(() =>
      checkFetch('/labs/workflow/api/43/schedule/', 'GET', ctx()),
    ).toThrow();
    // Making a schedule (it runs as the person, unattended) is not on the table.
    expect(() =>
      checkFetch('/labs/workflow/api/42/schedule/', 'POST', ctx(), '{}'),
    ).toThrow(/cannot be made/);
  });

  it('building footprints: for this workflow’s opportunity, polled only by its starter', async () => {
    expect(() =>
      checkFetch('/microplans/12/preview_footprints/', 'POST', ctx(), '{}'),
    ).toThrow(/does not cover/);
    const { d, w, rc } = setup({
      fetchAnswer: json({ status: 'queued', task_id: 'abc-123' }),
    });
    expect(() =>
      checkFetch('/microplans/preview_status/abc-123/', 'GET', rc),
    ).toThrow();
    const out = await d({
      id: 1,
      method: 'fetch',
      params: {
        url: '/microplans/10/preview_footprints/',
        method: 'POST',
        body: '{"areas":[]}',
      },
    });
    expect(out.error).toBeUndefined();
    expect(w.ask).not.toHaveBeenCalled();
    expect(rc.jobIds).toEqual(['abc-123']);
    expect(
      checkFetch('/microplans/preview_status/abc-123/', 'GET', rc).gate,
    ).toBe('read');
  });

  it('an audit save asks once per audit, and a yes holds for that audit only', async () => {
    const { d, w } = setup();
    const save = (id, rid) =>
      d({
        id: rid,
        method: 'fetch',
        params: {
          url: `/audit/api/${id}/save/`,
          method: 'POST',
          body: 'visit_results=%7B%7D',
          headers: { 'content-type': 'application/x-www-form-urlencoded' },
        },
      });
    await save(55, 1);
    await save(55, 2);
    await save(56, 3);
    expect(w.ask.mock.calls.map((c) => c[0])).toEqual([
      'This workflow wants to save review results to audit 55, as you.',
      'This workflow wants to save review results to audit 56, as you.',
    ]);
    expect(w.fetch).toHaveBeenCalledTimes(3);
  });

  it('the opportunity search answers with this workflow’s opportunities only', async () => {
    const { d } = setup({
      fetchAnswer: json({
        success: true,
        opportunities: [
          { id: 10, name: 'ours' },
          { id: 99, name: 'not ours' },
          { id: '11', name: 'ours too' },
        ],
      }),
    });
    const out = await d({
      id: 1,
      method: 'fetch',
      params: { url: '/audit/api/opportunities/search/?q=a', method: 'GET' },
    });
    const body = JSON.parse(new TextDecoder().decode(out.result.body));
    expect(body.opportunities.map((o) => o.name)).toEqual(['ours', 'ours too']);
    expect(body.success).toBe(true);
  });

  it('...and an answer it cannot read is not passed on', async () => {
    const { d } = setup({
      fetchAnswer: {
        ...json({}),
        body: new TextEncoder().encode('<html>').buffer,
      },
    });
    const out = await d({
      id: 1,
      method: 'fetch',
      params: { url: '/audit/api/opportunities/search/?q=a', method: 'GET' },
    });
    expect(out.error).toMatch(/could not be read/);
  });
});

describe('forms', () => {
  it('a FormData body goes url-encoded; a file is refused', async () => {
    const request = vi.fn(async () => ({
      status: 200,
      statusText: 'OK',
      headers: {},
      body: new ArrayBuffer(0),
    }));
    const f = makeSandboxFetch(
      { request },
      () => `${LABS}/labs/sandbox/render/`,
      vi.fn(),
    );
    const form = new FormData();
    form.append('visit_results', '{"1":"pass"}');
    form.append('csrfmiddlewaretoken', 'sandboxed');
    await f('/audit/api/55/save/', { method: 'POST', body: form });
    const params = request.mock.calls[0][1];
    expect(params.headers['content-type']).toMatch(/x-www-form-urlencoded/);
    expect(new URLSearchParams(params.body).get('visit_results')).toBe(
      '{"1":"pass"}',
    );
    const withFile = new FormData();
    withFile.append('photo', new Blob(['x']), 'x.png');
    await expect(
      f('/audit/api/55/save/', { method: 'POST', body: withFile }),
    ).rejects.toThrow(/file/);
  });

  it('the host drops the frame’s dummy token from a form; its header decides', async () => {
    const checked = checkFetch('/audit/api/55/save/', 'POST', ctx());
    const fetchImpl = vi.fn(async () => new Response('{}', { status: 200 }));
    await proxyFetch(
      checked,
      {
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body: 'visit_results=1&csrfmiddlewaretoken=sandboxed',
      },
      'REAL',
      fetchImpl,
    );
    const [, init] = fetchImpl.mock.calls[0];
    expect(init.body).toBe('visit_results=1');
    expect(init.headers['x-csrftoken']).toBe('REAL');
  });
});

describe('review fixes', () => {
  it('a closed stream keeps its place until the host’s read ends, and opens are budgeted', async () => {
    const settle = [];
    const stream = vi.fn(
      (c, e, o, s) =>
        new Promise((resolve) => {
          settle.push(resolve);
        }),
    );
    let t = 0;
    const pushed = [];
    const d = createDispatcher(
      {
        querySupply: vi.fn(),
        queryWorkflow: vi.fn(),
        navigate: vi.fn(),
        shareSelection: vi.fn(),
        resize: vi.fn(),
        copyText: vi.fn(),
        confirm: vi.fn(),
        download: vi.fn(),
        reportError: vi.fn(),
      },
      {
        labsOrigin: LABS,
        hasActivation: () => true,
        mode: 'workflow',
        routeContext: ctx(),
        workflow: {
          fetch: vi.fn(),
          ask: vi.fn(),
          actionKeys: () => [],
          stream,
        },
        push: (m) => pushed.push(m),
        now: () => t,
      },
    );
    const open = (i) =>
      d({
        id: i,
        method: 'streamOpen',
        params: { url: '/labs/workflow/api/42/pipeline-data/stream/' },
      });
    const opened = [];
    for (let i = 0; i < 6; i++) opened.push((await open(i)).result.stream);
    await tick();
    for (const s of opened)
      await d({ id: 99, method: 'streamClose', params: { stream: s } });
    // Closed, but the reads have not ended: still no room.
    expect((await open(7)).error).toMatch(/too many streams/);
    settle.forEach((r) => r());
    await tick();
    await tick();
    expect((await open(8)).result).toBeTruthy();
    expect(pushed).toEqual([]); // nothing after a close
    // Opens per minute are budgeted, whatever is closed.
    for (const r of settle.splice(0)) r();
    let refused = 0;
    for (let i = 0; i < 30; i++) {
      const out = await open(100 + i);
      if (out.error && /too many streams from this page/.test(out.error))
        refused += 1;
      settle.splice(0).forEach((r) => r());
      await tick();
      await tick();
    }
    expect(refused).toBeGreaterThan(0);
    t += 61_000;
    settle.splice(0).forEach((r) => r());
    await tick();
    await tick();
    expect((await open(200)).result).toBeTruthy();
  });

  it('footprints: a few areas a request, and starts are budgeted', async () => {
    const big = JSON.stringify({ areas: [1, 2, 3, 4, 5, 6] });
    expect(() =>
      checkFetch('/microplans/10/preview_footprints/', 'POST', ctx(), big),
    ).toThrow(/at most 5 areas/);
    expect(() =>
      checkFetch('/microplans/10/preview_footprints/', 'POST', ctx(), '{}'),
    ).toThrow(/at most 5 areas/);
    const { d, w } = setup({ fetchAnswer: json({ task_id: 't' }) });
    const outs = [];
    for (let i = 0; i < 31; i++) {
      outs.push(
        await d({
          id: i,
          method: 'fetch',
          params: {
            url: '/microplans/10/preview_footprints/',
            method: 'POST',
            body: '{"areas":[{}]}',
          },
        }),
      );
    }
    expect(outs.filter((o) => o.error).length).toBe(1);
    expect(outs[30].error).toMatch(/too many background jobs/);
    expect(w.fetch).toHaveBeenCalledTimes(30);
  });

  it('a checked body goes as the JSON it was checked as, whatever type the frame named', async () => {
    const checked = checkFetch(
      '/audit/api/audit/preview/',
      'POST',
      ctx(),
      JSON.stringify({
        opportunities: [10],
        x: 'a&csrfmiddlewaretoken=1&opportunities=99',
      }),
    );
    const fetchImpl = vi.fn(async () => new Response('{}', { status: 200 }));
    await proxyFetch(
      checked,
      {
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body: 'ignored',
      },
      'REAL',
      fetchImpl,
    );
    const [, init] = fetchImpl.mock.calls[0];
    expect(init.headers['content-type']).toBe('application/json');
    expect(init.body).toBe(checked.body);
  });

  it('a form token is dropped whatever the content type’s case', async () => {
    const checked = checkFetch('/audit/api/55/save/', 'POST', ctx());
    const fetchImpl = vi.fn(async () => new Response('{}', { status: 200 }));
    await proxyFetch(
      checked,
      {
        headers: { 'Content-Type': 'Application/X-WWW-Form-Urlencoded' },
        body: 'csrfmiddlewaretoken=x&visit_results=1',
      },
      'REAL',
      fetchImpl,
    );
    expect(fetchImpl.mock.calls[0][1].body).toBe('visit_results=1');
  });

  it('a refused stream lets go of its connection', async () => {
    const checked = checkFetch(
      '/labs/workflow/api/job/a/status/',
      'GET',
      ctx(),
    );
    const cancel = vi.fn(async () => undefined);
    const response = new Response('<html>', {
      status: 200,
      headers: { 'content-type': 'text/html' },
    });
    Object.defineProperty(response, 'body', { value: { cancel } });
    await expect(
      proxyStream(
        checked,
        () => {},
        () => {},
        new AbortController().signal,
        async () => response,
      ),
    ).rejects.toThrow(/does not answer as a stream/);
    expect(cancel).toHaveBeenCalled();
  });
});
