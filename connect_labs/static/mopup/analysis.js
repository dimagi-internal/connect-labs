// Phase 2 analysis screen: per-indicator thresholds/granularity + global
// cluster settings, recomputed live against a run's already-loaded data via
// MopupCandidatesView. Nothing here is "locked" — the lock/hand-off action
// (Phase 2 -> Phase 3) isn't built yet.
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
  const GRANULARITY_TOOLTIP =
    "How this indicator is evaluated: 'This WA only' flags a single work area on its own number. 'Cluster-aware' (recommended) only flags it if nearby work areas — or, for data-quality metrics, this FLW's other work areas — are also elevated, filtering out one-off noise. 'Whole-FLW average' rolls up all of an FLW's work areas into one number before comparing to the threshold.";
  const THRESHOLD_TOOLTIP =
    'The cutoff at which a work area is flagged as a mop-up candidate for this indicator. Set independently per indicator.';
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
          <td class="py-2 pr-2" title="${esc(
            INDICATOR_TOOLTIPS[def.key] || '',
          )}">${esc(def.label)} ⓘ</td>
          <td class="py-2 pr-2" title="${esc(THRESHOLD_TOOLTIP)}">
            <input type="number" step="0.01" min="0" max="1" class="ind-threshold base-input" style="width:6rem" value="${
              cfg.threshold
            }">
          </td>
          <td class="py-2 pr-2" title="${esc(GRANULARITY_TOOLTIP)}">
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
        </tr>`;
      })
      .join('');
  }

  function renderGlobalConfig() {
    $('cfg-neighbor-distance').value = globalConfig.neighbor_distance_m;
    $('cfg-min-neighbors').value = globalConfig.min_neighbor_count;
    $('cfg-min-portfolio').value = globalConfig.min_neighborhood_size;
    $('cfg-min-hsd').value = globalConfig.min_hsd_visits_floor;
    $('cfg-min-buildings').value = globalConfig.min_building_count;
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
          <td class="p-2">${r.total_work_areas}</td><td class="p-2">${
            r.candidate_count
          }</td><td class="p-2">${r.flagged_by_2_plus}</td>
        </tr>`,
      )
      .join('');
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
          <td class="p-2" title="${esc(SEVERITY_TOOLTIP)}">${
            c.severity_count
          }</td>
          <td class="p-2">${c.triggered_indicators.map(esc).join(', ')}</td>
        </tr>`,
      )
      .join('');
  }

  async function recompute() {
    indicatorConfigs = collectIndicatorConfigs();
    globalConfig = collectGlobalConfig();
    $('status').textContent = 'Recomputing…';
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
      if (!resp.ok || data.status !== 'ok') {
        $('status').textContent = data.detail || 'Failed to recompute.';
        return;
      }
      lastCandidates = data.candidates || [];
      $('live-count').textContent = data.candidate_count;
      renderWardSummary(data.ward_summary || []);
      renderCandidates();
      $(
        'status',
      ).textContent = `${data.total_work_areas} work area(s) evaluated.`;
    } catch (e) {
      $('status').textContent = 'Failed to recompute.';
    }
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
        $('status').textContent = data.detail || 'Failed to lock.';
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

  async function createPlan() {
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
      if (!resp.ok || data.status !== 'ok') {
        $('status').textContent = data.detail || 'Failed to create plan.';
        return;
      }
      $('status').textContent = 'Plan created — opening review…';
      if (data.urls && data.urls.review)
        window.location.href = data.urls.review;
    } catch (e) {
      $('status').textContent = 'Failed to create plan.';
    }
  }

  function init(cfg) {
    CFG = cfg;
    indicatorDefs = JSON.parse($('indicator-defs-data').textContent);
    indicatorConfigs = JSON.parse($('indicator-configs-data').textContent);
    globalConfig = JSON.parse($('global-config-data').textContent);
    renderIndicatorRows();
    renderGlobalConfig();
    $('recompute').addEventListener('click', recompute);
    $('sort-severity').addEventListener('click', () => {
      severitySortDesc = !severitySortDesc;
      renderCandidates();
    });
    $('lock-run').addEventListener('click', lockRun);
    $('create-plan').addEventListener('click', createPlan);
    if (!CFG.locked) recompute();
  }

  return { init };
})();
