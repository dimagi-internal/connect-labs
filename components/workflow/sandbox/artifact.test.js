// @vitest-environment happy-dom
// @vitest-environment-options {"settings": {"disableIframePageLoading": true}}
// A data space artifact is drawn by the workflow sandbox itself (./artifact.tsx): the same
// opaque-origin frame, shell and bridge, its queries' rows handed in as `pipelines`, and
// nothing else answered -- no fetch, no supply or workflow reads, no run.
import { describe, it, expect } from 'vitest';
import { artifactPayload, artifactPipelines, mountArtifact } from './artifact';
import { HELLO, INIT, SHELL_PATH } from './protocol';

const LABS = 'https://labs.connect.dimagi.com';

const INPUT = {
  labsOrigin: LABS,
  artifact: {
    id: 5,
    name: 'Visits per month',
    version: 2,
    code: 'function W({pipelines}) { return <div>{pipelines.by_month.rows.length}</div>; }',
  },
  results: {
    by_month: {
      columns: ['month', 'opportunity_name', 'visits'],
      rows: [
        ['2026-08', 'Opp One', 12],
        ['2026-09', 'Opp One', 15],
      ],
      row_count: 2,
      truncated: false,
      row_cap: { limit: 500 },
    },
    broken: {
      columns: [],
      rows: [],
      error: { code: 'invalid_sql', message: 'column "x" does not exist' },
    },
  },
  space: { id: 12, name: 'KMC' },
  pageUrl: `${LABS}/labs/data/space/12/`,
  today: '2026-10-25',
};

function tick() {
  return new Promise((r) => setTimeout(r, 0));
}

describe('an artifact in the sandbox', () => {
  it('hands each named query to the render as a pipeline of row objects', () => {
    const p = artifactPipelines(INPUT.results);
    expect(p.by_month.rows).toEqual([
      { month: '2026-08', opportunity_name: 'Opp One', visits: 12 },
      { month: '2026-09', opportunity_name: 'Opp One', visits: 15 },
    ]);
    expect(p.by_month.metadata).toMatchObject({
      row_count: 2,
      pipeline_name: 'by_month',
      truncated: false,
      row_cap: 500,
    });
    // A query that failed is a pipeline with its error, as a workflow's is.
    expect(p.broken.rows).toEqual([]);
    expect(p.broken.metadata.error).toBe('column "x" does not exist');
  });

  it('is drawn as a page: no run, no workers, nothing but its code and rows', () => {
    const payload = artifactPayload(INPUT);
    expect(payload.mode).toBe('page');
    expect(payload.renderCode).toBe(INPUT.artifact.code);
    expect(payload.workers).toEqual([]);
    expect(payload.instance).toEqual({});
    expect(payload.supply).toEqual({});
    expect(payload.workflows).toEqual({});
    expect(payload.mapboxToken).toBe('');
    expect(payload.definition).toMatchObject({ kind: 'artifact', id: 5 });
    expect(payload.apiEndpoints).toBeUndefined();
  });

  it('mounts the workflow sandbox frame: opaque origin, the shell, and refuses fetch', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const mounted = mountArtifact(container, INPUT);
    await tick();
    await tick();
    const iframe = container.querySelector('iframe');
    expect(iframe).not.toBeNull();
    // allow-scripts ONLY: no allow-same-origin, so the frame holds no labs cookies,
    // cannot read this page and its requests are cross-site (the shell's CSP adds the
    // rest: workflow/sandbox.py frame_policy).
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
    expect(iframe.getAttribute('src').split('?')[0]).toBe(SHELL_PATH);

    // The handshake, as the frame makes it; the host answers with INIT and a port.
    const posted = [];
    Object.defineProperty(iframe, 'contentWindow', {
      value: {
        postMessage: (msg, origin, ports) => posted.push({ msg, ports }),
      },
      configurable: true,
    });
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: HELLO },
        origin: 'null',
        source: iframe.contentWindow,
      }),
    );
    expect(posted).toHaveLength(1);
    expect(posted[0].msg.type).toBe(INIT);
    expect(posted[0].msg.payload.pipelines.by_month.rows).toHaveLength(2);
    const port = posted[0].ports[0];
    const answers = [];
    port.onmessage = (e) => answers.push(e.data);
    port.start?.();
    // Everything a frame could ask for beyond its rows is refused.
    port.postMessage({
      id: 1,
      method: 'fetch',
      params: { url: '/labs/data/api/query/' },
    });
    port.postMessage({
      id: 2,
      method: 'queryPipelineRows',
      params: { alias: 'x' },
    });
    port.postMessage({ id: 3, method: 'querySupply', params: { alias: 'x' } });
    port.postMessage({ id: 4, method: 'updateState', params: { state: {} } });
    for (let i = 0; i < 20 && answers.length < 4; i++) await tick();
    const byId = Object.fromEntries(answers.map((a) => [a.id, a]));
    expect(byId[1].error).toMatch(/not available on a page/);
    expect(byId[2].error).toMatch(/not available on a page/);
    expect(byId[3].error).toMatch(/only its own queries/);
    expect(byId[4].error).toBeTruthy();
    mounted.destroy();
  });
});
