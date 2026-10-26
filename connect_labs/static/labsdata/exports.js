// A data space's dictionary and de-identified exports (templates/labsdata/exports.html).
// Every call goes to labsdata/export_views.py, which opens the space as the person first;
// the page only draws what comes back and never holds a value from a visit.
(function () {
  'use strict';
  const cfg = window.LABSDATA_DX;
  if (!cfg) return;
  const $ = (id) => document.getElementById(id);
  const state = { dict: null, filter: 'all', search: '', selected: new Set() };
  const SENSITIVE = new Set(['identifier', 'free_text', 'gps', 'media']);
  const PARAMS = {
    round: ['step', 25],
    coarsen: ['decimals', 2],
    shift: ['max_days', 30],
    pseudonymise: ['namespace', ''],
  };

  function esc(v) {
    return String(v == null ? '' : v).replace(
      /[&<>"']/g,
      (c) =>
        ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        })[c],
    );
  }

  async function call(url, body) {
    const opts =
      body === undefined
        ? { credentials: 'same-origin' }
        : {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': cfg.csrf,
            },
            body: JSON.stringify(body),
          };
    const res = await fetch(url, opts);
    let data = {};
    try {
      data = await res.json();
    } catch (e) {
      /* not JSON */
    }
    if (!res.ok)
      throw new Error(data.message || `Request failed (${res.status})`);
    return data;
  }

  function showError(where, err) {
    $(where).innerHTML = err
      ? `<div class="dx-error">${esc(err.message || err)}</div>`
      : '';
  }

  // --- fields ---------------------------------------------------------------------

  function flaggedKeys() {
    return new Set(
      (state.dict.flags || []).map((f) => f.entry).filter(Boolean),
    );
  }

  function shown(entry, flagged) {
    if (
      state.search &&
      !`${entry.label} ${entry.meaning}`.toLowerCase().includes(state.search)
    )
      return false;
    if (state.filter === 'todo') return !entry.reviewed;
    if (state.filter === 'out') return entry.effective !== 'drop';
    if (state.filter === 'flagged') return flagged.has(entry.key);
    return true;
  }

  function settingCell(e) {
    if (!cfg.isManager) {
      const p = Object.keys(e.params || {}).length
        ? ` ${esc(JSON.stringify(e.params))}`
        : '';
      return `${esc(labelOf(e.setting))}${p}`;
    }
    const opts = state.dict.settings
      .map(
        (s) =>
          `<option value="${s.value}" ${s.value === e.setting ? 'selected' : ''}>${esc(s.label)}</option>`,
      )
      .join('');
    const param = PARAMS[e.setting];
    const pv = param ? ((e.params || {})[param[0]] ?? param[1]) : '';
    return (
      `<select data-act="setting" data-key="${esc(e.key)}">${opts}</select>` +
      (param
        ? ` <input class="param" type="text" data-act="param" data-key="${esc(e.key)}" title="${param[0]}" value="${esc(pv)}">`
        : '') +
      (e.reviewed
        ? ''
        : ` <button class="dx-btn ghost" style="padding:1px 6px;font-size:11px" data-act="review" data-key="${esc(e.key)}" title="Allow at this setting">Review</button>`) +
      (e.suggested_setting !== e.setting
        ? `<div class="dx-muted">Suggested: ${esc(labelOf(e.suggested_setting))}</div>`
        : '')
    );
  }

  function labelOf(setting) {
    const s = (state.dict.settings || []).find((x) => x.value === setting);
    return s ? s.label : setting;
  }

  function exportPill(e) {
    if (!e.reviewed)
      return '<span class="dx-pill todo">Dropped until reviewed</span>';
    if (e.effective === 'drop')
      return '<span class="dx-pill drop">Dropped</span>';
    return `<span class="dx-pill out">${esc(labelOf(e.effective))}</span>`;
  }

  function sourcesText(e) {
    if (e.column) return '<small>visits column</small>';
    const n = e.sources.length;
    const builds = [...new Set(e.sources.flatMap((s) => s.builds || []))].sort(
      (a, b) => a - b,
    );
    const spellings = [...new Set(e.sources.map((s) => s.path))];
    const extra =
      spellings.length > 1
        ? `<small>also: ${spellings.slice(1).map(esc).join(', ')}</small>`
        : '';
    const range = builds.length
      ? `builds ${builds[0]}${builds.length > 1 ? '–' + builds[builds.length - 1] : ''}`
      : 'no build recorded';
    const visits = e.sources.reduce((t, s) => t + (s.visits || 0), 0);
    return `${extra}<small>${n} form${n === 1 ? '' : 's'} · ${range} · ${visits} visits</small>`;
  }

  function drawFields() {
    const d = state.dict;
    $('dx-counts').textContent =
      `${d.counts.entries} · ${d.counts.reviewed} reviewed · ${d.counts.exported} exported`;
    $('dx-scanned').textContent = d.scanned_at
      ? `Scanned ${new Date(d.scanned_at).toLocaleString()} (${d.scanned_visits} visits)`
      : 'Never scanned';
    const flagged = flaggedKeys();
    const rows = d.entries.filter((e) => shown(e, flagged));
    $('dx-rows').innerHTML = rows.length
      ? rows
          .map(
            (e) => `
      <tr>
        <td>${cfg.isManager ? `<input type="checkbox" data-act="select" data-key="${esc(e.key)}" ${state.selected.has(e.key) ? 'checked' : ''}>` : ''}</td>
        <td class="path">${esc(e.label)}${flagged.has(e.key) ? ' <span class="dx-pill warn">flag</span>' : ''}${e.quasi ? ' <span class="dx-pill drop" title="Can single someone out together with other fields">combination</span>' : ''}${sourcesText(e)}</td>
        <td><span class="dx-kind ${SENSITIVE.has(e.kind) ? 'sens' : ''}">${esc(e.kind.replace('_', ' '))}</span></td>
        <td>${cfg.isManager ? `<textarea data-act="meaning" data-key="${esc(e.key)}" placeholder="What the question asks, per app version">${esc(e.meaning)}</textarea>` : esc(e.meaning)}</td>
        <td>${cfg.isManager ? `<select data-act="level" data-key="${esc(e.key)}"><option value="visit" ${e.level === 'visit' ? 'selected' : ''}>Visit</option><option value="case" ${e.level === 'case' ? 'selected' : ''}>Case</option></select>` : esc(e.level)}</td>
        <td>${settingCell(e)}</td>
        <td>${exportPill(e)}${e.reviewed_by ? `<div class="dx-muted">${esc(e.reviewed_by)}</div>` : ''}</td>
      </tr>`,
          )
          .join('')
      : '<tr><td colspan="7" class="dx-muted">No fields to show. Scan the space to find what its forms carry.</td></tr>';
    drawFlags();
    drawChanges();
    syncSelection();
  }

  function drawFlags() {
    const flags = state.dict.flags || [];
    if (!flags.length) {
      $('dx-flags').innerHTML = '';
      return;
    }
    $('dx-flags').innerHTML =
      `<div class="dx-note" style="margin:0 12px 8px"><b>${flags.length} thing(s) to look at</b>
      <ul class="dx-checks">${flags
        .slice(0, 50)
        .map(
          (
            f,
          ) => `<li><b>${esc(f.label)}</b>: ${esc((f.detail.paths || []).join(', '))}${f.detail.deliver_unit ? ` (${esc(f.detail.deliver_unit)})` : ''}${f.detail.newest_build ? ` · last in build ${esc(f.detail.last_build)}, newest ${esc(f.detail.newest_build)}` : ''}${f.detail.was ? ` · was ${esc(f.detail.was)}, now ${esc(f.detail.now)}` : ''}
        ${cfg.isManager ? ` <button class="dx-link" style="color:#16006d;text-decoration:underline" data-act="resolve" data-flag="${f.id}">Done</button>` : ''}</li>`,
        )
        .join('')}</ul></div>`;
  }

  function drawChanges() {
    $('dx-changes').innerHTML = (state.dict.changes || [])
      .map(
        (c) => `<tr>
      <td class="dx-muted">${esc(new Date(c.at).toLocaleString())}</td><td>${esc(c.user || '')}</td><td>${esc(c.action)}</td>
      <td class="path">${esc(c.entry)}</td><td class="dx-muted">${esc(summarise(c))}</td></tr>`,
      )
      .join('');
  }

  function summarise(c) {
    if (c.action === 'scan')
      return `${c.after.entries} new fields, ${c.after.sources} new spellings or forms, ${c.after.flags} flags over ${c.after.visits} visits`;
    const b = c.before || {},
      a = c.after || {};
    const parts = [];
    for (const k of [
      'setting',
      'level',
      'meaning',
      'quasi',
      'reviewed',
      'registry_id',
    ]) {
      if (JSON.stringify(b[k]) !== JSON.stringify(a[k]) && k in a)
        parts.push(
          `${k}: ${JSON.stringify(b[k] ?? '')} → ${JSON.stringify(a[k])}`,
        );
    }
    return parts.join('; ');
  }

  function syncSelection() {
    if (!cfg.isManager) return;
    $('dx-accept').disabled = state.selected.size === 0;
    $('dx-merge').disabled = state.selected.size !== 2;
  }

  async function loadDictionary() {
    try {
      state.dict = await call(cfg.dictionaryUrl);
      if (cfg.isManager && state.dict.registry_id)
        $('dx-registry').value = state.dict.registry_id;
      drawFields();
      showError('dx-fields-error', null);
    } catch (e) {
      showError('dx-fields-error', e);
    }
  }

  function entryOf(key) {
    return state.dict.entries.find((e) => e.key === key);
  }

  async function saveEntry(key, change) {
    try {
      const res = await call(cfg.entryUrl, { key, ...change });
      const i = state.dict.entries.findIndex((e) => e.key === key);
      state.dict.entries[i] = res.entry;
      await loadDictionary();
    } catch (e) {
      showError('dx-fields-error', e);
      await loadDictionary();
    }
  }

  function paramsFor(key, setting) {
    const p = PARAMS[setting];
    if (!p) return {};
    const input = document.querySelector(
      `input[data-act="param"][data-key="${CSS.escape(key)}"]`,
    );
    const raw = input ? input.value : p[1];
    return { [p[0]]: typeof p[1] === 'number' ? Number(raw) : raw };
  }

  $('dx-rows').addEventListener('change', (ev) => {
    const t = ev.target,
      key = t.dataset.key,
      act = t.dataset.act;
    if (act === 'select') {
      t.checked ? state.selected.add(key) : state.selected.delete(key);
      syncSelection();
      return;
    }
    if (act === 'setting') {
      saveEntry(key, { setting: t.value, params: paramsFor(key, t.value) });
      return;
    }
    if (act === 'param') {
      const e = entryOf(key);
      saveEntry(key, { setting: e.setting, params: paramsFor(key, e.setting) });
      return;
    }
    if (act === 'meaning') {
      saveEntry(key, { meaning: t.value });
      return;
    }
    if (act === 'level') {
      saveEntry(key, { level: t.value });
    }
  });
  $('dx-rows').addEventListener('click', (ev) => {
    const t = ev.target;
    if (t.dataset.act === 'review') {
      const e = entryOf(t.dataset.key);
      saveEntry(e.key, { setting: e.setting, params: e.params || {} });
    }
  });
  $('dx-flags').addEventListener('click', async (ev) => {
    const id = ev.target.dataset.flag;
    if (!id) return;
    try {
      await call(
        cfg.flagResolveUrl.replace(/\/0\/resolve\/$/, `/${id}/resolve/`),
        {},
      );
      await loadDictionary();
    } catch (e) {
      showError('dx-fields-error', e);
    }
  });
  document.querySelectorAll('.dx-filter button').forEach((b) =>
    b.addEventListener('click', () => {
      state.filter = b.dataset.filter;
      document
        .querySelectorAll('.dx-filter button')
        .forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      drawFields();
    }),
  );
  $('dx-search').addEventListener('input', (ev) => {
    state.search = ev.target.value.trim().toLowerCase();
    drawFields();
  });
  $('dx-scan').addEventListener('click', async () => {
    const btn = $('dx-scan');
    btn.disabled = true;
    btn.textContent = 'Scanning…';
    try {
      await call(cfg.scanUrl, {});
      await loadDictionary();
    } catch (e) {
      showError('dx-fields-error', e);
    }
    btn.disabled = false;
    btn.textContent = 'Scan for fields and app changes';
  });
  if (cfg.isManager) {
    $('dx-all').addEventListener('change', (ev) => {
      const flagged = flaggedKeys();
      state.dict.entries
        .filter((e) => shown(e, flagged))
        .forEach((e) =>
          ev.target.checked
            ? state.selected.add(e.key)
            : state.selected.delete(e.key),
        );
      drawFields();
    });
    $('dx-accept').addEventListener('click', async () => {
      if (
        !window.confirm(
          `Allow ${state.selected.size} field(s) at their suggested settings?`,
        )
      )
        return;
      try {
        await call(cfg.acceptUrl, { keys: [...state.selected] });
        state.selected.clear();
        await loadDictionary();
      } catch (e) {
        showError('dx-fields-error', e);
      }
    });
    $('dx-merge').addEventListener('click', async () => {
      const [a, b] = [...state.selected];
      const ea = entryOf(a),
        eb = entryOf(b);
      if (
        !window.confirm(
          `Treat "${eb.label}" as the same question as "${ea.label}"? The merged field takes the stricter setting and needs reviewing again.`,
        )
      )
        return;
      try {
        await call(cfg.mergeUrl, { into: a, other: b });
        state.selected.clear();
        await loadDictionary();
      } catch (e) {
        showError('dx-fields-error', e);
      }
    });
    $('dx-registry-save').addEventListener('click', async () => {
      try {
        await call(cfg.registryUrl, {
          registry_id: $('dx-registry').value.trim(),
        });
        await loadDictionary();
      } catch (e) {
        showError('dx-fields-error', e);
      }
    });
  }

  // --- lineage ---------------------------------------------------------------------

  $('dx-lineage-load').addEventListener('click', async () => {
    const body = $('dx-lineage-body');
    body.innerHTML =
      '<p class="dx-muted">Reading the workflow, its pipelines and registry…</p>';
    try {
      const res = await call(cfg.lineageUrl);
      if (!res.available) {
        body.innerHTML = `<p class="dx-muted">${esc(res.reason)}</p>`;
        return;
      }
      body.innerHTML = `<p class="dx-muted">${res.counts.properties} properties; ${res.counts.unset} read a field nobody has reviewed.</p>
        <div class="dx-wrap" style="max-height:420px"><table class="dx"><thead><tr><th>Property</th><th>Inherits</th><th>Reads</th><th>Unreviewed sources</th></tr></thead><tbody>
        ${res.properties
          .map(
            (
              p,
            ) => `<tr><td class="path">${esc(p.name)}<small>${esc(p.label || '')}</small></td>
          <td><span class="dx-pill ${p.inherits === 'unset' ? 'fail' : p.inherits === 'keep' ? 'out' : 'drop'}">${esc(p.inherits === 'unset' ? 'unset (sensitive)' : labelOf(p.inherits))}</span></td>
          <td class="dx-muted">${esc((p.reads || []).join(', '))}</td><td class="path">${esc((p.unset_paths || []).slice(0, 6).join(', '))}${(p.unset_paths || []).length > 6 ? '…' : ''}</td></tr>`,
          )
          .join('')}
        </tbody></table></div>`;
    } catch (e) {
      body.innerHTML = `<div class="dx-error">${esc(e.message)}</div>`;
    }
  });

  // --- exports ---------------------------------------------------------------------

  function checksList(x) {
    const checks = (x.checks && x.checks.checks) || [];
    if (!checks.length)
      return x.error
        ? `<span class="dx-pill fail">error</span> <span class="dx-muted">${esc(x.error)}</span>`
        : '<span class="dx-muted">—</span>';
    return `<ul class="dx-checks">${checks.map((c) => `<li><span class="dx-pill ${c.status}">${esc(c.status)}</span> <b>${esc(c.name.replace('_', ' '))}</b>: ${esc(c.summary)}${detail(c)}</li>`).join('')}</ul>`;
  }

  function detail(c) {
    if (c.name === 'untagged' && c.detail.fields)
      return `<div class="dx-muted">${c.detail.fields
        .slice(0, 20)
        .map((f) => esc(f.path))
        .join(', ')}</div>`;
    if (c.name === 'pii_scan') {
      const cols = { ...(c.detail.fail || {}), ...(c.detail.warn || {}) };
      const lines = Object.entries(cols).map(
        ([col, hits]) =>
          `${col}: ${Object.entries(hits)
            .map(([k, n]) => `${n} ${k.replace('_', ' ')}`)
            .join(', ')}`,
      );
      return lines.length
        ? `<div class="dx-muted">${lines.map(esc).join('<br>')}</div>`
        : '';
    }
    return '';
  }

  function actions(x) {
    const out = [];
    if (x.can_download)
      out.push(
        `<a class="dx-btn" href="${cfg.exportBase}${x.id}/download/">Download</a>`,
      );
    if (x.can_sign_off) {
      const warned = ((x.checks && x.checks.checks) || []).some(
        (c) => c.status === 'warn',
      );
      out.push(`<div class="dx-row-actions"><input type="text" data-note="${x.id}" placeholder="Who it is for, and why" size="28">
        ${warned ? `<label class="dx-muted"><input type="checkbox" data-ack="${x.id}"> I have read the warnings</label>` : ''}
        <button class="dx-btn" data-signoff="${x.id}">Sign off</button></div>`);
    }
    if (cfg.isManager && ['ready', 'blocked', 'approved'].includes(x.status))
      out.push(
        `<button class="dx-btn danger" data-reject="${x.id}">Reject</button>`,
      );
    if (x.status === 'ready' && !x.can_sign_off)
      out.push(
        '<span class="dx-muted">Waiting for another manager to sign off</span>',
      );
    if (x.signoff_note)
      out.push(
        `<div class="dx-muted">${esc(x.signed_off_by || '')}: ${esc(x.signoff_note)}</div>`,
      );
    if (x.expires_at && x.status === 'approved')
      out.push(
        `<div class="dx-muted">Deleted ${esc(new Date(x.expires_at).toLocaleDateString())}</div>`,
      );
    return out.join(' ');
  }

  async function loadExports() {
    try {
      const res = await call(cfg.exportsUrl);
      $('dx-export-rows').innerHTML = res.exports.length
        ? res.exports
            .map(
              (x) => `<tr>
        <td>${x.id}</td><td>${esc(x.requested_by || '')}<div class="dx-muted">${esc(new Date(x.created_at).toLocaleString())}</div></td>
        <td class="dx-muted">${esc(x.date_from || 'start')} → ${esc(x.date_to || 'latest')}</td>
        <td>${x.rows || ''}${x.source_rows ? `<div class="dx-muted">of ${x.source_rows} visits · ${x.fields} columns</div>` : ''}</td>
        <td>${esc(x.status_label)}</td><td>${checksList(x)}</td><td>${actions(x)}</td></tr>`,
            )
            .join('')
        : '<tr><td colspan="7" class="dx-muted">No exports yet.</td></tr>';
      showError('dx-exports-error', null);
      if (res.exports.some((x) => ['queued', 'building'].includes(x.status)))
        setTimeout(loadExports, 4000);
    } catch (e) {
      showError('dx-exports-error', e);
    }
  }

  $('dx-request').addEventListener('click', async () => {
    try {
      await call(cfg.exportsUrl, {
        date_from: $('dx-from').value || null,
        date_to: $('dx-to').value || null,
      });
      await loadExports();
    } catch (e) {
      showError('dx-exports-error', e);
    }
  });
  $('dx-export-rows').addEventListener('click', async (ev) => {
    const t = ev.target;
    try {
      if (t.dataset.signoff) {
        const id = t.dataset.signoff;
        const note = document.querySelector(`[data-note="${id}"]`).value;
        const ack = document.querySelector(`[data-ack="${id}"]`);
        await call(`${cfg.exportBase}${id}/sign-off/`, {
          note,
          acknowledge_warnings: ack ? ack.checked : false,
        });
        await loadExports();
      } else if (t.dataset.reject) {
        const note = window.prompt('Why is this export rejected?');
        if (note === null) return;
        await call(`${cfg.exportBase}${t.dataset.reject}/reject/`, { note });
        await loadExports();
      }
    } catch (e) {
      showError('dx-exports-error', e);
    }
  });

  loadDictionary();
  loadExports();
})();
