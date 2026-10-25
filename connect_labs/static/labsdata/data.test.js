// The Data page (/labs/data/): its CSV export guards against formula injection and
// opens in Excel as UTF-8, and a link from a result card (?run=1&download=1) runs the
// query once and highlights Download CSV -- never downloading by itself. On a data
// space's page every call sends the space alone, and the agent's page actions switch
// the canvas, as the person, returning no rows.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

const SCRIPT = readFileSync(new URL('./data.js', import.meta.url), 'utf8');
// The chart module the page loads before data.js (the results card's own, inlined there).
const CHART = readFileSync(
  new URL('./result_chart.js', import.meta.url),
  'utf8',
);

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function settle() {
  for (let i = 0; i < 12; i++) await tick();
}

const SPACE = {
  id: 42,
  name: 'Team space',
  description: '',
  sources: {
    programs: [],
    opportunities: [{ id: 7, name: 'Opp', llo: 'LLO', program_id: null }],
    workflow: null,
  },
  opportunity_ids: [7],
  program_ids: [],
  workflow: null,
  new_in_programs: [],
  changed: null,
  audience: 'holders',
  managers: [{ id: 3, username: 'ana' }],
  is_manager: true,
};
// The same space as one of its members (not a manager) sees it.
const MEMBER_SPACE = { ...SPACE, is_manager: false };

const RESULT = {
  columns: ['name', 'n'],
  rows: [['=cmd', 1]],
  row_count: 1,
  truncated: false,
  elapsed_ms: 2,
  opportunities: [{ id: 7, name: 'Opp' }],
};

function mount({
  intent = {},
  selected = true,
  result,
  saved,
  space = null,
  groups = [],
  items = [],
  queryFails = false,
  panel = false,
  canAdminAssign = false,
  conversations = [],
  // The opportunities' cached visit counts, as describe reports them: before a load from
  // Connect, and after one.
  cached = 3,
  cachedAfterLoad = 3,
  // Hold a load from Connect until the test releases it (mount returns releaseLoad).
  holdLoad = false,
} = {}) {
  const window = new Window({ url: 'https://labs.example/labs/data/' });
  const document = window.document;
  const options = {
    opportunities: [{ id: 7, name: 'Opp', llo: 'LLO', program_id: null }],
    programs: [],
    initial: {
      workflow: null,
      opportunity_ids: selected && !space ? [7] : [],
      program_ids: [],
    },
    space,
    can_admin_assign: canAdminAssign,
  };
  document.body.innerHTML = `
    <script type="application/json" id="labsdata-options">${JSON.stringify(options)}</script>
    <select id="ex-space-switch"></select><button id="ex-space-new">New</button>
    <details id="ex-space-more" class="ex-more" hidden><summary>More</summary>
      <button id="ex-sources-edit">Update</button><button id="ex-space-archive">Archive</button></details>
    <div id="ex-space-notice"></div>
    <div id="ex-chips"></div><div class="ex-picker"><input id="ex-add"><div id="ex-add-results"></div></div>
    <button id="ex-wf-btn">Workflow</button><span id="ex-wf-hint"></span><div id="ex-wf-list"></div>
    <div id="ex-sources-card"></div><button id="ex-sources-save" hidden>Save</button>
    <button id="ex-sources-cancel" hidden>Cancel</button>
    <div id="ex-cache"></div><div id="ex-tables"></div><span id="ex-tables-hint"></span>
    <div id="ex-space-items"></div><div id="ex-saved-card"${space ? ' hidden' : ''}><div id="ex-saved"></div></div>
    <div id="ex-conversations-card"><div id="ex-conversations"></div>
      <input id="ex-conversation-label"><button id="ex-conversation-save" disabled>Save this conversation</button>
      <p id="ex-conversation-hint"></p><div id="ex-conversation-error" hidden></div></div>
    <div id="ex-access-card"><select id="ex-audience" hidden><option value="holders"></option>
      <option value="private"></option></select><p id="ex-audience-text"></p>
      <div id="ex-managers"></div>
      <div id="ex-manager-add" hidden><input id="ex-manager-person"><button id="ex-manager-add-btn">Add</button></div>
      <div id="ex-manager-admin" hidden><input id="ex-manager-admin-person"><button id="ex-manager-admin-btn">Assign</button></div>
      <div id="ex-access-error" hidden></div></div>
    <div id="ex-tabs"></div>
    <div id="ex-panes"><div id="ex-pane-editor" data-tab="editor">
      <textarea id="ex-sql">SELECT 1</textarea>
      <button id="ex-run">Run</button><input id="ex-max" value="500">
      <button id="ex-save" class="ex-btn ghost">Save query</button>
      <button id="ex-csv" class="ex-btn ghost" disabled>Download CSV</button>
      <div id="ex-result"></div>
    </div></div>
    <div id="ex-save-dialog" hidden><input id="ex-save-name"><select id="ex-save-target"></select>
      <pre id="ex-save-sql"></pre><div id="ex-save-error" hidden></div>
      <button id="ex-save-cancel">Cancel</button><button id="ex-save-confirm">Save</button></div>
    <script type="application/json" id="ex-open-intent">${JSON.stringify(intent)}</script>`;
  const posts = [];
  const gets = [];
  let releaseLoad = null;
  window.LABSDATA = {
    describeUrl: '/d',
    queryUrl: '/q',
    visitsUrl: '/v',
    savedUrl: '/s/',
    indexUrl: '/labs/data/',
    spacesUrl: '/sp/',
    spacePageUrl: '/labs/data/space/0/',
    spaceApiUrl: '/sp/0/',
    csrf: 't',
    ...(panel
      ? {
          pageHoldsUrl: '/ph',
          panelPageToken: 'page-token',
          panelHolds: 'signed',
        }
      : {}),
  };
  const ok = (body) => ({ ok: true, status: 200, json: async () => body });
  window.fetch = async (url, init) => {
    if (!init || !init.body) {
      gets.push(url);
      if (url === '/sp/') return ok({ groups });
      if (url === '/sp/42/items/') return ok({ space_id: 42, items });
      if (url === '/sp/42/conversations/')
        return ok({ space_id: 42, conversations });
      return ok({ saved: saved || [] });
    }
    posts.push({ url, body: JSON.parse(init.body) });
    if (holdLoad && url === '/d' && JSON.parse(init.body).load_missing)
      await new Promise((resolve) => (releaseLoad = resolve));
    if (url.endsWith('/conversations/'))
      return ok({
        conversation: { id: 9, session_id: JSON.parse(init.body).session_id },
      });
    if (url === '/s/' || url.endsWith('/items/'))
      return ok({ saved: { id: 1, version: 2 } });
    if (url === '/q' && queryFails)
      return {
        ok: false,
        status: 400,
        json: async () => ({
          error: 'sql',
          message: 'invalid input syntax for type numeric: "a secret"',
        }),
      };
    if (url === '/v')
      return ok({ visits: [{ visit_id: 'v1' }], not_found: [] });
    const body =
      url === '/q'
        ? result || RESULT
        : {
            opportunities: [
              {
                opportunity_id: 7,
                opportunity_name: 'Opp',
                cached_visits: JSON.parse(init.body).load_missing
                  ? cachedAfterLoad
                  : cached,
              },
            ],
            loaded: [],
            relations: {},
            fields: [],
            sampled_visits: 0,
          };
    return ok(body);
  };
  const clicks = [];
  window.URL.createObjectURL = () => 'blob:x';
  window.HTMLAnchorElement.prototype.click = function () {
    clicks.push(this.getAttribute('download'));
  };
  window.eval(CHART);
  window.eval(SCRIPT);
  return {
    window,
    document,
    posts,
    gets,
    clicks,
    releaseLoad: () => releaseLoad && releaseLoad(),
  };
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
    await settle();
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
    await settle();
    expect(posts.filter((p) => p.url === '/q')).toHaveLength(0);
  });
});

describe('saving a query', () => {
  it('a card link opens the Save dialog and saves nothing until the person names it', async () => {
    const display = { columns: { n: { label: 'Visits' } } };
    const { document, posts } = mount({
      intent: { save: true, display },
    });
    await settle();
    const button = document.getElementById('ex-save');
    expect(button.classList.contains('ready')).toBe(true);
    expect(document.getElementById('ex-save-dialog').hidden).toBe(false);
    expect(posts.filter((p) => p.url === '/s/')).toHaveLength(0);
    document.getElementById('ex-save-name').value = 'Visits by place';
    document.getElementById('ex-save-confirm').click();
    await settle();
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

  it('a card link defaults to the space whose sources match its selection', async () => {
    const { document, posts } = mount({
      intent: { save: true },
      groups: [
        {
          key: 'opportunity:7',
          label: 'Opportunity: Opp',
          spaces: [
            {
              id: 42,
              name: 'Team space',
              opportunity_ids: [7],
              program_ids: [],
              workflow: null,
            },
          ],
        },
      ],
    });
    await settle();
    expect(document.getElementById('ex-save-target').value).toBe('42');
    document.getElementById('ex-save-name').value = 'Into the space';
    document.getElementById('ex-save-confirm').click();
    await settle();
    const saves = posts.filter((p) => p.url === '/sp/42/items/');
    expect(saves).toHaveLength(1);
    expect(saves[0].body).toEqual({
      name: 'Into the space',
      sql: 'SELECT 1',
      max_rows: 500,
      display: null,
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
    await settle();
    document.querySelector('#ex-saved button.open').click();
    await settle();
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

  it('a result over opportunities with nothing cached says it is not a real zero', async () => {
    const { document } = mount({
      selected: false,
      saved: [
        {
          id: 5,
          name: 'Count',
          sql: 'SELECT count(*) AS n FROM visits',
          selection: { opportunity_ids: [7], program_ids: [], workflow: null },
          max_rows: 50,
          display: null,
          version: 1,
        },
      ],
      result: {
        columns: ['n'],
        rows: [[0]],
        row_count: 1,
        truncated: false,
        elapsed_ms: 1,
        opportunities: [{ id: 7, name: 'Opp', cached_at: null }],
      },
    });
    await settle();
    document.querySelector('#ex-saved button.open').click();
    await settle();
    const warn = document.querySelector('#ex-result .ex-warn');
    expect(warn.textContent).toContain('not a real zero');
    expect(warn.textContent).toContain('Load them from Connect');
  });

  it('a saved display formats its cells: a month as a month, a fraction as a percent', async () => {
    const { document } = mount({
      selected: false,
      saved: [
        {
          id: 4,
          name: 'Monthly',
          sql: 'SELECT m, r, n FROM x',
          selection: { opportunity_ids: [7], program_ids: [], workflow: null },
          max_rows: 50,
          display: {
            columns: {
              m: { label: 'Month', format: 'month' },
              r: { label: 'Rate', format: 'percent_1' },
              n: { label: 'Visits', format: 'number_0' },
            },
          },
          version: 1,
        },
      ],
      result: {
        columns: ['m', 'r', 'n'],
        rows: [['2025-05-01T00:00:00+00:00', 0.253, '1234.4']],
        row_count: 1,
        truncated: false,
        elapsed_ms: 1,
        opportunities: [{ id: 7, name: 'Opp' }],
      },
    });
    await settle();
    document.querySelector('#ex-saved button.open').click();
    await settle();
    const td = [...document.querySelectorAll('#ex-result td')].map(
      (c) => c.textContent,
    );
    expect(td[0]).toBe('May 2025');
    expect(td[1]).toMatch(/^25\.3\s?%$/);
    expect(td[2]).toBe((1234).toLocaleString());
  });
});

describe("the page's agent reads what is on screen", () => {
  it('the plain page tells labs its selection, in the person own session', async () => {
    const { posts } = mount({ panel: true });
    await settle();
    const told = posts.filter((p) => p.url === '/ph');
    expect(told.length).toBe(1);
    expect(told[0].body).toEqual({
      page_token: 'page-token',
      page_holds: 'signed',
      opportunity_ids: [7],
      program_ids: [],
    });
  });

  it('a space page never does: its selection is the space', async () => {
    const { posts } = mount({ panel: true, space: SPACE });
    await settle();
    expect(posts.filter((p) => p.url === '/ph')).toEqual([]);
  });

  it('without a panel there is nothing to tell', async () => {
    const { posts } = mount();
    await settle();
    expect(posts.filter((p) => p.url === '/ph')).toEqual([]);
  });
});

describe('a data space page', () => {
  it('reads the space alone: every call sends space_id, and its sources are locked', async () => {
    const { document, posts } = mount({ space: SPACE });
    await settle();
    const described = posts.filter((p) => p.url === '/d');
    expect(described[0].body).toEqual({ space_id: 42, load_missing: false });
    document.getElementById('ex-run').click();
    await settle();
    const run = posts.find((p) => p.url === '/q');
    expect(run.body).toEqual({ space_id: 42, sql: 'SELECT 1', max_rows: 500 });
    // A source chip has no remove button; Update sources is offered instead.
    expect(document.querySelector('#ex-chips button')).toBeNull();
    expect(document.getElementById('ex-sources-edit').hidden).toBe(false);
    expect(document.querySelector('.ex-picker').hidden).toBe(true);
  });

  it('Update sources asks before changing them, and sends the confirm', async () => {
    const { window, document, posts } = mount({ space: SPACE });
    await settle();
    document.getElementById('ex-sources-edit').click();
    await settle();
    // Editing: chips can be removed, and the describe reads the edited selection.
    expect(document.querySelector('#ex-chips button')).not.toBeNull();
    window.confirm = () => false;
    document.getElementById('ex-sources-save').click();
    await settle();
    expect(posts.filter((p) => p.url.endsWith('/sources/'))).toHaveLength(0);
    window.confirm = () => true;
    window.location.reload = () => {};
    document.getElementById('ex-sources-save').click();
    await settle();
    const changed = posts.filter((p) => p.url === '/sp/42/sources/');
    expect(changed).toHaveLength(1);
    expect(changed[0].body).toEqual({
      confirm: true,
      opportunity_ids: [7],
      program_ids: [],
    });
  });
});

describe('who can open a space, and its managers', () => {
  it('a member sees the audience and managers read-only, and no management buttons', async () => {
    const { document } = mount({
      space: MEMBER_SPACE,
      items: [
        {
          id: 5,
          name: 'Mine',
          sql: 'SELECT 1',
          can_delete: true,
          versions: [],
        },
        {
          id: 6,
          name: 'Theirs',
          sql: 'SELECT 1',
          can_delete: false,
          versions: [],
        },
      ],
    });
    await settle();
    expect(document.getElementById('ex-audience').hidden).toBe(true);
    expect(document.getElementById('ex-audience-text').textContent).toBe(
      'Anyone with access to the sources',
    );
    expect(document.getElementById('ex-managers').textContent).toContain('ana');
    expect(document.querySelector('#ex-managers button')).toBeNull();
    expect(document.getElementById('ex-manager-add').hidden).toBe(true);
    expect(document.getElementById('ex-manager-admin').hidden).toBe(true);
    expect(document.getElementById('ex-space-archive').hidden).toBe(true);
    expect(document.getElementById('ex-sources-edit').hidden).toBe(true);
    // Delete only on the item they saved.
    const rows = document.querySelectorAll('#ex-space-items .ex-saved-row');
    expect(rows[0].querySelector('.del')).not.toBeNull();
    expect(rows[1].querySelector('.del')).toBeNull();
  });

  it('a labs admin who is not a manager is offered Assign', async () => {
    const { document } = mount({ space: MEMBER_SPACE, canAdminAssign: true });
    await settle();
    expect(document.getElementById('ex-manager-admin').hidden).toBe(false);
  });

  it('a manager sets the audience (after a confirm) and adds and removes managers', async () => {
    const { window, document, posts } = mount({ space: SPACE });
    await settle();
    expect(document.getElementById('ex-audience').hidden).toBe(false);
    expect(document.getElementById('ex-space-archive').hidden).toBe(false);
    const audience = document.getElementById('ex-audience');
    window.confirm = () => false;
    audience.value = 'private';
    audience.dispatchEvent(new window.Event('change'));
    await settle();
    expect(posts.filter((p) => p.url.endsWith('/audience/'))).toHaveLength(0);
    window.confirm = () => true;
    audience.value = 'private';
    audience.dispatchEvent(new window.Event('change'));
    await settle();
    expect(posts.find((p) => p.url === '/sp/42/audience/').body).toEqual({
      audience: 'private',
    });
    document.getElementById('ex-manager-person').value = 'bo@invented.example';
    document.getElementById('ex-manager-add-btn').click();
    await settle();
    expect(posts.find((p) => p.url === '/sp/42/managers/').body).toEqual({
      person: 'bo@invented.example',
    });
    // The only manager cannot be removed (the server says last_manager): not offered.
    expect(document.querySelector('#ex-managers button')).toBeNull();
  });

  it('a manager removes another manager', async () => {
    const two = {
      ...SPACE,
      managers: [
        { id: 3, username: 'ana' },
        { id: 4, username: 'bo' },
      ],
    };
    const { window, document, posts } = mount({ space: two });
    await settle();
    window.confirm = () => true;
    document.querySelector('#ex-managers button').click();
    await settle();
    expect(posts.some((p) => p.url === '/sp/42/managers/3/remove/')).toBe(true);
  });
});

describe('page actions (the agent switching the canvas)', () => {
  it('showQuery runs the query in a new tab as the person and returns no rows', async () => {
    const { window, document, posts } = mount({ space: SPACE });
    await settle();
    const ack = await window.LABSDATA_ACTIONS.showQuery({
      sql: 'SELECT 2',
      title: 'Two',
      display: { columns: { n: { label: 'Visits' } } },
    });
    expect(ack).toMatchObject({
      ok: true,
      shown: true,
      outcome: 'shown_to_person',
    });
    expect(JSON.stringify(ack)).not.toContain('=cmd');
    const run = posts.filter((p) => p.url === '/q').pop();
    expect(run.body).toEqual({ space_id: 42, sql: 'SELECT 2', max_rows: 500 });
    // The editor is untouched; the result is in the new tab.
    expect(document.getElementById('ex-sql').value).toBe('SELECT 1');
    const tabs = document.querySelectorAll('#ex-tabs .ex-tab');
    expect(tabs).toHaveLength(2);
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
    expect(document.getElementById('ex-pane-editor').hidden).toBe(true);
    const pane = document.querySelector(`#ex-panes [data-tab="${ack.tab}"]`);
    expect(pane.querySelectorAll('th')[1].textContent).toBe('Visits');
  });

  it('a failed showQuery tells the agent only the error code, never the message', async () => {
    const { window } = mount({ space: SPACE, queryFails: true });
    await settle();
    const ack = await window.LABSDATA_ACTIONS.showQuery({
      sql: 'SELECT x::numeric',
    });
    expect(ack.outcome).toBe('error_shown_to_person');
    expect(ack.error).toBe('sql');
    expect(JSON.stringify(ack)).not.toContain('a secret');
  });

  it('refuses bad input by throwing', async () => {
    const { window } = mount({ space: SPACE });
    await settle();
    const a = window.LABSDATA_ACTIONS;
    await expect(a.showQuery({ sql: '  ' })).rejects.toThrow(/sql/);
    await expect(
      a.showQuery({ sql: 'SELECT 1', display: 'x' }),
    ).rejects.toThrow(/display/);
    await expect(a.openItem({ kind: 'artifact', id: 1 })).rejects.toThrow(
      /Artifacts/,
    );
    await expect(a.openItem({ kind: 'query', id: 999 })).rejects.toThrow(
      /No such/,
    );
    await expect(a.showVisit({ visit_id: '../x' })).rejects.toThrow(/visit_id/);
    await expect(a.proposeSave({ sql: 'SELECT 1' })).rejects.toThrow(/name/);
    // On a space's page the sources are fixed.
    await expect(
      a.data_set_query({ sql: 'SELECT 1', opportunity_ids: [8] }),
    ).rejects.toThrow(/data space/);
  });

  it('proposeSave fills the dialog for this space and saves nothing', async () => {
    const { window, document, posts } = mount({ space: SPACE });
    await settle();
    const ack = await window.LABSDATA_ACTIONS.proposeSave({
      name: 'Weekly visits',
      sql: 'SELECT 3',
    });
    expect(ack).toMatchObject({ ok: true, proposed: true, saved: false });
    expect(document.getElementById('ex-save-dialog').hidden).toBe(false);
    expect(document.getElementById('ex-save-name').value).toBe('Weekly visits');
    expect(document.getElementById('ex-save-target').value).toBe('42');
    expect(posts.filter((p) => p.url.endsWith('/items/'))).toHaveLength(0);
  });

  it('openItem opens a space query in a tab; showVisit opens a visit over the space', async () => {
    const { window, posts } = mount({
      space: SPACE,
      items: [
        {
          id: 5,
          name: 'Kept',
          sql: 'SELECT 5',
          max_rows: 20,
          display: null,
          version: 2,
          versions: [
            { id: 5, version: 2, sql: 'SELECT 5', max_rows: 20, display: null },
            { id: 4, version: 1, sql: 'SELECT 4', max_rows: 20, display: null },
          ],
        },
      ],
    });
    await settle();
    const ack = await window.LABSDATA_ACTIONS.openItem({
      kind: 'query',
      id: 4,
    });
    expect(ack).toMatchObject({ ok: true, opened: true });
    expect(posts.filter((p) => p.url === '/q').pop().body).toEqual({
      space_id: 42,
      sql: 'SELECT 4',
      max_rows: 20,
    });
    const visit = await window.LABSDATA_ACTIONS.showVisit({ visit_id: 'v1' });
    expect(visit).toMatchObject({ ok: true, shown: true });
    expect(JSON.stringify(visit)).not.toContain('visit_id');
    expect(posts.filter((p) => p.url === '/v').pop().body).toEqual({
      space_id: 42,
      visit_ids: ['v1'],
    });
  });

  it('registers every action with canopy', async () => {
    const { window, document } = mount({ space: SPACE });
    const names = [];
    const host = { widget: { registerAction: (name) => names.push(name) } };
    document.dispatchEvent(
      new window.CustomEvent('canopy:ready', { detail: host }),
    );
    expect(names.sort()).toEqual(
      [
        'data_set_query',
        'openItem',
        'proposeSave',
        'showQuery',
        'showVisit',
      ].sort(),
    );
  });
});

describe('saved conversations (pointers to canopy chat sessions)', () => {
  const SID = '0f8fad5b-d9cb-469f-a165-70867728950e';
  const tellOpen = (window, document, session) =>
    document.dispatchEvent(
      new window.CustomEvent('canopy:session', { detail: session }),
    );

  it('saves only a conversation started on this space, and sends a pointer and a label alone', async () => {
    const { window, document, posts } = mount({ space: SPACE });
    await settle();
    const btn = document.getElementById('ex-conversation-save');
    expect(btn.disabled).toBe(true);

    tellOpen(window, document, {
      id: SID,
      resource: 'labs-data-space://99',
      private: true,
    });
    expect(btn.disabled).toBe(true);
    expect(
      document.getElementById('ex-conversation-hint').textContent,
    ).toContain('another page');

    tellOpen(window, document, {
      id: SID,
      resource: 'labs-data-space://42',
      private: true,
    });
    expect(btn.disabled).toBe(false);
    document.getElementById('ex-conversation-label').value = 'Why O2 dipped';
    btn.click();
    await settle();
    const saved = posts.filter((p) => p.url === '/sp/42/conversations/');
    expect(saved).toHaveLength(1);
    expect(saved[0].body).toEqual({
      session_id: SID,
      resource: 'labs-data-space://42',
      label: 'Why O2 dipped',
    });
  });

  it('a blank label saves as "Conversation, <date>"', async () => {
    const { window, document, posts } = mount({ space: SPACE });
    await settle();
    tellOpen(window, document, {
      id: SID,
      resource: 'labs-data-space://42',
      private: false,
    });
    document.getElementById('ex-conversation-save').click();
    await settle();
    const [saved] = posts.filter((p) => p.url === '/sp/42/conversations/');
    expect(saved.body.label).toMatch(/^Conversation, \d{1,2} \w+ \d{4}$/);
  });

  it("lists the space's conversations and opens one in the panel by its id", async () => {
    const { window, document } = mount({
      space: MEMBER_SPACE,
      conversations: [
        {
          id: 1,
          session_id: SID,
          label: 'Mine',
          saved_by: 'bob',
          created_at: '2026-10-24T10:00:00Z',
          can_delete: true,
        },
        {
          id: 2,
          session_id: 'x',
          label: 'Hers',
          saved_by: 'ana',
          created_at: '2026-10-24T09:00:00Z',
          can_delete: false,
        },
      ],
    });
    const opened = [];
    window.canopyHost = {
      openSession: (id) => (opened.push(id), true),
      session: () => null,
    };
    await settle();
    const rows = document.querySelectorAll('[data-conversation-id]');
    expect(rows).toHaveLength(2);
    // A member may delete their own pointer only.
    expect(rows[0].querySelector('.del')).not.toBeNull();
    expect(rows[1].querySelector('.del')).toBeNull();
    rows[0].querySelector('.open').click();
    expect(opened).toEqual([SID]);
  });

  it('an already-saved conversation is not offered again', async () => {
    const { window, document } = mount({
      space: SPACE,
      conversations: [
        {
          id: 1,
          session_id: SID,
          label: 'Mine',
          saved_by: 'ana',
          created_at: '',
          can_delete: true,
        },
      ],
    });
    await settle();
    tellOpen(window, document, {
      id: SID,
      resource: 'labs-data-space://42',
      private: false,
    });
    expect(document.getElementById('ex-conversation-save').disabled).toBe(true);
    expect(
      document.getElementById('ex-conversation-hint').textContent,
    ).toContain('is saved here');
  });
});

describe("a saved query's chart, on the canvas", () => {
  const MONTHS = {
    columns: ['opp', 'visits'],
    rows: [
      ['North', 30],
      ['South', 10],
      ['East', 20],
    ],
    row_count: 3,
    truncated: false,
    elapsed_ms: 1,
    opportunities: [{ id: 7, name: 'Opp' }],
  };
  const item = (display) => ({
    id: 5,
    name: 'Visits by opp',
    sql: 'SELECT opp, visits FROM x',
    max_rows: 50,
    display,
    version: 1,
    versions: [],
  });

  it('opens on the chart its display asked for, drawn as the card draws it, the table one click away', async () => {
    const { document } = mount({
      space: SPACE,
      result: MONTHS,
      items: [
        item({
          columns: { visits: { label: 'Visits' } },
          chart: { type: 'bar', x: 'opp', y: ['visits'] },
        }),
      ],
    });
    await settle();
    document.querySelector('#ex-space-items button.open').click();
    await settle();
    const pane = document.querySelector('.ex-pane[data-tab="t1"]');
    const tabs = [...pane.querySelectorAll('.ex-viewtabs button')];
    expect(tabs.map((b) => b.textContent)).toEqual(['Chart', 'Table']);
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    const chart = pane.querySelector('.ex-chart');
    expect(chart.hidden).toBe(false);
    // One bar per row, largest first, as the results card draws them.
    expect(chart.querySelectorAll('rect').length).toBe(3);
    expect(chart.querySelector('rect title').textContent).toContain('North');
    expect(chart.querySelector('.legend').textContent).toBe('Visits');
    expect(pane.querySelector('.ex-table-wrap').hidden).toBe(true);
    tabs[1].click();
    expect(pane.querySelector('.ex-table-wrap').hidden).toBe(false);
    expect(chart.hidden).toBe(true);
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
  });

  it('draws a line for a line chart, and nothing for a query saved without one', async () => {
    const line = mount({
      space: SPACE,
      result: MONTHS,
      items: [item({ chart: { type: 'line', x: 'opp', y: ['visits'] } })],
    });
    await settle();
    line.document.querySelector('#ex-space-items button.open').click();
    await settle();
    expect(line.document.querySelectorAll('.ex-chart polyline').length).toBe(1);

    const plain = mount({ space: SPACE, result: MONTHS, items: [item(null)] });
    await settle();
    plain.document.querySelector('#ex-space-items button.open').click();
    await settle();
    expect(plain.document.querySelector('.ex-viewtabs')).toBeNull();
    expect(plain.document.querySelector('.ex-chart')).toBeNull();
    expect(plain.document.querySelector('.ex-table-wrap').hidden).toBe(false);
  });
});

describe('opening a space loads what its sources have not loaded', () => {
  const UNLOADED = {
    ...RESULT,
    columns: ['n'],
    rows: [[0]],
    opportunities: [{ id: 7, name: 'Opp', cached_at: null }],
  };

  it('loads them once, as the person, without holding the page up, and says so', async () => {
    const { document, posts, releaseLoad } = mount({
      space: SPACE,
      cached: 0,
      cachedAfterLoad: 40,
      holdLoad: true,
      result: UNLOADED,
    });
    await settle();
    const described = posts.filter((p) => p.url === '/d');
    expect(described.map((p) => p.body)).toEqual([
      { space_id: 42, load_missing: false },
      { space_id: 42, load_missing: true },
    ]);
    expect(document.getElementById('ex-cache').textContent).toContain(
      'Loading 1 opportunity from Connect',
    );
    // The page works meanwhile; a result run now says it is not a real zero.
    document.getElementById('ex-run').click();
    await settle();
    const warn = document.querySelector('#ex-result .ex-warn');
    expect(warn.textContent).toContain('not a real zero');
    expect(warn.textContent).toContain('loading from Connect now');
    releaseLoad();
    await settle();
    expect(document.getElementById('ex-cache').textContent).toContain(
      '40 visits cached',
    );
    expect(warn.textContent).toContain('They have loaded now');
    const runs = posts.filter((p) => p.url === '/q').length;
    warn.querySelector('button').click();
    await settle();
    expect(posts.filter((p) => p.url === '/q').length).toBe(runs + 1);
    // Once: nothing more is loaded.
    expect(
      posts.filter((p) => p.url === '/d' && p.body.load_missing),
    ).toHaveLength(1);
  });

  it('a space whose sources are loaded loads nothing', async () => {
    const { posts } = mount({ space: SPACE });
    await settle();
    expect(posts.filter((p) => p.url === '/d')).toHaveLength(1);
  });

  it('the plain page never loads by itself', async () => {
    const { posts } = mount({ cached: 0 });
    await settle();
    const described = posts.filter((p) => p.url === '/d');
    expect(described).toHaveLength(1);
    expect(described[0].body.load_missing).toBe(false);
  });
});

describe("a space's rail", () => {
  it("keeps a manager's rare actions in More, and shows no More to a member", async () => {
    const manager = mount({ space: SPACE });
    await settle();
    expect(manager.document.getElementById('ex-space-more').hidden).toBe(false);
    const member = mount({ space: MEMBER_SPACE });
    await settle();
    expect(member.document.getElementById('ex-space-more').hidden).toBe(true);
  });

  it('shows "My queries" only when the person has some', async () => {
    const empty = mount({ space: SPACE, saved: [] });
    await settle();
    expect(empty.document.getElementById('ex-saved-card').hidden).toBe(true);
    const some = mount({
      space: SPACE,
      saved: [{ id: 1, name: 'Mine', sql: 'SELECT 1', version: 1 }],
    });
    await settle();
    expect(some.document.getElementById('ex-saved-card').hidden).toBe(false);
  });

  it('off a space, the switcher says in plain words that the selection is not saved', async () => {
    const { document } = mount();
    await settle();
    expect(document.querySelector('#ex-space-switch option').textContent).toBe(
      'Not saved — save this selection as a space',
    );
    expect(document.getElementById('ex-saved-card').hidden).toBe(false);
  });
});
