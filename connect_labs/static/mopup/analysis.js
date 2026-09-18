// Phase 2 analysis screen: per-indicator thresholds + global neighbor/gate
// settings, recomputed live against a run's already-loaded data via
// MopupCandidatesView. The first load (and only the first load) triggers a
// real, potentially slow Celery fetch server-side (see mopup/tasks.py) —
// this file polls MopupCandidatesView until it reports the fetch is done,
// showing the loading panel with real stage messages meanwhile, then
// reveals the controls. Every "Recompute" after that hits the same
// endpoint but returns instantly (the fetch never re-runs).
window.MopupAnalysis = (function () {
  function $(id) {
    return document.getElementById(id);
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(
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

  const INDICATOR_TOOLTIPS = {
    evc_shortfall:
      'How far the number of children actually served (approved Health Service Delivery visits) falls short of the expected number for this work area. A low ratio can mean the area was under-delivered — or that the expected-count estimate itself was too high for this specific cell. Only scored for work areas that have had at least one approved HSD visit — a work area with zero is excluded entirely (it’s already covered by NCF/inaccessible), not counted as a 0% rate.',
    ncf: "This work area has at least one approved visit that came back 'No Children Found' instead of a completed service visit. A work area logs at most one NCF-or-Inaccessible visit ever, so this and Inaccessible are mutually exclusive.",
    inaccessible:
      "This work area has at least one approved visit that came back 'Inaccessible' instead of a completed service visit. A work area logs at most one NCF-or-Inaccessible visit ever, so this and NCF are mutually exclusive.",
    deworming:
      'Of children served here, the share who received deworming medication. A low rate can reflect real refusals or a medication stockout — or an FLW not actually administering it.',
    muac: 'Of children served here, the share who had a MUAC (mid-upper arm circumference) measurement recorded.',
    vaccination:
      'Of children served here, the share who received any vaccine during the visit.',
  };
  const SEVERITY_TOOLTIP =
    'How many of the currently-enabled indicators flagged this work area. A plain count, not a weighted score.';

  let CFG = {};
  let indicatorDefs = [];
  let indicatorConfigs = {};
  let globalConfig = {};
  let lastCandidates = [];
  let lastGapCandidates = [];
  let lastWardSummaryRows = [];
  let lastGapSummaryByWard = {};
  let severitySortDesc = true;

  // One flat table row per indicator (design mockup, 2026-09, refined per
  // follow-up feedback) — EVC shortfall gets its own Neighbor distance/Min
  // neighbor count cells; NCF/Inaccessible (split from one combined row into
  // two, per later reviewer request) and deworming/MUAC/vaccination each
  // share ONE merged cell (colspan=2, rowspan = group size) holding both
  // inputs side by side plus a caption underneath -- a single overlapping
  // label rather than repeating "Applies to X, Y only" once per row in the
  // group. A light-grey sub-row directly under EVC's own row, and under the
  // LAST row of each of the other two groups, holds the settings that only
  // apply to that indicator (or group) -- same font/size as the rest of the
  // table, just a tinted background, so they read as part of the table
  // rather than a separate muted aside. Each of the three groups (EVC + its
  // sub-row, the NCF/Inaccessible pair + its trailing sub-row, the
  // deworming/MUAC/vaccination trio + its trailing sub-row) gets a border
  // box so its shared-settings relationship is visible at a glance.
  const TIER2_KEYS = ['deworming', 'muac', 'vaccination'];
  const NCF_GROUP_KEYS = ['ncf', 'inaccessible'];
  const GROUP_BORDER = 'border-gray-300';

  function subRowHtml(key, innerHtml, extraTdClasses) {
    return `<tr class="bg-gray-50 border-b border-gray-100" data-key="${key}">
        <td class="py-2 pr-2 pl-8 ${
          extraTdClasses || ''
        }" colspan="6">${innerHtml}</td>
      </tr>`;
  }

  function neighborCellsHtml(def, rowDisabled, sharedGroupsState, topBorder) {
    if (TIER2_KEYS.includes(def.key)) {
      if (sharedGroupsState.tier2.rendered) return '';
      sharedGroupsState.tier2.rendered = true;
      // Deliberately no .ind-neighbor-distance/.ind-neighbor-count classes
      // here -- this shared cell's disabled state is driven ONLY by the
      // global filter (handled separately below), never by any one of the
      // three rows' own "On" state, since it belongs to all three at once.
      return `
          <td class="py-2 pr-2 align-middle border-t-2 ${GROUP_BORDER}" colspan="2" rowspan="3">
            <div class="flex items-center gap-2">
              <input type="number" id="cfg-tier2-neighbor-distance" class="base-input" style="width:5rem" min="1">
              <input type="number" id="cfg-tier2-min-neighbor-count" class="base-input" style="width:5rem" min="1">
            </div>
            <div class="text-[11px] text-gray-500 mt-1">Applies to deworming, MUAC, vaccination</div>
          </td>`;
    }
    if (NCF_GROUP_KEYS.includes(def.key)) {
      if (sharedGroupsState.ncf.rendered) return '';
      sharedGroupsState.ncf.rendered = true;
      // Same shared-cell shape as the tier2 trio above, just for a pair --
      // NCF and Inaccessible always use the same neighbor-distance/
      // min-neighbor-count values (a WA logs at most one of the two visit
      // types, so there's nothing to differentiate between them here).
      return `
          <td class="py-2 pr-2 align-middle border-t-2 ${GROUP_BORDER}" colspan="2" rowspan="2">
            <div class="flex items-center gap-2">
              <input type="number" id="cfg-ncf-neighbor-distance" class="base-input" style="width:5rem" min="1">
              <input type="number" id="cfg-min-affected-neighbors-ncf" class="base-input" style="width:5rem" min="1">
            </div>
            <div class="text-[11px] text-gray-500 mt-1">Applies to NCF, Inaccessible</div>
          </td>`;
    }
    return `
          <td class="py-2 pr-2 ${
            topBorder || ''
          }"><input type="number" id="cfg-evc-neighbor-distance" class="base-input ind-neighbor-distance" style="width:5rem" min="1" ${
            rowDisabled ? 'disabled' : ''
          }></td>
          <td class="py-2 pr-2 ${
            topBorder || ''
          }"><input type="number" id="cfg-evc-min-neighbor-count" class="base-input ind-neighbor-count" style="width:5rem" min="1" ${
            rowDisabled ? 'disabled' : ''
          }></td>`;
  }

  function renderIndicatorRows() {
    const tb = $('indicator-rows');
    const sharedGroupsState = {
      tier2: { rendered: false },
      ncf: { rendered: false },
    };
    const rows = [];
    indicatorDefs.forEach((def) => {
      const cfg = indicatorConfigs[def.key] || {
        enabled: true,
        threshold: 0.5,
      };
      const isNcfGroup = NCF_GROUP_KEYS.includes(def.key);
      const isEvc = def.key === 'evc_shortfall';
      const isTier2 = TIER2_KEYS.includes(def.key);
      const rowDisabled = !cfg.enabled;
      // Stored/sent as a plain 0-1 rate throughout (matches the backend's
      // own comparison math) -- this is purely a display-layer convenience,
      // shown/entered as a percentage since that reads more intuitively
      // than "0.2". Rounded to 1 decimal place only to avoid floating-point
      // noise (e.g. 0.7 * 100 -> 69.99999999999999) on round-trip; real
      // precision beyond a tenth of a percent was never meaningful here.
      const thresholdCell = isNcfGroup
        ? `<span class="text-gray-400 italic">n/a</span>`
        : `<span class="inline-flex items-center gap-1">
            <input type="number" step="0.1" min="0" max="100" class="ind-threshold base-input" style="width:5.5rem" value="${
              Math.round(cfg.threshold * 1000) / 10
            }" ${rowDisabled ? 'disabled' : ''}>
            <span class="text-gray-500">%</span>
          </span>`;
      // Table borders only render per-cell (a <tr> border is a no-op without
      // border-collapse), so each group's box is built from border-t on
      // every cell of its first row, border-b on its trailing sub-row
      // below, and border-l/border-r on just the first/last cell of every
      // row in between -- same technique the ward-summary table already
      // uses for its column groups. EVC is a single-row group (top row IS
      // the only row); the NCF/Inaccessible pair's top row is "ncf", and the
      // deworming/MUAC/vaccination trio's top row is "deworming".
      const isGroupTop =
        isEvc || def.key === 'ncf' || (isTier2 && def.key === 'deworming');
      const isGroupMember = isEvc || isNcfGroup || isTier2;
      const topBorder = isGroupTop ? `border-t-2 ${GROUP_BORDER} ` : '';
      const leftBorder = isGroupMember ? `border-l-2 ${GROUP_BORDER} ` : '';
      const rightBorder = isGroupMember ? `border-r-2 ${GROUP_BORDER} ` : '';
      const midBorder = isGroupTop ? topBorder : '';
      rows.push(`<tr class="border-b border-gray-50 ${
        rowDisabled ? 'opacity-50' : ''
      }" data-key="${def.key}">
          <td class="py-2 pr-2 ${leftBorder}${topBorder}"><input type="checkbox" class="ind-enabled" ${
            cfg.enabled ? 'checked' : ''
          }></td>
          <td class="py-2 pr-2 ${midBorder}">${esc(
            def.label,
          )} <span class="info-icon" tabindex="0" data-tip="${esc(
            INDICATOR_TOOLTIPS[def.key] || '',
          )}">ⓘ</span></td>
          <td class="py-2 pr-2 ${midBorder}">${thresholdCell}</td>${neighborCellsHtml(
            def,
            rowDisabled,
            sharedGroupsState,
            midBorder,
          )}
          <td class="py-2 pr-2 ind-trigger-count ${rightBorder}${topBorder}">—</td>
        </tr>`);

      if (isEvc) {
        rows.push(
          subRowHtml(
            'evc_shortfall_settings',
            `<span class="inline-flex items-center gap-1">
              WA min EVC count
              <input type="number" id="cfg-min-evc-floor" class="base-input" style="width:5rem" min="0">
              <span class="info-icon" tabindex="0" data-tip="Excludes a work area from EVC shortfall entirely if its own EXPECTED visit count is below this — a plain worth-visiting cutoff, so a WA with both a low HSD/EVC ratio AND a low total EVC isn't considered for WA Revisit.">ⓘ</span>
            </span>`,
            `border-l-2 border-r-2 border-b-2 ${GROUP_BORDER}`,
          ),
        );
      }
      if (def.key === 'inaccessible') {
        rows.push(
          subRowHtml(
            'ncf_inaccessible_settings',
            `<span class="text-gray-600 mr-4">Applies to NCF/inaccessible only</span>
            <span class="inline-flex items-center gap-1">
              Min building count
              <input type="number" id="cfg-min-buildings" class="base-input" style="width:5rem" min="0">
              <span class="info-icon" tabindex="0" data-tip="The fewest real buildings a work area needs before an NCF or Inaccessible result there is treated as meaningful.">ⓘ</span>
            </span>`,
            `border-l-2 border-r-2 border-b-2 ${GROUP_BORDER}`,
          ),
        );
      }
      if (def.key === 'vaccination') {
        rows.push(
          subRowHtml(
            'tier2_settings',
            `<span class="text-gray-600 mr-4">Applies to deworming, MUAC, vaccination only</span>
            <span class="inline-flex items-center gap-1">
              WA min HSD-visits
              <input type="number" id="cfg-min-hsd" class="base-input" style="width:5rem" min="0">
              <span class="info-icon" tabindex="0" data-tip="The fewest approved Health Service Delivery visits a work area needs before its deworming/MUAC/vaccination rate is trusted at all.">ⓘ</span>
            </span>`,
            `border-l-2 border-r-2 border-b-2 ${GROUP_BORDER}`,
          ),
        );
      }
    });
    tb.innerHTML = rows.join('');
    applyIndicatorRowStates();
  }

  function renderIndicatorCounts(counts) {
    counts = counts || {};
    document.querySelectorAll('#indicator-rows tr').forEach((tr) => {
      const cell = tr.querySelector('.ind-trigger-count');
      if (cell) cell.textContent = counts[tr.dataset.key] ?? '—';
    });
  }

  // A single shared tooltip element, positioned in JS with `position: fixed`
  // rather than the `.info-icon`'s own CSS `::after` (see analysis.html's
  // comment on `#global-tooltip` for why -- scrolling ancestors clip an
  // absolutely-positioned tooltip anchored to the icon itself). Delegated on
  // `document` via `mouseover`/`mouseout`/`focusin`/`focusout` (which bubble,
  // unlike `mouseenter`/`mouseleave`) so it keeps working after
  // `renderIndicatorRows`/`renderCandidates`/etc. replace `.info-icon`
  // elements wholesale -- no per-element re-binding needed.
  function initTooltips() {
    const tip = document.createElement('div');
    tip.id = 'global-tooltip';
    document.body.appendChild(tip);

    function show(el) {
      const text = el.getAttribute('data-tip');
      if (!text) return;
      tip.textContent = text;
      const iconRect = el.getBoundingClientRect();
      const tipRect = tip.getBoundingClientRect();
      let top = iconRect.top - tipRect.height - 8;
      if (top < 4) top = iconRect.bottom + 8; // flip below if it'd go off the top
      let left = iconRect.left + iconRect.width / 2 - tipRect.width / 2;
      left = Math.max(4, Math.min(left, window.innerWidth - tipRect.width - 4));
      tip.style.top = `${top}px`;
      tip.style.left = `${left}px`;
      tip.classList.add('visible');
    }
    function hide() {
      tip.classList.remove('visible');
    }

    document.addEventListener('mouseover', (e) => {
      const el = e.target.closest('.info-icon');
      if (el) show(el);
    });
    document.addEventListener('mouseout', (e) => {
      const el = e.target.closest('.info-icon');
      if (el) hide();
    });
    document.addEventListener('focusin', (e) => {
      const el = e.target.closest('.info-icon');
      if (el) show(el);
    });
    document.addEventListener('focusout', (e) => {
      const el = e.target.closest('.info-icon');
      if (el) hide();
    });
  }

  // Two independent greying rules, applied together: a row's own "On"
  // checkbox greys its Threshold + (for EVC) its own neighbor inputs; the
  // global cluster-aware toggle greys EVERY neighbor distance/count input
  // (including the shared NCF/Inaccessible and deworming/MUAC/vaccination
  // cells) regardless of individual row state. Re-run after any relevant
  // checkbox changes, never on a full re-render, so in-progress edits/focus
  // aren't lost.
  function applyIndicatorRowStates() {
    const filterInput = $('cfg-cluster-filter-enabled');
    const filterOn = filterInput ? filterInput.checked : true;
    document.querySelectorAll('#indicator-rows tr[data-key]').forEach((tr) => {
      const enabledInput = tr.querySelector('.ind-enabled');
      const rowOn = enabledInput ? enabledInput.checked : true;
      tr.classList.toggle('opacity-50', !rowOn);
      const thresholdInput = tr.querySelector('.ind-threshold');
      if (thresholdInput) thresholdInput.disabled = !rowOn;
      const distanceInput = tr.querySelector('.ind-neighbor-distance');
      const countInput = tr.querySelector('.ind-neighbor-count');
      if (distanceInput) distanceInput.disabled = !filterOn || !rowOn;
      if (countInput) countInput.disabled = !filterOn || !rowOn;
    });
    const tier2Distance = $('cfg-tier2-neighbor-distance');
    const tier2Count = $('cfg-tier2-min-neighbor-count');
    if (tier2Distance) tier2Distance.disabled = !filterOn;
    if (tier2Count) tier2Count.disabled = !filterOn;
    const ncfDistance = $('cfg-ncf-neighbor-distance');
    const ncfCount = $('cfg-min-affected-neighbors-ncf');
    if (ncfDistance) ncfDistance.disabled = !filterOn;
    if (ncfCount) ncfCount.disabled = !filterOn;
  }

  function renderGlobalConfig() {
    $('cfg-min-evc-floor').value = globalConfig.min_evc_floor;
    $('cfg-evc-neighbor-distance').value = globalConfig.evc_neighbor_distance_m;
    $('cfg-evc-min-neighbor-count').value = globalConfig.evc_min_neighbor_count;
    $('cfg-min-buildings').value = globalConfig.min_building_count;
    $('cfg-ncf-neighbor-distance').value = globalConfig.ncf_neighbor_distance_m;
    $('cfg-min-affected-neighbors-ncf').value =
      globalConfig.min_affected_neighbors_ncf;
    $('cfg-cluster-filter-enabled').checked =
      !!globalConfig.cluster_aware_filter_enabled;
    $('cfg-tier2-neighbor-distance').value =
      globalConfig.tier2_neighbor_distance_m;
    $('cfg-tier2-min-neighbor-count').value =
      globalConfig.tier2_min_neighbor_count;
    $('cfg-min-hsd').value = globalConfig.min_hsd_visits_floor;
  }

  function collectIndicatorConfigs() {
    const out = {};
    document.querySelectorAll('#indicator-rows tr').forEach((tr) => {
      // Skip the per-indicator settings sub-rows (data-key ending in
      // "_settings") -- they have no .ind-enabled checkbox of their own,
      // they're rendered directly under the real indicator row.
      const enabledInput = tr.querySelector('.ind-enabled');
      if (!enabledInput) return;
      const key = tr.dataset.key;
      const thresholdInput = tr.querySelector('.ind-threshold');
      out[key] = {
        enabled: enabledInput.checked,
        // The input is a 0-100 percentage (display-only, see
        // renderIndicatorRows) -- convert back to the 0-1 rate the backend
        // has always stored/compared against.
        ...(thresholdInput
          ? { threshold: (parseFloat(thresholdInput.value) || 0) / 100 }
          : {}),
      };
    });
    return out;
  }

  function collectGlobalConfig() {
    return {
      min_evc_floor: parseInt($('cfg-min-evc-floor').value, 10) || 0,
      evc_neighbor_distance_m:
        parseFloat($('cfg-evc-neighbor-distance').value) || 0,
      evc_min_neighbor_count:
        parseInt($('cfg-evc-min-neighbor-count').value, 10) || 0,
      min_building_count: parseInt($('cfg-min-buildings').value, 10) || 0,
      ncf_neighbor_distance_m:
        parseFloat($('cfg-ncf-neighbor-distance').value) || 0,
      min_affected_neighbors_ncf:
        parseInt($('cfg-min-affected-neighbors-ncf').value, 10) || 0,
      cluster_aware_filter_enabled: $('cfg-cluster-filter-enabled').checked,
      tier2_neighbor_distance_m:
        parseFloat($('cfg-tier2-neighbor-distance').value) || 0,
      tier2_min_neighbor_count:
        parseInt($('cfg-tier2-min-neighbor-count').value, 10) || 0,
      min_hsd_visits_floor: parseInt($('cfg-min-hsd').value, 10) || 0,
    };
  }

  function wardRowKey(r) {
    return `${r.state}|${r.lga}|${r.ward}`;
  }

  // Step 3's live preview, grouped by ward -- cross-references the current
  // isolatedWaIds against the already-rendered candidate/gap-fill rows
  // (both carry ward/lga/state) rather than asking the server for a
  // separate per-ward aggregate.
  function isolatedCountByWard() {
    if (!isolatedWaIds.size) return {};
    const counts = {};
    [...lastCandidates, ...lastGapCandidates].forEach((c) => {
      if (!isolatedWaIds.has(c.wa_id)) return;
      const key = wardRowKey(c);
      counts[key] = (counts[key] || 0) + 1;
    });
    return counts;
  }

  function renderWardSummary(rows, gapSummaryByWard) {
    gapSummaryByWard = gapSummaryByWard || {};
    const isolatedByWard = isolatedCountByWard();
    $('ward-summary-rows').innerHTML = rows
      .map((r) => {
        const gap = gapSummaryByWard[wardRowKey(r)];
        const isolatedCount = isolatedByWard[wardRowKey(r)] || 0;
        const mainRow = `<tr class="border-b border-gray-50">
          <td class="p-2">${esc(r.ward)}</td><td class="p-2">${esc(
            r.lga,
          )}</td><td class="p-2">${esc(r.state)}</td>
          <td class="p-2 ward-col-connect ward-group-start">${
            r.total_work_areas
          }</td>
          <td class="p-2 ward-col-connect">${r.total_hsd}</td>
          <td class="p-2 ward-col-connect">${r.total_ncf}</td>
          <td class="p-2 ward-col-connect">${
            r.total_buildings
          }</td><td class="p-2 ward-col-connect">${r.total_evc}</td>
          <td class="p-2 ward-col-new ward-group-start">${
            r.candidate_count
          }</td>
          <td class="p-2 ward-col-new">${
            r.candidate_buildings
          }</td><td class="p-2 ward-col-new">${r.candidate_evc}</td>
          <td class="p-2 ward-col-new">${r.flagged_by_2_plus}</td>
        </tr>`;
        // Distinct, muted sub-rows directly under the ward's own row rather
        // than more columns — Step 2's new work areas are additional to,
        // not part of, the Connect-sourced totals above; Step 3's preview
        // count is a pending SUBTRACTION from whatever's shown above (not
        // yet removed until Lock in Step 3 — see candidateRowHtml's own
        // isolated tint for the matching per-row highlight).
        const gapRow = gap
          ? `<tr class="border-b border-gray-100 bg-emerald-50 text-emerald-800 text-xs">
          <td class="p-2 pl-2" colspan="8">+ Planning gaps (new)</td>
          <td class="p-2 ward-col-new ward-group-start">${gap.gap_wa_count}</td>
          <td class="p-2 ward-col-new">${gap.gap_buildings}</td>
          <td class="p-2 ward-col-new">${gap.gap_evc}</td>
          <td class="p-2 ward-col-new">—</td>
        </tr>`
          : '';
        const isolatedRow = isolatedCount
          ? `<tr class="border-b border-gray-100 bg-amber-50 text-amber-800 text-xs">
          <td class="p-2 pl-2" colspan="12">− Isolated (pending removal): ${isolatedCount}</td>
        </tr>`
          : '';
        return mainRow + gapRow + isolatedRow;
      })
      .join('');
  }

  const INDICATOR_LABELS = {
    evc_shortfall: 'EVC shortfall',
    ncf: 'NCF',
    inaccessible: 'Inaccessible',
    deworming: 'Deworming completion',
    muac: 'MUAC-recorded rate',
    vaccination: 'Vaccination-given rate',
    planning_gap: 'Planning gap (new)',
  };

  function triggeredIndicatorDisplay(c) {
    return c.triggered_indicators
      .map((key) => {
        const label = INDICATOR_LABELS[key] || key;
        const detail = (c.detail || {})[key] || {};
        if (key === 'ncf' || key === 'inaccessible') {
          return esc(`${label} (affected)`);
        }
        const rate = detail.rate;
        const num = detail.own_numerator;
        const denom = detail.own_denominator;
        if (rate == null || num == null || denom == null) return esc(label);
        return esc(`${label} (${Math.round(rate * 100)}%; ${num}/${denom})`);
      })
      .join(', ');
  }

  function candidateRowHtml(c, extraClass) {
    const isolated = isolatedWaIds.has(c.wa_id);
    // Step 3's preview tint takes priority over the gap-fill tint (both are
    // background colors on the same <tr>) -- an isolated gap-fill cell is
    // still worth flagging distinctly, since it's about to be removed.
    const rowClass = isolated ? 'bg-amber-100' : extraClass || '';
    const isolatedNote = isolated
      ? ` <span class="text-amber-700 font-medium">· Isolated — will be removed${
          isolationLastPreviewedDistanceM != null
            ? ` (>${isolationLastPreviewedDistanceM}m from nearest)`
            : ''
        }</span>`
      : '';
    return `<tr class="border-b border-gray-50 ${rowClass}" data-wa-id="${esc(c.wa_id)}">
          <td class="p-2">${esc(c.ward)}</td>
          <td class="p-2">${esc(c.flw_name || c.flw_username)}</td>
          <td class="p-2">${esc(c.wa_name)}</td>
          <td class="p-2">${esc(c.wag_name)}</td>
          <td class="p-2">${c.building_count}</td><td class="p-2">${
            c.expected_visit_count
          }</td>
          <td class="p-2" title="${esc(SEVERITY_TOOLTIP)}">${
            c.severity_count
          }</td>
          <td class="p-2">${triggeredIndicatorDisplay(c)}${isolatedNote}</td>
        </tr>`;
  }

  function renderCandidates() {
    const rows = [...lastCandidates].sort((a, b) => {
      if (a.tier !== b.tier) return a.tier - b.tier;
      return severitySortDesc
        ? b.severity_count - a.severity_count
        : a.severity_count - b.severity_count;
    });
    const html = rows.map((c) => candidateRowHtml(c)).join('');
    // Gap-fill rows (Step 2's new work areas for uncovered buildings, if
    // computed) always render after the execution-gap candidates, tinted so
    // they read as a distinct group rather than one more triggered WA.
    const gapHtml = lastGapCandidates
      .map((c) => candidateRowHtml(c, 'bg-emerald-50'))
      .join('');
    $('candidate-rows').innerHTML = html + gapHtml;
    // A full re-render (every Recompute, including the one triggered right
    // after excluding a work area) replaces every <tr> wholesale -- re-apply
    // whatever's currently selected via the map rather than losing the
    // highlight on the next unrelated setting change.
    highlightCandidateRow(selectedWaIds);
  }

  // ---------------------------------------------------------------------
  // Map — reuses the same shared PlanLayers module microplans' own review
  // page draws work areas with (static/maps/plan_layers.js), so mopup's map
  // looks and behaves identically rather than reimplementing layer paint.
  // ---------------------------------------------------------------------

  const INDICATOR_COLORS = {
    evc_shortfall: '#ef4444',
    ncf: '#f97316',
    inaccessible: '#ec4899',
    deworming: '#eab308',
    muac: '#8b5cf6',
    vaccination: '#06b6d4',
  };
  const NOT_INCLUDED_LABEL = 'Not included';
  const NOT_INCLUDED_KEY = 'not_included';
  const PLANNING_GAP_KEY = 'planning_gap';
  const BUILDING_POINT_KEY = 'building_point';

  // Show/hide toggles in the map legend -- purely a display filter (which
  // categories of feature get drawn on the map), never touches what's
  // included/excluded/flagged in the tables below, which keep reading
  // lastCandidates/lastGapCandidates independently of this. Persists across
  // Recompute and the Streets/Satellite toggle (both just re-call renderMap
  // with the same lastMapFeatures) since this is a plain module-level
  // object, not reset on every render -- only a full page reload clears it.
  let mapCategoryVisibility = {};

  function featureCategory(props) {
    if (props.source === 'planning_gap') return PLANNING_GAP_KEY;
    if (props.source === 'building_point') return BUILDING_POINT_KEY;
    return props.included ? props.first_indicator : NOT_INCLUDED_KEY;
  }

  function isCategoryVisible(key) {
    return mapCategoryVisibility[key] !== false;
  }

  let map = null;
  let mapReady = false;
  let mapBoundsFitted = false;

  function extendBboxWithGeometry(bbox, geometry) {
    if (!geometry) return;
    const rings =
      geometry.type === 'Polygon'
        ? geometry.coordinates
        : geometry.type === 'MultiPolygon'
          ? geometry.coordinates.flat()
          : [];
    rings.forEach((ring) =>
      ring.forEach(([lon, lat]) => {
        if (lon < bbox[0]) bbox[0] = lon;
        if (lat < bbox[1]) bbox[1] = lat;
        if (lon > bbox[2]) bbox[2] = lon;
        if (lat > bbox[3]) bbox[3] = lat;
      }),
    );
  }

  function geojsonBbox(...featureCollections) {
    const bbox = [Infinity, Infinity, -Infinity, -Infinity];
    featureCollections.forEach((fc) =>
      (fc?.features || []).forEach((f) =>
        extendBboxWithGeometry(bbox, f.geometry),
      ),
    );
    return bbox[0] === Infinity ? null : bbox;
  }

  const GAP_FILL_COLOR = '#10b981';
  const BUILDING_POINT_COLOR = '#a855f7';

  // PlanLayers.workAreas draws polygon fill/line layers — building-point
  // Points (the real detected buildings behind Step 2's gap-fill cells,
  // whichever source produced them) render as a separate circle layer
  // instead (see renderBuildingPoints), so they're filtered out here.
  // Also drops whatever category the legend toggles have hidden.
  function styleMapFeatures(fc) {
    return {
      type: 'FeatureCollection',
      features: (fc?.features || [])
        .filter(
          (f) =>
            f.properties.source !== 'building_point' &&
            isCategoryVisible(featureCategory(f.properties)),
        )
        .map((f) => {
          const color =
            f.properties.source === 'planning_gap'
              ? GAP_FILL_COLOR
              : f.properties.included
                ? INDICATOR_COLORS[f.properties.first_indicator] || '#3b82f6'
                : '#9ca3af';
          return {
            ...f,
            properties: {
              ...f.properties,
              fill: color,
              outline: color,
              // PlanLayers.workAreas forces a light grey fill/outline whenever
              // status === 'EXCLUDED' (see plan_layers.js) — reused as-is for
              // "not included in this mop-up round" rather than duplicating
              // that paint logic here.
              status: f.properties.included ? '' : 'EXCLUDED',
            },
          };
        }),
    };
  }

  function buildingPointFeatures(fc) {
    return {
      type: 'FeatureCollection',
      features: (fc?.features || []).filter(
        (f) =>
          f.properties.source === 'building_point' &&
          isCategoryVisible(BUILDING_POINT_KEY),
      ),
    };
  }

  // A checkbox-backed legend entry, not a plain swatch — lets a user hide a
  // category from the map without touching what's included/excluded/
  // flagged anywhere else (see mapCategoryVisibility above). Checked state
  // reads from mapCategoryVisibility so a re-render (Recompute, a
  // Streets/Satellite swap) reflects whatever the user last chose instead
  // of resetting to all-visible.
  function legendToggleHtml(key, color, label) {
    const checked = isCategoryVisible(key);
    return `<label class="inline-flex items-center gap-1 cursor-pointer select-none" style="opacity:${
      checked ? '1' : '0.45'
    }">
      <input type="checkbox" class="map-legend-toggle" data-category="${esc(
        key,
      )}" ${checked ? 'checked' : ''}>
      <span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${color};"></span>${esc(
        label,
      )}
    </label>`;
  }

  function renderMapLegend(fc) {
    const features = fc?.features || [];
    const present = new Set(
      features
        .filter(
          (f) =>
            f.properties.included && f.properties.source !== 'planning_gap',
        )
        .map((f) => f.properties.first_indicator),
    );
    const entries = Object.keys(INDICATOR_COLORS)
      .filter((key) => present.has(key))
      .map((key) =>
        legendToggleHtml(
          key,
          INDICATOR_COLORS[key],
          INDICATOR_LABELS[key] || key,
        ),
      );
    entries.push(
      legendToggleHtml(NOT_INCLUDED_KEY, '#9ca3af', NOT_INCLUDED_LABEL),
    );
    if (features.some((f) => f.properties.source === 'planning_gap')) {
      entries.push(
        legendToggleHtml(
          PLANNING_GAP_KEY,
          GAP_FILL_COLOR,
          'Planning gap (new)',
        ),
      );
    }
    if (features.some((f) => f.properties.source === 'building_point')) {
      entries.push(
        legendToggleHtml(BUILDING_POINT_KEY, BUILDING_POINT_COLOR, 'Buildings'),
      );
    }
    $('map-legend').innerHTML = entries.join('');
  }

  // Delegated on the container (called once, from init) rather than
  // attached per-render -- renderMapLegend replaces #map-legend's innerHTML
  // on every Recompute/style-swap, which would otherwise leak a duplicate
  // listener each time.
  function initMapLegendToggles() {
    $('map-legend').addEventListener('change', (e) => {
      const input = e.target.closest('.map-legend-toggle');
      if (!input) return;
      mapCategoryVisibility[input.dataset.category] = input.checked;
      input.closest('label').style.opacity = input.checked ? '1' : '0.45';
      if (lastMapFeatures) renderMap(lastMapFeatures);
    });
  }

  // A separate circle layer, not PlanLayers.workAreas (which only draws
  // polygons) — the real building positions behind Step 2's gap-fill
  // cells, not just the gridded cells themselves. Same layer for every
  // building source (Overture/OSM/Microsoft or an uploaded CSV).
  function renderBuildingPoints(fc) {
    window.PlanLayers.setSource(map, 'mopup-building-points', fc);
    if (!map.getLayer('mopup-building-points-circles')) {
      map.addLayer({
        id: 'mopup-building-points-circles',
        type: 'circle',
        source: 'mopup-building-points',
        paint: {
          'circle-radius': 3,
          'circle-color': BUILDING_POINT_COLOR,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#ffffff',
        },
      });
    }
  }

  let lastMapFeatures = null;
  // Multi-select: a Set of wa_ids (real work areas AND/OR Step 2 gap-fill
  // cells, both share one id-space -- see MopupExcludeWorkAreaView). Mirrors
  // the exact interaction convention microplans/review.js already
  // established for its own map (plain click replaces the selection,
  // shift/cmd/ctrl-click toggles membership) and the same Mapbox
  // feature-state mechanism (plan_layers.js's fill-opacity paint already
  // reacts to feature-state.sel per feature) -- ported here rather than
  // inventing a different pattern for a second map in the same app.
  let selectedWaIds = new Set();

  // Step 3's live preview: wa_ids the CURRENT distance threshold would
  // remove (both real work areas and gap-fill cells) -- updated by
  // previewIsolation(), never mutated directly. Empty once Step 3 is locked
  // in (the flagged WAs are gone from every table/the map by then, via the
  // normal excluded_wa_ids filtering every other exclusion already uses --
  // nothing special needed there).
  let isolatedWaIds = new Set();
  let isolationLastPreviewedDistanceM = null;

  // Highlights the candidate/gap-fill table row for every id in `waIds` --
  // called both right after a map click and after every renderCandidates()
  // re-render, so the highlight survives an unrelated Recompute rather than
  // only showing until the next setting change wipes the table's innerHTML.
  // Deliberately does NOT scroll the row into view (tried that first; per
  // user feedback, selecting a work area on the map shouldn't also jump the
  // page around) -- the highlight alone is enough to find it if the row's
  // already on screen.
  function highlightCandidateRow(waIds) {
    document
      .querySelectorAll('#candidate-rows tr[data-wa-id]')
      .forEach((tr) => {
        tr.classList.toggle('bg-yellow-100', waIds.has(tr.dataset.waId));
      });
  }

  // Applies the current selection to every rendered WA/gap-fill feature's
  // Mapbox feature-state (source 'wa', the id PlanLayers.workAreas promotes
  // features by) -- re-applied on every renderMap() call rather than
  // assumed to survive a source setData() refresh, same defensive pattern
  // microplans/review.js's own setSelState() uses.
  function applySelectionFeatureState(styledFeatureCollection) {
    if (!map || !mapReady) return;
    (styledFeatureCollection?.features || []).forEach((f) => {
      const waId = f.properties.wa_id;
      if (waId == null) return;
      map.setFeatureState(
        { source: 'wa', id: waId },
        { sel: selectedWaIds.has(waId) },
      );
    });
  }

  // Same defensive re-apply-after-render pattern as applySelectionFeatureState
  // (feature-state isn't guaranteed to survive a source setData() refresh),
  // for Step 3's own isolated-WA flag -- drives the hatch layer's opacity
  // (see renderMap/registerIsolationHatchPattern).
  function applyIsolationFeatureState(styledFeatureCollection) {
    if (!map || !mapReady) return;
    (styledFeatureCollection?.features || []).forEach((f) => {
      const waId = f.properties.wa_id;
      if (waId == null) return;
      map.setFeatureState(
        { source: 'wa', id: waId },
        { isolated: isolatedWaIds.has(waId) },
      );
    });
  }

  // Shared by every selection change: syncs the selection bar, the
  // candidate-table highlight, and the map's own feature-state.
  function refreshSelectionUI() {
    const n = selectedWaIds.size;
    if (n === 0) {
      $('map-selection-bar').classList.add('hidden');
    } else {
      $('map-selection-bar').classList.remove('hidden');
      if (n === 1) {
        const waId = [...selectedWaIds][0];
        const feature = (lastMapFeatures?.features || []).find(
          (f) => f.properties.wa_id === waId,
        );
        const ward = feature?.properties?.ward;
        $('map-selection-label').textContent = ward
          ? `Selected work area: ${waId} (${ward}) — also highlighted below`
          : `Selected work area: ${waId} — also highlighted below`;
      } else {
        $('map-selection-label').textContent =
          `${n} work areas selected — also highlighted below`;
      }
    }
    highlightCandidateRow(selectedWaIds);
    if (lastMapFeatures) {
      applySelectionFeatureState(styleMapFeatures(lastMapFeatures));
    }
  }

  // additive: shift/cmd/ctrl-click toggles this one id in/out of the
  // current selection; a plain click replaces the whole selection with just
  // this one (same convention as microplans/review.js).
  function toggleOrSelectWorkArea(waId, additive) {
    if (additive) {
      if (selectedWaIds.has(waId)) selectedWaIds.delete(waId);
      else selectedWaIds.add(waId);
    } else {
      selectedWaIds = new Set([waId]);
    }
    refreshSelectionUI();
  }

  function deselectAllWorkAreas() {
    selectedWaIds = new Set();
    refreshSelectionUI();
  }

  // Excludes/re-includes are relative to EVC (expected visit count), not
  // the underlying rate's own denominator -- e.g. deworming/MUAC/vaccination
  // are normally rates OF actual HSD visits, but the hover tooltip
  // deliberately shows them against EVC instead (a broader "how much of
  // what we EXPECTED here got the service" view), per explicit design.
  function evcPercentText(numerator, evc) {
    if (!evc) return '—';
    return `${Math.round((numerator / evc) * 100)}%`;
  }

  function mapHoverHtml(props) {
    if (props.source === 'planning_gap') {
      return `<div class="text-xs leading-snug space-y-0.5">
        <div class="font-semibold mb-1">Planning gap work area</div>
        <div>EVC: ${props.expected_visit_count}</div>
        <div>Buildings: ${props.building_count}</div>
      </div>`;
    }
    if (props.source !== 'existing_wa') return null; // uploaded-building dots: no tooltip
    const evc = props.expected_visit_count;
    return `<div class="text-xs leading-snug space-y-0.5">
      <div class="font-semibold">FLW Name: ${esc(props.flw_name || '—')}</div>
      <div class="font-semibold">WA Name: ${esc(props.wa_name || '—')}</div>
      <div class="font-semibold mb-1">WAG Name: ${esc(
        props.wag_name || '—',
      )}</div>
      <div>HSD visits: ${props.approved_hsd_count}</div>
      <div>EVC: ${evc}</div>
      <div>HSD visits / EVC: ${evcPercentText(
        props.approved_hsd_count,
        evc,
      )}</div>
      <div>Buildings: ${props.building_count}</div>
      <div>Deworming / EVC: ${evcPercentText(props.deworming_given, evc)}</div>
      <div>MUAC-recorded / EVC: ${evcPercentText(props.muac_given, evc)}</div>
      <div>Vaccination-given / EVC: ${evcPercentText(
        props.vaccination_given,
        evc,
      )}</div>
    </div>`;
  }

  // Registered once (from initMap's 'load' handler), not per-render -- safe
  // to attach layer-scoped listeners before the layers themselves exist
  // (PlanLayers.workAreas adds them lazily, on the first renderMap call);
  // Mapbox GL simply won't fire them until there's something to hit.
  function attachMapInteractivity() {
    const layerIds = ['wa-fill', 'wa-fill-dot'];
    const hoverPopup = new mapboxgl.Popup({
      closeButton: false,
      closeOnClick: false,
      maxWidth: '260px',
    });

    layerIds.forEach((layerId) => {
      map.on('mousemove', layerId, (e) => {
        if (!e.features.length) return;
        map.getCanvas().style.cursor = 'pointer';
        const html = mapHoverHtml(e.features[0].properties);
        if (!html) {
          hoverPopup.remove();
          return;
        }
        hoverPopup.setLngLat(e.lngLat).setHTML(html).addTo(map);
      });
      map.on('mouseleave', layerId, () => {
        map.getCanvas().style.cursor = '';
        hoverPopup.remove();
      });
    });

    // ONE click handler across both layers (queried once via
    // queryRenderedFeatures), not a separate map.on('click', layerId, ...)
    // per layer -- a point that matches both wa-fill (the polygon) and
    // wa-fill-dot (its own centroid, which sits INSIDE that same polygon,
    // so the two commonly overlap at intermediate zoom before the dot fades
    // out) would otherwise fire the toggle logic twice for one physical
    // click, silently canceling a shift-click's add/remove back to a no-op.
    // Also handles the empty-background case (deselect all) in the same
    // pass, since it's the same "what's under this point" query either way.
    map.on('click', (e) => {
      const hitLayers = layerIds.filter((id) => map.getLayer(id));
      if (!hitLayers.length) return;
      const hits = map.queryRenderedFeatures(e.point, { layers: hitLayers });
      if (!hits.length) {
        deselectAllWorkAreas();
        return;
      }
      const props = hits[0].properties;
      // Both real work areas and Step 2's planning-gap cells are
      // selectable/excludable -- uploaded-building dots (their own source,
      // not one of these two) are the only thing NOT clickable here, same
      // as they get no hover tooltip above.
      if (props.source !== 'existing_wa' && props.source !== 'planning_gap') {
        return;
      }
      const oe = e.originalEvent || {};
      toggleOrSelectWorkArea(
        props.wa_id,
        oe.shiftKey || oe.metaKey || oe.ctrlKey,
      );
    });
  }

  async function excludeSelectedWorkAreas() {
    if (selectedWaIds.size === 0) return;
    const button = $('map-exclude-button');
    button.disabled = true;
    try {
      const resp = await fetch(CFG.excludeWorkAreaUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CFG.csrfToken,
        },
        body: JSON.stringify({
          wa_ids: [...selectedWaIds],
          excluded: true,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || data.status !== 'ok') {
        alert(data.detail || 'Failed to exclude the selected work area(s).');
        return;
      }
      deselectAllWorkAreas();
      // Reuses the normal Recompute path -- the server-side exclusion
      // filter (_apply_exclusions) is applied there, so this one fetch
      // already re-renders the candidate table/ward summary/map correctly;
      // no separate rendering logic needed here.
      pollOrEvaluate();
    } finally {
      button.disabled = false;
    }
  }

  // A small 45°-diagonal dark-grey hatch, tiled — Step 3's "which WAs are
  // about to be removed" map treatment, layered ON TOP OF a feature's own
  // normal fill color (planning-gap green included) rather than replacing
  // it, so its source is still visible underneath the hatch. Registered
  // once per style document (map.setStyle wipes registered images along
  // with everything else — see reapplyMapLayers), guarded by hasImage so a
  // style swap or repeated init doesn't try to re-add it and throw.
  function registerIsolationHatchPattern() {
    if (!map || map.hasImage('isolation-hatch')) return;
    const size = 12;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    ctx.strokeStyle = '#374151';
    ctx.lineWidth = 2;
    ctx.beginPath();
    // Two parallel diagonal strokes near opposite corners of the tile so
    // the line continues seamlessly into the next tile in every direction.
    ctx.moveTo(-2, size * 0.5 - 2);
    ctx.lineTo(size * 0.5 + 2, -2);
    ctx.moveTo(size * 0.5 - 2, size + 2);
    ctx.lineTo(size + 2, size * 0.5 - 2);
    ctx.stroke();
    map.addImage('isolation-hatch', ctx.getImageData(0, 0, size, size));
  }

  // Reads the SAME shared 'wa' source PlanLayers.workAreas already sets up
  // (plan_layers.js itself is not modified — mirrors this app's existing
  // zero-footprint discipline toward shared map code) — a separate layer
  // so the hatch composites on top of whatever color the normal fill/line
  // layers already gave a feature, rather than replacing it. Invisible
  // (opacity 0) unless feature-state.isolated is set; added once, the
  // per-feature state is what actually turns it on/off (see
  // applyIsolationFeatureState).
  function addIsolationHatchLayer() {
    if (map.getLayer('wa-isolated-hatch')) return;
    map.addLayer({
      id: 'wa-isolated-hatch',
      type: 'fill',
      source: 'wa',
      paint: {
        'fill-pattern': 'isolation-hatch',
        'fill-opacity': [
          'case',
          ['boolean', ['feature-state', 'isolated'], false],
          0.85,
          0,
        ],
      },
    });
  }

  function renderMap(rawMapFeatures) {
    lastMapFeatures = rawMapFeatures;
    if (!map || !mapReady) return;
    const styled = styleMapFeatures(rawMapFeatures);
    window.PlanLayers.workAreas(map, { data: styled, promoteId: 'wa_id' });
    applySelectionFeatureState(styled);
    registerIsolationHatchPattern();
    addIsolationHatchLayer();
    applyIsolationFeatureState(styled);
    renderBuildingPoints(buildingPointFeatures(rawMapFeatures));
    renderMapLegend(rawMapFeatures);
    if (!mapBoundsFitted) {
      const bbox = geojsonBbox(styled, wardBoundariesData);
      if (bbox) {
        map.fitBounds(
          [
            [bbox[0], bbox[1]],
            [bbox[2], bbox[3]],
          ],
          { padding: 24, duration: 0 },
        );
        mapBoundsFitted = true;
      }
    }
  }

  let wardBoundariesData = { type: 'FeatureCollection', features: [] };

  // Streets/Satellite toggle (#map-style-toggle) — mapboxgl.Map#setStyle
  // swaps the ENTIRE style document, which wipes every source/layer this
  // file added on top of it (ward boundary line, work-area polygons,
  // building-point dots). reapplyMapLayers() re-adds all of that; it's the
  // same "add ward boundary, then replay the last-known data" logic the
  // initial 'load' handler already needed (a first data poll can land
  // before Mapbox's own 'load' fires), just factored out so a style swap
  // can reuse it via a one-shot 'style.load' listener instead of duplicating
  // it.
  const MAP_STYLES = {
    streets: 'mapbox://styles/mapbox/light-v11',
    satellite: 'mapbox://styles/mapbox/satellite-streets-v12',
  };
  let currentMapStyle = 'streets';

  function reapplyMapLayers() {
    if (wardBoundariesData.features.length) {
      window.PlanLayers.setSource(
        map,
        'mopup-ward-boundaries',
        wardBoundariesData,
      );
      if (!map.getLayer('mopup-ward-boundary-line')) {
        map.addLayer({
          id: 'mopup-ward-boundary-line',
          type: 'line',
          source: 'mopup-ward-boundaries',
          paint: {
            'line-color': '#1f2937',
            'line-width': 1.5,
            'line-dasharray': [2, 1],
          },
        });
      }
    }
    if (lastMapFeatures) renderMap(lastMapFeatures);
  }

  function setMapStyle(styleKey) {
    if (!map || !MAP_STYLES[styleKey] || styleKey === currentMapStyle) return;
    currentMapStyle = styleKey;
    document.querySelectorAll('.map-style-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.style === styleKey);
    });
    map.once('style.load', reapplyMapLayers);
    map.setStyle(MAP_STYLES[styleKey]);
  }

  function initMapStyleToggle() {
    document.querySelectorAll('.map-style-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.style === currentMapStyle);
      btn.addEventListener('click', () => setMapStyle(btn.dataset.style));
    });
  }

  function initMap(mapboxToken) {
    const el = $('analysis-map');
    if (!el || !window.mapboxgl || !mapboxToken) return;
    mapboxgl.accessToken = mapboxToken;
    try {
      map = new mapboxgl.Map({
        container: 'analysis-map',
        style: MAP_STYLES[currentMapStyle],
        center: [0, 0],
        zoom: 1,
      });
    } catch (e) {
      return; // headless / no WebGL
    }
    // This map has no use for either gesture, and both default-enabled
    // Mapbox handlers are bound to the SAME modifier keys the multi-select
    // click handler reads (shift/ctrl/cmd) to decide additive-vs-replace --
    // boxZoom (shift+drag) and dragRotate (ctrl/cmd+drag) intercept a
    // modifier-held mousedown for their own gesture detection and suppress
    // the normal 'click' event entirely once they do, before it ever
    // reaches attachMapInteractivity's handler. Disabling both guarantees a
    // shift/ctrl/cmd-click always reaches the multi-select toggle instead
    // of silently being swallowed as a (too-small-to-matter) box-zoom or
    // rotate attempt.
    map.boxZoom.disable();
    map.dragRotate.disable();
    // General safeguard beyond the one-shot showReady() resize — covers a
    // browser-window resize, and any other layout shift of the container
    // after construction.
    if (window.ResizeObserver) {
      new ResizeObserver(() => map && map.resize()).observe(el);
    }
    map.on('load', () => {
      mapReady = true;
      attachMapInteractivity();
      reapplyMapLayers();
    });
  }

  let pollTimer = null;
  let dataReady = false;
  let recomputeDebounceTimer = null;

  // Every threshold/setting change recomputes automatically, ~300ms after
  // the last change (debounced so a rapid-fire burst -- holding down a
  // number input's spinner, or a checkbox + its dependent fields both
  // changing at once -- collapses into one request rather than one per
  // event). The Recompute button (`init`'s click listener) bypasses this
  // debounce for an immediate update, for reviewers who'd rather not wait
  // out the ~300ms + round-trip.
  function scheduleRecompute() {
    if (!dataReady) return;
    clearTimeout(recomputeDebounceTimer);
    recomputeDebounceTimer = setTimeout(pollOrEvaluate, 300);
  }

  function showLoadingPanel(message) {
    $('loading-panel').classList.remove('hidden');
    $('analysis-body').classList.add('hidden');
    $('loading-message').textContent = message;
    $('loading-retry').classList.add('hidden');
  }

  function showLoadingError(message) {
    $('loading-panel').classList.remove('hidden');
    $('analysis-body').classList.add('hidden');
    $('loading-message').textContent = message;
    $('loading-retry').classList.remove('hidden');
  }

  function showReady() {
    $('loading-panel').classList.add('hidden');
    $('analysis-body').classList.remove('hidden');
    // #analysis-body is `display:none` while loading, so Mapbox — which
    // sizes its canvas from the container's bounding box at construction
    // time — locked in a 0-or-stale size and never grew to fill the page on
    // its own. Nudge it once the container actually has real dimensions.
    if (map) map.resize();
  }

  // The single entry point for both "check whether the initial fetch is
  // done yet" (called repeatedly while loading) and "recompute against
  // already-fetched data" (called once loaded, on every threshold tweak).
  // Same endpoint either way — the response shape says which one happened.
  async function pollOrEvaluate() {
    if (dataReady) {
      indicatorConfigs = collectIndicatorConfigs();
      globalConfig = collectGlobalConfig();
      $('status').textContent = 'Updating…';
    }
    try {
      const resp = await fetch(CFG.candidatesUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CFG.csrfToken,
        },
        body: JSON.stringify({
          indicator_configs: indicatorConfigs,
          global_config: globalConfig,
        }),
      });
      const data = await resp.json();

      if (data.status === 'pending' || data.status === 'running') {
        showLoadingPanel(data.message || 'Loading…');
        pollTimer = setTimeout(pollOrEvaluate, 2000);
        return;
      }
      if (data.status === 'failed' || data.status === 'error') {
        showLoadingError(data.message || data.detail || 'Failed to load data.');
        return; // deliberate stop — no auto-retry loop on a persistent error
      }
      if (data.status !== 'ok') {
        // Any Celery state build_task_progress doesn't map to pending/
        // running/failed/error previously fell through here and was
        // silently treated as a real, ready dataset -- fail loudly instead
        // of rendering whatever partial/undefined fields happened to be
        // present.
        showLoadingError('Unexpected response — try again.');
        return;
      }

      dataReady = true;
      showReady();
      lastCandidates = data.candidates || [];
      lastGapCandidates = data.gap_candidates || [];
      lastWardSummaryRows = data.ward_summary || [];
      lastGapSummaryByWard = data.gap_summary_by_ward;
      // Once Step 2 is locked in, Erase/Recompute stay disabled regardless
      // of whether there happen to be gap candidates -- nothing left to
      // erase or recompute (see lockPlanningGaps()).
      $('erase-planning-gaps').disabled =
        CFG.planningGapsLocked || lastGapCandidates.length === 0;
      $('live-count').textContent = data.candidate_count;
      renderWardSummary(lastWardSummaryRows, lastGapSummaryByWard);
      renderCandidates();
      renderIndicatorCounts(data.per_indicator_counts);
      renderMap(data.map_features);
      $('status').textContent =
        `${data.total_work_areas} work area(s) evaluated.`;
      // Keep Step 3's preview in sync with whatever just changed the active
      // WA set (a manual "Exclude WAs" click, an Erase, a threshold tweak)
      // -- a neighbor being excluded can newly isolate another WA, or vice
      // versa, so a stale preview would mislead right up to Lock in Step 3.
      if (CFG.planningGapsLocked && !CFG.isolationFilterLocked) {
        previewIsolation();
      }
    } catch (e) {
      showLoadingError('Failed to load data.');
    }
  }

  function retryLoad() {
    clearTimeout(pollTimer);
    showLoadingPanel('Retrying…');
    pollOrEvaluate();
  }

  async function lockRun() {
    $('status').textContent = 'Locking candidates…';
    try {
      const resp = await fetch(CFG.lockUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CFG.csrfToken,
        },
        body: JSON.stringify({
          indicator_configs: indicatorConfigs,
          global_config: globalConfig,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || data.status !== 'ok') {
        $('status').textContent =
          data.detail || data.message || 'Failed to lock.';
        return;
      }
      $('status').textContent =
        `Locked ${data.locked_count} candidate work area(s).`;
      $('lock-run').disabled = true;
      $('lock-run').textContent = `Locked (${data.locked_count} WAs)`;
      // "Create WA Revisit plan" no longer unlocks here -- since Step 3
      // (the isolation filter) shipped, it also requires
      // isolation_filter_locked, set only by lockIsolationFilter()'s own
      // success handler.
      $('planning-gaps-section').classList.remove('hidden');
    } catch (e) {
      $('status').textContent = 'Failed to lock.';
    }
  }

  let createPlanPollTimer = null;

  // Offloaded to a Celery task server-side (a real multi-ward hand-off can
  // still be slow) — this polls the SAME endpoint every 2s, mirroring
  // pollOrEvaluate's dispatch-once/poll-many pattern, until it reports a
  // terminal status. Planning-gap features (if Step 2 was ever run) are
  // already stored on the run — this never recomputes them.
  async function createPlan() {
    $('create-plan').disabled = true;
    $('status').textContent = 'Creating plan…';
    try {
      const resp = await fetch(CFG.createPlanUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CFG.csrfToken,
        },
        body: JSON.stringify({}),
      });
      const data = await resp.json();

      if (data.status === 'pending' || data.status === 'running') {
        $('status').textContent = data.message || 'Creating plan…';
        createPlanPollTimer = setTimeout(createPlan, 2000);
        return;
      }
      if (data.status === 'failed' || data.status === 'error') {
        $('status').textContent =
          data.detail || data.message || data.error || 'Failed to create plan.';
        $('create-plan').disabled = false;
        return;
      }
      if (data.status !== 'ok') {
        // Any Celery state build_task_progress doesn't map to pending/
        // running/failed/error (e.g. a transient/retry state) previously
        // fell through here and was silently treated as success --
        // confirmed live as "Plan created" with no work-area count. Fail
        // loudly instead of guessing.
        $('status').textContent = 'Unexpected response — try again.';
        $('create-plan').disabled = false;
        return;
      }

      const warnings = data.planning_gap_warnings || {};
      const warnedWards = Object.keys(warnings);
      let msg = data.planning_gap_cells_added
        ? `Plan created (${data.planning_gap_cells_added} planning-gap work area(s) added)`
        : 'Plan created';
      if (warnedWards.length) {
        msg += ` — planning-gap check failed for ${warnedWards.join(', ')} (${
          warnings[warnedWards[0]]
        })`;
      }
      $('status').textContent = msg + ' — opening review…';
      if (data.urls && data.urls.review)
        window.location.href = data.urls.review;
    } catch (e) {
      $('status').textContent = 'Failed to create plan.';
      $('create-plan').disabled = false;
    }
  }

  let planningGapsPollTimer = null;

  function selectedGapMode() {
    const checked = document.querySelector('.gap-mode-radio:checked');
    return checked ? checked.value : 'overture';
  }

  // Step 2's four modes show different controls: Overture's source picker,
  // the upload form, or (for "skip") none of the building-source config at
  // all — there's nothing to configure when no new work areas will be
  // added. Every mode's own confidence field (Overture's/Open Buildings'
  // always-populated sliders, upload's optional one) lives in the shared
  // controls grid next to Min buildings per work area/Work-area size, not in
  // its own mode-specific block — only one of the three is ever visible at
  // once, so they share that same grid slot.
  function updateGapModeVisibility() {
    const mode = selectedGapMode();
    $('gap-mode-overture-controls').classList.toggle(
      'hidden',
      mode !== 'overture',
    );
    $('gap-mode-upload-controls').classList.toggle('hidden', mode !== 'upload');
    $('gap-mode-shared-controls').classList.toggle('hidden', mode === 'skip');
    $('gap-mode-overture-confidence-wrap').classList.toggle(
      'hidden',
      mode !== 'overture',
    );
    $('gap-mode-open-buildings-confidence-wrap').classList.toggle(
      'hidden',
      mode !== 'open_buildings',
    );
    $('gap-mode-upload-confidence-wrap').classList.toggle(
      'hidden',
      mode !== 'upload',
    );
    $('planning-gaps-recompute').classList.toggle('hidden', mode === 'skip');
  }

  function collectPlanningGapsConfig() {
    const mode = selectedGapMode();
    // Upload mode's confidence field is a separate, optional input (blank
    // by default -- no filtering unless the reviewer's file has a
    // confidence column AND they choose to use it) from Overture mode's and
    // Open Buildings direct-fetch mode's always-populated confidence sliders.
    const confidenceInput =
      mode === 'upload'
        ? $('gap-cfg-upload-min-confidence')
        : mode === 'open_buildings'
          ? $('gap-cfg-open-buildings-min-confidence')
          : $('gap-cfg-min-confidence');
    const confidenceValue = parseFloat(confidenceInput.value);
    return {
      mode,
      building_sources: [
        ...document.querySelectorAll('.gap-src-cb:checked'),
      ].map((cb) => cb.value),
      min_confidence: Number.isNaN(confidenceValue) ? null : confidenceValue,
      min_buildings_per_cell:
        parseInt($('gap-cfg-min-buildings').value, 10) || 1,
      cell_size_m: parseFloat($('gap-cfg-cell-size').value) || 100,
    };
  }

  // Offloaded to a Celery task server-side — the real building fetch this
  // triggers (the FIRST time it's needed, never before) measured well over
  // a minute against real ward data this session. Polls the same endpoint
  // every 2s until it reports a terminal status, same pattern as
  // pollOrEvaluate/createPlan. On success, re-runs the normal Recompute so
  // the map/tables pick up the newly-stored gap-fill features (persisted
  // server-side onto the run by MopupPlanningGapsView — no separate lock
  // step for Step 2).
  async function previewPlanningGaps() {
    if (selectedGapMode() === 'skip') return;
    $('planning-gaps-recompute').disabled = true;
    $('planning-gaps-status').textContent = 'Checking planning gaps…';
    try {
      const resp = await fetch(CFG.planningGapsUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CFG.csrfToken,
        },
        body: JSON.stringify(collectPlanningGapsConfig()),
      });
      const data = await resp.json();

      if (data.status === 'pending' || data.status === 'running') {
        $('planning-gaps-status').textContent =
          data.message || 'Checking planning gaps…';
        planningGapsPollTimer = setTimeout(previewPlanningGaps, 2000);
        return;
      }
      $('planning-gaps-recompute').disabled = false;
      if (data.status === 'failed' || data.status === 'error') {
        $('planning-gaps-status').textContent =
          data.detail ||
          data.message ||
          data.error ||
          'Failed to check planning gaps.';
        return;
      }
      if (data.status !== 'ok') {
        // Any Celery state build_task_progress doesn't map to pending/
        // running/failed/error (e.g. a transient/retry state, more likely
        // to surface on a genuinely multi-minute first fetch like Google
        // Open Buildings mode's cold-cache path) previously fell through
        // here and rendered "undefined planning-gap work area(s) added." --
        // confirmed live. Fail loudly instead of guessing.
        $('planning-gaps-status').textContent =
          'Unexpected response — try again.';
        return;
      }

      const warnings = data.warnings || {};
      const warnedWards = Object.keys(warnings);
      let msg = `${data.cells_added} planning-gap work area(s) added.`;
      if (warnedWards.length) {
        msg += ` Failed for ${warnedWards.join(', ')} (${
          warnings[warnedWards[0]]
        }).`;
      }
      $('planning-gaps-status').textContent = msg;
      pollOrEvaluate();
    } catch (e) {
      $('planning-gaps-status').textContent = 'Failed to check planning gaps.';
      $('planning-gaps-recompute').disabled = false;
    }
  }

  // Step 2's "Erase planning gaps" action: deletes every planning-gap work
  // area this run has computed (MopupErasePlanningGapsView), independent of
  // Step 1's own candidate set. A normal Recompute right after picks up the
  // now-empty gap-candidate list, same pattern previewPlanningGaps already
  // uses after a successful fetch.
  async function erasePlanningGaps() {
    if (lastGapCandidates.length === 0) return;
    const confirmed = confirm(
      `Delete all ${lastGapCandidates.length} planning-gap work area(s) for this run? This cannot be undone.`,
    );
    if (!confirmed) return;
    $('erase-planning-gaps').disabled = true;
    $('planning-gaps-status').textContent = 'Erasing planning gaps…';
    try {
      const resp = await fetch(CFG.erasePlanningGapsUrl, {
        method: 'POST',
        headers: { 'X-CSRFToken': CFG.csrfToken },
      });
      const data = await resp.json();
      if (data.status !== 'ok') {
        $('planning-gaps-status').textContent =
          data.detail || 'Failed to erase planning gaps.';
        $('erase-planning-gaps').disabled = false;
        return;
      }
      $('planning-gaps-status').textContent = 'Planning-gap work areas erased.';
      pollOrEvaluate();
    } catch (e) {
      $('planning-gaps-status').textContent = 'Failed to erase planning gaps.';
      $('erase-planning-gaps').disabled = false;
    }
  }

  // Step 2's "upload your own" mode: POSTs the file as multipart form data
  // (not JSON, unlike every other endpoint on this page) so Django's
  // request.FILES sees it. Stores the file server-side; Recompute (a
  // separate click, same as Overture mode) is what actually reads it and
  // computes gap cells.
  async function uploadBuildingsFile() {
    const input = $('gap-upload-file');
    const file = input.files[0];
    if (!file) {
      $('gap-upload-status').textContent = 'Choose a CSV file first.';
      return;
    }
    $('gap-upload-button').disabled = true;
    $('gap-upload-status').textContent = 'Uploading…';
    try {
      const body = new FormData();
      body.append('file', file);
      const resp = await fetch(CFG.uploadBuildingsUrl, {
        method: 'POST',
        headers: { 'X-CSRFToken': CFG.csrfToken },
        body,
      });
      const data = await resp.json();
      $('gap-upload-button').disabled = false;
      if (data.status !== 'ok') {
        $('gap-upload-status').textContent =
          data.detail || 'Failed to upload file.';
        return;
      }
      $('gap-upload-status').textContent =
        `Uploaded: ${data.filename} (${data.matched_rows} row(s) matched this run's ward(s))`;
    } catch (e) {
      $('gap-upload-button').disabled = false;
      $('gap-upload-status').textContent = 'Failed to upload file.';
    }
  }

  // Step 2's own "Lock in Step 2" action — freezes whatever
  // planning_gap_features the latest successful Recompute (or "skip")
  // produced (nothing recomputed here), freezes Step 2's own controls, and
  // reveals Step 3 (the isolation filter needs a stable combined
  // candidate+gap-fill set — see core.isolation.isolated_work_area_ids).
  // One-way, same as Step 1's own lockRun() — no unlock.
  async function lockPlanningGaps() {
    $('lock-planning-gaps').disabled = true;
    try {
      const resp = await fetch(CFG.lockPlanningGapsUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CFG.csrfToken,
        },
        body: JSON.stringify({}),
      });
      const data = await resp.json();
      if (!resp.ok || data.status !== 'ok') {
        $('planning-gaps-status').textContent =
          data.detail || 'Failed to lock Step 2.';
        $('lock-planning-gaps').disabled = false;
        return;
      }
      CFG.planningGapsLocked = true;
      $('lock-planning-gaps').textContent = 'Locked';
      $('erase-planning-gaps').disabled = true;
      $('planning-gaps-recompute').disabled = true;
      $('isolation-filter-section').classList.remove('hidden');
      // Show Step 3's preview immediately with whatever distance is
      // pre-filled, rather than leaving the newly-revealed section empty
      // until the reviewer touches the input.
      previewIsolation();
    } catch (e) {
      $('planning-gaps-status').textContent = 'Failed to lock Step 2.';
      $('lock-planning-gaps').disabled = false;
    }
  }

  let isolationDebounceTimer = null;

  // Same debounce convention scheduleRecompute already uses for Step 1's
  // thresholds — a rapid-fire burst on the distance input's spinner
  // collapses into one preview request instead of one per keystroke.
  function scheduleIsolationPreview() {
    clearTimeout(isolationDebounceTimer);
    isolationDebounceTimer = setTimeout(previewIsolation, 300);
  }

  // Live preview only — never removes anything itself (see
  // lockIsolationFilter for the actual commit). Re-renders the candidate
  // table/ward summary/map with whatever the server just flagged as
  // isolated at the current distance.
  async function previewIsolation() {
    if (CFG.isolationFilterLocked) return;
    const distanceM = parseFloat($('isolation-distance-m').value);
    if (!distanceM || distanceM <= 0) {
      $('isolation-preview-status').textContent = 'Enter a positive distance.';
      return;
    }
    $('isolation-preview-status').textContent = 'Checking…';
    try {
      const resp = await fetch(CFG.isolationPreviewUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CFG.csrfToken,
        },
        body: JSON.stringify({ distance_m: distanceM }),
      });
      const data = await resp.json();
      if (!resp.ok || data.status !== 'ok') {
        $('isolation-preview-status').textContent =
          data.detail || 'Failed to preview.';
        return;
      }
      isolatedWaIds = new Set(data.isolated_wa_ids);
      isolationLastPreviewedDistanceM = distanceM;
      $('isolation-preview-status').textContent = isolatedWaIds.size
        ? `${isolatedWaIds.size} work area(s) would be removed — highlighted below and on the map.`
        : 'No work areas are isolated at this distance.';
      renderWardSummary(lastWardSummaryRows, lastGapSummaryByWard);
      renderCandidates();
      if (lastMapFeatures) {
        applyIsolationFeatureState(styleMapFeatures(lastMapFeatures));
      }
    } catch (e) {
      $('isolation-preview-status').textContent = 'Failed to preview.';
    }
  }

  // Step 3's own "Lock in Step 3" action — the actual commit. The server
  // recomputes the isolated set itself from distance_m (never trusts this
  // page's own isolatedWaIds preview for a destructive action) and unions
  // it into excluded_wa_ids -- the same field the map's own "Exclude WAs"
  // action already uses, so the removed work areas vanish from every
  // table/the map via the normal Recompute that follows, same as any other
  // exclusion. One-way, same as Step 1/Step 2's locks.
  async function lockIsolationFilter() {
    const distanceM = parseFloat($('isolation-distance-m').value);
    if (!distanceM || distanceM <= 0) {
      $('isolation-preview-status').textContent = 'Enter a positive distance.';
      return;
    }
    $('lock-isolation-filter').disabled = true;
    $('isolation-preview-status').textContent = 'Locking in Step 3…';
    try {
      const resp = await fetch(CFG.lockIsolationFilterUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CFG.csrfToken,
        },
        body: JSON.stringify({ distance_m: distanceM }),
      });
      const data = await resp.json();
      if (!resp.ok || data.status !== 'ok') {
        $('isolation-preview-status').textContent =
          data.detail || 'Failed to lock Step 3.';
        $('lock-isolation-filter').disabled = false;
        return;
      }
      CFG.isolationFilterLocked = true;
      isolatedWaIds = new Set();
      $('lock-isolation-filter').textContent = 'Locked';
      $('isolation-distance-m').disabled = true;
      $('isolation-preview-status').textContent =
        `${data.excluded_count} isolated work area(s) removed.`;
      $('create-plan').disabled = false;
      pollOrEvaluate();
    } catch (e) {
      $('isolation-preview-status').textContent = 'Failed to lock Step 3.';
      $('lock-isolation-filter').disabled = false;
    }
  }

  function init(cfg) {
    CFG = cfg;
    indicatorDefs = JSON.parse($('indicator-defs-data').textContent);
    indicatorConfigs = JSON.parse($('indicator-configs-data').textContent);
    globalConfig = JSON.parse($('global-config-data').textContent);
    wardBoundariesData = JSON.parse(
      $('ward-boundaries-data')?.textContent ||
        '{"type":"FeatureCollection","features":[]}',
    );
    initMap(cfg.mapboxToken);
    initMapStyleToggle();
    initMapLegendToggles();
    renderIndicatorRows();
    renderGlobalConfig();
    applyIndicatorRowStates(); // re-apply now that the real filter state is loaded
    $('cfg-cluster-filter-enabled').addEventListener('change', () => {
      applyIndicatorRowStates();
      scheduleRecompute();
    });
    $('indicator-rows').addEventListener('change', (e) => {
      if (e.target.classList.contains('ind-enabled')) applyIndicatorRowStates();
      scheduleRecompute();
    });
    $('recompute').addEventListener('click', () => {
      clearTimeout(recomputeDebounceTimer);
      pollOrEvaluate();
    });
    $('loading-retry').addEventListener('click', retryLoad);
    $('sort-severity').addEventListener('click', () => {
      severitySortDesc = !severitySortDesc;
      renderCandidates();
    });
    $('lock-run').addEventListener('click', lockRun);
    $('create-plan').addEventListener('click', createPlan);
    $('planning-gaps-recompute').addEventListener('click', previewPlanningGaps);
    $('erase-planning-gaps').addEventListener('click', erasePlanningGaps);
    $('lock-planning-gaps').addEventListener('click', lockPlanningGaps);
    document
      .querySelectorAll('.gap-mode-radio')
      .forEach((r) => r.addEventListener('change', updateGapModeVisibility));
    updateGapModeVisibility();
    $('gap-upload-button').addEventListener('click', uploadBuildingsFile);
    $('map-exclude-button').addEventListener('click', excludeSelectedWorkAreas);
    $('isolation-distance-m').addEventListener(
      'input',
      scheduleIsolationPreview,
    );
    $('lock-isolation-filter').addEventListener('click', lockIsolationFilter);
    // pollOrEvaluate() (below) already triggers Step 3's own initial
    // preview when Step 2 is locked but Step 3 isn't yet -- see its own
    // success handler -- so there's nothing extra to kick off here.
    initTooltips();
    showLoadingPanel(
      'Loading work-area, visit, and geometry data for this opportunity…',
    );
    pollOrEvaluate();
  }

  return { init };
})();
