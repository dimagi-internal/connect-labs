// Phase 2 analysis screen: per-indicator thresholds/granularity + global
// cluster settings, recomputed live against a run's already-loaded data via
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
  let severitySortDesc = true;

  function renderIndicatorRows() {
    const tb = $('indicator-rows');
    tb.innerHTML = indicatorDefs
      .map((def) => {
        const cfg = indicatorConfigs[def.key] || {
          enabled: true,
          threshold: 0.5,
          granularity: 'cluster_aware',
        };
        return `<tr class="border-b border-gray-50" data-key="${def.key}">
          <td class="py-2 pr-2"><input type="checkbox" class="ind-enabled" ${
            cfg.enabled ? 'checked' : ''
          }></td>
          <td class="py-2 pr-2">${esc(
            def.label,
          )} <span class="info-icon" tabindex="0" data-tip="${esc(
            INDICATOR_TOOLTIPS[def.key] || '',
          )}">ⓘ</span></td>
          <td class="py-2 pr-2">
            <input type="number" step="0.01" min="0" max="1" class="ind-threshold base-input" style="width:6rem" value="${
              cfg.threshold
            }">
          </td>
          <td class="py-2 pr-2">
            <select class="ind-granularity base-input" style="width:10rem">
              <option value="wa_only" ${
                cfg.granularity === 'wa_only' ? 'selected' : ''
              }>This WA only</option>
              <option value="cluster_aware" ${
                cfg.granularity === 'cluster_aware' ? 'selected' : ''
              }>Cluster-aware</option>
              <option value="flw_average" ${
                cfg.granularity === 'flw_average' ? 'selected' : ''
              }>Whole-FLW average</option>
            </select>
          </td>
          <td class="py-2 pr-2 ind-trigger-count">—</td>
        </tr>`;
      })
      .join('');
    updateNcfThresholdState();
  }

  // Cluster-aware NCF/inaccessible compares a raw affected-neighbor COUNT
  // (against "Min affected neighbors (NCF)") rather than this indicator's own
  // Threshold value, which has no effect in that mode — grey the Threshold
  // input out with an explanatory tooltip so it doesn't look like a live
  // control that's silently ignored.
  function updateNcfThresholdState() {
    const row = document.querySelector(
      '#indicator-rows tr[data-key="ncf_inaccessible_rate"]',
    );
    if (!row) return;
    const thresholdInput = row.querySelector('.ind-threshold');
    const granularitySelect = row.querySelector('.ind-granularity');
    const isClusterAware = granularitySelect.value === 'cluster_aware';
    thresholdInput.disabled = isClusterAware;
    thresholdInput.title = isClusterAware
      ? 'Not used under Cluster-aware — see "Min affected neighbors (NCF)" below instead.'
      : '';
    thresholdInput.classList.toggle('opacity-40', isClusterAware);
  }

  function renderIndicatorCounts(counts) {
    counts = counts || {};
    document.querySelectorAll('#indicator-rows tr').forEach((tr) => {
      const cell = tr.querySelector('.ind-trigger-count');
      if (cell) cell.textContent = counts[tr.dataset.key] ?? '—';
    });
  }

  function renderGlobalConfig() {
    $('cfg-neighbor-distance').value = globalConfig.neighbor_distance_m;
    $('cfg-min-neighbors').value = globalConfig.min_neighbor_count;
    $('cfg-min-portfolio').value = globalConfig.min_neighborhood_size;
    $('cfg-min-hsd').value = globalConfig.min_hsd_visits_floor;
    $('cfg-min-buildings').value = globalConfig.min_building_count;
    $('cfg-min-affected-neighbors-ncf').value =
      globalConfig.min_affected_neighbors_ncf;
    $('cfg-include-not-visited').checked =
      !!globalConfig.include_not_yet_visited;
  }

  function collectIndicatorConfigs() {
    const out = {};
    document.querySelectorAll('#indicator-rows tr').forEach((tr) => {
      const key = tr.dataset.key;
      out[key] = {
        enabled: tr.querySelector('.ind-enabled').checked,
        threshold: parseFloat(tr.querySelector('.ind-threshold').value) || 0,
        granularity: tr.querySelector('.ind-granularity').value,
      };
    });
    return out;
  }

  function collectGlobalConfig() {
    return {
      neighbor_distance_m: parseFloat($('cfg-neighbor-distance').value) || 0,
      min_neighbor_count: parseInt($('cfg-min-neighbors').value, 10) || 0,
      min_neighborhood_size: parseInt($('cfg-min-portfolio').value, 10) || 0,
      min_hsd_visits_floor: parseInt($('cfg-min-hsd').value, 10) || 0,
      min_building_count: parseInt($('cfg-min-buildings').value, 10) || 0,
      min_affected_neighbors_ncf:
        parseInt($('cfg-min-affected-neighbors-ncf').value, 10) || 0,
      include_not_yet_visited: $('cfg-include-not-visited').checked,
    };
  }

  function renderWardSummary(rows) {
    $('ward-summary-rows').innerHTML = rows
      .map(
        (r) => `<tr class="border-b border-gray-50">
          <td class="p-2">${esc(r.ward)}</td><td class="p-2">${esc(
            r.lga,
          )}</td><td class="p-2">${esc(r.state)}</td>
          <td class="p-2">${r.total_work_areas}</td>
          <td class="p-2">${r.total_buildings}</td><td class="p-2">${
            r.total_evc
          }</td>
          <td class="p-2">${r.candidate_count}</td>
          <td class="p-2">${r.candidate_buildings}</td><td class="p-2">${
            r.candidate_evc
          }</td>
          <td class="p-2">${r.flagged_by_2_plus}</td>
        </tr>`,
      )
      .join('');
  }

  const INDICATOR_LABELS = {
    evc_shortfall: 'EVC shortfall',
    ncf_inaccessible_rate: 'NCF / inaccessible rate',
    deworming: 'Deworming completion',
    muac: 'MUAC-recorded rate',
    vaccination: 'Vaccination-given rate',
  };

  function triggeredIndicatorDisplay(c) {
    return c.triggered_indicators
      .map((key) => {
        const label = INDICATOR_LABELS[key] || key;
        const detail = (c.detail || {})[key] || {};
        const rate = detail.rate;
        const num = detail.own_numerator;
        const denom = detail.own_denominator;
        if (rate == null || num == null || denom == null) return esc(label);
        return esc(`${label} (${rate.toFixed(2)}; ${num}/${denom})`);
      })
      .join(', ');
  }

  function renderCandidates() {
    const rows = [...lastCandidates].sort((a, b) =>
      severitySortDesc
        ? b.severity_count - a.severity_count
        : a.severity_count - b.severity_count,
    );
    $('candidate-rows').innerHTML = rows
      .map(
        (c) => `<tr class="border-b border-gray-50">
          <td class="p-2">${esc(c.ward)}</td><td class="p-2">${esc(
            c.lga,
          )}</td><td class="p-2">${esc(c.state)}</td>
          <td class="p-2">${esc(c.flw_username)}</td>
          <td class="p-2">${c.building_count}</td><td class="p-2">${
            c.expected_visit_count
          }</td>
          <td class="p-2" title="${esc(SEVERITY_TOOLTIP)}">${
            c.severity_count
          }</td>
          <td class="p-2">${triggeredIndicatorDisplay(c)}</td>
        </tr>`,
      )
      .join('');
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
      $('live-count').textContent = data.candidate_count;
      renderWardSummary(data.ward_summary || []);
      renderCandidates();
      renderIndicatorCounts(data.per_indicator_counts);
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
    } catch (e) {
      $('status').textContent = 'Failed to lock.';
    }
  }

  let createPlanPollTimer = null;

  // Offloaded to a Celery task server-side (a real include_planning_gaps
  // hand-off can take well over a minute) — this polls the SAME endpoint
  // every 2s, mirroring pollOrEvaluate's dispatch-once/poll-many pattern,
  // until it reports a terminal status.
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
        body: JSON.stringify({
          include_planning_gaps: $('include-planning-gaps').checked,
        }),
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

  function init(cfg) {
    CFG = cfg;
    indicatorDefs = JSON.parse($('indicator-defs-data').textContent);
    indicatorConfigs = JSON.parse($('indicator-configs-data').textContent);
    globalConfig = JSON.parse($('global-config-data').textContent);
    renderIndicatorRows();
    renderGlobalConfig();
    $('indicator-rows').addEventListener('change', (e) => {
      if (e.target.classList.contains('ind-granularity'))
        updateNcfThresholdState();
    });
    $('recompute').addEventListener('click', pollOrEvaluate);
    $('loading-retry').addEventListener('click', retryLoad);
    $('sort-severity').addEventListener('click', () => {
      severitySortDesc = !severitySortDesc;
      renderCandidates();
    });
    $('lock-run').addEventListener('click', lockRun);
    $('create-plan').addEventListener('click', createPlan);
    showLoadingPanel(
      'Loading work-area, visit, and geometry data for this opportunity…',
    );
    pollOrEvaluate();
  }

  return { init };
})();
