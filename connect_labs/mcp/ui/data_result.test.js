// The data_query results View (ui://labs/data-result) against a stand-in MCP Apps host:
// it reads the kept result from the tool result's resource_link (labs-result://) as its
// viewer, pages and sorts through it (saying the sort is per page), never subscribes,
// names each refusal (an expired one falls back on the facts' sample) -- and never runs the query again (no tools/call).
// Spec: canopy-web docs/superpowers/specs/2026-10-23-host-held-results-design.md.
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

const HTML = readFileSync(
  new URL('./data_result.html', import.meta.url),
  'utf8',
);
const SCRIPT = HTML.slice(
  HTML.indexOf('<script>') + '<script>'.length,
  HTML.lastIndexOf('</script>'),
);
const URI = 'labs-result://AbC123';
const COLUMNS = ['worker', 'visits'];
// 250 rows: three pages at the default page size of 100.
const ROWS = Array.from({ length: 250 }, (_, i) => [
  `w${String(i).padStart(3, '0')}`,
  (i * 7) % 50,
]);

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function settle() {
  for (let i = 0; i < 6; i++) await tick();
}

// A host: answers ui/initialize, resources/read (paging like the canopy SDK) and
// resources/subscribe, and records every request the View makes.
function mount({ read } = {}) {
  const window = new Window();
  const document = window.document;
  document.body.innerHTML = '<div id="app"></div>';
  const sent = [];
  const parent = {
    postMessage(msg) {
      sent.push(msg);
      if (msg.id === undefined) return;
      let reply;
      if (msg.method === 'ui/initialize') {
        reply = {
          result: {
            protocolVersion: '2026-01-26',
            hostCapabilities: {
              serverTools: {},
              serverResources: { subscribe: true },
            },
            hostContext: {},
          },
        };
      } else if (msg.method === 'resources/read') {
        reply = (read || defaultRead)(msg.params.uri);
      } else if (msg.method === 'resources/subscribe') {
        reply = { result: {} };
      } else {
        reply = { error: { code: -32601, message: 'not here' } };
      }
      setTimeout(() => deliver({ jsonrpc: '2.0', id: msg.id, ...reply }), 0);
    },
  };
  Object.defineProperty(window, 'parent', {
    value: parent,
    configurable: true,
  });
  function deliver(data) {
    const event = new window.MessageEvent('message', { data });
    Object.defineProperty(event, 'source', { value: parent });
    window.dispatchEvent(event);
  }
  // The View's own script, with this window as its globals.
  new Function('window', 'document', 'Element', 'ResizeObserver', SCRIPT)(
    window,
    document,
    window.Element,
    undefined,
  );
  return {
    window,
    document,
    sent,
    notify: (method, params) => deliver({ jsonrpc: '2.0', method, params }),
    requests: (method) => sent.filter((m) => m.method === method),
    text: () => document.getElementById('app').textContent,
    click: (selector) =>
      document
        .querySelector(selector)
        .dispatchEvent(new window.Event('click', { bubbles: true })),
  };
}

function defaultRead(uri) {
  const url = new URL(uri.replace('labs-result://', 'https://x/'));
  const size = Number(url.searchParams.get('page_size') || 100);
  const pages = Math.ceil(ROWS.length / size);
  const page = Math.min(pages, Number(url.searchParams.get('page') || 1));
  const body = {
    columns: COLUMNS,
    rows: ROWS.slice((page - 1) * size, page * size),
    sql_executed: 'SELECT worker, visits FROM visits',
    open_on_page: 'https://labs.example/labs/data/?opps=1',
  };
  return {
    result: {
      contents: [
        {
          uri,
          mimeType: 'application/json',
          text: JSON.stringify(body),
          _meta: {
            'labs/result': {
              page,
              page_size: size,
              pages,
              row_count: ROWS.length,
              snapshot: true,
            },
          },
        },
      ],
    },
  };
}

const LINK = {
  type: 'resource_link',
  uri: URI,
  name: 'Query over Opp',
  mimeType: 'application/json',
};
// What a sensitive conversation's View receives: the link and nothing else.
const SENSITIVE_RESULT = {
  content: [LINK],
  structuredContent: null,
  _meta: { 'canopy/withheld': true },
  isError: false,
};

describe('the results View reads the kept result', () => {
  let view;
  beforeEach(async () => {
    view = mount();
    await settle();
    view.notify('ui/notifications/tool-input', {
      arguments: { opportunity_ids: [1], sql: 'SELECT 1' },
    });
    view.notify('ui/notifications/tool-result', SENSITIVE_RESULT);
    await settle();
  });

  it('reads page one from the link and never runs the query', () => {
    const reads = view.requests('resources/read');
    expect(reads.map((m) => m.params.uri)).toEqual([
      `${URI}?page=1&page_size=100`,
    ]);
    expect(view.requests('tools/call')).toEqual([]);
    expect(view.document.querySelectorAll('tbody tr').length).toBe(100);
    expect(view.text()).toContain('Rows 1–100 of 250');
  });

  it('pages through the result on the server', async () => {
    view.click('#next');
    await settle();
    expect(view.requests('resources/read').at(-1).params.uri).toBe(
      `${URI}?page=2&page_size=100`,
    );
    expect(view.document.querySelector('tbody td').textContent).toBe('w100');
    expect(view.text()).toContain('Rows 101–200 of 250');
  });

  it('pages with a compact Rows … of … and ‹ ›, and no page-size picker', () => {
    const pager = view.document.querySelector('.pager');
    expect(pager.textContent).toContain('Rows 1–100 of 250');
    expect(view.document.getElementById('prev').disabled).toBe(true);
    expect(view.document.getElementById('next').disabled).toBe(false);
    expect(view.document.getElementById('size')).toBeNull();
    expect(view.document.querySelector('select')).toBeNull();
  });

  it('right-aligns a numeric column header with its numbers', () => {
    const heads = view.document.querySelectorAll('th');
    expect(heads[0].className).toBe('');
    expect(heads[1].className).toBe('num');
  });

  it('sorts within the page', () => {
    view.click('th[data-sort="1"]');
    const first = () =>
      Number(
        view.document.querySelector('tbody tr td:nth-child(2)').textContent,
      );
    expect(first()).toBe(0);
    view.click('th[data-sort="1"]');
    expect(first()).toBe(49);
    expect(view.requests('resources/read').length).toBe(1);
  });

  it('says the sort is within the page when there are more pages', () => {
    expect(view.text()).not.toContain('Sorted within this page only');
    view.click('th[data-sort="1"]');
    expect(view.text()).toContain('Sorted within this page only');
  });

  it('does not subscribe: Labs never changes a kept result', async () => {
    expect(view.requests('resources/subscribe')).toEqual([]);
    // A host that relays an update anyway still gets a fresh read.
    view.notify('notifications/resources/updated', { uri: URI });
    await settle();
    expect(view.requests('resources/read').length).toBe(2);
  });
});

describe('a read the host refuses says why', () => {
  async function refused(message, data) {
    const view = mount({
      read: () => ({ error: { code: -32603, message, data } }),
    });
    await settle();
    view.notify('ui/notifications/tool-result', SENSITIVE_RESULT);
    await settle();
    return view;
  }

  it('expired', async () => {
    const view = await refused(
      'host_refused: labs refused: expired: this result has expired; run the query again',
    );
    expect(view.text()).toContain(
      'This result has expired — ask again in the chat.',
    );
    expect(view.requests('tools/call')).toEqual([]);
  });

  it('expired, with the sample the tool result carried, said to be a sample', async () => {
    const view = mount({
      read: () => ({
        error: {
          code: -32603,
          message:
            'host_refused: labs refused: expired: this result has expired; run the query again',
        },
      }),
    });
    await settle();
    view.notify('ui/notifications/tool-result', {
      content: [LINK],
      structuredContent: {
        row_count: 250,
        columns: COLUMNS,
        sample_rows: ROWS.slice(0, 20).map(([worker, visits]) => ({
          worker,
          visits,
        })),
      },
    });
    await settle();
    expect(view.document.querySelectorAll('tbody tr').length).toBe(20);
    expect(view.text()).toContain('The full result has expired');
    expect(view.text()).toContain('only the sample of 20 of 250 rows');
    expect(view.requests('tools/call')).toEqual([]);
  });

  it('decides on the structured code, never the words of the reason', async () => {
    const view = await refused(
      'forbidden: this result expired for you long ago, not_found anywhere',
      { code: 'forbidden' },
    );
    expect(view.text()).toContain('This result is not yours to see');
    expect(view.text()).not.toContain('expired');
  });

  it('expired, from the structured code', async () => {
    const view = await refused('gone', { code: 'expired' });
    expect(view.text()).toContain(
      'This result has expired — ask again in the chat.',
    );
  });

  it('sensitive, from the structured code', async () => {
    const view = await refused('forbidden: no', {
      code: 'forbidden',
      sensitive: true,
    });
    expect(view.text()).toContain('sensitive data, which stays in Labs');
  });

  it('Connect could not confirm access: try again, not "not yours"', async () => {
    for (const [message, data] of [
      [
        "unavailable: Connect couldn't confirm your access right now; try again",
        { code: 'unavailable' },
      ],
      [
        "host_refused: labs refused: unavailable: Connect couldn't confirm your access right now; try again",
      ],
    ]) {
      const view = await refused(message, data);
      expect(view.text()).toContain(
        'Connect couldn’t confirm your access right now',
      );
      expect(view.text()).not.toContain('not yours');
    }
  });

  it('a message-only host: the code is read from the head of Labs’ message', async () => {
    const view = await refused(
      'host_refused: labs refused: forbidden: this result is not yours to read; it expired',
    );
    expect(view.text()).toContain('This result is not yours to see');
  });

  it('not yours', async () => {
    const view = await refused(
      'host_refused: labs refused: forbidden: this result is not yours to read',
    );
    expect(view.text()).toContain('This result is not yours to see');
  });

  it('sensitive data that stays in Labs, with the Data page when the facts name it', async () => {
    const view = mount({
      read: () => ({
        error: {
          code: -32603,
          message:
            'host_refused: labs refused: forbidden: this result holds sensitive data',
        },
      }),
    });
    await settle();
    view.notify('ui/notifications/tool-result', {
      content: [{ type: 'text', text: '{}' }, LINK],
      structuredContent: {
        row_count: 3,
        columns: COLUMNS,
        open_on_page: 'https://labs.example/labs/data/?opps=1',
      },
    });
    await settle();
    expect(view.text()).toContain('sensitive data, which stays in Labs');
    view.click('#open');
    expect(view.requests('ui/open-link').map((m) => m.params.url)).toEqual([
      'https://labs.example/labs/data/?opps=1',
    ]);
  });

  it('no link at all: ask again in the chat', async () => {
    const view = mount();
    await settle();
    view.notify('ui/notifications/tool-result', {
      content: [],
      structuredContent: null,
      _meta: { 'canopy/withheld': true },
    });
    await settle();
    expect(view.text()).toContain('ask again in the chat');
    expect(view.requests('resources/read')).toEqual([]);
    expect(view.requests('tools/call')).toEqual([]);
  });
});

it('a withheld result offers the Data page and shows no rows', async () => {
  const view = mount({
    read: (uri) => ({
      result: {
        contents: [
          {
            uri,
            mimeType: 'application/json',
            text: JSON.stringify({
              withheld: true,
              message: 'Production visit data is sensitive.',
              sql_executed: 'SELECT 1',
              open_on_page: 'https://labs.example/labs/data/?opps=1',
            }),
            _meta: { 'labs/result': { snapshot: true } },
          },
        ],
      },
    }),
  });
  await settle();
  view.notify('ui/notifications/tool-result', SENSITIVE_RESULT);
  await settle();
  expect(view.text()).toContain('Sensitive data stays in Labs');
  expect(view.document.querySelector('table')).toBeNull();
  expect(view.document.getElementById('open')).not.toBeNull();
});

it('a result that fits on one page shows no paging controls', async () => {
  const rows = ROWS.slice(0, 27);
  const view = mount({
    read: (uri) => ({
      result: {
        contents: [
          {
            uri,
            mimeType: 'application/json',
            text: JSON.stringify({ columns: COLUMNS, rows }),
            _meta: {
              'labs/result': {
                page: 1,
                page_size: 100,
                pages: 1,
                row_count: 27,
                snapshot: true,
              },
            },
          },
        ],
      },
    }),
  });
  await settle();
  view.notify('ui/notifications/tool-result', SENSITIVE_RESULT);
  await settle();
  expect(view.document.querySelectorAll('tbody tr').length).toBe(27);
  expect(view.text()).toContain('27 rows');
  expect(view.document.querySelector('.pager')).toBeNull();
  expect(view.document.getElementById('prev')).toBeNull();
  // Sorting a single page needs no "within this page" caveat either.
  view.click('th[data-sort="1"]');
  expect(view.text()).not.toContain('Sorted within this page only');
});
