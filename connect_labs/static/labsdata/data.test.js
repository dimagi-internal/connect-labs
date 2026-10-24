// The Data page (/labs/data/): its CSV export guards against formula injection and
// opens in Excel as UTF-8, and a link from a result card (?run=1&download=1) runs the
// query once and highlights Download CSV -- never downloading by itself.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

const SCRIPT = readFileSync(new URL('./data.js', import.meta.url), 'utf8');

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function mount({ intent = {}, selected = true, result, saved } = {}) {
  const window = new Window({ url: 'https://labs.example/labs/data/' });
  const document = window.document;
  const options = {
    opportunities: [{ id: 7, name: 'Opp', llo: 'LLO', program_id: null }],
    programs: [],
    initial: {
      workflow: null,
      opportunity_ids: selected ? [7] : [],
      program_ids: [],
    },
  };
  document.body.innerHTML = `
    <script type="application/json" id="labsdata-options">${JSON.stringify(options)}</script>
    <div id="ex-chips"></div><div class="ex-picker"><input id="ex-add"><div id="ex-add-results"></div></div>
    <button id="ex-wf-btn">Workflow</button><span id="ex-wf-hint"></span><div id="ex-wf-list"></div>
    <div id="ex-cache"></div><div id="ex-tables"></div><span id="ex-tables-hint"></span>
    <textarea id="ex-sql">SELECT 1</textarea>
    <button id="ex-run">Run</button><input id="ex-max" value="500">
    <button id="ex-save" class="ex-btn ghost">Save query</button><div id="ex-saved"></div>
    <button id="ex-csv" class="ex-btn ghost" disabled>Download CSV</button>
    <div id="ex-result"></div><div id="ex-visit"></div>
    <script type="application/json" id="ex-open-intent">${JSON.stringify(intent)}</script>`;
  const posts = [];
  window.LABSDATA = {
    describeUrl: '/d',
    queryUrl: '/q',
    visitsUrl: '/v',
    savedUrl: '/s/',
    csrf: 't',
  };
  window.fetch = async (url, init) => {
    if (!init || !init.body)
      return {
        ok: true,
        status: 200,
        json: async () => ({ saved: saved || [] }),
      };
    posts.push({ url, body: JSON.parse(init.body) });
    if (url === '/s/')
      return {
        ok: true,
        status: 200,
        json: async () => ({ saved: { id: 1, version: 2 } }),
      };
    const body =
      url === '/q'
        ? result || {
            columns: ['name', 'n'],
            rows: [['=cmd', 1]],
            row_count: 1,
            truncated: false,
            elapsed_ms: 2,
            opportunities: [{ id: 7, name: 'Opp' }],
          }
        : {
            opportunities: [],
            loaded: [],
            relations: {},
            fields: [],
            sampled_visits: 0,
          };
    return { ok: true, status: 200, json: async () => body };
  };
  const clicks = [];
  window.URL.createObjectURL = () => 'blob:x';
  window.HTMLAnchorElement.prototype.click = function () {
    clicks.push(this.getAttribute('download'));
  };
  window.eval(SCRIPT);
  return { window, document, posts, clicks };
}

describe('the CSV export', () => {
  const { window } = mount();
  const { csvCell, toCsv } = window.LABSDATA_CSV;

  it('prefixes a text cell that would run as a formula', () => {
    for (const s of ['=SUM(A1)', '+1+1', '-2+3', '@cmd', '\tx', '\rx'])
      expect(csvCell(s).replace(/^"|"$/g, '').startsWith("'")).toBe(true);
    // Fullwidth forms some spreadsheets also evaluate.
    expect(csvCell('＝1+1')).toBe("'＝1+1");
  });

  it('leaves numbers, and text that is only a number, alone', () => {
    expect(csvCell(-12.5)).toBe('-12.5');
    expect(csvCell('-12.5')).toBe('-12.5');
    expect(csvCell('+3e5')).toBe('+3e5');
    expect(csvCell('home')).toBe('home');
    expect(csvCell(null)).toBe('');
  });

  it('quotes commas, quotes and line breaks, with CRLF rows', () => {
    expect(csvCell('a,"b"')).toBe('"a,""b"""');
    expect(toCsv(['a', 'b'], [[1, 'x\ny']])).toBe('a,b\r\n1,"x\ny"\r\n');
  });
});

describe('a link from a result card', () => {
  it('runs the query once on open and highlights Download CSV, downloading nothing', async () => {
    const { document, posts, clicks } = mount({
      intent: { run: true, download: true },
    });
    for (let i = 0; i < 8; i++) await tick();
    const runs = posts.filter((p) => p.url === '/q');
    expect(runs).toHaveLength(1);
    expect(runs[0].body).toMatchObject({
      opportunity_ids: [7],
      sql: 'SELECT 1',
      max_rows: 500,
    });
    const button = document.getElementById('ex-csv');
    expect(button.disabled).toBe(false);
    expect(button.classList.contains('ready')).toBe(true);
    expect(clicks).toEqual([]);
    button.click();
    expect(clicks).toHaveLength(1);
    expect(clicks[0]).toMatch(/^labs-data-\d{8}-\d{6}\.csv$/);
  });

  it('runs nothing without the run hint', async () => {
    const { posts } = mount({ intent: { run: false, download: true } });
    for (let i = 0; i < 8; i++) await tick();
    expect(posts.filter((p) => p.url === '/q')).toHaveLength(0);
  });
});

describe('saving a query', () => {
  it('a card link highlights Save and saves nothing until the person names it', async () => {
    const display = { columns: { n: { label: 'Visits' } } };
    const { window, document, posts } = mount({
      intent: { save: true, display },
    });
    for (let i = 0; i < 8; i++) await tick();
    const button = document.getElementById('ex-save');
    expect(button.classList.contains('ready')).toBe(true);
    expect(posts.filter((p) => p.url === '/s/')).toHaveLength(0);
    window.prompt = () => 'Visits by place';
    button.click();
    for (let i = 0; i < 8; i++) await tick();
    const saves = posts.filter((p) => p.url === '/s/');
    expect(saves).toHaveLength(1);
    // The query, its opportunities, row cap and display -- never rows.
    expect(saves[0].body).toEqual({
      name: 'Visits by place',
      sql: 'SELECT 1',
      opportunity_ids: [7],
      program_ids: [],
      max_rows: 500,
      display,
    });
  });

  it('opening a saved query runs it again, as the person, with its labels', async () => {
    const { document, posts } = mount({
      selected: false,
      saved: [
        {
          id: 3,
          name: 'By place',
          sql: 'SELECT place, n FROM x',
          selection: { opportunity_ids: [7], program_ids: [], workflow: null },
          max_rows: 50,
          display: { columns: { n: { label: 'Visits' } } },
          version: 1,
        },
      ],
      result: {
        columns: ['place', 'n'],
        rows: [['home', 3]],
        row_count: 1,
        truncated: false,
        elapsed_ms: 1,
        opportunities: [{ id: 7, name: 'Opp' }],
      },
    });
    for (let i = 0; i < 8; i++) await tick();
    document.querySelector('#ex-saved button.open').click();
    for (let i = 0; i < 8; i++) await tick();
    const runs = posts.filter((p) => p.url === '/q');
    expect(runs).toHaveLength(1);
    expect(runs[0].body).toEqual({
      opportunity_ids: [7],
      program_ids: [],
      sql: 'SELECT place, n FROM x',
      max_rows: 50,
    });
    const th = document.querySelectorAll('#ex-result th');
    expect(th[1].textContent).toBe('Visits');
    expect(th[1].getAttribute('title')).toBe('n');
  });
});
