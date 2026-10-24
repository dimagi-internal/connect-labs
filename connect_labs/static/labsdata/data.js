// The Data page (/labs/data/). Talks to /labs/data/api/{describe,query,visits,workflows}/
// -- the same service the data_* MCP tools call -- and keeps the canopy panel's page
// state on the selection so the agent works on what you see.
//
// The selection is programs and opportunities, or one workflow (which brings its own
// opportunities and its pipelines as tables). Only ids live here: the server checks
// every one, every time, against what the person holds.
//
// On a data space's page (/labs/data/space/<id>/, labsdata/spaces.py) the selection IS
// the space: every call sends `space_id` alone and the server opens the space as the
// person. Three regions: the left rail (#ex-rail: space, sources, tables, saved
// queries), the canvas (#ex-canvas: one thing at a time, with a tab strip keeping the
// last few, so the agent switching the canvas never loses the person's editor), and
// canopy's docked panel. The agent switches the canvas with page actions
// (registerAgentActions, below), which run here, as the person, and return no rows.
(function () {
  'use strict';
  const cfg = window.LABSDATA;
  const options = JSON.parse(
    document.getElementById('labsdata-options').textContent,
  );
  const $ = (id) => document.getElementById(id);
  let lastResult = null;
  let lastSql = '';
  let described = null;
  // Clicking a cell opens the rows behind it; ``trail`` is how to get back. Each
  // entry is a result as it was shown: its SQL, its rows, and the line above it.
  const trail = [];
  let banner = null;
  const opportunityById = new Map(options.opportunities.map((o) => [o.id, o]));
  const programById = new Map(options.programs.map((p) => [p.id, p]));
  const state = {
    programs: [...(options.initial.program_ids || [])],
    opps: [...(options.initial.opportunity_ids || [])],
    workflow: options.initial.workflow || null, // {id, opportunity_id|program_id, name?}
    // On a space's page, true while the person edits its sources (Update sources).
    editingSources: false,
  };
  // The space this page is, or null: {id, name, sources, opportunity_ids, program_ids,
  // workflow, new_in_programs, changed}.
  const space = options.space || null;
  // Spaces the person may open (GET spaces), flat; filled by loadSpaces.
  let openable = [];
  // Saved queries on the page: the space's (latest of each name, with versions) and
  // the person's own. page_openItem opens only these.
  let spaceItems = [];
  let mySaved = [];
  // The canvas's tabs: the editor (always first, never closed) and the last few things
  // shown -- a saved query, the agent's query, a visit. {id, kind, title, pane}.
  const tabs = [];
  let activeTab = 'editor';
  let tabSeq = 0;
  const MAX_TABS = 6;

  function idUrl(base, id) {
    return base.replace(/\/0\/$/, `/${Number(id)}/`);
  }

  // The space's own sources as an editable selection (Update sources starts from them).
  function seedFromSpace() {
    if (!space) return;
    const w = space.workflow;
    state.workflow = w
      ? {
          ...w,
          name: (space.sources.workflow && space.sources.workflow.name) || '',
        }
      : null;
    state.programs = w ? [] : [...(space.program_ids || [])];
    const inPrograms = new Set(space.program_ids || []);
    state.opps = w
      ? []
      : (space.sources.opportunities || [])
          .filter((o) => !inPrograms.has(o.program_id))
          .map((o) => o.id);
  }
  seedFromSpace();

  // Whether calls read the space (not a selection being edited).
  function inSpace() {
    return !!space && !state.editingSources;
  }

  // What the server needs to resolve the selection.
  function selectionBody() {
    if (inSpace()) return { space_id: space.id };
    if (state.workflow) {
      const { id, opportunity_id, program_id } = state.workflow;
      return {
        workflow: opportunity_id ? { id, opportunity_id } : { id, program_id },
      };
    }
    return { opportunity_ids: state.opps, program_ids: state.programs };
  }

  function hasSelection() {
    return (
      inSpace() ||
      !!state.workflow ||
      state.opps.length > 0 ||
      state.programs.length > 0
    );
  }

  // The opportunities the last describe resolved the selection to.
  function selected() {
    return described
      ? described.opportunities.map((o) => o.opportunity_id)
      : [];
  }

  // Whether the person is one of this space's managers (the server decides every write).
  function isManager() {
    return !!space && !!space.is_manager;
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    });
    children
      .flat()
      .forEach((c) =>
        node.append(
          c instanceof Node
            ? c
            : document.createTextNode(c == null ? '' : String(c)),
        ),
      );
    return node;
  }

  // How a result is shown: the labels a result card (or a saved query) brought with it
  // (`labsdata/display.py`). The raw column name stays on hover.
  let display = null;

  async function post(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrf },
      body: JSON.stringify(body),
    });
    const data = await res
      .json()
      .catch(() => ({ error: 'bad_response', message: `HTTP ${res.status}` }));
    if (!res.ok) throw data;
    return data;
  }

  function workflowParam(w) {
    return w.opportunity_id
      ? `${w.id}:opp:${w.opportunity_id}`
      : `${w.id}:program:${w.program_id}`;
  }

  function syncUrlAndPanel() {
    // A space's page is its own address: the selection is not in the URL.
    if (!space) {
      const url = new URL(window.location.href);
      ['opps', 'programs', 'workflow'].forEach((k) =>
        url.searchParams.delete(k),
      );
      if (state.workflow)
        url.searchParams.set('workflow', workflowParam(state.workflow));
      if (state.programs.length)
        url.searchParams.set('programs', state.programs.join(','));
      if (state.opps.length) url.searchParams.set('opps', state.opps.join(','));
      window.history.replaceState(null, '', url);
      reportSelection();
    }
    pushPageState();
  }

  // Tell labs what the plain page now shows, in the person's own session: the page's agent
  // reads only that (labsdata/page_holds.py). Only the latest change matters.
  let reported = '';
  function reportSelection() {
    if (!cfg.pageHoldsUrl || !cfg.panelPageToken) return;
    const body = {
      page_token: cfg.panelPageToken,
      page_holds: cfg.panelHolds,
      ...selectionBody(),
    };
    const key = JSON.stringify(body);
    if (key === reported) return;
    reported = key;
    post(cfg.pageHoldsUrl, body).catch(() => {
      reported = '';
    });
  }

  // The panel's page state: what the person is looking at, as ids. On a space's page
  // `filters.space_id` is what the agent passes to every data_* call. The resource
  // was set by the server that drew the page; canopyHost keeps it.
  function pushPageState() {
    const ids = space
      ? (space.opportunity_ids || []).slice()
      : selected().length
        ? selected()
        : [];
    if (window.canopyHost && window.canopyHost.updatePageState) {
      const tab = tabs.find((t) => t.id === activeTab);
      window.canopyHost.updatePageState({
        visible_ids: ids,
        filters: {
          opportunity_ids: ids,
          ...(space ? { space_id: space.id } : selectionBody()),
          canvas: tab ? `${tab.kind}: ${tab.title}` : 'editor',
        },
      });
    }
  }

  // ---- the selection: chips, the add picker, the workflow list -------------

  // A chip; with no onRemove it is locked (a space's source, outside Update sources).
  function chip(label, sub, onRemove, extraClass) {
    return el(
      'span',
      {
        class:
          'ex-chip' +
          (extraClass ? ' ' + extraClass : '') +
          (onRemove ? '' : ' locked'),
      },
      label,
      sub ? el('small', {}, sub) : '',
      onRemove
        ? el(
            'button',
            {
              type: 'button',
              title: 'Remove',
              'aria-label': `Remove ${label}`,
              onclick: onRemove,
            },
            '×',
          )
        : '',
    );
  }

  function show(id, visible) {
    const node = $(id);
    if (node) node.hidden = !visible;
  }

  function renderChips() {
    const box = $('ex-chips');
    box.replaceChildren();
    const locked = inSpace();
    if (state.workflow) {
      const w = state.workflow;
      const n = described && described.workflow ? selected().length : null;
      box.append(
        chip(
          `Workflow: ${w.name || '#' + w.id}`,
          n ? `${n} opp${n === 1 ? '' : 's'}` : '',
          locked
            ? null
            : () => {
                state.workflow = null;
                changed();
              },
          'wf',
        ),
      );
    }
    state.programs.forEach((pid) => {
      const p = programById.get(pid);
      box.append(
        chip(
          p ? p.name : `Program ${pid}`,
          'program',
          locked
            ? null
            : () => {
                state.programs = state.programs.filter((x) => x !== pid);
                changed();
              },
        ),
      );
    });
    state.opps.forEach((oid) => {
      const o = opportunityById.get(oid);
      box.append(
        chip(
          o ? o.name : `Opportunity ${oid}`,
          `#${oid}`,
          locked
            ? null
            : () => {
                state.opps = state.opps.filter((x) => x !== oid);
                changed();
              },
        ),
      );
    });
    if (!box.childNodes.length)
      box.append(
        el('span', { class: 'ex-muted text-sm' }, 'Nothing chosen yet.'),
      );
    // A space's sources are fixed until the person chooses Update sources.
    const picker = $('ex-add').closest('.ex-picker');
    if (picker) picker.hidden = locked;
    // Only a space's managers change its sources (spaces.require_manager decides; this
    // only hides what a member could not do).
    show('ex-sources-edit', isManager() && !state.editingSources);
    show('ex-sources-save', !!space && state.editingSources);
    show('ex-sources-cancel', !!space && state.editingSources);
    $('ex-add').disabled = !!state.workflow;
    $('ex-add').placeholder = state.workflow
      ? 'a workflow brings its own opportunities'
      : 'add a program or opportunity…';
    $('ex-wf-btn').hidden = !!state.workflow || locked;
    $('ex-wf-hint').textContent =
      state.workflow || locked
        ? ''
        : hasSelection()
          ? 'from the programs and opportunities chosen'
          : 'choose a program or opportunity first';
    $('ex-wf-btn').disabled = !hasSelection();
  }

  function renderPicker() {
    const box = $('ex-add-results');
    const q = $('ex-add').value.trim().toLowerCase();
    box.replaceChildren();
    const hit = (text) => !q || text.toLowerCase().includes(q);
    const programs = options.programs
      .filter((p) => !state.programs.includes(p.id) && hit(`${p.name} ${p.id}`))
      .slice(0, 30);
    const opps = options.opportunities
      .filter(
        (o) => !state.opps.includes(o.id) && hit(`${o.name} ${o.llo} ${o.id}`),
      )
      .slice(0, 60);
    programs.forEach((p) =>
      box.append(
        el(
          'button',
          {
            type: 'button',
            class: 'ex-option',
            onclick: () => {
              state.programs.push(p.id);
              closePicker();
              changed();
            },
          },
          el('span', { class: 'kind' }, 'Program'),
          p.name || `Program ${p.id}`,
          el(
            'small',
            {},
            `${p.opportunities} opportunit${p.opportunities === 1 ? 'y' : 'ies'} you hold`,
          ),
        ),
      ),
    );
    opps.forEach((o) =>
      box.append(
        el(
          'button',
          {
            type: 'button',
            class: 'ex-option',
            onclick: () => {
              state.opps.push(o.id);
              closePicker();
              changed();
            },
          },
          el('span', { class: 'kind' }, 'Opp'),
          o.name,
          el('small', {}, `${o.llo || '—'} · #${o.id}`),
        ),
      ),
    );
    if (!box.childNodes.length)
      box.append(el('p', { class: 'px-3 py-2 text-sm ex-muted' }, 'No match.'));
    box.hidden = false;
  }

  function closePicker() {
    $('ex-add').value = '';
    $('ex-add-results').hidden = true;
  }

  async function showWorkflows() {
    const box = $('ex-wf-list');
    box.hidden = false;
    box.replaceChildren(
      el('p', { class: 'px-3 py-2 text-sm ex-muted' }, 'Finding workflows…'),
    );
    try {
      const data = await post(cfg.workflowsUrl, {
        opportunity_ids: state.opps,
        program_ids: state.programs,
      });
      box.replaceChildren();
      const list = el('div');
      if (data.workflows.length > 8) {
        const filter = el('input', {
          class: 'ex-search',
          placeholder: 'filter workflows',
          'aria-label': 'Filter workflows',
        });
        filter.addEventListener('input', () => {
          const q = filter.value.trim().toLowerCase();
          list.querySelectorAll('.ex-option').forEach((b) => {
            b.hidden = !!q && !b.textContent.toLowerCase().includes(q);
          });
        });
        box.append(el('div', { class: 'p-2' }, filter));
        setTimeout(() => filter.focus());
      }
      box.append(list);
      data.workflows.forEach((w) =>
        list.append(
          el(
            'button',
            {
              type: 'button',
              class: 'ex-option',
              onclick: () => {
                state.workflow = { id: w.id, name: w.name, ...w.scope };
                state.opps = [];
                state.programs = [];
                box.hidden = true;
                changed();
              },
            },
            w.name || `Workflow ${w.id}`,
            el(
              'small',
              {},
              `#${w.id} · ${w.pipelines} pipeline${w.pipelines === 1 ? '' : 's'}`,
            ),
          ),
        ),
      );
      if (!data.workflows.length)
        box.append(
          el(
            'p',
            { class: 'px-3 py-2 text-sm ex-muted' },
            'No workflows in these programs or opportunities.',
          ),
        );
      if (data.scopes_not_searched.length)
        box.append(
          el(
            'p',
            { class: 'px-3 py-2 text-xs ex-muted' },
            `Searched the first ${data.scopes_searched.length} programs and opportunities only; narrow the selection to see the rest.`,
          ),
        );
    } catch (e) {
      box.replaceChildren(
        el(
          'div',
          { class: 'ex-error' },
          e.message || 'Could not list workflows.',
        ),
      );
    }
  }

  function changed() {
    $('ex-wf-list').hidden = true;
    described = null;
    renderChips();
    describe(false);
  }

  function renderCache(opps, loaded) {
    const box = $('ex-cache');
    box.replaceChildren();
    const total = opps.reduce((n, o) => n + (o.cached_visits || 0), 0);
    if (opps.length > 1)
      box.append(
        el(
          'div',
          { class: 'mb-1' },
          el('b', {}, `${opps.length} opportunities`),
          ` — ${total.toLocaleString()} visits cached`,
        ),
      );
    opps.slice(0, opps.length > 6 ? 0 : 6).forEach((o) => {
      const line = el(
        'div',
        {},
        el('b', {}, o.opportunity_name),
        ` — ${o.cached_visits.toLocaleString()} visits cached`,
        o.latest_visit_date ? `, latest ${o.latest_visit_date}` : '',
      );
      box.append(line);
    });
    const missing = opps.filter((o) => !o.cached_visits);
    if (missing.length) {
      box.append(
        el(
          'button',
          { class: 'ex-btn ghost mt-2', onclick: () => describe(true) },
          `Load ${missing.length} from Connect`,
        ),
      );
    }
    (loaded || [])
      .filter((l) => l.error)
      .forEach((l) =>
        box.append(
          el(
            'div',
            { class: 'ex-error mt-2' },
            `#${l.opportunity_id}: ${l.error}`,
          ),
        ),
      );
  }

  function insertAtCursor(text) {
    const ta = $('ex-sql');
    const start = ta.selectionStart;
    ta.value =
      ta.value.slice(0, start) + text + ta.value.slice(ta.selectionEnd);
    ta.focus();
    ta.selectionStart = ta.selectionEnd = start + text.length;
  }

  // The relations the selection can query: visits, and a workflow's pipelines. A
  // name inserts the relation, a column inserts the column.
  function renderTables(data) {
    const box = $('ex-tables');
    box.replaceChildren();
    const rels = Object.entries(data.relations || {});
    rels.forEach(([name, rel], i) => {
      const cols = rel.columns.map((c) =>
        el(
          'button',
          {
            type: 'button',
            class: 'ex-col',
            title: `${c.type}${c.means ? ' — ' + c.means : ''}`,
            onclick: () => insertAtCursor(c.name),
          },
          c.name,
        ),
      );
      const head = el(
        'summary',
        {},
        el(
          'button',
          {
            type: 'button',
            class: 'ex-name',
            onclick: (e) => {
              e.preventDefault();
              insertAtCursor(name);
            },
          },
          name,
        ),
        el(
          'span',
          { class: 'meta' },
          ` ${rel.pipeline ? rel.pipeline.name + ' · ' : ''}${rel.grain}`,
        ),
      );
      const stale = (rel.stale_opportunities || []).length;
      box.append(
        el(
          'details',
          {
            class: 'ex-table-entry',
            ...(i === 0 && rels.length === 1 ? { open: '' } : {}),
          },
          head,
          el('div', { class: 'mt-1' }, cols),
          stale
            ? el(
                'div',
                { class: 'ex-warn' },
                `No current results for ${stale} of ${Object.keys(rel.rows_by_opportunity).length} opportunities: ` +
                  'the workflow has not been run since its pipeline changed. Open the workflow to compute them.',
              )
            : '',
        ),
      );
    });
    (data.pipelines_not_offered || []).forEach((p) =>
      box.append(
        el(
          'div',
          { class: 'ex-table-entry ex-muted' },
          `${p.name || p.alias || '#' + p.pipeline_id}: not offered — ${p.reason}`,
        ),
      ),
    );
    const names = rels.map(([n]) => n);
    $('ex-tables-hint').replaceChildren(
      'Query ',
      ...names.flatMap((n, i) => [i ? ', ' : '', el('code', {}, n)]),
      '.',
    );
  }

  async function describe(loadMissing) {
    syncUrlAndPanel();
    if (!hasSelection()) {
      $('ex-cache').textContent =
        'Choose programs, opportunities or a workflow.';
      $('ex-tables').replaceChildren(
        el('p', { class: 'px-3 pb-3 text-sm ex-muted' }, '—'),
      );
      return;
    }
    $('ex-cache').textContent = loadMissing
      ? 'Loading from Connect…'
      : 'Reading…';
    try {
      described = await post(cfg.describeUrl, {
        ...selectionBody(),
        load_missing: !!loadMissing,
      });
      if (described.workflow && state.workflow)
        state.workflow.name = described.workflow.name;
      renderChips();
      syncUrlAndPanel();
      renderCache(described.opportunities, described.loaded);
      renderTables(described);
    } catch (e) {
      $('ex-cache').replaceChildren(
        el(
          'div',
          { class: 'ex-error' },
          e.message || 'Could not read this selection.',
        ),
      );
    }
  }

  function renderResult(data) {
    const box = $('ex-result');
    box.replaceChildren();
    const drillable = (data.drill && data.drill.columns) || [];
    const table = resultTable(data, {
      drillable,
      onDrill: drillInto,
      labels: display,
    });
    appendResultCard(box, data, table, { trail: renderTrail(), drillable });
    $('ex-csv').disabled = !data.rows.length;
  }

  // A result's footer line: rows, time, and what it was over.
  function appendResultCard(box, data, table, { trail: trailEl, drillable }) {
    const n = data.opportunities.length;
    const opps = data.workflow
      ? `${data.workflow.name} (${n} opportunit${n === 1 ? 'y' : 'ies'})`
      : data.opportunities.length > 3
        ? `${data.opportunities.length} opportunities`
        : data.opportunities.map((o) => o.name).join(', ');
    box.append(
      el(
        'div',
        { class: 'ex-card' },
        trailEl || '',
        el('div', { class: 'ex-table-wrap' }, table),
        el(
          'div',
          { class: 'ex-status' },
          el('b', {}, `${data.row_count.toLocaleString()} rows`),
          data.truncated ? ' (truncated — raise max rows or aggregate)' : '',
          ` · ${data.elapsed_ms} ms · over ${space ? space.name + ': ' : ''}${opps}`,
          (drillable || []).some(Boolean)
            ? ' · click a number to see the rows behind it'
            : '',
        ),
      ),
    );
  }

  // The table of a result. `drillable` cells open the rows behind them (the editor
  // only); a visit_id opens the visit in a tab. `labels` is the result's display.
  function resultTable(
    data,
    { drillable = [], onDrill = null, labels = null },
  ) {
    const label = (c) => {
      const spec = labels && labels.columns && labels.columns[c];
      return (spec && spec.label) || c;
    };
    const table = el('table', { class: 'ex-table' });
    table.append(
      el(
        'thead',
        {},
        el(
          'tr',
          {},
          data.columns.map((c) => {
            const shown = label(c);
            return el('th', shown === c ? {} : { title: c }, shown);
          }),
        ),
      ),
    );
    const tbody = el('tbody');
    const visitCol = data.columns.indexOf('visit_id');
    data.rows.forEach((r) =>
      tbody.append(
        el(
          'tr',
          {},
          r.map((v, i) =>
            el(
              'td',
              onDrill && drillable[i] && i !== visitCol
                ? {
                    class: 'ex-drill',
                    title:
                      'See the rows behind this, and the SQL that selects them',
                    onclick: () => onDrill(r, i, data.columns[i]),
                  }
                : {
                    title:
                      typeof v === 'object' && v !== null
                        ? JSON.stringify(v)
                        : String(v ?? ''),
                  },
              v === null
                ? el('span', { class: 'ex-muted' }, 'null')
                : i === visitCol
                  ? el(
                      'button',
                      {
                        class: 'ex-link',
                        title: 'Open this visit, form and all',
                        onclick: () =>
                          openVisit(
                            String(v),
                            (data.opportunities || []).map((o) => o.id),
                          ),
                      },
                      String(v),
                    )
                  : typeof v === 'object'
                    ? JSON.stringify(v)
                    : typeof v === 'number'
                      ? v.toLocaleString()
                      : v,
            ),
          ),
        ),
      ),
    );
    table.append(tbody);
    return table;
  }

  // What the shown result is, when it was reached by clicking a cell, and the way back.
  function renderTrail() {
    if (!banner && !trail.length) return '';
    const parts = [];
    if (trail.length)
      parts.push(
        el(
          'button',
          { type: 'button', class: 'ex-link', onclick: back },
          '← Back to your query',
        ),
      );
    if (banner) {
      parts.push(
        el(
          'span',
          {},
          `Rows behind ${banner.column} = `,
          el('b', {}, formatCell(banner.value)),
          banner.group ? ` for ${banner.group}` : '',
          `: ${banner.rows_behind.toLocaleString()} row${banner.rows_behind === 1 ? '' : 's'}`,
        ),
      );
      if (banner.matches === true)
        parts.push(el('span', { class: 'ok' }, '✓ matches the cell'));
      else if (banner.matches === false)
        parts.push(
          el(
            'span',
            { class: 'off' },
            `✗ the cell says ${formatCell(banner.value)} — check the query's conditions`,
          ),
        );
      else
        parts.push(
          el('span', { class: 'ex-muted' }, `(${banner.description})`),
        );
    }
    return el('div', { class: 'ex-trail' }, parts);
  }

  function formatCell(v) {
    if (v === null || v === undefined) return 'null';
    return typeof v === 'number' ? v.toLocaleString() : String(v);
  }

  async function drillInto(row, column, columnName) {
    try {
      const d = await post(cfg.drillUrl, {
        ...selectionBody(),
        sql: lastSql,
        row,
        column,
      });
      const groupCols = (lastResult.drill.kinds || [])
        .map((kind, i) => (kind === 'group' && i !== column ? i : -1))
        .filter((i) => i >= 0)
        .map((i) => `${lastResult.columns[i]} = ${formatCell(row[i])}`);
      trail.push({ sql: lastSql, result: lastResult, banner });
      banner = {
        column: columnName,
        value: row[column],
        group: groupCols.join(', '),
        rows_behind: d.rows_behind,
        matches: d.matches,
        description: d.description,
      };
      $('ex-sql').value = d.sql;
      await run({ keepTrail: true });
    } catch (e) {
      $('ex-result').prepend(
        el(
          'div',
          { class: 'ex-error mb-2' },
          e.message || 'Could not open the rows behind that cell.',
        ),
      );
    }
  }

  function back() {
    const prev = trail.pop();
    if (!prev) return;
    banner = prev.banner;
    lastSql = prev.sql;
    lastResult = prev.result;
    $('ex-sql').value = prev.sql;
    renderResult(prev.result);
  }

  // ---- the canvas: tabs -----------------------------------------------------

  function renderTabs() {
    const strip = $('ex-tabs');
    if (!strip) return;
    strip.replaceChildren(
      ...tabs.map((t) =>
        el(
          'div',
          {
            class: 'ex-tab',
            role: 'tab',
            'data-tab': t.id,
            'aria-selected': String(t.id === activeTab),
          },
          t.kind === 'editor' ? '' : el('span', { class: 'kind' }, t.kind),
          el(
            'button',
            {
              type: 'button',
              class: 'ex-tab-open',
              title: t.title,
              onclick: () => showTab(t.id),
            },
            el('span', {}, t.title),
          ),
          t.kind === 'editor'
            ? ''
            : el(
                'button',
                {
                  type: 'button',
                  class: 'x',
                  title: 'Close',
                  'aria-label': `Close ${t.title}`,
                  onclick: () => closeTab(t.id),
                },
                '×',
              ),
        ),
      ),
    );
  }

  function showTab(id) {
    if (!tabs.some((t) => t.id === id)) id = 'editor';
    activeTab = id;
    tabs.forEach((t) => (t.pane.hidden = t.id !== id));
    renderTabs();
    pushPageState();
  }

  // A new tab (kind: query | saved | visit) with an empty pane; the oldest non-editor
  // tab goes once there are more than MAX_TABS.
  function openTab(kind, title) {
    const id = `t${++tabSeq}`;
    const pane = el('div', {
      class: 'ex-pane flex flex-col gap-4',
      'data-tab': id,
    });
    $('ex-panes').append(pane);
    const tab = { id, kind, title: String(title || kind).slice(0, 120), pane };
    tabs.push(tab);
    while (tabs.length > MAX_TABS + 1) {
      const oldest = tabs.findIndex((t) => t.kind !== 'editor');
      tabs[oldest].pane.remove();
      tabs.splice(oldest, 1);
    }
    showTab(id);
    return tab;
  }

  function closeTab(id) {
    const i = tabs.findIndex((t) => t.id === id && t.kind !== 'editor');
    if (i < 0) return;
    tabs[i].pane.remove();
    tabs.splice(i, 1);
    if (activeTab === id) showTab('editor');
    else renderTabs();
  }

  // A query shown in its own tab: run here, as the person, over the page's selection
  // (or the given one: a personal saved query brings its own). No drill-down -- "Open
  // in editor" for that -- and the person's editor is left as it was.
  async function openQueryTab({
    kind = 'query',
    title,
    sql,
    display: shown = null,
    max_rows,
    selection = null,
  }) {
    const tab = openTab(kind, title || 'Query');
    const rows = Number(max_rows) || Number($('ex-max').value) || 500;
    const body = { ...(selection || selectionBody()), sql, max_rows: rows };
    const out = el(
      'div',
      {},
      el('p', { class: 'text-sm ex-muted' }, 'Running…'),
    );
    tab.pane.append(
      el(
        'div',
        { class: 'ex-card' },
        el(
          'div',
          { class: 'ex-pane-head' },
          el('h2', {}, tab.title),
          el(
            'button',
            {
              type: 'button',
              class: 'ex-btn ghost',
              onclick: () => {
                $('ex-sql').value = sql;
                $('ex-max').value = rows;
                display = shown;
                showTab('editor');
                run();
              },
            },
            'Open in editor',
          ),
          el(
            'button',
            {
              type: 'button',
              class: 'ex-btn ghost',
              onclick: () =>
                openSaveDialog({
                  name: title || '',
                  sql,
                  display: shown,
                  max_rows: rows,
                }),
            },
            'Save…',
          ),
        ),
        el(
          'details',
          {},
          el('summary', { class: 'px-3 py-1 text-xs ex-muted' }, 'SQL'),
          el('pre', { class: 'ex-pane-sql' }, sql),
        ),
      ),
      out,
    );
    try {
      const data = await post(cfg.queryUrl, body);
      out.replaceChildren();
      appendResultCard(out, data, resultTable(data, { labels: shown }), {});
      return { ok: true, tab: tab.id };
    } catch (e) {
      out.replaceChildren(
        el('div', { class: 'ex-error' }, e.message || 'The query failed.'),
      );
      return { ok: false, tab: tab.id, error: e.error || 'query_failed' };
    }
  }

  // Row level: the whole visit -- every column and the full submitted form -- in a tab,
  // so a number can be checked against the visits behind it.
  async function openVisit(visitId, opportunityIds) {
    const tab = openTab('visit', `Visit ${visitId}`);
    tab.pane.append(el('p', { class: 'text-sm ex-muted' }, 'Loading visit…'));
    const scopeOf = inSpace()
      ? { space_id: space.id }
      : {
          // The opportunities the shown result came from, not the current ticks: the
          // person may have changed the selection since running it.
          opportunity_ids:
            opportunityIds && opportunityIds.length
              ? opportunityIds
              : lastResult && lastResult.opportunities
                ? lastResult.opportunities.map((o) => o.id)
                : selected(),
        };
    try {
      const data = await post(cfg.visitsUrl, {
        ...scopeOf,
        visit_ids: [visitId],
      });
      const visit = data.visits[0];
      tab.pane.replaceChildren(
        el(
          'div',
          { class: 'ex-card' },
          el('div', { class: 'ex-bar' }, el('b', {}, `Visit ${visitId}`)),
          visit
            ? el('pre', { class: 'ex-json' }, JSON.stringify(visit, null, 2))
            : el(
                'div',
                { class: 'ex-error' },
                'Not in these opportunities’ cached visits.',
              ),
        ),
      );
      return { ok: true, tab: tab.id };
    } catch (e) {
      tab.pane.replaceChildren(
        el(
          'div',
          { class: 'ex-error' },
          e.message || 'Could not load the visit.',
        ),
      );
      return { ok: false, tab: tab.id, error: e.error || 'visit_failed' };
    }
  }

  async function run({ keepTrail = false } = {}) {
    if (!keepTrail) {
      // A query of the person's own starts a new trail.
      trail.length = 0;
      banner = null;
    }
    if (!hasSelection()) {
      $('ex-result').replaceChildren(
        el(
          'div',
          { class: 'ex-error' },
          'Choose a program, an opportunity or a workflow.',
        ),
      );
      return;
    }
    $('ex-run').disabled = true;
    $('ex-result').replaceChildren(
      el('p', { class: 'text-sm ex-muted' }, 'Running…'),
    );
    try {
      const sql = $('ex-sql').value;
      lastResult = await post(cfg.queryUrl, {
        ...selectionBody(),
        sql,
        max_rows: Number($('ex-max').value) || 500,
      });
      lastSql = sql;
      renderResult(lastResult);
    } catch (e) {
      lastResult = null;
      $('ex-csv').disabled = true;
      $('ex-result').replaceChildren(
        el('div', { class: 'ex-error' }, e.message || 'The query failed.'),
      );
    } finally {
      $('ex-run').disabled = false;
    }
  }

  // A cell as Excel and Sheets will read it (OWASP CSV injection, as Scout's
  // frontend/src/lib/csv.ts): form answers are typed by field workers, and a text cell
  // starting with = + - @, a tab or a CR -- or their fullwidth forms, which some CJK
  // spreadsheets also treat as formulas -- would run as a formula, so it gets a leading
  // '. A number, or text that is only a number, cannot be a formula and is left alone.
  const FORMULA_PREFIX = /^[=+\-@\t\r]/;
  const PLAIN_NUMBER = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;
  function csvCell(v) {
    if (v === null || v === undefined) return '';
    let s = typeof v === 'object' ? JSON.stringify(v) : String(v);
    const numeric = typeof v === 'number' || PLAIN_NUMBER.test(s);
    if (!numeric && FORMULA_PREFIX.test(s.normalize('NFKC'))) s = `'${s}`;
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }
  function toCsv(columns, rows) {
    return (
      [columns, ...rows].map((r) => r.map(csvCell).join(',')).join('\r\n') +
      '\r\n'
    );
  }
  function csv() {
    if (!lastResult) return;
    const stamp = new Date()
      .toISOString()
      .slice(0, 19)
      .replace(/[-:]/g, '')
      .replace('T', '-');
    const a = el('a', {
      href: URL.createObjectURL(
        // The BOM makes Excel read the file as UTF-8 (names and answers in any script).
        new Blob(['\ufeff', toCsv(lastResult.columns, lastResult.rows)], {
          type: 'text/csv;charset=utf-8',
        }),
      ),
      download: `labs-data-${stamp}.csv`,
    });
    a.click();
  }
  window.LABSDATA_CSV = { csvCell, toCsv };

  // ---- saved queries: a space's, and the person's own ("My queries") ----------
  // The query, never its rows (labsdata/models.py). Opening one runs it again, live, as
  // the person -- what they may see now.

  function savedRow(q, { onOpen, onDelete, versions }) {
    const row = el(
      'div',
      { class: 'ex-saved-row', 'data-query-id': q.id },
      el(
        'button',
        { class: 'open', title: q.sql, onclick: () => onOpen(q) },
        q.name,
        q.version > 1 ? el('small', {}, ` v${q.version}`) : '',
        q.saved_by ? el('small', {}, ` · ${q.saved_by}`) : '',
      ),
      // A space's item says whether this person may delete it (managers: any; members:
      // their own). A personal query is always the person's own.
      q.can_delete === false
        ? ''
        : el(
            'button',
            {
              class: 'del',
              title: 'Delete this saved query (every version)',
              onclick: () => onDelete(q),
            },
            '✕',
          ),
    );
    if (!versions || versions.length < 2) return row;
    return el(
      'div',
      {},
      row,
      el(
        'details',
        { class: 'ex-versions' },
        el('summary', {}, `${versions.length} versions`),
        ...versions.map((v) =>
          el(
            'div',
            {},
            el(
              'button',
              {
                type: 'button',
                onclick: () => openSpaceItem(v.id),
              },
              `v${v.version}`,
            ),
            ` · ${v.saved_by || ''} · ${(v.created_at || '').slice(0, 10)}`,
          ),
        ),
      ),
    );
  }

  function emptyLine(text) {
    return el('p', { class: 'px-3 pb-3 text-sm ex-muted' }, text);
  }

  async function getJson(url) {
    const res = await fetch(url, { headers: { Accept: 'application/json' } });
    const data = await res
      .json()
      .catch(() => ({ error: 'bad_response', message: `HTTP ${res.status}` }));
    if (!res.ok) throw data;
    return data;
  }

  async function loadSaved() {
    if (!cfg.savedUrl) return;
    const box = $('ex-saved');
    try {
      const data = await getJson(cfg.savedUrl);
      mySaved = data.saved || [];
      box.replaceChildren();
      if (!mySaved.length) {
        box.append(emptyLine('None yet. Save a query to run it again.'));
        return;
      }
      mySaved.forEach((q) =>
        box.append(savedRow(q, { onOpen: openSaved, onDelete: deleteSaved })),
      );
    } catch (e) {
      box.replaceChildren(emptyLine('Could not load saved queries.'));
    }
  }

  // A personal query brings its own selection. Off a space it opens in the editor, as
  // before; on a space's page it opens in a tab over its own selection, leaving the
  // space's selection as it is.
  async function openSaved(q) {
    const sel = q.selection || {};
    if (space) {
      return openQueryTab({
        kind: 'saved',
        title: q.name,
        sql: q.sql,
        display: q.display || null,
        max_rows: q.max_rows,
        selection: sel.workflow
          ? { workflow: sel.workflow }
          : {
              opportunity_ids: sel.opportunity_ids || [],
              program_ids: sel.program_ids || [],
            },
      });
    }
    state.workflow = sel.workflow || null;
    state.opps = state.workflow ? [] : [...(sel.opportunity_ids || [])];
    state.programs = state.workflow ? [] : [...(sel.program_ids || [])];
    $('ex-sql').value = q.sql;
    $('ex-max').value = q.max_rows;
    display = q.display || null;
    renderChips();
    showTab('editor');
    await describe(false);
    await run();
    return { ok: !!lastResult };
  }
  async function deleteSaved(q) {
    if (!window.confirm(`Delete the saved query “${q.name}”?`)) return;
    try {
      await post(`${cfg.savedUrl}${q.id}/delete/`, {});
    } finally {
      loadSaved();
    }
  }

  function spaceApi(id, suffix) {
    return idUrl(cfg.spaceApiUrl, id) + (suffix || '');
  }

  async function loadSpaceItems() {
    if (!space) return;
    const box = $('ex-space-items');
    try {
      const data = await getJson(spaceApi(space.id, 'items/'));
      spaceItems = data.items || [];
      box.replaceChildren();
      if (!spaceItems.length) {
        box.append(
          emptyLine(
            'Nothing saved in this space yet. Save a query here and everyone who can open the space sees it.',
          ),
        );
        return;
      }
      spaceItems.forEach((q) =>
        box.append(
          savedRow(q, {
            onOpen: (item) => openSpaceItem(item.id),
            onDelete: deleteSpaceItem,
            versions: q.versions,
          }),
        ),
      );
    } catch (e) {
      box.replaceChildren(emptyLine(e.message || 'Could not load this space.'));
    }
  }

  // A saved query of this space (any version), as {name, version, sql, display, max_rows}.
  function findSpaceItem(id) {
    for (const q of spaceItems) {
      const v = (q.versions || []).find((x) => x.id === id);
      if (v) return { ...v, name: q.name };
      if (q.id === id) return q;
    }
    return null;
  }

  // In a tab, run over the space.
  function openSpaceItem(id) {
    const q = findSpaceItem(Number(id));
    if (!q) return Promise.resolve({ ok: false, error: 'not_found' });
    return openQueryTab({
      kind: 'saved',
      title: q.version > 1 ? `${q.name} (v${q.version})` : q.name,
      sql: q.sql,
      display: q.display || null,
      max_rows: q.max_rows,
    });
  }

  async function deleteSpaceItem(q) {
    if (
      !window.confirm(
        `Delete “${q.name}” from this space, every version? Everyone who can open the space loses it.`,
      )
    )
      return;
    try {
      await post(spaceApi(space.id, `items/${q.id}/delete/`), {});
    } finally {
      loadSpaceItems();
    }
  }

  // ---- spaces: the switcher, a new space, Update sources, archive -------------

  async function loadSpaces() {
    const select = $('ex-space-switch');
    if (!select || !cfg.spacesUrl) return;
    try {
      const data = await getJson(cfg.spacesUrl);
      openable = (data.groups || []).flatMap((g) =>
        g.spaces.map((s) => ({ ...s, group: g.label })),
      );
      select.replaceChildren(
        el(
          'option',
          { value: '' },
          space
            ? 'Leave this space (an unsaved one from my selection)'
            : 'Unsaved space: this selection',
        ),
        ...(data.groups || []).map((g) =>
          el(
            'optgroup',
            { label: g.label },
            ...g.spaces.map((s) =>
              el('option', { value: String(s.id) }, s.name),
            ),
          ),
        ),
      );
      select.value = space ? String(space.id) : '';
      renderSaveTargets();
    } catch (e) {
      openable = [];
    }
  }

  function goToSpace(id) {
    window.location.assign(id ? idUrl(cfg.spacePageUrl, id) : cfg.indexUrl);
  }

  async function newSpace() {
    if (!hasSelection()) {
      window.alert('Choose programs, opportunities or a workflow first.');
      return;
    }
    const name = window.prompt(
      'Name the space. Everyone who holds every one of these sources will see it and what is saved in it:',
      '',
    );
    if (!name || !name.trim()) return;
    try {
      const out = await post(cfg.spacesUrl, {
        name: name.trim(),
        ...selectionBody(),
      });
      goToSpace(out.space.id);
    } catch (e) {
      window.alert(e.message || 'Could not make the space.');
    }
  }

  function startEditingSources() {
    state.editingSources = true;
    seedFromSpace();
    described = null;
    renderChips();
    describe(false);
  }

  function cancelEditingSources() {
    state.editingSources = false;
    seedFromSpace();
    described = null;
    renderChips();
    describe(false);
  }

  // The one dangerous write: the person's own click, after this warning, in their
  // own session. No page action reaches it.
  async function saveSources() {
    if (!hasSelection()) {
      window.alert('A space needs at least one source.');
      return;
    }
    const ok = window.confirm(
      'Change this space’s sources?\n\n' +
        'Anyone who does not hold every new source loses the whole space and everything saved in it. ' +
        'Labs cannot tell you who that is. Its saved queries will run over the new sources.',
    );
    if (!ok) return;
    try {
      await post(spaceApi(space.id, 'sources/'), {
        confirm: true,
        ...selectionBody(),
      });
      window.location.reload();
    } catch (e) {
      window.alert(e.message || 'Could not change the sources.');
    }
  }

  async function archiveSpace() {
    if (
      !window.confirm(
        `Archive “${space.name}”? It disappears for everyone who can open it.`,
      )
    )
      return;
    try {
      await post(spaceApi(space.id, 'archive/'), {});
      goToSpace(null);
    } catch (e) {
      window.alert(e.message || 'Could not archive the space.');
    }
  }

  // ---- who can open the space, and its managers ------------------------------
  // Managers' own clicks, in their own session (CSRF), audited server-side. No page action
  // reaches any of these (tests/test_spaces_gate.py): an agent can never change who can
  // open a space or who manages it.
  const AUDIENCE_TEXT = {
    holders: 'Anyone with access to the sources',
    private: 'Only managers',
  };

  function accessError(e) {
    const box = $('ex-access-error');
    if (!box) return;
    box.hidden = !e;
    box.textContent = e ? e.message || 'That did not work.' : '';
  }

  function renderAccess() {
    if (!space || !$('ex-access-card')) return;
    const manager = isManager();
    show('ex-space-archive', manager);
    show('ex-audience', manager);
    show('ex-audience-text', !manager);
    $('ex-audience').value = space.audience;
    $('ex-audience-text').textContent =
      AUDIENCE_TEXT[space.audience] || space.audience;
    const box = $('ex-managers');
    box.replaceChildren(
      ...(space.managers || []).map((m) =>
        el(
          'div',
          { class: 'flex items-center justify-between gap-2' },
          el('span', {}, m.username),
          manager
            ? el(
                'button',
                {
                  class: 'ex-btn ghost',
                  type: 'button',
                  title: 'Stop being a manager of this space',
                  onclick: () => manageRemoveManager(m),
                },
                'Remove',
              )
            : '',
        ),
      ),
    );
    if (!(space.managers || []).length)
      box.append(
        el(
          'span',
          { class: 'ex-muted' },
          'No managers. A labs admin can add one.',
        ),
      );
    show('ex-manager-add', manager);
    show('ex-manager-admin', !manager && !!options.can_admin_assign);
  }

  function manageDone(out) {
    if (out && out.space) Object.assign(space, out.space);
    accessError(null);
    renderAccess();
    renderChips();
  }

  async function manageSetAudience(audience) {
    if (
      audience === 'private' &&
      !window.confirm(
        'Only this space’s managers will be able to open it. Everyone else loses it and everything saved in it.',
      )
    ) {
      renderAccess();
      return;
    }
    try {
      manageDone(await post(spaceApi(space.id, 'audience/'), { audience }));
    } catch (e) {
      accessError(e);
      renderAccess();
    }
  }

  async function manageAddManager() {
    const person = $('ex-manager-person').value.trim();
    if (!person) return;
    try {
      manageDone(await post(spaceApi(space.id, 'managers/'), { person }));
      $('ex-manager-person').value = '';
    } catch (e) {
      accessError(e);
    }
  }

  async function manageRemoveManager(m) {
    if (!window.confirm(`Stop ${m.username} managing this space?`)) return;
    try {
      manageDone(
        await post(spaceApi(space.id, `managers/${m.id}/remove/`), {}),
      );
    } catch (e) {
      accessError(e);
    }
  }

  async function manageAdminAddManager() {
    const person = $('ex-manager-admin-person').value.trim();
    if (!person) return;
    try {
      await post(spaceApi(space.id, 'admin/managers/'), { person });
      window.location.reload();
    } catch (e) {
      accessError(e);
    }
  }

  // What the space says about itself: a program that has gained opportunities, or a
  // workflow that moved under it.
  function renderSpaceNotice() {
    const box = $('ex-space-notice');
    if (!box || !space) return;
    box.replaceChildren();
    (space.new_in_programs || []).forEach((p) => {
      const prog = programById.get(p.program_id);
      const n = p.opportunity_ids.length;
      box.append(
        el(
          'div',
          { class: 'ex-notice' },
          `${prog ? prog.name : 'Program ' + p.program_id} has ${n} opportunit${n === 1 ? 'y' : 'ies'} you hold that this space does not include. Update sources to add ${n === 1 ? 'it' : 'them'}.`,
        ),
      );
    });
    if (space.changed)
      box.append(el('div', { class: 'ex-warn' }, space.changed.message));
  }

  // ---- the Save dialog -------------------------------------------------------
  // Fills from the editor, a tab's query, or the agent (page_proposeSave). Nothing is
  // saved until the person clicks Save: the page's own POST, as them, behind CSRF.
  let pendingSave = null;

  // The space whose sources are the current selection, if one exists (a card's Save
  // lands on the Data page with the card's selection).
  function matchingSpace() {
    if (space) return space;
    const opps = new Set(selected());
    if (!opps.size) return null;
    const same = (a, b) =>
      a.length === b.size && a.every((x) => b.has(Number(x)));
    return (
      openable.find((s) =>
        state.workflow
          ? s.workflow && Number(s.workflow.id) === Number(state.workflow.id)
          : !s.workflow &&
            same(s.opportunity_ids, opps) &&
            same(s.program_ids, new Set(state.programs)),
      ) || null
    );
  }

  function renderSaveTargets() {
    const select = $('ex-save-target');
    if (!select) return;
    const current = select.value;
    select.replaceChildren(
      el('option', { value: '' }, 'My queries (only you)'),
      ...openable.map((s) =>
        el('option', { value: String(s.id) }, `Space: ${s.name}`),
      ),
    );
    if (space && !openable.some((s) => s.id === space.id))
      select.append(
        el('option', { value: String(space.id) }, `Space: ${space.name}`),
      );
    if (current) select.value = current;
  }

  function openSaveDialog({ name = '', sql, display: shown = null, max_rows }) {
    pendingSave = {
      sql,
      display: shown,
      max_rows: Number(max_rows) || Number($('ex-max').value) || 500,
    };
    renderSaveTargets();
    const target = matchingSpace();
    $('ex-save-target').value = target ? String(target.id) : '';
    $('ex-save-name').value = name;
    $('ex-save-sql').textContent = sql;
    $('ex-save-error').hidden = true;
    $('ex-save-dialog').hidden = false;
    $('ex-save-name').focus();
    return target;
  }

  function closeSaveDialog() {
    $('ex-save-dialog').hidden = true;
    pendingSave = null;
  }

  // A personal query keeps the selection it ran over: the space's sources when saved
  // from a space's page.
  function personalSelection() {
    if (!inSpace()) return selectionBody();
    if (space.workflow) return { workflow: space.workflow };
    return {
      opportunity_ids: space.opportunity_ids,
      program_ids: [],
    };
  }

  async function confirmSave() {
    if (!pendingSave) return;
    const name = $('ex-save-name').value.trim();
    if (!name) {
      $('ex-save-error').textContent = 'Give the query a name.';
      $('ex-save-error').hidden = false;
      return;
    }
    const target = $('ex-save-target').value;
    $('ex-save-confirm').disabled = true;
    try {
      const body = { name, ...pendingSave };
      const out = target
        ? await post(spaceApi(target, 'items/'), body)
        : await post(cfg.savedUrl, { ...body, ...personalSelection() });
      closeSaveDialog();
      $('ex-save').classList.remove('ready');
      $('ex-save').textContent =
        out.saved.version > 1 ? `Saved (v${out.saved.version})` : 'Saved';
      setTimeout(() => ($('ex-save').textContent = 'Save query'), 2500);
      if (target && space && Number(target) === space.id) loadSpaceItems();
      else loadSaved();
    } catch (e) {
      $('ex-save-error').textContent = e.message || 'Could not save the query.';
      $('ex-save-error').hidden = false;
    } finally {
      $('ex-save-confirm').disabled = false;
    }
  }

  function save() {
    if (!hasSelection()) {
      $('ex-result').replaceChildren(
        el(
          'div',
          { class: 'ex-error' },
          'Choose a program, an opportunity or a workflow to save the query over.',
        ),
      );
      return;
    }
    openSaveDialog({
      sql: $('ex-sql').value,
      display,
      max_rows: Number($('ex-max').value) || 500,
    });
  }

  // ---- wiring ----------------------------------------------------------------

  function on(id, event, fn) {
    const node = $(id);
    if (node) node.addEventListener(event, fn);
  }

  on('ex-save', 'click', save);
  on('ex-save-confirm', 'click', confirmSave);
  on('ex-save-cancel', 'click', closeSaveDialog);
  on('ex-save-name', 'keydown', (e) => {
    if (e.key === 'Enter') confirmSave();
    if (e.key === 'Escape') closeSaveDialog();
  });
  on('ex-space-switch', 'change', (e) => goToSpace(e.target.value));
  on('ex-space-new', 'click', newSpace);
  on('ex-space-archive', 'click', archiveSpace);
  on('ex-sources-edit', 'click', startEditingSources);
  on('ex-sources-cancel', 'click', cancelEditingSources);
  on('ex-sources-save', 'click', saveSources);
  on('ex-audience', 'change', (e) => manageSetAudience(e.target.value));
  on('ex-manager-add-btn', 'click', manageAddManager);
  on('ex-manager-person', 'keydown', (e) => {
    if (e.key === 'Enter') manageAddManager();
  });
  on('ex-manager-admin-btn', 'click', manageAdminAddManager);
  $('ex-add').addEventListener('input', renderPicker);
  $('ex-add').addEventListener('focus', renderPicker);
  $('ex-add').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closePicker();
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.ex-picker')) $('ex-add-results').hidden = true;
  });
  $('ex-wf-btn').addEventListener('click', showWorkflows);
  $('ex-run').addEventListener('click', () => run());
  $('ex-csv').addEventListener('click', csv);
  $('ex-sql').addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      run();
    }
  });
  if ($('ex-panes')) {
    tabs.push({
      id: 'editor',
      kind: 'editor',
      title: 'Editor',
      pane: $('ex-pane-editor') || $('ex-panes').firstElementChild,
    });
    renderTabs();
  }
  renderChips();
  renderSpaceNotice();
  renderAccess();
  // Opened from a result card (`service.open_on_page_url`): the query, its selection
  // and its row cap are already in place. `run` runs it as the person, once;
  // `download` then highlights Download CSV. Nothing downloads without a click.
  const intent = (() => {
    try {
      return JSON.parse($('ex-open-intent').textContent) || {};
    } catch (e) {
      return {};
    }
  })();
  display = intent.display || null;
  const describedReady = hasSelection() ? describe(false) : Promise.resolve();
  loadSaved();
  loadSpaceItems();
  // A card's Save: highlight Save and, once the spaces are known, open the dialog on
  // the space matching the card's selection (or My queries). Nothing saves by itself.
  const spacesLoaded = loadSpaces();
  if (intent.save) {
    $('ex-save').classList.add('ready');
    $('ex-save').focus();
    if ($('ex-save-dialog'))
      spacesLoaded.then(() =>
        describedReady.then(() =>
          openSaveDialog({
            sql: $('ex-sql').value,
            display,
            max_rows: Number($('ex-max').value) || 500,
          }),
        ),
      );
  }
  if (intent.run && hasSelection()) {
    run().then(() => {
      if (intent.download && lastResult && lastResult.rows.length) {
        $('ex-csv').classList.add('ready');
        $('ex-csv').focus();
      }
    });
  } else if (intent.download) {
    $('ex-run').focus();
  }

  // ---- page actions: how the agent switches the canvas -------------------------
  // canopy turns each into an agent tool `page_<name>` (canopy-web embedding guide §7);
  // the callback runs in THIS tab, as the person. Each returns only an acknowledgement
  // -- never rows, counts, answers or a query's error text (a cast error can quote a
  // value) -- so sensitive data stays in the person's browser and never travels back
  // through canopy. Each checks its input and throws to refuse. None of them saves
  // anything or changes a space's sources: those are the person's own clicks.

  const MAX_SQL = 20000;

  function checkSql(sql) {
    if (typeof sql !== 'string' || !sql.trim())
      throw new Error(
        'sql is required: one SELECT, as you would give data_query',
      );
    if (sql.length > MAX_SQL)
      throw new Error(`sql is longer than ${MAX_SQL} characters`);
    return sql;
  }

  function checkText(value, field, max, required) {
    if (value === undefined || value === null || value === '') {
      if (required) throw new Error(`${field} is required`);
      return '';
    }
    if (typeof value !== 'string') throw new Error(`${field} must be a string`);
    const text = value.trim();
    if (required && !text) throw new Error(`${field} is required`);
    if (text.length > max)
      throw new Error(`${field} is longer than ${max} characters`);
    return text;
  }

  // A display as data_query takes it (labsdata/display.py): an object, checked again by
  // the server when it is saved. Here only its shape.
  function checkDisplay(value) {
    if (value === undefined || value === null) return null;
    if (typeof value !== 'object' || Array.isArray(value))
      throw new Error('display must be an object (as data_query takes it)');
    if (JSON.stringify(value).length > 20000)
      throw new Error('display is too large');
    return value;
  }

  const ACTIONS = {
    showQuery: [
      async ({ sql, display: shown, title } = {}) => {
        checkSql(sql);
        const name =
          checkText(title, 'title', 200, false) || 'Query from the agent';
        const out = await openQueryTab({
          kind: 'query',
          title: name,
          sql,
          display: checkDisplay(shown),
        });
        return {
          ok: true,
          shown: true,
          tab: out.tab,
          outcome: out.ok ? 'shown_to_person' : 'error_shown_to_person',
          ...(out.ok
            ? {}
            : {
                error: out.error,
                hint: 'Check the SQL with data_validate (pass space_id on a space) and show it again.',
              }),
        };
      },
      {
        description:
          'Show a query in the Data page canvas and run it there, in the person’s browser, as them. Use it instead of handing ' +
          'SQL back in the chat. Opens a new canvas tab (the person’s editor is untouched). Returns only an acknowledgement: ' +
          'the rows are shown to the person, never returned to you. Validate the SQL with data_validate first.',
        parameters: {
          type: 'object',
          properties: {
            sql: {
              type: 'string',
              description:
                'One SELECT over `visits` (and, with a workflow, its pipelines p_<alias>). Runs over the page’s selection or space.',
            },
            title: {
              type: 'string',
              description:
                'A short plain-words title for the tab, e.g. "Visits per worker, last 30 days".',
            },
            display: {
              type: 'object',
              description:
                'How to show the result, as data_query’s `display`: column labels and formats ({columns: {col: {label, format}}}).',
            },
          },
          required: ['sql'],
        },
      },
    ],
    openItem: [
      async ({ kind, id } = {}) => {
        if (kind === 'artifact')
          throw new Error(
            'Artifacts are not on this page yet: open a saved query (kind "query").',
          );
        if (kind !== 'query') throw new Error('kind must be "query"');
        const n = Number(id);
        if (!Number.isInteger(n) || n <= 0)
          throw new Error('id must be a saved query id');
        if (space && findSpaceItem(n)) {
          const out = await openSpaceItem(n);
          return {
            ok: true,
            opened: true,
            tab: out.tab,
            outcome: out.ok ? 'shown_to_person' : 'error_shown_to_person',
          };
        }
        const mine = mySaved.find((q) => q.id === n);
        if (!mine)
          throw new Error(
            space
              ? 'No such saved query in this space or in the person’s own queries.'
              : 'No such saved query among the person’s own queries.',
          );
        const out = await openSaved(mine);
        return {
          ok: true,
          opened: true,
          outcome: out && out.ok ? 'shown_to_person' : 'error_shown_to_person',
        };
      },
      {
        description:
          'Open a saved query in the Data page canvas and run it as the person: one of this space’s queries (any version) or ' +
          'one of the person’s own ("My queries"). Returns only an acknowledgement.',
        parameters: {
          type: 'object',
          properties: {
            kind: {
              type: 'string',
              enum: ['query'],
              description: 'What to open. Only "query" today.',
            },
            id: {
              type: 'integer',
              description:
                'The saved query id (or a version’s id), from the page’s left rail.',
            },
          },
          required: ['kind', 'id'],
        },
      },
    ],
    proposeSave: [
      async ({ name, sql, display: shown } = {}) => {
        const label = checkText(name, 'name', 200, true);
        checkSql(sql);
        const target = openSaveDialog({
          name: label,
          sql,
          display: checkDisplay(shown),
        });
        return {
          ok: true,
          proposed: true,
          saved: false,
          // Where the dialog will save unless the person picks otherwise.
          target: target ? `space ${target.id}` : 'the person’s own queries',
          note: 'Nothing is saved until the person clicks Save.',
        };
      },
      {
        description:
          'Fill the Data page’s Save dialog with a query for the person to keep (in this space, by default). Nothing is saved ' +
          'until they click Save; they can rename it or choose where it goes. The query is kept, never its rows.',
        parameters: {
          type: 'object',
          properties: {
            name: {
              type: 'string',
              description: 'A name for the saved query.',
            },
            sql: {
              type: 'string',
              description: 'The SELECT to save (validated again when saved).',
            },
            display: {
              type: 'object',
              description: 'How to show its result, as data_query’s `display`.',
            },
          },
          required: ['name', 'sql'],
        },
      },
    ],
    showVisit: [
      async ({ visit_id } = {}) => {
        const id = typeof visit_id === 'number' ? String(visit_id) : visit_id;
        if (typeof id !== 'string' || !/^[A-Za-z0-9_.:-]{1,100}$/.test(id))
          throw new Error(
            'visit_id must be a Connect visit id (the visit_id column)',
          );
        const out = await openVisit(id);
        return {
          ok: true,
          shown: true,
          tab: out.tab,
          outcome: out.ok ? 'shown_to_person' : 'error_shown_to_person',
        };
      },
      {
        description:
          'Open one visit -- every column and the full submitted form -- in the Data page canvas, for the person. Returns only an acknowledgement.',
        parameters: {
          type: 'object',
          properties: {
            visit_id: {
              type: 'string',
              description: 'The Connect visit id (the `visit_id` column).',
            },
          },
          required: ['visit_id'],
        },
      },
    ],
    // The older way to hand SQL back (`page_data_set_query`): puts it in the editor and
    // optionally runs it. On a space's page the selection is the space's.
    data_set_query: [
      async ({
        sql,
        opportunity_ids,
        program_ids,
        workflow,
        run: runIt,
      } = {}) => {
        checkSql(sql);
        const ids = (xs) =>
          Array.isArray(xs) ? xs.map(Number).filter(Number.isFinite) : [];
        const names =
          (workflow && workflow.id) ||
          ids(opportunity_ids).length ||
          ids(program_ids).length;
        if (space && names)
          throw new Error(
            'This page is a data space: its sources are fixed. Send sql alone (or use page_showQuery).',
          );
        if (workflow && workflow.id) {
          state.workflow = {
            id: Number(workflow.id),
            ...(workflow.opportunity_id
              ? { opportunity_id: Number(workflow.opportunity_id) }
              : { program_id: Number(workflow.program_id) }),
          };
          state.opps = [];
          state.programs = [];
          described = null;
          renderChips();
          await describe(false);
        } else if (ids(opportunity_ids).length || ids(program_ids).length) {
          state.workflow = null;
          state.opps = ids(opportunity_ids);
          state.programs = ids(program_ids);
          described = null;
          renderChips();
          await describe(false);
        }
        showTab('editor');
        $('ex-sql').value = sql;
        $('ex-sql').focus();
        if (!runIt) return { ok: true, placed: true, ran: false };
        await run();
        return {
          ok: true,
          placed: true,
          ran: true,
          outcome: lastResult ? 'shown_to_person' : 'error_shown_to_person',
        };
      },
      {
        description:
          'Put a SQL query into the Data page editor for the person, over the selection you give (and optionally run it in their browser). ' +
          'Prefer page_showQuery, which shows it in its own canvas tab. Returns only an acknowledgement: the result is shown to the person, never returned to you.',
        parameters: {
          type: 'object',
          properties: {
            sql: {
              type: 'string',
              description:
                'One SELECT over `visits`. Validate it with data_validate first.',
            },
            opportunity_ids: {
              type: 'array',
              items: { type: 'integer' },
              description:
                'Optional (not on a space’s page): query these opportunities (the person must hold them).',
            },
            program_ids: {
              type: 'array',
              items: { type: 'integer' },
              description:
                'Optional (not on a space’s page): query these programs -- every opportunity of each the person holds.',
            },
            workflow: {
              type: 'object',
              description:
                'Optional (not on a space’s page), instead of opportunities and programs: a workflow, as {id, opportunity_id} or {id, program_id}. Its pipelines become tables p_<alias>.',
              properties: {
                id: { type: 'integer' },
                opportunity_id: { type: 'integer' },
                program_id: { type: 'integer' },
              },
              required: ['id'],
            },
            run: {
              type: 'boolean',
              description: 'Run it now so the person sees the result.',
            },
          },
          required: ['sql'],
        },
      },
    ],
  };
  // For tests and the page harness: call an action as canopy would.
  window.LABSDATA_ACTIONS = Object.fromEntries(
    Object.entries(ACTIONS).map(([name, [fn]]) => [name, fn]),
  );

  function registerAgentActions(host) {
    if (!host || !host.widget || !host.widget.registerAction) return;
    Object.entries(ACTIONS).forEach(([name, [fn, spec]]) =>
      host.widget.registerAction(name, fn, spec),
    );
  }
  if (window.canopyHost) registerAgentActions(window.canopyHost);
  else
    document.addEventListener(
      'canopy:ready',
      (e) => registerAgentActions(e.detail),
      { once: true },
    );
})();
