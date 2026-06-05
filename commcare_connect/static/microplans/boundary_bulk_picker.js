/*
 * BoundaryBulkPicker — pick MULTIPLE admin boundaries on a map and create one
 * boundary-only ward-plan per selection, filed into a study (group).
 *
 * Reuses the existing standalone layers wholesale — it does NOT re-implement map
 * interaction:
 *   - MicroplansAdminBoundaries (multi-select; renders its own soft highlight via
 *     its SEL_FILL/SEL_LINE layers; calls back onAreaAdd/onAreaRemove).
 *   - MicroplansServiceDelivery (one-or-more opps' delivery points, visual context).
 *   - MicroplansMapPanel (the Layers/Inspect registry both layers mount into).
 *
 * It owns only: the selected-boundary set (geometry + admin labels for the POST),
 * a small footer ("N wards selected" + "Create N plans"), and the bulk POST.
 *
 * Host contract:
 *   BoundaryBulkPicker.create({
 *     map,              // a mapboxgl.Map (host-owned)
 *     panel,            // a MicroplansMapPanel instance (host-owned)
 *     mount,            // element to render the footer into
 *     csrf,             // CSRF token string
 *     sdPickerEl,       // the #sd-picker element (Alpine multi-opp picker) or null
 *     urls: {
 *       bulk_create,                       // POST target
 *       boundary_viewport, geometry, areas,// admin-boundaries layer urls
 *       sd_preview, sd_pipelines, sd_derive// service-delivery layer urls (optional)
 *     },
 *     getCountryIso,    // () => iso|null (optional; helps boundary fetch)
 *     onCreated,        // (plan_ids) => {}  host navigates back to the study
 *   }) -> controller { destroy(), selectedCount() }
 *
 * Designed to be embeddable: it takes an existing map + panel + mount, so the same
 * surface can later be dropped into a tabbed UI alongside the single-plan editor.
 */
(function (global) {
  'use strict';

  function create(opts) {
    const map = opts.map;
    const panel = opts.panel;
    const mount = opts.mount;
    const csrf = opts.csrf || '';
    const urls = opts.urls || {};
    const onCreated = opts.onCreated || function () {};
    const getCountryIso = opts.getCountryIso || (() => null);

    // boundaryId -> { name, lga, state, geometry, boundary_id }
    const selected = new Map();

    // ---- footer UI ----------------------------------------------------------
    const bar = document.createElement('div');
    bar.className = 'bbp-bar';
    bar.innerHTML =
      '<div class="bbp-count" data-testid="bbp-count">No wards selected</div>' +
      '<button type="button" class="bbp-create button button-sm primary-dark" ' +
      'data-testid="bbp-create" disabled>Create plans</button>' +
      '<div class="bbp-status" data-testid="bbp-status"></div>';
    mount.appendChild(bar);
    const countEl = bar.querySelector('.bbp-count');
    const createBtn = bar.querySelector('.bbp-create');
    const statusEl = bar.querySelector('.bbp-status');

    function labelsFromDesc(desc) {
      // desc carries {level, name, parent_name} (see admin_boundaries_layer.js).
      const d = desc || {};
      const name = String(d.name || '').trim() || 'Untitled ward';
      const lvl = Number(d.level || 0);
      const parent = String(d.parent_name || '').trim();
      // A ward is typically the most granular unit; its parent is the LGA. State is
      // derived server-side from the geometry when blank, so we leave it empty.
      const lga = lvl >= 3 ? parent || name : name;
      return { name, lga, state: '' };
    }

    function refresh() {
      const n = selected.size;
      countEl.textContent =
        n === 0
          ? 'No wards selected'
          : n + ' ward' + (n === 1 ? '' : 's') + ' selected';
      createBtn.disabled = n === 0;
      createBtn.textContent =
        n === 0
          ? 'Create plans'
          : 'Create ' + n + ' plan' + (n === 1 ? '' : 's');
    }

    // ---- register the layers (reuse — do not re-implement) -------------------
    let adminLayer = null;
    if (global.MicroplansAdminBoundaries && urls.boundary_viewport) {
      adminLayer = global.MicroplansAdminBoundaries.register({
        panel: panel,
        map: map,
        csrf: csrf,
        urls: {
          viewport: urls.boundary_viewport,
          geometry: urls.geometry,
          areas: urls.areas,
        },
        getCountryIso: getCountryIso,
        // This surface is always in the area-selection phase.
        isAreaPhase: () => true,
        onAreaAdd: (boundaryId, geometry, desc) => {
          const { name, lga, state } = labelsFromDesc(desc);
          selected.set(boundaryId, {
            name,
            lga,
            state,
            geometry,
            boundary_id: String(boundaryId),
          });
          refresh();
        },
        onAreaRemove: (boundaryId) => {
          selected.delete(boundaryId);
          refresh();
        },
      });
    }

    if (
      global.MicroplansServiceDelivery &&
      opts.sdPickerEl &&
      urls.sd_preview
    ) {
      global.MicroplansServiceDelivery.register({
        panel: panel,
        map: map,
        csrf: csrf,
        pickerEl: opts.sdPickerEl,
        urls: {
          preview: urls.sd_preview,
          pipelines: urls.sd_pipelines,
          derive: urls.sd_derive,
        },
        // Visual context only on this surface — a derived boundary is not wired to a
        // draw control here; the planner picks admin boundaries directly.
        onBoundary: () => {},
      });
    }

    // ---- create ------------------------------------------------------------
    async function createPlans() {
      if (!selected.size) return;
      createBtn.disabled = true;
      statusEl.textContent = 'Creating ' + selected.size + ' plan(s)…';
      const boundaries = Array.from(selected.values());
      try {
        const resp = await fetch(urls.bulk_create, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
          body: JSON.stringify({ boundaries }),
        });
        const data = await resp.json();
        if (!resp.ok || data.status !== 'ok') {
          statusEl.textContent =
            (data && data.detail) || 'Could not create the plans.';
          createBtn.disabled = false;
          return;
        }
        statusEl.textContent =
          'Created ' + (data.plan_ids || []).length + ' plan(s).';
        onCreated(data.plan_ids || []);
      } catch (e) {
        statusEl.textContent = 'Network error creating plans.';
        createBtn.disabled = false;
      }
    }
    createBtn.addEventListener('click', createPlans);

    refresh();

    return {
      selectedCount: () => selected.size,
      destroy() {
        createBtn.removeEventListener('click', createPlans);
        if (bar.parentNode) bar.parentNode.removeChild(bar);
        if (adminLayer && typeof adminLayer.destroy === 'function')
          adminLayer.destroy();
      },
    };
  }

  global.BoundaryBulkPicker = { create };
})(window);
