// The Data page (/labs/data/). Talks to /labs/data/api/{describe,query,visits,workflows}/
// -- the same service the data_* MCP tools call -- and keeps the canopy panel's page
// state on the selection so the agent works on what you see.
//
// The selection is programs and opportunities, or one workflow (which brings its own
// opportunities and its pipelines as tables). Only ids live here: the server checks
// every one, every time, against what the person holds.
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
  };

  // What the server needs to resolve the selection.
  function selectionBody() {
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
      !!state.workflow || state.opps.length > 0 || state.programs.length > 0
    );
  }

  // The opportunities the last describe resolved the selection to.
  function selected() {
    return described
      ? described.opportunities.map((o) => o.opportunity_id)
      : [];
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
    const url = new URL(window.location.href);
    ['opps', 'programs', 'workflow'].forEach((k) => url.searchParams.delete(k));
    if (state.workflow)
      url.searchParams.set('workflow', workflowParam(state.workflow));
    if (state.programs.length)
      url.searchParams.set('programs', state.programs.join(','));
    if (state.opps.length) url.searchParams.set('opps', state.opps.join(','));
    window.history.replaceState(null, '', url);
    const ids = selected();
    if (window.canopyHost && window.canopyHost.updatePageState) {
      window.canopyHost.updatePageState({
        resource: 'labs-data://opportunities/' + ids.join(','),
        visible_ids: ids,
        filters: { opportunity_ids: ids, ...selectionBody() },
      });
    }
  }

  // ---- the selection: chips, the add picker, the workflow list -------------

  function chip(label, sub, onRemove, extraClass) {
    return el(
      'span',
      { class: 'ex-chip' + (extraClass ? ' ' + extraClass : '') },
      label,
      sub ? el('small', {}, sub) : '',
      el(
        'button',
        {
          type: 'button',
          title: 'Remove',
          'aria-label': `Remove ${label}`,
          onclick: onRemove,
        },
        '×',
      ),
    );
  }

  function renderChips() {
    const box = $('ex-chips');
    box.replaceChildren();
    if (state.workflow) {
      const w = state.workflow;
      const n = described && described.workflow ? selected().length : null;
      box.append(
        chip(
          `Workflow: ${w.name || '#' + w.id}`,
          n ? `${n} opp${n === 1 ? '' : 's'}` : '',
          () => {
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
        chip(p ? p.name : `Program ${pid}`, 'program', () => {
          state.programs = state.programs.filter((x) => x !== pid);
          changed();
        }),
      );
    });
    state.opps.forEach((oid) => {
      const o = opportunityById.get(oid);
      box.append(
        chip(o ? o.name : `Opportunity ${oid}`, `#${oid}`, () => {
          state.opps = state.opps.filter((x) => x !== oid);
          changed();
        }),
      );
    });
    if (!box.childNodes.length)
      box.append(
        el('span', { class: 'ex-muted text-sm' }, 'Nothing chosen yet.'),
      );
    $('ex-add').disabled = !!state.workflow;
    $('ex-add').placeholder = state.workflow
      ? 'a workflow brings its own opportunities'
      : 'add a program or opportunity…';
    $('ex-wf-btn').hidden = !!state.workflow;
    $('ex-wf-hint').textContent = state.workflow
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
    const table = el('table', { class: 'ex-table' });
    table.append(
      el(
        'thead',
        {},
        el(
          'tr',
          {},
          data.columns.map((c) => el('th', {}, c)),
        ),
      ),
    );
    const tbody = el('tbody');
    const visitCol = data.columns.indexOf('visit_id');
    const drillable = (data.drill && data.drill.columns) || [];
    data.rows.forEach((r) =>
      tbody.append(
        el(
          'tr',
          {},
          r.map((v, i) =>
            el(
              'td',
              drillable[i] && i !== visitCol
                ? {
                    class: 'ex-drill',
                    title:
                      'See the rows behind this, and the SQL that selects them',
                    onclick: () => drillInto(r, i, data.columns[i]),
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
                        onclick: () => openVisit(String(v)),
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
        renderTrail(),
        el('div', { class: 'ex-table-wrap' }, table),
        el(
          'div',
          { class: 'ex-status' },
          el('b', {}, `${data.row_count.toLocaleString()} rows`),
          data.truncated ? ' (truncated — raise max rows or aggregate)' : '',
          ` · ${data.elapsed_ms} ms · over ${opps}`,
          drillable.some(Boolean)
            ? ' · click a number to see the rows behind it'
            : '',
        ),
      ),
    );
    $('ex-csv').disabled = !data.rows.length;
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

  // Row level: the whole visit -- every column and the full submitted form -- so a
  // number can be checked against the visits behind it.
  async function openVisit(visitId) {
    const box = $('ex-visit');
    box.replaceChildren(
      el('p', { class: 'text-sm ex-muted' }, 'Loading visit…'),
    );
    box.hidden = false;
    try {
      const data = await post(cfg.visitsUrl, {
        // The opportunities the shown result came from, not the current ticks: the
        // person may have changed the selection since running it.
        opportunity_ids:
          lastResult && lastResult.opportunities
            ? lastResult.opportunities.map((o) => o.id)
            : selected(),
        visit_ids: [visitId],
      });
      const visit = data.visits[0];
      box.replaceChildren(
        el(
          'div',
          { class: 'ex-bar' },
          el('b', {}, `Visit ${visitId}`),
          el('span', { class: 'flex-1' }),
          el(
            'button',
            { class: 'ex-btn ghost', onclick: () => (box.hidden = true) },
            'Close',
          ),
        ),
        visit
          ? el('pre', { class: 'ex-json' }, JSON.stringify(visit, null, 2))
          : el(
              'div',
              { class: 'ex-error' },
              'Not in these opportunities’ cached visits.',
            ),
      );
    } catch (e) {
      box.replaceChildren(
        el(
          'div',
          { class: 'ex-error' },
          e.message || 'Could not load the visit.',
        ),
      );
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

  function csv() {
    if (!lastResult) return;
    const esc = (v) => {
      let s =
        v === null || v === undefined
          ? ''
          : typeof v === 'object'
            ? JSON.stringify(v)
            : String(v);
      // Form answers are typed by field workers; a text cell starting with a formula
      // character would run when the CSV is opened in Excel or Sheets.
      if (typeof v === 'string' && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
      return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [lastResult.columns.map(esc).join(',')].concat(
      lastResult.rows.map((r) => r.map(esc).join(',')),
    );
    const a = el('a', {
      href: URL.createObjectURL(
        new Blob([lines.join('\n')], { type: 'text/csv' }),
      ),
      download: 'labs-data.csv',
    });
    a.click();
  }

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
  renderChips();
  if (hasSelection()) describe(false);

  // The agent's way to hand SQL back to Labs. canopy turns this into the agent tool
  // `page_data_set_query`; the callback runs in THIS tab. It returns only an
  // acknowledgement -- never rows, counts or answers: sensitive data stays in the
  // person's browser and never travels back through canopy.
  function registerAgentActions(host) {
    if (!host || !host.widget || !host.widget.registerAction) return;
    host.widget.registerAction(
      'data_set_query',
      async ({ sql, opportunity_ids, program_ids, workflow, run: runIt }) => {
        if (typeof sql !== 'string' || !sql.trim())
          throw new Error('sql is required');
        const ids = (xs) =>
          Array.isArray(xs) ? xs.map(Number).filter(Number.isFinite) : [];
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
          'Returns only an acknowledgement: the result is shown to the person, never returned to you.',
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
                'Optional: query these opportunities (the person must hold them).',
            },
            program_ids: {
              type: 'array',
              items: { type: 'integer' },
              description:
                'Optional: query these programs -- every opportunity of each the person holds.',
            },
            workflow: {
              type: 'object',
              description:
                'Optional, instead of opportunities and programs: a workflow, as {id, opportunity_id} or {id, program_id} (the scope it lives in). Its pipelines become tables p_<alias>.',
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
