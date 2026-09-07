// Phase 1 setup screen: opportunity -> ward(s) -> optional date range -> create run.
// No visit-form data is touched here — only the cheap work-area case pull
// (see connect_labs/mopup/core/work_areas.py).
window.MopupSetup = (function () {
  function $(id) {
    return document.getElementById(id);
  }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': CFG.csrfToken,
      },
      body: JSON.stringify(body),
    });
  }

  let CFG = {};
  let wards = []; // last loaded ward summary rows
  const selectedWards = new Map(); // ward-key -> {ward,lga,state}

  function wardKey(w) {
    return [w.state, w.lga, w.ward].join('|');
  }

  function renderWards() {
    const list = $('ward-list');
    if (!wards.length) {
      list.innerHTML =
        '<div class="p-3 text-sm text-gray-400">No wards loaded yet.</div>';
      return;
    }
    list.innerHTML = wards
      .map((w) => {
        const key = wardKey(w);
        const checked = selectedWards.has(key) ? 'checked' : '';
        return `<label class="flex items-center gap-2 p-2 text-sm cursor-pointer hover:bg-gray-50">
          <input type="checkbox" data-key="${key}" class="ward-checkbox" ${checked}>
          <span class="flex-1">${w.ward} <span class="text-gray-400">(${w.lga}, ${w.state})</span></span>
          <span class="text-gray-400 text-xs">${w.work_area_count} WAs · ${w.building_count} buildings</span>
        </label>`;
      })
      .join('');
    list.querySelectorAll('.ward-checkbox').forEach((cb) => {
      cb.addEventListener('change', () => {
        const w = wards.find((x) => wardKey(x) === cb.dataset.key);
        if (cb.checked) selectedWards.set(cb.dataset.key, w);
        else selectedWards.delete(cb.dataset.key);
        refreshStartButton();
      });
    });
  }

  function refreshStartButton() {
    const oppId = $('opp-select').value;
    $('start-analysis').disabled = !oppId;
  }

  async function loadWards(refresh) {
    const oppId = $('opp-select').value;
    if (!oppId) return;
    $('ward-status').textContent = 'Loading work areas…';
    $('refresh-wards').disabled = true;
    try {
      const url =
        CFG.wardListUrl +
        '?opportunity_id=' +
        encodeURIComponent(oppId) +
        (refresh ? '&refresh=1' : '');
      const resp = await fetch(url);
      const data = await resp.json();
      if (!resp.ok || data.status !== 'ok') {
        $('ward-status').textContent =
          data.detail || 'Failed to load work areas.';
        return;
      }
      wards = data.wards || [];
      selectedWards.clear();
      $('ward-status').textContent = wards.length + ' ward(s) found.';
      renderWards();
    } catch (e) {
      $('ward-status').textContent = 'Failed to load work areas.';
    } finally {
      $('refresh-wards').disabled = false;
    }
  }

  async function startAnalysis() {
    const oppId = $('opp-select').value;
    if (!oppId) return;
    $('status').textContent = 'Creating run…';
    try {
      const resp = await post(CFG.createRunUrl, {
        opportunity_id: Number(oppId),
        wards: [...selectedWards.values()],
        date_from: $('date-from').value || null,
        date_to: $('date-to').value || null,
      });
      const data = await resp.json();
      if (!resp.ok || data.status !== 'ok') {
        $('status').textContent = data.detail || 'Failed to create run.';
        return;
      }
      $('status').textContent =
        'Run #' +
        data.run_id +
        ' created (' +
        (data.selected_wards.length || 'all') +
        ' ward(s) selected). Phase 2 analysis screen is not built yet.';
    } catch (e) {
      $('status').textContent = 'Failed to create run.';
    }
  }

  function init(cfg) {
    CFG = cfg;
    $('opp-select').addEventListener('change', () => {
      const has = !!$('opp-select').value;
      $('refresh-wards').disabled = !has;
      refreshStartButton();
      if (has) loadWards(false);
      else {
        wards = [];
        selectedWards.clear();
        renderWards();
      }
    });
    $('refresh-wards').addEventListener('click', () => loadWards(true));
    $('start-analysis').addEventListener('click', startAnalysis);
    renderWards();
  }

  return { init };
})();
