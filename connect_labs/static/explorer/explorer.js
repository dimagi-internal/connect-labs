// SQL explorer page. Talks to /labs/explorer/api/{describe,query}/ -- the same
// service the explorer_* MCP tools call -- and keeps the canopy panel's page
// state on the selected opportunities so the agent works on what you see.
(function () {
  'use strict';
  const cfg = window.EXPLORER;
  const $ = (id) => document.getElementById(id);
  let lastResult = null;
  let described = null;

  function selected() {
    return Array.from(document.querySelectorAll('#ex-opps input:checked')).map(
      (el) => Number(el.value),
    );
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

  function syncUrlAndPanel() {
    const ids = selected();
    const url = new URL(window.location.href);
    if (ids.length) url.searchParams.set('opps', ids.join(','));
    else url.searchParams.delete('opps');
    window.history.replaceState(null, '', url);
    if (window.canopyHost && window.canopyHost.updatePageState) {
      window.canopyHost.updatePageState({
        resource: 'labs-explorer://opportunities/' + ids.join(','),
        visible_ids: ids,
        filters: { opportunity_ids: ids },
      });
    }
  }

  function renderCache(opps, loaded) {
    const box = $('ex-cache');
    box.replaceChildren();
    opps.forEach((o) => {
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

  // Search filters the index already loaded -- no round trip per keystroke.
  function matching(fields) {
    const q = $('ex-field-search').value.trim().toLowerCase();
    if (!q) return fields;
    return fields.filter(
      (f) =>
        f.path.toLowerCase().includes(q) ||
        (f.values || []).some((v) => String(v.value).toLowerCase().includes(q)),
    );
  }

  function renderFields(data) {
    const box = $('ex-fields');
    box.replaceChildren();
    $('ex-sampled').textContent = data.sampled_visits
      ? `· ${data.sampled_visits} visits sampled`
      : '';
    data = { ...data, fields: matching(data.fields) };
    if (!data.fields.length) {
      box.append(
        el(
          'p',
          { class: 'px-3 pb-3 text-sm ex-muted' },
          data.sampled_visits ? 'No matching fields.' : 'No visits cached yet.',
        ),
      );
      return;
    }
    data.fields.slice(0, 300).forEach((f) => {
      const accessor = f.sql.includes('--')
        ? f.sql.split(' AS item')[0]
        : f.sql;
      const row = el(
        'div',
        {
          class: 'ex-field',
          title: f.sql,
          onclick: () => insertAtCursor(accessor),
        },
        el('code', {}, f.path),
        el('div', { class: 'meta' }, `${f.type} · filled ${f.filled_pct}%`),
        (f.values || [])
          .slice(0, 8)
          .map((v) =>
            el('span', { class: 'ex-chip' }, `${v.value} (${v.count})`),
          ),
      );
      box.append(row);
    });
  }

  async function describe(loadMissing) {
    const ids = selected();
    syncUrlAndPanel();
    if (!ids.length) {
      $('ex-cache').textContent = 'Pick opportunities to load their fields.';
      $('ex-fields').replaceChildren(
        el('p', { class: 'px-3 pb-3 text-sm ex-muted' }, '—'),
      );
      return;
    }
    $('ex-cache').textContent = loadMissing
      ? 'Loading from Connect…'
      : 'Reading…';
    try {
      described = await post(cfg.describeUrl, {
        opportunity_ids: ids,
        load_missing: !!loadMissing,
      });
      renderCache(described.opportunities, described.loaded);
      renderFields(described);
    } catch (e) {
      $('ex-cache').replaceChildren(
        el(
          'div',
          { class: 'ex-error' },
          e.message || 'Could not read these opportunities.',
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
    data.rows.forEach((r) =>
      tbody.append(
        el(
          'tr',
          {},
          r.map((v) =>
            el(
              'td',
              {
                title:
                  typeof v === 'object' && v !== null
                    ? JSON.stringify(v)
                    : String(v ?? ''),
              },
              v === null
                ? el('span', { class: 'ex-muted' }, 'null')
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
    const opps = data.opportunities.map((o) => o.name).join(', ');
    box.append(
      el(
        'div',
        { class: 'ex-card' },
        el('div', { class: 'ex-table-wrap' }, table),
        el(
          'div',
          { class: 'ex-status' },
          el('b', {}, `${data.row_count.toLocaleString()} rows`),
          data.truncated ? ' (truncated — raise max rows or aggregate)' : '',
          ` · ${data.elapsed_ms} ms · over ${opps}`,
        ),
      ),
    );
    $('ex-csv').disabled = !data.rows.length;
  }

  async function run() {
    const ids = selected();
    if (!ids.length) {
      $('ex-result').replaceChildren(
        el('div', { class: 'ex-error' }, 'Pick at least one opportunity.'),
      );
      return;
    }
    $('ex-run').disabled = true;
    $('ex-result').replaceChildren(
      el('p', { class: 'text-sm ex-muted' }, 'Running…'),
    );
    try {
      lastResult = await post(cfg.queryUrl, {
        opportunity_ids: ids,
        sql: $('ex-sql').value,
        max_rows: Number($('ex-max').value) || 500,
      });
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
      const s =
        v === null || v === undefined
          ? ''
          : typeof v === 'object'
            ? JSON.stringify(v)
            : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [lastResult.columns.map(esc).join(',')].concat(
      lastResult.rows.map((r) => r.map(esc).join(',')),
    );
    const a = el('a', {
      href: URL.createObjectURL(
        new Blob([lines.join('\n')], { type: 'text/csv' }),
      ),
      download: 'explorer.csv',
    });
    a.click();
  }

  $('ex-opps').addEventListener('change', () => describe(false));
  $('ex-opp-filter').addEventListener('input', (e) => {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll('#ex-opps .ex-opp').forEach((row) => {
      row.style.display = row.dataset.text.includes(q) ? '' : 'none';
    });
  });
  $('ex-field-search').addEventListener('input', () => {
    if (described) renderFields(described);
  });
  $('ex-run').addEventListener('click', run);
  $('ex-csv').addEventListener('click', csv);
  $('ex-sql').addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      run();
    }
  });
  if (selected().length) describe(false);
})();
