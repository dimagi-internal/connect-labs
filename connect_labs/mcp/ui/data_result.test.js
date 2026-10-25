// The data_query results View (ui://labs/data-result) against a stand-in MCP Apps host:
// it reads the kept result from the tool result's resource_link (labs-result://) as its
// viewer, pages and sorts through it (saying the sort is per page), never subscribes,
// names each refusal (an expired one falls back on the facts' sample) -- and never runs the query again (no tools/call).
// Spec: canopy-web docs/superpowers/specs/2026-10-23-host-held-results-design.md.
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

// As Labs serves it (connect_labs/mcp/ui/__init__.py): the shared chart module inlined
// at its @include marker.
const HTML = readFileSync(
  new URL('./data_result.html', import.meta.url),
  'utf8',
).replace(/\/\*@include:([a-z0-9_]+\/[a-z0-9_]+\.js)\*\//g, (_, path) =>
  readFileSync(new URL(`../../static/${path}`, import.meta.url), 'utf8'),
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
function mount({ read, hostContext } = {}) {
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
            hostContext: hostContext || {},
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
  // Labs sorts the whole result before the page is cut (?sort=&dir=).
  let rows = ROWS;
  const sort = url.searchParams.get('sort');
  if (sort) {
    const i = COLUMNS.indexOf(sort);
    const dir = url.searchParams.get('dir') === 'desc' ? -1 : 1;
    rows = ROWS.slice().sort((a, b) =>
      a[i] < b[i] ? -dir : a[i] > b[i] ? dir : 0,
    );
  }
  const body = {
    columns: COLUMNS,
    rows: rows.slice((page - 1) * size, page * size),
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

  it('sorts every row, not just the page, when there are more pages', async () => {
    const first = () =>
      Number(
        view.document.querySelector('tbody tr td:nth-child(2)').textContent,
      );
    view.click('th[data-sort="1"]');
    await settle();
    let reads = view.requests('resources/read');
    expect(reads[reads.length - 1].params.uri).toBe(
      `${URI}?page=1&page_size=100&sort=visits&dir=asc`,
    );
    expect(first()).toBe(0);
    view.click('th[data-sort="1"]');
    await settle();
    reads = view.requests('resources/read');
    expect(reads[reads.length - 1].params.uri).toContain(
      '&sort=visits&dir=desc',
    );
    // The page Labs sorted, largest first.
    expect(first()).toBe(49);
    // Paging keeps the sort.
    view.click('#next');
    await settle();
    reads = view.requests('resources/read');
    expect(reads[reads.length - 1].params.uri).toBe(
      `${URI}?page=2&page_size=100&sort=visits&dir=desc`,
    );
    expect(view.text()).not.toContain('Sorted within this page only');
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

// A host whose kept result is `body` (one page).
function holding(body) {
  return (uri) => ({
    result: {
      contents: [
        {
          uri,
          mimeType: 'application/json',
          text: JSON.stringify(body),
          _meta: {
            'labs/result': {
              page: 1,
              page_size: 100,
              pages: 1,
              row_count: (body.rows || []).length,
              snapshot: true,
            },
          },
        },
      ],
    },
  });
}

async function showing(body, options = {}) {
  const view = mount({ read: holding(body), ...options });
  await settle();
  view.notify('ui/notifications/tool-input', {
    arguments: { opportunity_ids: [1], sql: 'SELECT 1' },
  });
  view.notify('ui/notifications/tool-result', SENSITIVE_RESULT);
  await settle();
  return view;
}

const cells = (view, row = 0) => [
  ...view.document.querySelectorAll('tbody tr')[row].querySelectorAll('td'),
];

describe('cells are formatted by what their column holds', () => {
  const TYPED = {
    columns: [
      'opportunity_id',
      'visits',
      'approval_rate',
      'first_visit',
      'read_at',
      'form',
      'llo',
    ],
    column_types: [
      'integer',
      'integer',
      'numeric',
      'date',
      'timestamp',
      'json',
      'text',
    ],
    rows: [
      [
        10234,
        12345,
        0.123456,
        '2026-03-05',
        '2026-03-05T14:30:00+00:00',
        { a: 1, b: 2 },
        null,
      ],
    ],
  };

  it('an id is plain and left-aligned; counts get separators; a rate is a percent', async () => {
    const view = await showing(TYPED);
    const [id, visits, rate] = cells(view);
    expect(id.textContent).toBe('10234');
    expect(id.className).toBe('');
    expect(visits.textContent).toBe('12,345');
    expect(visits.className).toBe('num');
    // `approval_rate`, a fraction: a percent with no hint from the agent.
    expect(rate.textContent).toBe('12.3%');
    const heads = view.document.querySelectorAll('th');
    expect(heads[0].className).toBe('');
    expect(heads[1].className).toBe('num');
  });

  it('dates and timestamps in a short form, null as a muted dash', async () => {
    const view = await showing(TYPED);
    const [, , , date, stamp, , empty] = cells(view);
    expect(date.textContent).not.toBe('2026-03-05');
    expect(date.textContent).toMatch(/2026/);
    expect(date.textContent).toMatch(/Mar/);
    expect(stamp.textContent).not.toContain('T14');
    expect(empty.textContent).toBe('—');
    expect(empty.className).toBe('null');
  });

  it('JSON is a chip that opens the whole value', async () => {
    const view = await showing(TYPED);
    const chip = view.document.querySelector('.chip');
    expect(chip.textContent).toBe('{2 keys}');
    view.click('.chip');
    expect(view.document.querySelector('.json-panel pre').textContent).toBe(
      JSON.stringify({ a: 1, b: 2 }, null, 2),
    );
    view.click('#json-close');
    expect(view.document.querySelector('.json-panel')).toBeNull();
  });

  it('without column_types an id is still read from its name', async () => {
    const view = await showing({
      columns: ['flw_id', 'visits'],
      rows: [[10234, 10234]],
    });
    const [id, visits] = cells(view);
    expect(id.textContent).toBe('10234');
    expect(visits.textContent).toBe('10,234');
  });
});

it('headers read as words, with the column name as the tooltip', async () => {
  const view = await showing({
    columns: ['pct_first_fu_0_3d', 'llo_name'],
    rows: [[0.5, 'A']],
  });
  const heads = view.document.querySelectorAll('th');
  expect(heads[0].querySelector('.label').textContent).toBe(
    'Pct first FU 0–3d',
  );
  expect(heads[0].getAttribute('title')).toContain('pct_first_fu_0_3d');
  expect(heads[1].textContent).toBe('LLO name');
});

it('shows none of the notes written for the agent', async () => {
  const view = await showing({
    columns: COLUMNS,
    rows: [['w1', 1]],
    notes: ['Read more with site_read on `result_uri`.'],
  });
  expect(view.text()).not.toContain('site_read');
  expect(view.document.querySelector('.notes')).toBeNull();
});

describe('a result cut at a row cap', () => {
  const rows = [['w1', 1]];
  it('says so when the default or hard cap cut it', async () => {
    for (const source of ['default', 'hard']) {
      const view = await showing({
        columns: COLUMNS,
        rows,
        truncated: true,
        row_cap: { limit: 500, source },
      });
      expect(view.text()).toContain('cut at the row cap');
    }
  });

  it('does not, when the query asked for that many', async () => {
    const view = await showing({
      columns: COLUMNS,
      rows,
      truncated: true,
      row_cap: { limit: 50, source: 'requested' },
    });
    expect(view.text()).not.toContain('cut at the row cap');
    expect(view.text()).toContain('the first 50, as asked');
  });
});

it('no rows: says so, and the SQL is still there', async () => {
  const view = await showing({
    columns: COLUMNS,
    rows: [],
    sql_executed: 'SELECT worker FROM visits WHERE false',
  });
  expect(view.text()).toContain('The query returned no rows');
  expect(view.document.querySelector('table')).toBeNull();
  view.click('[data-tab="sql"]');
  expect(view.document.querySelector('pre').textContent).toBe(
    'SELECT worker FROM visits WHERE false',
  );
});

it('a failed query shows its code, what to do, and the SQL it tried', async () => {
  const view = mount();
  await settle();
  view.notify('ui/notifications/tool-input', {
    arguments: { opportunity_ids: [1], sql: 'SELECT * FROM visits' },
  });
  view.notify('ui/notifications/tool-result', {
    isError: true,
    content: [
      {
        type: 'text',
        text:
          'The query ran past 20s. Filter by date or opportunity, or aggregate.\n' +
          JSON.stringify({
            code: 'INVALID_SCHEMA',
            error: 'timeout',
            category: 'capacity',
            retryable: false,
            recovery_action: 'narrow_or_aggregate',
          }),
      },
    ],
  });
  await settle();
  expect(view.text()).toContain('The query could not run');
  expect(view.text()).toContain('timeout');
  expect(view.text()).toContain('The query ran past 20s');
  expect(view.text()).toContain('narrowed');
  expect(view.document.querySelector('pre').textContent).toBe(
    'SELECT * FROM visits',
  );
  expect(view.text()).not.toContain('recovery_action');
});

it('an expired result offers the Data page and to ask again in the chat', async () => {
  const view = mount({
    read: () => ({
      error: { code: -32603, message: 'gone', data: { code: 'expired' } },
    }),
  });
  await settle();
  view.notify('ui/notifications/tool-result', {
    content: [LINK],
    structuredContent: {
      open_on_page: 'https://labs.example/labs/data/?opps=1',
    },
  });
  await settle();
  expect(view.text()).toContain('This result has expired');
  expect(view.document.getElementById('open')).not.toBeNull();
  view.click('#ask');
  await settle();
  const [ask] = view.requests('ui/message');
  expect(ask.params.role).toBe('user');
  expect(ask.params.content.type).toBe('text');
  expect(ask.params.content.text).toMatch(/run the query again/);
  expect(view.requests('tools/call')).toEqual([]);
});

describe('the host decides how big the card may be', () => {
  it('no Expand unless the host offers full screen', async () => {
    const plain = await showing({ columns: COLUMNS, rows: [['w1', 1]] });
    expect(plain.document.getElementById('expand')).toBeNull();
    const roomy = await showing(
      { columns: COLUMNS, rows: [['w1', 1]] },
      { hostContext: { availableDisplayModes: ['inline', 'fullscreen'] } },
    );
    expect(roomy.document.getElementById('expand')).not.toBeNull();
  });

  it("caps the table in px from the host's maxHeight, not vh", async () => {
    const view = await showing(
      { columns: COLUMNS, rows: [['w1', 1]] },
      { hostContext: { containerDimensions: { maxHeight: 900 } } },
    );
    expect(
      view.document.documentElement.style.getPropertyValue('--table-max'),
    ).toBe('790px');
    expect(HTML).not.toMatch(/max-height:\s*\d+vh/);
  });
});

// A kept result of one page, as Labs keeps it: with the agent's display, the SQL laid
// out to read and each opportunity's freshness.
function onePage(body) {
  return () => ({
    result: {
      contents: [
        {
          uri: URI,
          mimeType: 'application/json',
          text: JSON.stringify(body),
          _meta: {
            'labs/result': {
              page: 1,
              page_size: 100,
              pages: 1,
              row_count: (body.rows || []).length,
              snapshot: true,
            },
          },
        },
      ],
    },
  });
}
async function shown(body) {
  const view = mount({ read: onePage(body) });
  await settle();
  view.notify('ui/notifications/tool-result', SENSITIVE_RESULT);
  await settle();
  return view;
}
const HOURS = 3600 * 1000;
const BY_LLO = {
  columns: ['llo', 'flw_id', 'visit_year', 'visits', 'cases', 'approval_rate'],
  column_types: ['text', 'integer', 'integer', 'integer', 'integer', 'numeric'],
  rows: [
    ['North', 100230, 2026, 1200, 300, 0.913],
    ['South', 100247, 2026, 4800, 900, 0.72],
    ['East', 100264, 2026, 2500, 610, 0.85],
  ],
  sql_executed: 'select llo, count(*) as visits from visits group by llo',
  sql_formatted:
    'SELECT\n  llo,\n  COUNT(*) AS visits\nFROM visits\nGROUP BY\n  llo',
  open_on_page:
    'https://labs.example/labs/data/?opps=1%2C2&sql=select+1&max_rows=500',
  opportunities: [
    {
      id: 1,
      name: 'Opp One',
      cached_at: new Date(Date.now() - 3 * HOURS).toISOString(),
    },
    {
      id: 2,
      name: 'Opp Two',
      cached_at: new Date(Date.now() - 0.5 * HOURS).toISOString(),
    },
  ],
};

describe("the agent's display", () => {
  const DISPLAY = {
    columns: {
      visits: { label: 'Visits <b>', format: 'number_0' },
      approval_rate: { label: 'Approval rate', format: 'percent_1' },
      cases: { format: 'compact_1' },
    },
  };

  it('labels the columns, raw name on hover, as plain text', async () => {
    const view = await shown({ ...BY_LLO, display: DISPLAY });
    const th = [...view.document.querySelectorAll('th')];
    const visits = th[3];
    expect(visits.textContent).toBe('Visits <b>');
    expect(visits.querySelector('b')).toBe(null);
    expect(visits.getAttribute('title')).toContain('visits');
    // A column with no label reads as its own name in words.
    expect(th[0].textContent).toBe('LLO');
  });

  it('applies the formats', async () => {
    const view = await shown({ ...BY_LLO, display: DISPLAY });
    const cells = [
      ...view.document.querySelectorAll('tbody tr'),
    ][1].querySelectorAll('td');
    expect(cells[3].textContent).toBe('4,800');
    expect(cells[5].textContent).toBe('72.0%');
    expect(cells[4].textContent).toBe('900');
  });

  it('draws the chart the agent asked for', async () => {
    const view = await shown({
      ...BY_LLO,
      display: { chart: { type: 'line', x: 'llo', y: ['cases'] } },
    });
    view.click('[data-tab="chart"]');
    expect(view.document.querySelectorAll('polyline').length).toBe(1);
    expect(view.document.querySelectorAll('rect').length).toBe(0);
  });

  it('a stat shows one headline number', async () => {
    const view = await shown({
      columns: ['total'],
      rows: [[48213]],
      display: {
        columns: {
          total: { label: 'Visits this quarter', format: 'number_0' },
        },
        chart: { type: 'stat', y: ['total'] },
      },
    });
    view.click('[data-tab="chart"]');
    expect(view.document.querySelector('.stat-value').textContent).toBe(
      '48,213',
    );
    expect(view.document.querySelector('.stat').textContent).toContain(
      'Visits this quarter',
    );
  });
});

describe('the tab the card opens on', () => {
  it("opens on the chart the agent's display asked for, the table one click away", async () => {
    const view = await shown({
      ...BY_LLO,
      display: { chart: { type: 'bar', x: 'llo', y: ['visits'] } },
    });
    expect(
      view.document
        .querySelector('[data-tab="chart"]')
        .getAttribute('aria-selected'),
    ).toBe('true');
    expect(view.document.querySelectorAll('rect').length).toBe(3);
    view.click('[data-tab="table"]');
    expect(view.document.querySelectorAll('tbody tr').length).toBe(3);
  });

  it('opens on the table when the agent asked for no chart', async () => {
    const view = await shown(BY_LLO);
    expect(
      view.document
        .querySelector('[data-tab="table"]')
        .getAttribute('aria-selected'),
    ).toBe('true');
  });
});

describe('month buckets read as months', () => {
  const MONTHLY = {
    columns: ['month', 'approved_visits'],
    column_types: ['date', 'integer'],
    rows: [
      ['2026-01-01', 1862],
      ['2026-02-01', 2565],
      ['2026-03-01', 4047],
    ],
  };

  it('a date_trunc month column shows "Jan 2026", even with format date', async () => {
    const view = await shown({
      ...MONTHLY,
      display: { columns: { month: { label: 'Month', format: 'date' } } },
    });
    expect(cells(view, 0)[0].textContent).toMatch(/^Jan\.? 2026$/);
    expect(cells(view, 0)[0].textContent).not.toMatch(/1,/);
  });

  it('timestamps at the start of each month too, and on the chart axis', async () => {
    const view = await shown({
      columns: ['month', 'approved_visits'],
      rows: [
        ['2026-01-01T00:00:00+00:00', 1862],
        ['2026-02-01T00:00:00+00:00', 2565],
        ['2026-03-01T00:00:00+00:00', 4047],
      ],
      display: { chart: { type: 'line', x: 'month', y: ['approved_visits'] } },
    });
    const ticks = [...view.document.querySelectorAll('svg text')].map(
      (t) => t.textContent,
    );
    expect(ticks.some((t) => /^Jan\.? 2026$/.test(t))).toBe(true);
    expect(ticks.some((t) => /T00/.test(t))).toBe(false);
  });

  it('a column of ordinary dates keeps its day', async () => {
    const view = await shown({
      columns: ['visit_date', 'visits'],
      column_types: ['date', 'integer'],
      rows: [
        ['2026-01-01', 1],
        ['2026-01-02', 2],
      ],
    });
    expect(cells(view, 0)[0].textContent).toMatch(/Jan/);
    expect(cells(view, 0)[0].textContent).not.toMatch(/^Jan\.? 2026$/);
    expect(cells(view, 1)[0].textContent).toMatch(/\b2\b/);
  });
});

describe('a chart the card picks itself', () => {
  it('leaves out ids and years, and never mixes a rate with a count', async () => {
    const view = await shown(BY_LLO);
    view.click('[data-tab="chart"]');
    const legend = view.document.querySelector('.legend').textContent;
    expect(legend).toContain('Visits');
    expect(legend).toContain('Cases');
    expect(legend).not.toMatch(/FLW ID|year|Approval/i);
  });

  it('a rate alone is charted on a percent axis', async () => {
    const view = await shown({
      columns: ['llo', 'approval_rate'],
      rows: [
        ['North', 0.9],
        ['South', 0.5],
      ],
    });
    view.click('[data-tab="chart"]');
    const ticks = [...view.document.querySelectorAll('svg text')].map(
      (t) => t.textContent,
    );
    expect(ticks.some((t) => /%$/.test(t))).toBe(true);
  });

  it('sorts bars largest first', async () => {
    const view = await shown(BY_LLO);
    view.click('[data-tab="chart"]');
    const labels = [
      ...view.document.querySelectorAll('svg text[transform]'),
    ].map((t) => t.textContent);
    expect(labels).toEqual(['South', 'East', 'North']);
  });

  it('caps the series at five', async () => {
    const cols = ['k', 'a', 'b', 'c', 'd', 'e', 'f', 'g'];
    const view = await shown({
      columns: cols,
      rows: [['x', 1, 2, 3, 4, 5, 6, 7]],
    });
    view.click('[data-tab="chart"]');
    expect(view.document.querySelectorAll('.legend span').length).toBe(5);
  });
});

describe('axis labels and the month so far', () => {
  it('leaves a prefix every bar shares off the axis, says it once, and keeps each full name on hover', async () => {
    const view = await shown({
      columns: ['opportunity_name', 'avg_weight'],
      rows: [
        ['[Synthetic] KMC - KE - Kikapu - P1 - May 26', 2100],
        ['[Synthetic] KMC - UG - NAMA - P1 - Apr 26', 1900],
        ['[Synthetic] KMC - NG - EHA - P1 - Mar 26', 1800],
      ],
      display: {
        chart: { type: 'bar', x: 'opportunity_name', y: ['avg_weight'] },
      },
    });
    const labels = [
      ...view.document.querySelectorAll('svg text[transform]'),
    ].map((t) => t.textContent);
    expect(labels.every((l) => !l.startsWith('[Synthetic]'))).toBe(true);
    expect(new Set(labels).size).toBe(3);
    expect(labels[0]).toMatch(/^KE - Kik/);
    // Cut in the middle: the end, which tells the opportunities apart, still shows.
    expect(labels.every((l) => l.length <= 18)).toBe(true);
    expect(labels[0]).toMatch(/May 26$/);
    const titles = [...view.document.querySelectorAll('svg g > title')].map(
      (t) => t.textContent,
    );
    expect(titles).toContain('[Synthetic] KMC - KE - Kikapu - P1 - May 26');
    expect(view.document.querySelector('.chart-note').textContent).toContain(
      '[Synthetic] KMC -',
    );
  });

  it('leaves room for every turned bar label, below and to the right', async () => {
    // 6.5px a character at 11px, turned 40 degrees: what the chart reserves room by.
    const reach = (t) => t.length * 6.5;
    const view = await shown({
      columns: ['username', 'visits'],
      rows: [
        ['amina.wanjiru.ke', 300],
        ['brian_otieno_long_username', 280],
        ['hassan.abdullahi.ibrahim', 100],
      ],
      display: { chart: { type: 'bar', x: 'username', y: ['visits'] } },
    });
    const svg = view.document.querySelector('svg');
    const [, , w, h] = svg.getAttribute('viewBox').split(' ').map(Number);
    for (const t of svg.querySelectorAll('text[transform]')) {
      const [x, y] = /translate\(([\d.]+),([\d.]+)\)/
        .exec(t.getAttribute('transform'))
        .slice(1)
        .map(Number);
      const len = reach(t.textContent);
      expect(x + Math.cos((40 * Math.PI) / 180) * len).toBeLessThanOrEqual(w);
      expect(y + Math.sin((40 * Math.PI) / 180) * len).toBeLessThanOrEqual(h);
    }
  });

  it("keeps a line chart's first and last dates inside the chart", async () => {
    const view = await shown({
      columns: ['visit_date', 'visits'],
      column_types: ['date', 'integer'],
      rows: [
        ['2026-09-21', 120],
        ['2026-10-05', 90],
        ['2026-10-20', 140],
      ],
      display: { chart: { type: 'line', x: 'visit_date', y: ['visits'] } },
    });
    const svg = view.document.querySelector('svg');
    const w = Number(svg.getAttribute('width'));
    const dates = [...svg.querySelectorAll('text[text-anchor="middle"]')].map(
      (t) => [Number(t.getAttribute('x')), t.textContent.length * 6.5],
    );
    expect(dates.length).toBe(3);
    for (const [x, len] of dates) {
      expect(x - len / 2).toBeGreaterThanOrEqual(0);
      expect(x + len / 2).toBeLessThanOrEqual(w);
    }
  });

  it('labels that share nothing are drawn as they are', async () => {
    const view = await shown(BY_LLO);
    view.click('[data-tab="chart"]');
    expect(view.document.querySelector('.chart-note')).toBeNull();
  });

  it("marks this month's point as the month so far", async () => {
    const now = new Date();
    const month = (back) => {
      const d = new Date(
        Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1),
      );
      return d.toISOString().slice(0, 10);
    };
    const view = await shown({
      columns: ['month', 'approved_visits'],
      column_types: ['date', 'integer'],
      rows: [
        [month(2), 3203],
        [month(1), 3349],
        [month(0), 610],
      ],
      display: { chart: { type: 'line', x: 'month', y: ['approved_visits'] } },
    });
    expect(view.document.querySelector('polyline.partial')).not.toBeNull();
    expect(view.document.querySelector('circle.partial')).not.toBeNull();
    expect(view.document.querySelector('.chart-note').textContent).toMatch(
      /so far/,
    );
  });

  it('a series of past months has no partial mark', async () => {
    const view = await shown({
      columns: ['month', 'approved_visits'],
      column_types: ['date', 'integer'],
      rows: [
        ['2026-01-01', 1862],
        ['2026-02-01', 2565],
      ],
      display: { chart: { type: 'line', x: 'month', y: ['approved_visits'] } },
    });
    expect(view.document.querySelector('.partial')).toBeNull();
  });
});

describe('provenance', () => {
  it('says how fresh the data is: the oldest, the rest on hover', async () => {
    const view = await shown(BY_LLO);
    const fresh = view.document.querySelector('.fresh');
    expect(fresh.textContent).toBe('Data as of 3 h ago (oldest)');
    expect(fresh.getAttribute('title')).toContain(
      'Opp Two: data as of 30 min ago',
    );
  });

  it('shows the SQL laid out to read', async () => {
    const view = await shown(BY_LLO);
    view.click('[data-tab="sql"]');
    expect(view.document.querySelector('pre').textContent).toBe(
      BY_LLO.sql_formatted,
    );
  });
});

describe('which opportunities a result read', () => {
  it('names up to three; more read as a count, the names on hover', async () => {
    const few = await shown(BY_LLO);
    expect(few.text()).toContain('Opp One, Opp Two');
    const many = await shown({
      ...BY_LLO,
      opportunities: Array.from({ length: 12 }, (_, i) => ({
        id: i,
        name: `Opp ${i}`,
      })),
    });
    const scope = many.document.querySelector('.scope');
    expect(scope.textContent).toBe('12 opportunities');
    expect(scope.getAttribute('title')).toContain('Opp 11');
    expect(many.text()).not.toContain('Opp 0, Opp 1');
  });
});

describe('download and save go through the Data page', () => {
  it('Download CSV opens the query there to run again and offer the CSV', async () => {
    const view = await shown(BY_LLO);
    view.click('#download');
    await settle();
    const [open] = view.requests('ui/open-link');
    expect(open.params.url).toBe(`${BY_LLO.open_on_page}&run=1&download=1`);
    // Never a download from inside the frame, and no tool call.
    expect(view.requests('tools/call')).toEqual([]);
  });

  it('Save opens the page with the Save button highlighted and the display', async () => {
    const display = { columns: { visits: { label: 'Visits' } } };
    const view = await shown({ ...BY_LLO, display });
    view.click('#save');
    await settle();
    const url = new URL(view.requests('ui/open-link')[0].params.url);
    expect(url.searchParams.get('save')).toBe('1');
    expect(url.searchParams.get('run')).toBe('1');
    expect(JSON.parse(url.searchParams.get('display'))).toEqual(display);
    expect(url.searchParams.get('sql')).toBe('select 1');
  });

  it('a workflow’s result offers Save to a space, through the Data page', async () => {
    const view = await shown({
      ...BY_LLO,
      open_on_page:
        'https://labs.example/labs/data/?workflow=9%3Aprogram%3A3&sql=select+1',
    });
    expect(view.document.getElementById('save').textContent).toBe(
      'Save to a space',
    );
    view.click('#save');
    await settle();
    const url = new URL(view.requests('ui/open-link')[0].params.url);
    expect(url.pathname).toBe('/labs/data/');
    expect(url.searchParams.get('workflow')).toBe('9:program:3');
    expect(url.searchParams.get('save')).toBe('1');
    // Off a workflow it is the plain Save.
    const plain = await shown(BY_LLO);
    expect(plain.document.getElementById('save').textContent).toBe('Save');
  });

  it('a withheld result still offers Save (its SQL), not a download', async () => {
    const view = await shown({
      withheld: true,
      message: 'Production rows stay in Labs.',
      sql_executed: 'select 1',
      open_on_page: 'https://labs.example/labs/data/?opps=1&sql=select+1',
    });
    expect(view.document.getElementById('save')).not.toBe(null);
    expect(view.document.getElementById('download')).toBe(null);
  });
});

describe('a percent column with no hint', () => {
  it('reads 0-100 values named pct_* as percentages, and leaves other numbers alone', async () => {
    const view = await shown({
      columns: ['llo', 'pct_first_fu_0_3d', 'visits', 'pct_over'],
      rows: [
        ['North', 70, 12, 150],
        ['South', 42.5, 9, 20],
      ],
    });
    const cells = view.document.querySelectorAll('tbody tr:first-child td');
    expect(cells[1].textContent).toBe('70%');
    expect(cells[2].textContent).toBe('12');
    // Outside 0-100 it is not a percentage, whatever it is called.
    expect(cells[3].textContent).toBe('150');
    const second = view.document.querySelectorAll('tbody tr:nth-child(2) td');
    expect(second[1].textContent).toBe('42.5%');
  });
});
