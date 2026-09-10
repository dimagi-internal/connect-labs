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
      'How far the number of children actually served (approved Health Service Delivery visits) falls short of the expected number for this work area. A low ratio can mean the area was under-delivered — or that the expected-count estimate itself was too high for this specific cell.',
    ncf_inaccessible_rate:
      "The share of visits to this work area that came back 'No Children Found' or 'Inaccessible' instead of a completed service visit. A high rate can mean the area genuinely has few children — or that it was skipped.",
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
  let severitySortDesc = true;

  // One flat table row per indicator now (design mockup, 2026-09) — EVC
  // shortfall and NCF/inaccessible each get their own Neighbor distance/Min
  // neighbor count cells; deworming/MUAC/vaccination share a single pair
  // rendered as one rowspan=3 cell spanning their three rows. Everything
  // else (Include-not-visited, WA min EVC count, WA min HSD-visits, Min
  // building count) lives in the three scoped cards below the table
  // (see analysis.html) since those aren't naturally table columns.
  const TIER2_KEYS = ['deworming', 'muac', 'vaccination'];

  function neighborCellsHtml(def, rowDisabled, tier2State) {
    if (TIER2_KEYS.includes(def.key)) {
      if (tier2State.rendered) return '';
      tier2State.rendered = true;
      // Deliberately no .ind-neighbor-distance/.ind-neighbor-count classes
      // here -- this shared cell's disabled state is driven ONLY by the
      // global filter (handled separately below), never by any one of the
      // three rows' own "On" state, since it belongs to all three at once.
      return `
          <td class="py-2 pr-2 align-middle" rowspan="3">
            <input type="number" id="cfg-tier2-neighbor-distance" class="base-input" style="width:5rem" min="1">
            <div class="text-[11px] text-gray-500 mt-1">Applies to deworming, MUAC, vaccination</div>
          </td>
          <td class="py-2 pr-2 align-middle" rowspan="3">
            <input type="number" id="cfg-tier2-min-neighbor-count" class="base-input" style="width:5rem" min="1">
          </td>`;
    }
    const distanceId =
      def.key === 'evc_shortfall'
        ? 'cfg-evc-neighbor-distance'
        : 'cfg-ncf-neighbor-distance';
    const countId =
      def.key === 'evc_shortfall'
        ? 'cfg-evc-min-neighbor-count'
        : 'cfg-min-affected-neighbors-ncf';
    return `
          <td class="py-2 pr-2"><input type="number" id="${distanceId}" class="base-input ind-neighbor-distance" style="width:5rem" min="1" ${
            rowDisabled ? 'disabled' : ''
          }></td>
          <td class="py-2 pr-2"><input type="number" id="${countId}" class="base-input ind-neighbor-count" style="width:5rem" min="1" ${
            rowDisabled ? 'disabled' : ''
          }></td>`;
  }

  function renderIndicatorRows() {
    const tb = $('indicator-rows');
    const tier2State = { rendered: false };
    tb.innerHTML = indicatorDefs
      .map((def) => {
        const cfg = indicatorConfigs[def.key] || {
          enabled: true,
          threshold: 0.5,
        };
        const isNcf = def.key === 'ncf_inaccessible_rate';
        const rowDisabled = !cfg.enabled;
        const thresholdCell = isNcf
          ? `<span class="text-gray-400 italic">n/a</span>`
          : `<input type="number" step="0.01" min="0" max="1" class="ind-threshold base-input" style="width:6rem" value="${
              cfg.threshold
            }" ${rowDisabled ? 'disabled' : ''}>`;
        return `<tr class="border-b border-gray-50 ${
          rowDisabled ? 'opacity-50' : ''
        }" data-key="${def.key}">
          <td class="py-2 pr-2"><input type="checkbox" class="ind-enabled" ${
            cfg.enabled ? 'checked' : ''
          }></td>
          <td class="py-2 pr-2">${esc(
            def.label,
          )} <span class="info-icon" tabindex="0" data-tip="${esc(
            INDICATOR_TOOLTIPS[def.key] || '',
          )}">ⓘ</span></td>
          <td class="py-2 pr-2">${thresholdCell}</td>${neighborCellsHtml(
            def,
            rowDisabled,
            tier2State,
          )}
          <td class="py-2 pr-2 ind-trigger-count">—</td>
        </tr>`;
      })
      .join('');
    applyIndicatorRowStates();
  }

  function renderIndicatorCounts(counts) {
    counts = counts || {};
    document.querySelectorAll('#indicator-rows tr').forEach((tr) => {
      const cell = tr.querySelector('.ind-trigger-count');
      if (cell) cell.textContent = counts[tr.dataset.key] ?? '—';
    });
  }

  // Two independent greying rules, applied together: a row's own "On"
  // checkbox greys its Threshold + (for EVC/NCF) its own neighbor inputs;
  // the global cluster-aware toggle greys EVERY neighbor distance/count
  // input (including the shared deworming/MUAC/vaccination cell) regardless
  // of individual row state. Re-run after any relevant checkbox changes,
  // never on a full re-render, so in-progress edits/focus aren't lost.
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
  }

  function renderGlobalConfig() {
    $('cfg-include-not-visited').checked =
      !!globalConfig.include_not_yet_visited;
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
        ...(thresholdInput
          ? { threshold: parseFloat(thresholdInput.value) || 0 }
          : {}),
      };
    });
    return out;
  }

  function collectGlobalConfig() {
    return {
      include_not_yet_visited: $('cfg-include-not-visited').checked,
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

  function renderWardSummary(rows, gapSummaryByWard) {
    gapSummaryByWard = gapSummaryByWard || {};
    $('ward-summary-rows').innerHTML = rows
      .map((r) => {
        const gap = gapSummaryByWard[wardRowKey(r)];
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
        if (!gap) return mainRow;
        // A distinct, muted sub-row directly under the ward's own row rather
        // than more columns — Step 2's new work areas are additional to,
        // not part of, the Connect-sourced totals above.
        const gapRow = `<tr class="border-b border-gray-100 bg-emerald-50 text-emerald-800 text-xs">
          <td class="p-2 pl-2" colspan="7">+ Planning gaps (new)</td>
          <td class="p-2 ward-col-new ward-group-start">${gap.gap_wa_count}</td>
          <td class="p-2 ward-col-new">${gap.gap_buildings}</td>
          <td class="p-2 ward-col-new">${gap.gap_evc}</td>
          <td class="p-2 ward-col-new">—</td>
        </tr>`;
        return mainRow + gapRow;
      })
      .join('');
  }

  const INDICATOR_LABELS = {
    evc_shortfall: 'EVC shortfall',
    ncf_inaccessible_rate: 'NCF / inaccessible',
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
        if (key === 'ncf_inaccessible_rate') {
          const signals = [];
          if (detail.own_ncf_form) signals.push('NCF visit');
          if (detail.own_inaccessible_form) signals.push('Inaccessible visit');
          const signal = signals.join(' + ') || 'affected';
          return esc(`${label} (affected — ${signal})`);
        }
        const rate = detail.rate;
        const num = detail.own_numerator;
        const denom = detail.own_denominator;
        if (rate == null || num == null || denom == null) return esc(label);
        return esc(`${label} (${rate.toFixed(2)}; ${num}/${denom})`);
      })
      .join(', ');
  }

  function candidateRowHtml(c, extraClass) {
    return `<tr class="border-b border-gray-50 ${extraClass || ''}">
          <td class="p-2">${esc(c.ward)}</td><td class="p-2">${esc(
            c.lga,
          )}</td><td class="p-2">${esc(c.state)}</td>
          <td class="p-2">${esc(c.flw_username)}</td>
          <td class="p-2">${c.building_count}</td><td class="p-2">${
            c.expected_visit_count
          }</td>
          <td class="p-2">${c.tier ? `Tier ${c.tier}` : '—'}</td>
          <td class="p-2" title="${esc(SEVERITY_TOOLTIP)}">${
            c.severity_count
          }</td>
          <td class="p-2">${triggeredIndicatorDisplay(c)}</td>
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
  }

  // ---------------------------------------------------------------------
  // Map — reuses the same shared PlanLayers module microplans' own review
  // page draws work areas with (static/maps/plan_layers.js), so mopup's map
  // looks and behaves identically rather than reimplementing layer paint.
  // ---------------------------------------------------------------------

  const INDICATOR_COLORS = {
    evc_shortfall: '#ef4444',
    ncf_inaccessible_rate: '#f97316',
    deworming: '#eab308',
    muac: '#8b5cf6',
    vaccination: '#06b6d4',
  };
  const NOT_INCLUDED_LABEL = 'Not included';

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
  const UPLOADED_BUILDING_COLOR = '#a855f7';

  // PlanLayers.workAreas draws polygon fill/line layers — uploaded-building
  // Points (Step 2's "upload your own" mode) render as a separate circle
  // layer instead (see renderBuildingPoints), so they're filtered out here.
  function styleMapFeatures(fc) {
    return {
      type: 'FeatureCollection',
      features: (fc?.features || [])
        .filter((f) => f.properties.source !== 'uploaded_building')
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
        (f) => f.properties.source === 'uploaded_building',
      ),
    };
  }

  function swatch(color, label) {
    return `<span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${color};margin-right:4px;"></span>${esc(
      label,
    )}</span>`;
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
    const swatches = Object.keys(INDICATOR_COLORS)
      .filter((key) => present.has(key))
      .map((key) =>
        swatch(INDICATOR_COLORS[key], INDICATOR_LABELS[key] || key),
      );
    swatches.push(swatch('#9ca3af', NOT_INCLUDED_LABEL));
    if (features.some((f) => f.properties.source === 'planning_gap')) {
      swatches.push(swatch(GAP_FILL_COLOR, 'Planning gap (new)'));
    }
    if (features.some((f) => f.properties.source === 'uploaded_building')) {
      swatches.push(swatch(UPLOADED_BUILDING_COLOR, 'Uploaded buildings'));
    }
    $('map-legend').innerHTML = swatches.join('');
  }

  // A separate circle layer, not PlanLayers.workAreas (which only draws
  // polygons) — the real building positions behind Step 2's "upload your
  // own" gap-fill cells, not just the gridded cells themselves.
  function renderBuildingPoints(fc) {
    window.PlanLayers.setSource(map, 'mopup-uploaded-buildings', fc);
    if (!map.getLayer('mopup-uploaded-buildings-circles')) {
      map.addLayer({
        id: 'mopup-uploaded-buildings-circles',
        type: 'circle',
        source: 'mopup-uploaded-buildings',
        paint: {
          'circle-radius': 3,
          'circle-color': UPLOADED_BUILDING_COLOR,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#ffffff',
        },
      });
    }
  }

  let lastMapFeatures = null;

  function renderMap(rawMapFeatures) {
    lastMapFeatures = rawMapFeatures;
    if (!map || !mapReady) return;
    const styled = styleMapFeatures(rawMapFeatures);
    window.PlanLayers.workAreas(map, { data: styled, promoteId: 'wa_id' });
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

  function initMap(mapboxToken) {
    const el = $('analysis-map');
    if (!el || !window.mapboxgl || !mapboxToken) return;
    mapboxgl.accessToken = mapboxToken;
    try {
      map = new mapboxgl.Map({
        container: 'analysis-map',
        style: 'mapbox://styles/mapbox/light-v11',
        center: [0, 0],
        zoom: 1,
      });
    } catch (e) {
      return; // headless / no WebGL
    }
    // General safeguard beyond the one-shot showReady() resize — covers a
    // browser-window resize, and any other layout shift of the container
    // after construction.
    if (window.ResizeObserver) {
      new ResizeObserver(() => map && map.resize()).observe(el);
    }
    map.on('load', () => {
      mapReady = true;
      if (wardBoundariesData.features.length) {
        window.PlanLayers.setSource(
          map,
          'mopup-ward-boundaries',
          wardBoundariesData,
        );
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
      // The first data poll can complete before Mapbox's own 'load' fires
      // (e.g. a backgrounded tab throttles its render loop) — renderMap()
      // would have already returned early in that race, so replay the
      // last-known data now that the map can actually take layers.
      if (lastMapFeatures) renderMap(lastMapFeatures);
    });
  }

  let pollTimer = null;
  let dataReady = false;

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

      // status === 'ok'
      dataReady = true;
      showReady();
      lastCandidates = data.candidates || [];
      lastGapCandidates = data.gap_candidates || [];
      $('live-count').textContent = data.candidate_count;
      renderWardSummary(data.ward_summary || [], data.gap_summary_by_ward);
      renderCandidates();
      renderIndicatorCounts(data.per_indicator_counts);
      renderMap(data.map_features);
      $(
        'status',
      ).textContent = `${data.total_work_areas} work area(s) evaluated.`;
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
      $(
        'status',
      ).textContent = `Locked ${data.locked_count} candidate work area(s).`;
      $('lock-run').disabled = true;
      $('lock-run').textContent = `Locked (${data.locked_count} WAs)`;
      $('create-plan').disabled = false;
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

      // status === 'ok'
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

  // Step 2's three modes show different controls: Overture's source/
  // confidence pickers, the upload form, or (for "skip") none of the
  // building-source config at all — there's nothing to configure when no
  // new work areas will be added.
  function updateGapModeVisibility() {
    const mode = selectedGapMode();
    $('gap-mode-overture-controls').classList.toggle(
      'hidden',
      mode !== 'overture',
    );
    $('gap-mode-upload-controls').classList.toggle('hidden', mode !== 'upload');
    $('gap-mode-shared-controls').classList.toggle('hidden', mode === 'skip');
    $('planning-gaps-recompute').classList.toggle('hidden', mode === 'skip');
  }

  function collectPlanningGapsConfig() {
    return {
      mode: selectedGapMode(),
      building_sources: [
        ...document.querySelectorAll('.gap-src-cb:checked'),
      ].map((cb) => cb.value),
      min_confidence: parseFloat($('gap-cfg-min-confidence').value) || null,
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

      // status === 'ok'
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
      $(
        'gap-upload-status',
      ).textContent = `Uploaded: ${data.filename} (${data.matched_rows} row(s) matched this run's ward(s))`;
    } catch (e) {
      $('gap-upload-button').disabled = false;
      $('gap-upload-status').textContent = 'Failed to upload file.';
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
    renderIndicatorRows();
    renderGlobalConfig();
    applyIndicatorRowStates(); // re-apply now that the real filter state is loaded
    $('cfg-cluster-filter-enabled').addEventListener(
      'change',
      applyIndicatorRowStates,
    );
    $('indicator-rows').addEventListener('change', (e) => {
      if (e.target.classList.contains('ind-enabled')) applyIndicatorRowStates();
    });
    $('recompute').addEventListener('click', pollOrEvaluate);
    $('loading-retry').addEventListener('click', retryLoad);
    $('sort-severity').addEventListener('click', () => {
      severitySortDesc = !severitySortDesc;
      renderCandidates();
    });
    $('lock-run').addEventListener('click', lockRun);
    $('create-plan').addEventListener('click', createPlan);
    $('planning-gaps-recompute').addEventListener('click', previewPlanningGaps);
    document
      .querySelectorAll('.gap-mode-radio')
      .forEach((r) => r.addEventListener('change', updateGapModeVisibility));
    updateGapModeVisibility();
    $('gap-upload-button').addEventListener('click', uploadBuildingsFile);
    showLoadingPanel(
      'Loading work-area, visit, and geometry data for this opportunity…',
    );
    pollOrEvaluate();
  }

  return { init };
})();
