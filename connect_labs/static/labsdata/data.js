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
  // The workflow sandbox artifacts are drawn in ({src, origin, mode}), or null when it is off.
  if (cfg.sandbox === undefined) {
    const node = document.getElementById('ex-artifact-sandbox');
    try {
      cfg.sandbox = node ? JSON.parse(node.textContent) : null;
    } catch (e) {
      cfg.sandbox = null;
    }
  }
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
    show('ex-space-more', isManager() && !state.editingSources);
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

  // A load from Connect in flight (describe with load_missing), and the warnings that
  // say a result is not a real zero, repainted when it starts and ends.
  let loading = false;
  let loadsDone = 0;
  let describeSeq = 0;
  const loadWatchers = [];
  function watchLoad(node, paint) {
    loadWatchers.push({ node, paint });
  }
  function repaintLoadWatchers() {
    for (let i = loadWatchers.length - 1; i >= 0; i--) {
      if (!loadWatchers[i].node.isConnected) loadWatchers.splice(i, 1);
      else loadWatchers[i].paint();
    }
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
    const seq = ++describeSeq;
    const missing = described
      ? described.opportunities.filter((o) => !o.cached_visits).length
      : 0;
    $('ex-cache').textContent = loadMissing
      ? missing
        ? `Loading ${missing} opportunit${missing === 1 ? 'y' : 'ies'} from Connect… You can keep working; a result run before this finishes leaves ${missing === 1 ? 'it' : 'them'} out.`
        : 'Loading from Connect…'
      : 'Reading…';
    if (loadMissing) {
      loading = true;
      repaintLoadWatchers();
    }
    let got;
    try {
      got = await post(cfg.describeUrl, {
        ...selectionBody(),
        load_missing: !!loadMissing,
      });
    } catch (e) {
      if (seq === describeSeq)
        $('ex-cache').replaceChildren(
          el(
            'div',
            { class: 'ex-error' },
            e.message || 'Could not read this selection.',
          ),
        );
      return;
    } finally {
      if (loadMissing) {
        loading = false;
        loadsDone += 1;
        // Kept before repainting, so "loaded" is judged on what the load reported.
        if (got && seq === describeSeq) described = got;
        repaintLoadWatchers();
      }
    }
    // A later describe (the selection changed meanwhile) owns the rail.
    if (seq !== describeSeq) return;
    described = got;
    if (described.workflow && state.workflow)
      state.workflow.name = described.workflow.name;
    renderChips();
    syncUrlAndPanel();
    renderCache(described.opportunities, described.loaded);
    renderTables(described);
  }

  // Opening a space loads what its sources have not loaded yet, as the person, once: a
  // teammate following a link should see real numbers, not zeros. It never holds the page
  // up -- the rail says it is loading, and a result run meanwhile says it is not a real
  // zero. A load that fails leaves the rail's "Load from Connect" to try again.
  let loadedOnOpen = false;
  async function loadMissingOnOpen() {
    if (!inSpace() || loadedOnOpen || !described) return;
    if (!described.opportunities.some((o) => !o.cached_visits)) return;
    loadedOnOpen = true;
    await describe(true);
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
    appendResultCard(box, data, table, {
      trail: renderTrail(),
      drillable,
      shown: display,
      rerun: () => run({ keepTrail: true }),
    });
    $('ex-csv').disabled = !data.rows.length;
  }

  // The chart a result's display asked for (`display.chart`), drawn by the module the
  // results card draws with (result_chart.js, the same SVG), or '' when there is none to
  // draw: no chart asked for, or none that fits the rows.
  function chartOf(data, shown, width) {
    const C = window.LabsResultChart;
    if (!C || !shown || !shown.chart || !data.rows || !data.rows.length)
      return '';
    const view = C.view(data, shown);
    const axes = view.axes();
    if (!axes.fromDisplay) return '';
    return view.draw({ ...axes, rows: data.rows, width });
  }

  // Chart | Table, as the results card has them: the chart first when the display asked
  // for one (it is what whoever saved it meant to be seen), the table one click away.
  function chartAndTable(data, shown, tableWrap) {
    const html = chartOf(data, shown, chartWidth());
    if (!html) return [tableWrap];
    const chartBox = el('div', { class: 'ex-chart' });
    // Every value in it was escaped by the module (result_chart.js esc).
    chartBox.innerHTML = html;
    tableWrap.hidden = true;
    const tabs = el('div', { class: 'ex-viewtabs', role: 'tablist' });
    const pick = (which) => {
      chartBox.hidden = which !== 'chart';
      tableWrap.hidden = which !== 'table';
      tabs
        .querySelectorAll('button')
        .forEach((b) =>
          b.setAttribute('aria-selected', String(b.dataset.view === which)),
        );
    };
    ['chart', 'table'].forEach((which) =>
      tabs.append(
        el(
          'button',
          {
            type: 'button',
            role: 'tab',
            'data-view': which,
            'aria-selected': String(which === 'chart'),
            onclick: () => pick(which),
          },
          which === 'chart' ? 'Chart' : 'Table',
        ),
      ),
    );
    return [tabs, chartBox, tableWrap];
  }

  // The width a chart has: the canvas's, less the card's padding.
  function chartWidth() {
    const canvas = $('ex-canvas') || $('ex-panes');
    const w = canvas && canvas.clientWidth;
    return w ? Math.max(320, w - 26) : 800;
  }

  // A result's footer line: rows, time, and what it was over.
  function appendResultCard(
    box,
    data,
    table,
    { trail: trailEl, drillable, shown = null, rerun = null },
  ) {
    const n = data.opportunities.length;
    const opps = data.workflow
      ? `${data.workflow.name} (${n} opportunit${n === 1 ? 'y' : 'ies'})`
      : data.opportunities.length > 3
        ? `${data.opportunities.length} opportunities`
        : data.opportunities.map((o) => o.name).join(', ');
    // A result over opportunities with nothing cached is not a result about them: a 0
    // here means "not loaded", and it must say so where the number is, not only in the
    // Sources card further down the rail.
    const unloaded = (data.opportunities || []).filter(
      (o) => 'cached_at' in o && !o.cached_at,
    );
    let notLoaded = '';
    if (unloaded.length) {
      const action = el('span');
      const loadsBefore = loadsDone;
      // What to do about it, as the load stands: loading now (a space loads its sources
      // when it opens), loaded since this ran (run it again), or not loaded (load them).
      const paint = () => {
        const loaded =
          loadsDone > loadsBefore &&
          !!described &&
          unloaded.every((o) =>
            described.opportunities.some(
              (d) => d.opportunity_id === o.id && d.cached_visits > 0,
            ),
          );
        if (loading)
          action.replaceChildren('They are loading from Connect now…');
        else if (loaded && rerun)
          action.replaceChildren(
            'They have loaded now: ',
            el(
              'button',
              { type: 'button', class: 'ex-link', onclick: rerun },
              'run it again',
            ),
            '.',
          );
        else
          action.replaceChildren(
            el(
              'button',
              {
                type: 'button',
                class: 'ex-link',
                onclick: () => describe(true),
              },
              'Load them from Connect',
            ),
            ', then run it again.',
          );
      };
      paint();
      notLoaded = el(
        'div',
        { class: 'ex-warn', style: 'margin: 8px 12px' },
        unloaded.length === n
          ? `None of these ${n} opportunities is loaded yet, so this result counts nothing — it is not a real zero. `
          : `${unloaded.length} of ${n} opportunities aren't loaded yet, so this result leaves out their visits. `,
        action,
      );
      watchLoad(notLoaded, paint);
    }
    box.append(
      el(
        'div',
        { class: 'ex-card' },
        trailEl || '',
        notLoaded,
        ...chartAndTable(
          data,
          shown,
          el('div', { class: 'ex-table-wrap' }, table),
        ),
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

  // A cell as the result's display says (labsdata/display.py FORMATS): number_N,
  // percent_N (a fraction), currency_N, compact_N, date, month. No format: as before.
  function formatValue(v, format, currency) {
    if (!format) return typeof v === 'number' ? v.toLocaleString() : v;
    if (format === 'date' || format === 'month') {
      const d = new Date(String(v).length === 10 ? `${v}T00:00:00Z` : v);
      if (Number.isNaN(d.getTime())) return v;
      const opts =
        format === 'month'
          ? { month: 'short', year: 'numeric', timeZone: 'UTC' }
          : {
              day: 'numeric',
              month: 'short',
              year: 'numeric',
              timeZone: 'UTC',
            };
      return d.toLocaleDateString('en-GB', opts);
    }
    const n = typeof v === 'number' ? v : Number(v);
    if (v === '' || Number.isNaN(n)) return v;
    const [kind, places] = format.split('_');
    const digits = Math.max(0, Math.min(4, Number(places) || 0));
    const opts = {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    };
    try {
      if (kind === 'percent')
        return n.toLocaleString(undefined, { ...opts, style: 'percent' });
      if (kind === 'currency')
        return n.toLocaleString(undefined, {
          ...opts,
          style: 'currency',
          currency,
        });
      if (kind === 'compact')
        return n.toLocaleString(undefined, { ...opts, notation: 'compact' });
      return n.toLocaleString(undefined, opts);
    } catch (e) {
      return n.toLocaleString();
    }
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
    const formats = data.columns.map(
      (c) =>
        (labels &&
          labels.columns &&
          labels.columns[c] &&
          labels.columns[c].format) ||
        null,
    );
    const currency = (labels && labels.currency) || 'USD';
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
                    : formatValue(v, formats[i], currency),
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
      dropTab(tabs[oldest]);
      tabs.splice(oldest, 1);
    }
    showTab(id);
    return tab;
  }

  // A tab's pane goes, and whatever it drew with it (an artifact's sandboxed frame).
  function dropTab(tab) {
    if (tab.destroy) tab.destroy();
    tab.pane.remove();
  }

  function closeTab(id) {
    const i = tabs.findIndex((t) => t.id === id && t.kind !== 'editor');
    if (i < 0) return;
    dropTab(tabs[i]);
    tabs.splice(i, 1);
    if (activeTab === id) showTab('editor');
    else renderTabs();
  }

  // A tab opened from the rail (a saved query, low on a long rail or above the canvas
  // on a phone) is out of sight: bring the canvas's tab strip into view.
  function revealCanvas() {
    const strip = $('ex-tabs');
    if (!strip || typeof strip.getBoundingClientRect !== 'function') return;
    const r = strip.getBoundingClientRect();
    if (r.top < 0 || r.top > window.innerHeight * 0.6)
      strip.scrollIntoView({ block: 'start' });
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
    revealCanvas();
    const ran = await runInto(out, body, shown);
    // Layout above the canvas can still move while the query runs; look again.
    if (ran.ok) revealCanvas();
    return { ...ran, tab: tab.id };
  }

  // A tab's query, run (again) into its result box.
  async function runInto(out, body, shown) {
    try {
      const data = await post(cfg.queryUrl, body);
      out.replaceChildren();
      appendResultCard(out, data, resultTable(data, { labels: shown }), {
        shown,
        rerun: () => {
          out.replaceChildren(
            el('p', { class: 'text-sm ex-muted' }, 'Running…'),
          );
          runInto(out, body, shown);
        },
      });
      return { ok: true };
    } catch (e) {
      out.replaceChildren(
        el('div', { class: 'ex-error' }, e.message || 'The query failed.'),
      );
      return { ok: false, error: e.error || 'query_failed' };
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

  function savedRow(
    q,
    {
      onOpen,
      onDelete,
      versions,
      onOpenVersion = openSpaceItem,
      title = q.sql,
      what = 'saved query',
      idAttr = 'data-query-id',
    },
  ) {
    const row = el(
      'div',
      { class: 'ex-saved-row', [idAttr]: q.id },
      el(
        'button',
        { class: 'open', title, onclick: () => onOpen(q) },
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
              title: `Delete this ${what} (every version)`,
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
                onclick: () => onOpenVersion(v.id),
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
      // On a space's page "My queries" shows only when the person has some: empty, it
      // reads as if the space had nothing in it.
      if (space) show('ex-saved-card', mySaved.length > 0);
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

  // ---- artifacts: small labs pages kept in the space ---------------------------
  // JSX render code and the named queries it reads (labsdata/models.py SpaceArtifact),
  // drawn ONLY in the workflow sandbox: the opaque-origin frame workflows and pages draw
  // in (components/workflow/sandbox/artifact.tsx, published by the space-artifact bundle
  // as window.LabsArtifact). This page never compiles or runs an artifact's code: it asks
  // labs to run the queries over the space, as the person, and hands the frame the rows.

  let spaceArtifacts = [];
  let artifactsLoading = Promise.resolve();

  function loadSpaceArtifacts() {
    artifactsLoading = fetchSpaceArtifacts();
    return artifactsLoading;
  }

  async function fetchSpaceArtifacts() {
    const box = $('ex-artifacts');
    if (!space || !box) return;
    try {
      const data = await getJson(spaceApi(space.id, 'artifacts/'));
      spaceArtifacts = data.artifacts || [];
      box.replaceChildren();
      if (!spaceArtifacts.length) {
        box.append(
          emptyLine(
            'None yet. Ask the agent for a small dashboard over this space, then save it here.',
          ),
        );
        return;
      }
      spaceArtifacts.forEach((a) =>
        box.append(
          savedRow(a, {
            onOpen: (item) => openSpaceArtifact(item.id),
            onDelete: deleteSpaceArtifact,
            versions: a.versions,
            onOpenVersion: openSpaceArtifact,
            title: `Reads ${(a.queries || []).map((q) => q.name).join(', ')}`,
            what: 'artifact',
            idAttr: 'data-artifact-id',
          }),
        ),
      );
    } catch (e) {
      box.replaceChildren(
        emptyLine(e.message || 'Could not load the artifacts.'),
      );
    }
  }

  // An artifact of this space, any version: {id, name, version}.
  function findSpaceArtifact(id) {
    for (const a of spaceArtifacts) {
      const v = (a.versions || []).find((x) => x.id === id);
      if (v) return { ...v, name: a.name };
      if (a.id === id) return a;
    }
    return null;
  }

  // The sandbox host, once its bundle has loaded (deferred); refused when it never does.
  const HOST_WAIT_MS = 20000;
  let artifactHost = null;
  function whenArtifactHost() {
    if (window.LabsArtifact) return Promise.resolve(window.LabsArtifact);
    if (!artifactHost)
      artifactHost = new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          artifactHost = null;
          reject(
            new Error(
              'The sandbox that draws artifacts did not load. Reload the page.',
            ),
          );
        }, HOST_WAIT_MS);
        document.addEventListener(
          'labs-artifact:ready',
          () => {
            clearTimeout(timer);
            resolve(window.LabsArtifact);
          },
          { once: true },
        );
      });
    return artifactHost;
  }

  // Draw an artifact into `box` in the sandbox's frame: its code and its rows, nothing else.
  async function drawArtifact(tab, box, artifact, results) {
    if (!cfg.sandbox) {
      box.replaceChildren(
        el(
          'div',
          { class: 'ex-error' },
          'Artifacts are drawn only in the page sandbox, which is switched off on this server.',
        ),
      );
      return false;
    }
    let host;
    try {
      host = await whenArtifactHost();
    } catch (e) {
      box.replaceChildren(el('div', { class: 'ex-error' }, e.message));
      return false;
    }
    if (!box.isConnected) return false; // its tab was closed meanwhile
    box.replaceChildren();
    const mounted = host.mount(box, {
      labsOrigin: cfg.sandbox.origin || window.location.origin,
      artifact,
      results,
      space: space ? { id: space.id, name: space.name } : null,
      pageUrl: window.location.href,
    });
    tab.destroy = () => mounted.destroy();
    return true;
  }

  // What failed, by query name and code only: a database error can quote a value.
  function failedQueries(results) {
    return Object.entries(results || {})
      .filter(([, r]) => r && r.error)
      .map(([name, r]) => ({ query: name, error: r.error.code || 'failed' }));
  }

  // A tab for an artifact: its head (name, the queries it reads, Save for a draft), then
  // the frame. `run` fetches {artifact, results}; `consent`, when set, is a question the
  // person answers before the frame is drawn.
  async function openArtifactTab({ kind, title, queries, run, onSave }) {
    const tab = openTab(kind, title);
    const reads = el('details', {});
    const showQueries = (list) =>
      reads.replaceChildren(
        el(
          'summary',
          { class: 'px-3 py-1 text-xs ex-muted' },
          `Reads ${list.map((q) => q.name).join(', ') || '…'}`,
        ),
        ...list.map((q) =>
          el('pre', { class: 'ex-pane-sql' }, `-- ${q.name}\n${q.sql}`),
        ),
      );
    const box = el(
      'div',
      { class: 'ex-artifact-frame' },
      el('p', { class: 'text-sm ex-muted px-3 py-2' }, 'Running its queries…'),
    );
    tab.pane.append(
      el(
        'div',
        { class: 'ex-card' },
        el(
          'div',
          { class: 'ex-pane-head' },
          el('h2', {}, tab.title),
          onSave
            ? el(
                'button',
                { type: 'button', class: 'ex-btn ghost', onclick: onSave },
                'Save…',
              )
            : '',
        ),
        reads,
        box,
      ),
    );
    showQueries(queries);
    revealCanvas();
    let out;
    try {
      out = await run();
      // A saved version says what it reads itself.
      if (out.artifact && Array.isArray(out.artifact.queries))
        showQueries(out.artifact.queries);
    } catch (e) {
      box.replaceChildren(
        el(
          'div',
          { class: 'ex-error' },
          e.message || 'Its queries could not run.',
        ),
      );
      return { ok: false, tab: tab.id, error: e.error || 'artifact_failed' };
    }
    const failed = failedQueries(out.results);
    if (failed.length)
      tab.pane.firstChild.append(
        el(
          'div',
          { class: 'ex-warn mx-3 mb-2' },
          `${failed.map((f) => f.query).join(', ')} could not run; the artifact shows what it can.`,
        ),
      );
    const draw = () => drawArtifact(tab, box, out.artifact, out.results);
    if (out.consent) {
      // An agent's draft over production data: its code is the agent's, and what the
      // frame is handed can leave it (the sandbox's stated residual risk), so the person
      // draws it, not the agent.
      box.replaceChildren(
        el(
          'div',
          { class: 'ex-notice mx-3 my-2' },
          out.consent,
          ' ',
          el(
            'button',
            {
              type: 'button',
              class: 'ex-btn ghost ml-2',
              'data-testid': 'artifact-draw',
              onclick: (e) => {
                e.currentTarget.disabled = true;
                draw().then((drawn) => out.onDrawn && drawn && out.onDrawn());
              },
            },
            'Draw it',
          ),
        ),
      );
      return { ok: true, tab: tab.id, waiting: true, failed };
    }
    const drawn = await draw();
    if (drawn && out.onDrawn) out.onDrawn();
    return { ok: drawn, tab: tab.id, failed };
  }

  // What the frame is handed can leave it (the sandbox's stated residual risk), so over
  // data that is not synthetic an artifact's code reaches the person's rows only on THEIR
  // click: a draft the agent shows waits for "Draw it", one the agent opens does too, and the
  // agent may propose saving only a draft the person drew (`drawnDrafts`).
  const CONSENT =
    'The agent opened this. Its code runs in the sandbox over your data, which is not synthetic.';
  const drawnDrafts = new Set();
  function draftKey({ code, queries }) {
    return JSON.stringify([
      code,
      queries.map((q) => [q.name, q.sql, q.max_rows || null]),
    ]);
  }

  // A saved artifact (any version): its code and its queries' rows, run over the space as
  // the person, checked again as at its save. `byAgent`: the agent asked (page_openItem).
  function openSpaceArtifact(id, { byAgent = false } = {}) {
    const a = findSpaceArtifact(Number(id));
    if (!a) return Promise.resolve({ ok: false, error: 'not_found' });
    return openArtifactTab({
      kind: 'artifact',
      title: a.version > 1 ? `${a.name} (v${a.version})` : a.name,
      queries: [],
      run: async () => {
        const out = await post(
          spaceApi(space.id, `artifacts/${Number(id)}/run/`),
          {},
        );
        return {
          ...out,
          consent: byAgent && !out.all_generated ? CONSENT : null,
        };
      },
    });
  }

  // An agent's draft: never saved. Its queries run over what the page reads, as the person;
  // its code goes from here straight into the frame.
  function openArtifactDraft({ name, code, queries }) {
    return openArtifactTab({
      kind: 'draft',
      title: name,
      queries,
      onSave: () => openArtifactSaveDialog({ name, code, queries }),
      run: async () => {
        const data = await post(cfg.artifactDraftUrl, {
          ...selectionBody(),
          queries,
        });
        return {
          artifact: { name, code, draft: true },
          results: data.results,
          consent: data.all_generated
            ? null
            : 'The agent drafted this artifact. Its code runs in the sandbox over your data, which is not synthetic.',
          onDrawn: () => drawnDrafts.add(draftKey({ code, queries })),
        };
      },
    });
  }

  async function deleteSpaceArtifact(a) {
    if (
      !window.confirm(
        `Delete the artifact “${a.name}” from this space, every version? Everyone who can open the space loses it.`,
      )
    )
      return;
    try {
      await post(spaceApi(space.id, `artifacts/${a.id}/delete/`), {});
    } finally {
      loadSpaceArtifacts();
    }
  }

  // ---- saved conversations: pointers to canopy chat sessions -------------------
  // A pointer is canopy's session id, a label the person typed, who saved it and when --
  // never the conversation's words (a private one is sealed to its people; labs is not one
  // of them). The panel reports the conversation it has open as {id, resource, private}
  // (canopy's embedding guide §5e); only one started on THIS space's page may be saved
  // here. Opening one asks canopy to open it in the panel, as the person: canopy's session
  // ACL decides, and someone not in a private one is offered to ask to join.

  let spaceConversations = [];
  // The last `canopy:session` report (SDK >= 0.22.1); older panels are read on demand.
  let panelSession = null;

  function panelHost() {
    return window.canopyHost || null;
  }

  function openPanelSession() {
    const host = panelHost();
    if (!host) return panelSession;
    if (typeof host.session === 'function') return host.session();
    const w = host.widget;
    return w && typeof w.currentSession === 'function'
      ? w.currentSession()
      : panelSession;
  }

  function spaceResource() {
    return space ? `labs-data-space://${space.id}` : '';
  }

  function defaultConversationLabel() {
    const d = new Date();
    return `Conversation, ${d.getDate()} ${d.toLocaleString('en-GB', { month: 'short' })} ${d.getFullYear()}`;
  }

  function renderConversationSave() {
    const btn = $('ex-conversation-save');
    const hint = $('ex-conversation-hint');
    if (!btn || !hint) return;
    const s = openPanelSession();
    let ok = false;
    let text;
    if (!s) text = 'Start or open a conversation in the panel to save it here.';
    else if (s.resource !== spaceResource())
      text =
        'The conversation open in the panel was started on another page. Only one started on this space can be saved here.';
    else if (spaceConversations.some((c) => c.session_id === s.id))
      text = 'The conversation open in the panel is saved here.';
    else {
      ok = true;
      text =
        'Saves a link to the conversation open in the panel, never its words. Whoever opens it sees it only if canopy lets them.';
    }
    btn.disabled = !ok;
    hint.textContent = text;
    const label = $('ex-conversation-label');
    if (label && !label.placeholder)
      label.placeholder = defaultConversationLabel();
  }

  function conversationRow(c) {
    return el(
      'div',
      { class: 'ex-saved-row', 'data-conversation-id': c.id },
      el(
        'button',
        {
          class: 'open',
          title: 'Open in the panel',
          onclick: () => openConversation(c),
        },
        c.label,
        el(
          'small',
          {},
          ` · ${c.saved_by || ''} · ${(c.created_at || '').slice(0, 10)}`,
        ),
      ),
      c.can_delete
        ? el(
            'button',
            {
              class: 'del',
              title:
                'Remove from this space (the conversation itself stays in canopy)',
              onclick: () => deleteConversation(c),
            },
            '✕',
          )
        : '',
    );
  }

  async function loadConversations() {
    const box = $('ex-conversations');
    if (!space || !box) return;
    try {
      const data = await getJson(spaceApi(space.id, 'conversations/'));
      spaceConversations = data.conversations || [];
      box.replaceChildren();
      if (!spaceConversations.length)
        box.append(
          el(
            'p',
            { class: 'text-sm ex-muted' },
            'No conversations saved here yet. Ask the agent in the panel, then save the conversation for your team.',
          ),
        );
      spaceConversations.forEach((c) => box.append(conversationRow(c)));
    } catch (e) {
      box.replaceChildren(
        el(
          'p',
          { class: 'text-sm ex-muted' },
          e.message || 'Could not load conversations.',
        ),
      );
    }
    renderConversationSave();
  }

  function conversationError(message) {
    const box = $('ex-conversation-error');
    if (!box) return;
    box.textContent = message || '';
    box.hidden = !message;
  }

  async function saveConversation() {
    const s = openPanelSession();
    if (!s) return renderConversationSave();
    conversationError('');
    const label = $('ex-conversation-label');
    try {
      // The pointer and the label the person typed; nothing of the conversation.
      await post(spaceApi(space.id, 'conversations/'), {
        session_id: s.id,
        resource: s.resource,
        label: (label && label.value.trim()) || defaultConversationLabel(),
      });
      if (label) label.value = '';
    } catch (e) {
      conversationError(e.message || 'Could not save the conversation.');
    }
    await loadConversations();
  }

  async function deleteConversation(c) {
    if (
      !window.confirm(
        `Remove “${c.label}” from this space? The conversation itself stays in canopy.`,
      )
    )
      return;
    conversationError('');
    try {
      await post(spaceApi(space.id, `conversations/${c.id}/delete/`), {});
    } catch (e) {
      conversationError(e.message || 'Could not remove it.');
    }
    await loadConversations();
  }

  function openConversation(c) {
    conversationError('');
    const host = panelHost();
    const opened =
      host &&
      (typeof host.openSession === 'function'
        ? host.openSession(c.session_id)
        : host.widget && typeof host.widget.openSession === 'function'
          ? (host.widget.openSession(c.session_id), true)
          : false);
    if (!opened)
      conversationError(
        'The agent panel cannot open saved conversations here yet. Reload the page and try again.',
      );
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
            : 'Not saved — save this selection as a space',
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
    closeMore();
    state.editingSources = true;
    seedFromSpace();
    described = null;
    renderChips();
    describe(false);
    // Chosen from the menu at the top of the rail: the editing happens in Sources.
    const card = $('ex-sources-card');
    if (card && typeof card.scrollIntoView === 'function')
      card.scrollIntoView({ block: 'nearest' });
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

  // The manager's "More" menu closes once a choice is made from it.
  function closeMore() {
    const more = $('ex-space-more');
    if (more) more.open = false;
  }

  async function archiveSpace() {
    closeMore();
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
          // The last manager cannot be removed (spaces.LastManager): don't offer it.
          manager && (space.managers || []).length > 1
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
    saveDialogFor('query');
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
    saveDialogFor('query');
  }

  // The dialog's words: a query's (most of the time) or an artifact's.
  const SAVE_QUERY_HINT = $('ex-save-hint')
    ? $('ex-save-hint').textContent
    : '';
  function saveDialogFor(kind) {
    const artifact = kind === 'artifact';
    if ($('ex-save-title'))
      $('ex-save-title').textContent = artifact
        ? 'Save artifact'
        : 'Save query';
    if ($('ex-save-hint'))
      $('ex-save-hint').textContent = artifact
        ? 'The artifact is saved, never its rows: opening it runs its queries again, as whoever opens it, and draws it in the sandbox.'
        : SAVE_QUERY_HINT;
    $('ex-save-target').disabled = artifact;
  }

  // The same dialog for an artifact: its code and named queries, kept in THIS space (an
  // artifact belongs to a space). Nothing is saved until the person clicks Save.
  function openArtifactSaveDialog({ name = '', code, queries }) {
    if (!space) return null;
    pendingSave = { kind: 'artifact', code, queries };
    renderSaveTargets();
    $('ex-save-target').value = String(space.id);
    saveDialogFor('artifact');
    $('ex-save-name').value = name;
    // Everything that will be kept: the queries, then the code the frame will run.
    $('ex-save-sql').textContent =
      queries.map((q) => `-- ${q.name}\n${q.sql}`).join('\n\n') +
      `\n\n// code\n${code}`;
    $('ex-save-error').hidden = true;
    $('ex-save-dialog').hidden = false;
    $('ex-save-name').focus();
    return space;
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
      $('ex-save-error').textContent =
        pendingSave.kind === 'artifact'
          ? 'Give the artifact a name.'
          : 'Give the query a name.';
      $('ex-save-error').hidden = false;
      return;
    }
    const target = $('ex-save-target').value;
    $('ex-save-confirm').disabled = true;
    if (pendingSave.kind === 'artifact') {
      try {
        const { code, queries } = pendingSave;
        await post(spaceApi(space.id, 'artifacts/'), { name, code, queries });
        closeSaveDialog();
        loadSpaceArtifacts();
      } catch (e) {
        $('ex-save-error').textContent =
          e.message || 'Could not save the artifact.';
        $('ex-save-error').hidden = false;
      } finally {
        $('ex-save-confirm').disabled = false;
      }
      return;
    }
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
  on('ex-conversation-save', 'click', saveConversation);
  on('ex-conversation-label', 'keydown', (e) => {
    if (e.key === 'Enter') saveConversation();
  });
  // The panel reports the conversation it has open (SDK >= 0.22.1); an older panel is
  // asked when the person comes to save (focus).
  document.addEventListener('canopy:session', (e) => {
    panelSession = e.detail || null;
    renderConversationSave();
  });
  on('ex-conversation-label', 'focus', renderConversationSave);
  on('ex-conversations-card', 'mouseenter', renderConversationSave);
  $('ex-add').addEventListener('input', renderPicker);
  $('ex-add').addEventListener('focus', renderPicker);
  $('ex-add').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closePicker();
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.ex-picker')) $('ex-add-results').hidden = true;
    if (!e.target.closest('.ex-more')) closeMore();
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
  describedReady.then(loadMissingOnOpen);
  loadSaved();
  loadSpaceItems();
  loadSpaceArtifacts();
  loadConversations();
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

  // An artifact as an agent hands it over (page_showArtifactDraft, page_proposeSave):
  // {name, code, queries: [{name, sql, max_rows}]}. Its shape here; labs checks every query
  // against the space again when it runs or is saved, and the frame compiles the code.
  const MAX_CODE = 512 * 1024;
  const MAX_ARTIFACT_QUERIES = 8;
  const QUERY_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;

  function checkArtifact({ name, code, queries } = {}) {
    const label = checkText(name, 'name', 200, true);
    if (typeof code !== 'string' || !code.trim())
      throw new Error(
        'code is required: JSX render code, as a workflow’s render_code',
      );
    if (new Blob([code]).size > MAX_CODE)
      throw new Error('code is longer than 512 KB');
    if (!Array.isArray(queries) || !queries.length)
      throw new Error(
        'queries is required: [{name, sql, max_rows}], each read as pipelines.<name>.rows',
      );
    if (queries.length > MAX_ARTIFACT_QUERIES)
      throw new Error(`at most ${MAX_ARTIFACT_QUERIES} queries`);
    const seen = new Set();
    const checked = queries.map((q) => {
      if (!q || typeof q !== 'object' || Array.isArray(q))
        throw new Error('each query is {name, sql, max_rows}');
      const extra = Object.keys(q).filter(
        (k) => !['name', 'sql', 'max_rows'].includes(k),
      );
      // An artifact reads its space: a query names no sources of its own.
      if (extra.length)
        throw new Error(
          `a query is {name, sql, max_rows}; it cannot carry ${extra.join(', ')}`,
        );
      if (typeof q.name !== 'string' || !QUERY_NAME.test(q.name))
        throw new Error(
          'a query’s name is how the code reads it (pipelines.<name>): letters, digits and _',
        );
      if (seen.has(q.name)) throw new Error(`two queries are named ${q.name}`);
      seen.add(q.name);
      checkSql(q.sql);
      const rows = q.max_rows === undefined ? undefined : Number(q.max_rows);
      if (rows !== undefined && !(Number.isInteger(rows) && rows > 0))
        throw new Error('max_rows must be a positive integer');
      return {
        name: q.name,
        sql: q.sql,
        ...(rows ? { max_rows: rows } : {}),
      };
    });
    return { name: label, code, queries: checked };
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
        if (kind !== 'query' && kind !== 'artifact')
          throw new Error('kind must be "query" or "artifact"');
        const n = Number(id);
        if (!Number.isInteger(n) || n <= 0)
          throw new Error(`id must be a saved ${kind} id`);
        if (kind === 'artifact') {
          await artifactsLoading;
          if (!space || !findSpaceArtifact(n))
            throw new Error(
              space
                ? 'No such artifact in this space.'
                : 'Artifacts are kept in a data space; this page has none open.',
            );
          const out = await openSpaceArtifact(n, { byAgent: true });
          return {
            ok: true,
            opened: true,
            tab: out.tab,
            outcome: out.waiting
              ? 'waiting_for_person'
              : out.ok
                ? 'shown_to_person'
                : 'error_shown_to_person',
            ...(out.failed && out.failed.length
              ? { queries_failed: out.failed }
              : {}),
          };
        }
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
          'Open a saved query or an artifact in the Data page canvas, run as the person: one of this space’s queries (any ' +
          'version) or one of the person’s own ("My queries"), or one of this space’s artifacts (any version), drawn in the ' +
          'sandbox. Returns only an acknowledgement.',
        parameters: {
          type: 'object',
          properties: {
            kind: {
              type: 'string',
              enum: ['query', 'artifact'],
              description: 'What to open: a saved query, or an artifact.',
            },
            id: {
              type: 'integer',
              description:
                'The saved query’s or artifact’s id (or a version’s id): data_describe with space_id lists them under space.items.',
            },
          },
          required: ['kind', 'id'],
        },
      },
    ],
    showArtifactDraft: [
      async (input = {}) => {
        const draft = checkArtifact(input);
        if (!cfg.sandbox)
          throw new Error(
            'Artifacts are drawn only in the page sandbox, which is off on this server.',
          );
        const out = await openArtifactDraft(draft);
        return {
          ok: true,
          shown: !out.waiting && out.ok,
          tab: out.tab,
          saved: false,
          outcome: out.waiting
            ? 'waiting_for_person'
            : out.ok
              ? 'shown_to_person'
              : 'error_shown_to_person',
          ...(out.waiting
            ? {
                note: 'This data is not synthetic: the person draws your draft with a click, so its code never runs over their data unasked.',
              }
            : {}),
          ...(out.error ? { error: out.error } : {}),
          ...(out.failed && out.failed.length
            ? {
                queries_failed: out.failed,
                hint: 'Check each query with data_validate (pass space_id on a space) and draft again.',
              }
            : {}),
        };
      },
      {
        description:
          'Draw a DRAFT artifact -- a small dashboard: JSX render code plus the named queries it reads -- in the Data page ' +
          'canvas, in the workflow sandbox, over this page’s data, as the person. Nothing is saved. The code is written as a ' +
          'workflow’s render code (function WorkflowUI({ pipelines }), with React, window.LabsReport and Chart.js available) and reads each ' +
          'query’s rows as pipelines.<name>.rows (objects keyed by column). Validate each SQL with data_validate first. ' +
          'Returns only an acknowledgement, never rows; offer to keep it with page_proposeSave (kind "artifact").',
        parameters: {
          type: 'object',
          properties: {
            name: {
              type: 'string',
              description:
                'A short plain-words name, e.g. "Visits per month by opportunity".',
            },
            code: {
              type: 'string',
              description:
                'JSX render code, as a workflow’s: function WorkflowUI({ pipelines }) { ... } reading pipelines.<query name>.rows.',
            },
            queries: {
              type: 'array',
              description:
                'The named queries it reads, at most 8: one SELECT each over the page’s data (as data_query takes it).',
              items: {
                type: 'object',
                properties: {
                  name: {
                    type: 'string',
                    description:
                      'How the code reads it: pipelines.<name>.rows. Letters, digits and _.',
                  },
                  sql: { type: 'string' },
                  max_rows: { type: 'integer' },
                },
                required: ['name', 'sql'],
              },
            },
          },
          required: ['name', 'code', 'queries'],
        },
      },
    ],
    proposeSave: [
      async ({
        kind = 'query',
        name,
        sql,
        display: shown,
        code,
        queries,
      } = {}) => {
        if (kind !== 'query' && kind !== 'artifact')
          throw new Error('kind must be "query" or "artifact"');
        if (kind === 'artifact') {
          const artifact = checkArtifact({ name, code, queries });
          if (!space)
            throw new Error(
              'An artifact is kept in a data space, and this page has none open: the person can save this selection as a space first.',
            );
          // Only what the person has seen drawn: the same code and queries, as a draft.
          if (!drawnDrafts.has(draftKey(artifact)))
            throw new Error(
              'Propose saving only a draft the person has seen drawn: show exactly this code and these queries with page_showArtifactDraft first (over data that is not synthetic, the person draws it).',
            );
          openArtifactSaveDialog(artifact);
          return {
            ok: true,
            proposed: true,
            saved: false,
            target: `space ${space.id}`,
            note: 'Nothing is saved until the person clicks Save.',
          };
        }
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
          'Fill the Data page’s Save dialog for the person to keep a query (in this space, by default) or, with kind ' +
          '"artifact", an artifact in this space (its name, code and queries, as page_showArtifactDraft takes them). Nothing ' +
          'is saved until they click Save; they can rename it. The query or artifact is kept, never its rows.',
        parameters: {
          type: 'object',
          properties: {
            kind: {
              type: 'string',
              enum: ['query', 'artifact'],
              description:
                'What to save: a query (the default) or an artifact.',
            },
            name: {
              type: 'string',
              description: 'A name for the saved query or artifact.',
            },
            sql: {
              type: 'string',
              description:
                'A query: the SELECT to save (validated again when saved).',
            },
            display: {
              type: 'object',
              description:
                'A query: how to show its result, as data_query’s `display`.',
            },
            code: {
              type: 'string',
              description: 'An artifact: its JSX render code.',
            },
            queries: {
              type: 'array',
              description:
                'An artifact: its named queries, [{name, sql, max_rows}].',
              items: { type: 'object' },
            },
          },
          required: ['name'],
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
