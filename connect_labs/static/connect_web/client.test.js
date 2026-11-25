// The player's client (api.ts), the safe markdown (text.ts) and the progress poller (progress.ts).
import { describe, expect, it, vi } from 'vitest';

import fixtures from '../../connect_web/tests/fixtures/formplayer_responses.json';
import { ApiError, EngineClient } from './api';
import { POLL_DELAYS, pollProgress, progressed } from './progress';
import { escapeHtml, markdownToHtml } from './text';

const config = {
  opportunityId: 'opp-1',
  opportunityName: 'Nets',
  appKind: 'deliver',
  appName: 'Nets Deliver',
  csrfToken: 'csrf-abc',
  api: {
    navigate: '/cw/navigate/',
    answer: '/cw/answer/',
    submit: '/cw/submit/',
    progress: '/cw/progress/',
  },
  mediaMaxBytes: 4 * 1048576 - 1024,
};

const json = (status, body) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

describe('EngineClient', () => {
  it('posts JSON to labs with the CSRF token, never to Formplayer', async () => {
    const fetch = vi.fn(async () => json(200, fixtures.menu_start));
    const client = new EngineClient(config, fetch);
    const resp = await client.navigate(['0']);
    expect(resp.type).toBe('commands');
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/cw/navigate/');
    expect(init.method).toBe('POST');
    expect(init.headers['X-CSRFToken']).toBe('csrf-abc');
    const body = JSON.parse(init.body);
    expect(body.selections).toEqual(['0']);
    expect(body).not.toHaveProperty('username');
    expect(body).not.toHaveProperty('domain');
    expect(body).not.toHaveProperty('app_id');
  });

  it('sends requests one at a time, in order', async () => {
    const order = [];
    let release;
    const gate = new Promise((r) => (release = r));
    const fetch = vi.fn(async (url, init) => {
      const { ix } = JSON.parse(init.body);
      order.push(`start ${ix}`);
      if (ix === '1') await gate;
      order.push(`end ${ix}`);
      return json(200, fixtures.answer_accepted);
    });
    const client = new EngineClient(config, fetch);
    const a = client.answer('s', '1', 'a');
    const b = client.answer('s', '2', 'b');
    await Promise.resolve();
    release();
    await Promise.all([a, b]);
    expect(order).toEqual(['start 1', 'end 1', 'start 2', 'end 2']);
  });

  it('turns labs errors into ApiError with code and retryability', async () => {
    const fetch = vi.fn(async () =>
      json(410, {
        error: {
          code: 'SESSION_EXPIRED',
          message: 'This form session has expired.',
          retryable: false,
        },
      }),
    );
    const err = await new EngineClient(config, fetch)
      .answer('s', '1', 'a')
      .catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe('SESSION_EXPIRED');
    expect(err.isSessionGone).toBe(true);
  });

  it('reports a network failure as retryable', async () => {
    const fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    const err = await new EngineClient(config, fetch)
      .navigate([])
      .catch((e) => e);
    expect(err.isNetwork).toBe(true);
    expect(err.retryable).toBe(true);
  });

  it('a queue keeps going after a failure', async () => {
    const fetch = vi
      .fn()
      .mockImplementationOnce(async () =>
        json(502, { error: { code: 'ENGINE_ERROR', message: 'x' } }),
      )
      .mockImplementationOnce(async () => json(200, fixtures.answer_accepted));
    const client = new EngineClient(config, fetch);
    await expect(client.answer('s', '1', 'a')).rejects.toBeInstanceOf(ApiError);
    await expect(client.answer('s', '1', 'b')).resolves.toMatchObject({
      status: 'accepted',
    });
  });

  it('a CSRF failure (Django 403 page) reads as "reload"', async () => {
    const fetch = vi.fn(
      async () => new Response('<html>CSRF</html>', { status: 403 }),
    );
    const err = await new EngineClient(config, fetch)
      .navigate([])
      .catch((e) => e);
    expect(err.code).toBe('FORBIDDEN');
    expect(err.message).toMatch(/Reload/);
  });

  it('submit sends the location only when there is one', async () => {
    const fetch = vi.fn(async () => json(200, fixtures.submit_success));
    const client = new EngineClient(config, fetch);
    await client.submit('s', { 1: 'a' }, '9.1 7.4 0 10');
    await client.submit('s', { 1: 'a' }, null);
    expect(JSON.parse(fetch.mock.calls[0][1].body).meta_location).toBe(
      '9.1 7.4 0 10',
    );
    expect(JSON.parse(fetch.mock.calls[1][1].body)).not.toHaveProperty(
      'meta_location',
    );
  });
});

describe('markdown captions', () => {
  it('formats the little markdown HQ allows', () => {
    expect(markdownToHtml(fixtures.form.tree[0].caption_markdown)).toBe(
      '<span><strong>Welcome!</strong> Check the <em>net</em> first.</span>',
    );
    expect(markdownToHtml('- one\n- two')).toBe(
      '<ul class="ml-5 list-disc"><li>one</li><li>two</li></ul>',
    );
    expect(markdownToHtml('[site](https://example.org)')).toContain(
      'href="https://example.org"',
    );
  });

  it('can never inject markup or script', () => {
    const html = markdownToHtml(
      '<img src=x onerror=alert(1)> **<b>x</b>** [x](javascript:alert(1)) "q"',
    );
    expect(html).not.toMatch(/<img|<b>|href="javascript|<a /);
    expect(html).toContain('&lt;img');
    expect(escapeHtml(`<a href="x">'`)).toBe(
      '&lt;a href=&quot;x&quot;&gt;&#39;',
    );
  });
});

describe('waiting for Connect after a submission', () => {
  const base = {
    claimed: true,
    learn: {
      modules_done: 1,
      modules_total: 2,
      assessments: 0,
      passed: false,
      complete: false,
    },
    deliver: { visits: 2, statuses: { approved: 1, pending: 1 } },
  };
  const more = (patch) => ({
    ...base,
    ...patch,
    learn: { ...base.learn, ...(patch.learn || {}) },
    deliver: { ...base.deliver, ...(patch.deliver || {}) },
  });

  it('knows when progress moved', () => {
    expect(progressed(base, more({ deliver: { visits: 3 } }), 'deliver')).toBe(
      true,
    );
    expect(progressed(base, base, 'deliver')).toBe(false);
    expect(
      progressed(base, more({ learn: { modules_done: 2 } }), 'learn'),
    ).toBe(true);
    expect(progressed(base, more({ learn: { assessments: 1 } }), 'learn')).toBe(
      true,
    );
    expect(progressed(base, more({ deliver: { visits: 3 } }), 'learn')).toBe(
      false,
    );
    expect(progressed(null, base, 'learn')).toBe(false);
  });

  it('polls with backoff until it moves', async () => {
    const sleeps = [];
    const answers = [base, base, more({ deliver: { visits: 3 } })];
    const fetch = vi.fn(async () => json(200, answers.shift()));
    const result = await pollProgress({
      url: '/p',
      kind: 'deliver',
      before: base,
      fetchImpl: fetch,
      sleep: async (ms) => sleeps.push(ms),
    });
    expect(result).toBe('processed');
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(sleeps).toEqual([3000, 5000, 8000]);
  });

  it('gives up after the last check, riding out failures', async () => {
    const fetch = vi
      .fn()
      .mockImplementationOnce(async () => {
        throw new TypeError('offline');
      })
      .mockImplementation(async () => json(200, base));
    const result = await pollProgress({
      url: '/p',
      kind: 'learn',
      before: base,
      fetchImpl: fetch,
      sleep: async () => undefined,
    });
    expect(result).toBe('waiting');
    expect(fetch).toHaveBeenCalledTimes(POLL_DELAYS.length);
  });
});

describe('opportunityUrl', () => {
  it('leads back to the opportunity on the form tab, and flags a submission', async () => {
    const { opportunityUrl } = await import('./api');
    const cfg = { opportunityId: '9b0c-uuid', appKind: 'learn' };
    expect(opportunityUrl(cfg)).toBe(
      '/connect-web/opportunities/9b0c-uuid/?tab=learn',
    );
    expect(opportunityUrl({ ...cfg, appKind: 'deliver' }, true)).toBe(
      '/connect-web/opportunities/9b0c-uuid/?tab=deliver&submitted=deliver',
    );
  });
  it('cannot be steered off the route by the page', async () => {
    const { opportunityUrl } = await import('./api');
    expect(
      opportunityUrl({
        opportunityId: '../../x?y=1#z',
        appKind: 'javascript:',
      }),
    ).toBe('/connect-web/opportunities/..%2F..%2Fx%3Fy%3D1%23z/?tab=learn');
  });
});
