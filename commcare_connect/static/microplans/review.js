(function () {
  // Django context injected by review.html via window.MP_REVIEW —
  // keeps template vars in the template; this stays plain, lintable JS.
  const CFG = window.MP_REVIEW || {};
  const TOKEN = CFG.mapbox_token;
  const CSRF = CFG.csrf_token;
  // Country (alpha-3) to seed the boundary layer/search on a cold new-plan page,
  // derived server-side from the program's footprint ("" when unknown → the layer
  // auto-detects from whatever boundaries load once the user navigates).
  const OPP_COUNTRY_ISO = CFG.map_country_iso;
  // Plan-scoped URLs. `let` (not const) because create-in-place adopts the new
  // plan's URLs after a fresh Generate plan, without a page reload.
  let PLAN_URL = CFG.plan_url;
  let EDIT_URL = CFG.edit_url;
  let CSV_URL = CFG.csv_url;
  const COMPARE_URL = CFG.compare_url;
  let FOOTPRINTS_URL = CFG.footprints_url;
  const PREVIEW_FOOTPRINTS_URL = CFG.preview_footprints_url;
  let REGROUP_URL = CFG.regroup_url;
  let REASSIGN_URL = CFG.reassign_url;
  let REGENERATE_URL = CFG.regenerate_url;
  const PREVIEW_COVERAGE_URL = CFG.preview_coverage_url;
  const PREVIEW_FRAME_URL = CFG.preview_frame_url;
  const COMPARABILITY_URL = CFG.arm_comparability_url;
  const COMPARE_SURROUNDING_URL = CFG.compare_surrounding_url;
  // Sampling-mode state (two-arm rooftop study). Declared early so the
  // service-delivery derive-boundary handler can tag derived polys to the arm.
  let mpMode = 'coverage'; // "coverage" | "sampling"
  let currentArm = 'intervention'; // "intervention" | "comparison"
  let lastSample = null; // { pins, hulls } from the most recent Generate
  const ARM_COLOR = { intervention: '#10b981', comparison: '#3b82f6' };
  const COUNTRIES_URL = CFG.countries_url;
  const BOUNDARY_VIEWPORT_URL = CFG.boundary_viewport_url;
  const SD_PREVIEW_URL = CFG.preview_service_delivery_url;
  const SD_PIPELINES_URL = CFG.service_delivery_pipelines_url;
  const DERIVE_BOUNDARY_URL = CFG.derive_boundary_url;
  const ADMIN_AREAS_URL = CFG.admin_areas_url;
  const ADMIN_AREA_GEOMETRY_URL = CFG.admin_area_geometry_url;
  const CREATE_PLAN_URL = CFG.create_plan_url;
  // When the editor is opened from a group page (?group=<id>), the created plan
  // files into that group — see ProgramCreatePlanView's group_id handling.
  const GROUP_ID = new URLSearchParams(location.search).get('group');
  const PROGRAM_URL = CFG.program_url;
  // `let`: create-in-place sets this to the new plan's id after Generate plan.
  let PLAN_ID = CFG.plan_id;
  // No plan yet → many widgets are absent from the DOM. Guarded everywhere.
  const compareLink = document.getElementById('compare-link');
  if (compareLink) compareLink.href = COMPARE_URL;
  const $ = (id) => document.getElementById(id);

  // Connect-import auto-fill. Picking an admin boundary carries its canonical
  // level (1=state, 2=LGA/county, 3=ward) + parent_name, so we can fill the
  // LGA/State the Connect CSV requires instead of making the user retype them.
  // Only fills BLANK fields (never clobbers a manual edit), and finally makes
  // the plan-name "auto-fill from picked area" real.
  function _setIfEmpty(id, val) {
    const el = $(id);
    if (el && !String(el.value || '').trim() && val) el.value = val;
  }
  // `desc` is the admin-boundary descriptor from admin_boundaries_layer.js:
  // { name, level (canonical 1/2/3), parent_name, source, ... }.
  function autofillFromBoundary(desc) {
    const d = desc || {};
    const lvl = Number(d.level || 0);
    const name = String(d.name || '').trim();
    const parent = String(d.parent_name || '').trim();
    if (lvl === 1) {
      _setIfEmpty('inp-plan-state', name);
    } else if (lvl === 2) {
      _setIfEmpty('inp-plan-region', name);
      _setIfEmpty('inp-plan-state', parent);
    } else if (lvl >= 3) {
      _setIfEmpty('inp-plan-region', parent || name);
    }
    _setIfEmpty('inp-plan-name', name || parent);
    updateConnectImportSummary();
  }
  function updateConnectImportSummary() {
    const r = String($('inp-plan-region')?.value || '').trim();
    const s = String($('inp-plan-state')?.value || '').trim();
    const el = $('ci-summary');
    if (el)
      el.textContent =
        r || s ? [r, s].filter(Boolean).join(' · ') : 'auto-filled from area';
  }
  ['inp-plan-region', 'inp-plan-state'].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener('input', updateConnectImportSummary);
  });

  Microplans.setCsrf(CSRF);
  const post = (url, body, opts) =>
    Microplans.post(url, body, Object.assign({ csrf: CSRF }, opts || {}));
  // The new-plan page shares this template with the per-plan review page;
  // many widgets (table, KPIs, dim sidebar) aren't rendered until a plan
  // exists. `on(id, evt, fn)` no-ops when the element is missing so all the
  // existing listener attachments stay one-liners without explicit guards.
  function on(id, evt, fn) {
    const el = $(id);
    if (el) el.addEventListener(evt, fn);
  }
  // Escape user-controlled values before innerHTML interpolation (group, worker,
  // reason are LLO-typed and could carry HTML).
  const esc = Microplans.esc;

  // Sample-details rail (per-arm stats / rationale / source counts / comparability)
  // lives in static/microplans/review/sample_details.js.
  const _sd = window.MPReview.sampleDetails({
    $: $,
    esc: esc,
    ARM_COLOR: ARM_COLOR,
    COMPARABILITY_URL: COMPARABILITY_URL,
    CSRF: CSRF,
  });
  const renderSourceCounts = _sd.renderSourceCounts;
  const renderArmStats = _sd.renderArmStats;
  const renderRationale = _sd.renderRationale;
  const updateComparability = _sd.updateComparability;

  let WAS = []; // current work areas (raw plan rows)
  const selected = new Set(); // selected wa ids
  let activeDim = null; // string | null — current click-to-filter value
  let colorDim = 'worker'; // "worker" | "group" — drives map color + sidebar
  const DIM_FIELD = { worker: 'opportunity_access', group: 'work_area_group' };

  // ---- color per assignment key (CHW name or group) -----------------------
  // Golden-angle HSL hash → adjacent territories visually distinct without a
  // palette lookup. Connect-gis uses the same trick on group_id.
  const colorFor = Microplans.colorFor;
  // The aggregation key: depends on which dimension the user is coloring by.
  function keyOf(w) {
    return (w[DIM_FIELD[colorDim]] || '').trim();
  }

  // ---- map (best-effort; needs WebGL) ----
  let map = null,
    mapReady = false;
  if (TOKEN) {
    mapboxgl.accessToken = TOKEN;
    try {
      map = new mapboxgl.Map({
        container: 'review-map',
        style: 'mapbox://styles/mapbox/satellite-streets-v12',
        center: [CFG.map_center_lng, CFG.map_center_lat],
        zoom: CFG.map_zoom,
      });
      map.on('load', () => {
        mapReady = true;
        refreshMap();
        drawSamplingOverlay();
      });
    } catch (e) {
      /* headless / no webgl */
    }
  }
  // Work-area fill/outline + opacity paint now lives in the shared PlanLayers
  // component (static/maps/plan_layers.js); fc() still builds the per-feature data.
  function fc() {
    // Pre-compute BOTH worker and group colors per feature so the fill and
    // outline encode different dimensions simultaneously. Whichever colorDim
    // is active becomes the fill; the OTHER one becomes the outline (bolder
    // stroke), so the user sees both at once.
    return {
      type: 'FeatureCollection',
      features: WAS.filter((w) => w.geometry).map((w) => {
        const workerColor = colorFor((w.opportunity_access || '').trim());
        const groupColor = colorFor((w.work_area_group || '').trim());
        const fill = colorDim === 'worker' ? workerColor : groupColor;
        const outline = colorDim === 'worker' ? groupColor : workerColor;
        return {
          type: 'Feature',
          id: w.id,
          geometry: w.geometry,
          properties: {
            id: w.id,
            status: w.status,
            group: w.work_area_group || '',
            worker: w.opportunity_access || '',
            fill,
            outline,
          },
        };
      }),
    };
  }
  // ---- hover tooltip on cells (cell ID, worker, group, counts, status) ----
  // ---- work-area inspector (replaces the old cursor-following Mapbox popup) ----
  // Hover = a light preview (work area only); plain click = pin the full
  // inspector (work area AND its group); Shift/⌘-click multi-selects work areas
  // and pins a BULK panel (count + aggregates + bulk actions). All render into
  // the map panel's Inspect tab — nothing floats over the map. `pinnedInspectFn`
  // (a thunk → html|node) survives hover so mouseout reverts to the pinned view.
  let pinnedInspectFn = null;
  let hoverWAId = null;
  const kvRow = (k, v) =>
    `<div style="display:flex;justify-content:space-between;gap:.6rem;padding:2px 0"><span style="color:#8a90a0">${k}</span><span style="font-weight:600;text-align:right">${v}</span></div>`;
  function groupStats(groupName) {
    if (!groupName) return null;
    const members = WAS.filter(
      (x) => (x.work_area_group || '').trim() === groupName,
    );
    if (!members.length) return null;
    const workers = Array.from(
      new Set(
        members.map((x) => (x.opportunity_access || '').trim()).filter(Boolean),
      ),
    );
    return {
      count: members.length,
      buildings: members.reduce((s, x) => s + (x.building_count || 0), 0),
      visits: members.reduce((s, x) => s + (x.expected_visit_count || 0), 0),
      workers,
    };
  }
  function waInspectHTML(w, includeGroup) {
    const worker = (w.opportunity_access || '').trim();
    const groupName = (w.work_area_group || '').trim();
    const status = (w.status || 'active').toLowerCase();
    const excluded = w.status === 'EXCLUDED';
    const workerHTML = worker
      ? esc(worker)
      : "<i style='color:#9ca3af'>unassigned</i>";
    const reason =
      excluded && w.excluded_reason
        ? `<div style="color:#dc2626;margin-top:3px">${esc(
            w.excluded_reason,
          )}</div>`
        : '';
    let html = `<div style="font-size:.72rem;line-height:1.45">
      <div style="font-weight:700;font-family:ui-monospace,monospace;margin-bottom:4px">${esc(
        w.id,
      )}</div>
      ${kvRow('worker', workerHTML)}
      ${kvRow('group', groupName ? esc(groupName) : '—')}
      ${kvRow('buildings', (w.building_count || 0).toLocaleString())}
      ${kvRow(
        'expected visits',
        (w.expected_visit_count || 0).toLocaleString(),
      )}
      ${kvRow(
        'status',
        `<span style="color:${excluded ? '#dc2626' : '#16a34a'}">${esc(
          status,
        )}</span>`,
      )}
      ${reason}`;
    const gs = includeGroup ? groupStats(groupName) : null;
    if (gs) {
      html += `<div style="margin-top:8px;padding-top:7px;border-top:1px solid #eceef1">
        <div style="font-weight:700;margin-bottom:4px">${esc(
          groupName,
        )} <span style="color:#9ca3af;font-weight:500">· its group</span></div>
        ${kvRow('work areas', gs.count)}
        ${kvRow('buildings', gs.buildings.toLocaleString())}
        ${kvRow('expected visits', gs.visits.toLocaleString())}
        ${kvRow(
          'worker' + (gs.workers.length > 1 ? 's' : ''),
          gs.workers.length ? esc(gs.workers.join(', ')) : '—',
        )}
      </div>`;
    }
    return html + '</div>';
  }
  function inspectWA(id, pin) {
    if (!mapPanel) return;
    const w = WAS.find((x) => x.id === id);
    if (!w) return;
    if (pin) pinnedInspectFn = () => waInspectHTML(w, true); // pin the single WA+group view
    mapPanel.setInspect(waInspectHTML(w, pin)); // switches to the Inspect tab
  }
  // Bulk panel for a multi-selection (Shift/⌘-click). Aggregates + bulk actions
  // that reuse the same edit() endpoint as the rail's "Bulk (selected)" section.
  function bulkInspectNode(ids) {
    const members = WAS.filter((w) => ids.includes(w.id));
    const buildings = members.reduce((s, w) => s + (w.building_count || 0), 0);
    const visits = members.reduce(
      (s, w) => s + (w.expected_visit_count || 0),
      0,
    );
    const groups = new Set(
      members.map((w) => (w.work_area_group || '').trim()).filter(Boolean),
    ).size;
    const workers = new Set(
      members.map((w) => (w.opportunity_access || '').trim()).filter(Boolean),
    ).size;
    const cell = (v, k) =>
      `<div style="background:#f7f8fb;border:1px solid #eceef1;border-radius:6px;padding:.4rem .5rem"><div style="font-weight:700;font-size:.85rem">${v}</div><div style="font-size:.58rem;color:#8a90a0;text-transform:uppercase;letter-spacing:.04em">${k}</div></div>`;
    const wrap = document.createElement('div');
    wrap.style.fontSize = '.72rem';
    wrap.innerHTML = `
      <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.45rem">
        <span style="font-weight:800;font-size:1rem;color:#2d36b3">${
          ids.length
        }</span>
        <span style="font-weight:600;color:#555">work areas selected</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.4rem;margin-bottom:.35rem">${cell(
        buildings.toLocaleString(),
        'buildings',
      )}${cell(visits.toLocaleString(), 'expected visits')}</div>
      <div style="display:flex;justify-content:space-between;padding:2px 0"><span style="color:#8a90a0">groups spanned</span><b>${groups}</b></div>
      <div style="display:flex;justify-content:space-between;padding:2px 0"><span style="color:#8a90a0">workers spanned</span><b>${workers}</b></div>
      <div class="bulk-acts" style="display:grid;gap:.35rem;margin-top:.55rem"></div>`;
    const acts = wrap.querySelector('.bulk-acts');
    const mkBtn = (label, fn, danger) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.style.cssText = `font:700 11px/1 inherit;padding:.5rem;border-radius:6px;cursor:pointer;border:1px solid ${
        danger ? '#f3c08a' : '#cdd2f5'
      };color:${danger ? '#b45309' : '#3843d0'};background:${
        danger ? '#fff8f0' : '#fff'
      }`;
      b.addEventListener('click', fn);
      acts.appendChild(b);
    };
    mkBtn(`Reassign ${ids.length} → worker…`, () => {
      const w = prompt('Assign the selected work areas to which worker?');
      if (w)
        edit({ action: 'reassign', wa_ids: [...ids], opportunity_access: w });
    });
    mkBtn(`Move ${ids.length} → group…`, () => {
      const g = prompt('Move the selected work areas to which group?');
      if (g) edit({ action: 'regroup', wa_ids: [...ids], work_area_group: g });
    });
    mkBtn(
      `Exclude ${ids.length} work areas`,
      () => {
        const r = prompt(`Reason for excluding ${ids.length} work areas?`);
        if (r !== null)
          edit({ action: 'exclude', wa_ids: [...ids], reason: r });
      },
      true,
    );
    return wrap;
  }
  // Reflect the current selection in the Inspect tab: single → WA+group, many → bulk.
  function syncInspectToSelection(lastId) {
    if (!mapPanel) return;
    if (selected.size > 1) {
      pinnedInspectFn = () => bulkInspectNode([...selected]);
      mapPanel.setInspect(bulkInspectNode([...selected]));
    } else {
      inspectWA(selected.size === 1 ? [...selected][0] : lastId, true);
    }
  }
  function revertInspect() {
    if (!mapPanel) return;
    if (pinnedInspectFn) mapPanel.setInspect(pinnedInspectFn());
    else mapPanel.clearInspect();
  }

  function refreshMap() {
    if (!map || !mapReady) return;
    const data = fc();
    // Work-area territories via the shared PlanLayers component (same paint the
    // monitoring render uses). Selection + hover interactivity is review-only and
    // wired once, on first creation.
    const firstTime = !map.getLayer('wa-fill');
    window.PlanLayers.workAreas(map, { data: data });
    if (firstTime) {
      // Plain click selects just this work area (pins its WA+group inspector).
      // Shift/⌘/Ctrl-click adds/removes it from the selection and, once more than
      // one is selected, pins the bulk panel. Hover previews a single work area
      // (no group); mouseout reverts to whatever is pinned (single or bulk).
      map.on('click', 'wa-fill', (e) => {
        const id = e.features[0].properties.id;
        const oe = e.originalEvent || {};
        if (oe.shiftKey || oe.metaKey || oe.ctrlKey) {
          if (selected.has(id)) selected.delete(id);
          else selected.add(id);
        } else {
          selected.clear();
          selected.add(id);
        }
        renderTable();
        setSelState();
        syncInspectToSelection(id);
      });
      map.on('mouseenter', 'wa-fill', () => {
        map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', 'wa-fill', () => {
        map.getCanvas().style.cursor = '';
        hoverWAId = null;
        revertInspect();
      });
      map.on('mousemove', 'wa-fill', (e) => {
        const f = e.features[0];
        if (!f) return;
        if (f.properties.id === hoverWAId) return; // only rebuild when the cell changes
        hoverWAId = f.properties.id;
        inspectWA(hoverWAId, false);
      });
    }
    Microplans.fitTo(map, data, { maxZoom: 16, animate: false, duration: 0 });
  }
  function setSelState() {
    if (!map || !mapReady) return;
    const field = DIM_FIELD[colorDim];
    WAS.forEach((w) => {
      map.setFeatureState(
        { source: 'wa', id: w.id },
        {
          sel: selected.has(w.id),
          dim: activeDim !== null && (w[field] || '') !== activeDim,
        },
      );
    });
  }

  // ---- map Layers/Inspector panel + footprints layer ----
  // The panel (microplans/map_panel.js) owns the overlay toggles; footprints is
  // its first registered layer. Admin boundaries + service delivery register
  // here too (follow-up). `__mpPanel` is exposed for those + debugging.
  let mapPanel = null,
    fpLayer = null,
    footprintsLoaded = false,
    footprintsOn = false,
    adminBoundaries = null;
  const setStatus = (t) => {
    const s = $('status');
    if (s) s.textContent = t;
  };
  // Building-footprints overlay lives in static/microplans/review/footprints.js.
  // Live state via ctx accessors; static deps (URLs / csrf / collectAreas / setStatus) by value.
  const _fp = window.MPReview.footprints({
    get map() {
      return map;
    },
    get mapReady() {
      return mapReady;
    },
    get fpLayer() {
      return fpLayer;
    },
    get planRevision() {
      return planRevision;
    },
    set footprintsLoaded(v) {
      footprintsLoaded = v;
    },
    FOOTPRINTS_URL: FOOTPRINTS_URL,
    PREVIEW_FOOTPRINTS_URL: PREVIEW_FOOTPRINTS_URL,
    CSRF: CSRF,
    collectAreas: collectAreas,
    setStatus: setStatus,
  });
  const loadFootprints = _fp.loadFootprints;
  const setFootprintsVisible = _fp.setFootprintsVisible;
  const reloadFootprintsDebounced = Microplans.debounce(loadFootprints, 600);
  // The picked wards to re-list in the boundary rail on load. Stashed because the
  // admin-boundary layer and the plan payload arrive in an undefined order — restore
  // fires from whichever lands last (on plan load; again right after the admin layer
  // registers), so the rail repopulates regardless of the race. Declared HERE, above
  // the map-panel block that calls tryRestoreBoundaryRail(), so it isn't in the TDZ.
  let pendingBoundaryRestore = null;
  function tryRestoreBoundaryRail() {
    if (
      adminBoundaries &&
      pendingBoundaryRestore &&
      pendingBoundaryRestore.length
    )
      adminBoundaries.restore(pendingBoundaryRestore);
  }
  if (map && window.MicroplansMapPanel && $('map-panel-mount')) {
    mapPanel = MicroplansMapPanel.create({ map, mount: $('map-panel-mount') });
    fpLayer = mapPanel.registerLayer({
      id: 'footprints',
      label: 'Building footprints',
      color: '#f59e0b',
      meta: '',
      onToggle: async (isOn, layer) => {
        footprintsOn = isOn;
        if (isOn) {
          const ok = await loadFootprints();
          if (!ok) {
            layer.setEnabled(false, false);
            footprintsOn = false;
            return;
          }
        }
        setFootprintsVisible(isOn);
      },
    });
    window.__mpPanel = mapPanel;

    // Service-delivery layer — re-homed from the (removed) setup page. Registers
    // into the panel, driven by the shared multi-select opportunity picker; a
    // derived boundary lands in the MapboxDraw area layer (`draw`, lazy).
    if (window.MicroplansServiceDelivery && $('sd-picker') && SD_PREVIEW_URL) {
      MicroplansServiceDelivery.register({
        panel: mapPanel,
        map,
        csrf: CSRF,
        pickerEl: $('sd-picker'),
        deriveHost: $('area-draw'),
        urls: {
          preview: SD_PREVIEW_URL,
          pipelines: SD_PIPELINES_URL,
          derive: DERIVE_BOUNDARY_URL,
        },
        onBoundary: (feature) => {
          const geom = feature.geometry;
          const polys =
            geom.type === 'MultiPolygon'
              ? geom.coordinates.map((c) => ({
                  type: 'Polygon',
                  coordinates: c,
                }))
              : [geom];
          // In sampling mode a derived boundary belongs to the active study arm.
          polys.forEach((g) => {
            if (draw)
              draw.add({
                type: 'Feature',
                geometry: g,
                properties: mpMode === 'sampling' ? { arm: currentArm } : {},
              });
          });
          Microplans.fitTo(map, feature);
          if (typeof setAreaInput === 'function') setAreaInput('draw');
        },
      });
    }

    // Admin "Boundaries" layer — render the boundaries we have for the region as
    // outlines; smallest-wins inspect; in the area phase Shift/⌘-click (or a search
    // result) adds the boundary's full geometry to the MapboxDraw area layer.
    if (window.MicroplansAdminBoundaries && BOUNDARY_VIEWPORT_URL) {
      const adminDrawIds = {}; // boundary_id -> [draw feature ids]
      function adminAddGeometry(boundaryId, geom, arm) {
        if (!draw) return;
        // In sampling mode each picked boundary belongs to a study arm (set via the
        // per-boundary pill in the rail). Tag the draw feature so collectArmAreas +
        // the sample paint read it; coverage mode carries no arm.
        const props =
          mpMode === 'sampling' ? { arm: arm || 'intervention' } : {};
        const polys =
          geom.type === 'MultiPolygon'
            ? geom.coordinates.map((c) => ({ type: 'Polygon', coordinates: c }))
            : [geom];
        adminDrawIds[boundaryId] = [];
        polys.forEach((g) => {
          const ids = draw.add({
            type: 'Feature',
            geometry: g,
            properties: { ...props },
          });
          (ids || []).forEach((id) => adminDrawIds[boundaryId].push(id));
        });
        if (typeof refreshAreaStats === 'function') refreshAreaStats();
      }
      // Re-tag a picked boundary's draw feature(s) when its arm pill changes, so the
      // next Generate samples it under the right arm.
      function adminSetArm(boundaryId, arm) {
        if (!draw || mpMode !== 'sampling') return;
        (adminDrawIds[boundaryId] || []).forEach((id) => {
          try {
            draw.setFeatureProperty(id, 'arm', arm);
          } catch (_) {}
        });
      }
      function adminRemoveGeometry(boundaryId) {
        if (draw && adminDrawIds[boundaryId])
          draw.delete(adminDrawIds[boundaryId]);
        delete adminDrawIds[boundaryId];
        if (typeof refreshAreaStats === 'function') refreshAreaStats();
      }
      adminBoundaries = MicroplansAdminBoundaries.register({
        panel: mapPanel,
        map,
        csrf: CSRF,
        controlsHost: document.getElementById('area-admin'),
        urls: {
          viewport: BOUNDARY_VIEWPORT_URL,
          geometry: ADMIN_AREA_GEOMETRY_URL,
          areas: ADMIN_AREAS_URL,
          compareSurrounding: COMPARE_SURROUNDING_URL,
        },
        // Surrounding-ward control finder renders its ranked results below the map.
        comparePanel: document.getElementById('surrounding-compare'),
        getSamplingConfig: () => samplingConfig(),
        getCountryIso: () =>
          (typeof OPP_COUNTRY_ISO !== 'undefined' && OPP_COUNTRY_ISO) || null,
        isAreaPhase: () => {
          const d = document.querySelector('details.area-def');
          return !!(d && d.open);
        },
        onAreaAdd: (boundaryId, geometry, feature, arm) => {
          adminAddGeometry(boundaryId, geometry, arm);
          autofillFromBoundary(feature);
          recordWardPopulation(boundaryId, feature);
        },
        onAreaRemove: (boundaryId) => {
          adminRemoveGeometry(boundaryId);
          forgetWardPopulation(boundaryId);
        },
        onArmChange: (boundaryId, arm) => adminSetArm(boundaryId, arm),
        armEnabled: () => mpMode === 'sampling',
      });
      // Boundaries is the default area mode on a fresh plan — turn the layer on
      // so its lines render and clicks select straight away. Defer to style-load:
      // enabling adds map layers, which THROWS (and would halt the rest of init,
      // killing every rail button) if the style isn't ready yet.
      if (adminBoundaries && !PLAN_ID) {
        const enableBoundaries = () => {
          try {
            adminBoundaries.enable();
          } catch (e) {
            /* style not ready / layer race — non-fatal */
          }
        };
        if (map.isStyleLoaded && map.isStyleLoaded()) enableBoundaries();
        else map.once('load', enableBoundaries);
      }
      // The plan may have loaded its wards before this layer registered — now that
      // adminBoundaries exists, repopulate the rail (no-op if nothing pending).
      tryRestoreBoundaryRail();
    }
  }

  // ---- render ----
  // Optimistic-concurrency token. Every plan payload (load + each save response)
  // carries the current `revision`; we echo it back on the next save so the
  // server can 409 if someone else changed the plan in between.
  let planRevision = null;
  // A 409 = the plan changed in another tab/session since we loaded it; saving
  // would clobber the newer state. Warn and reload to the latest.
  function handleConflict(resp, data, setMsg) {
    if (resp.status !== 409) return false;
    setMsg(
      (data && data.detail ? data.detail + ' ' : '') +
        'Reloading to the latest…',
    );
    setTimeout(() => location.reload(), 2000);
    return true;
  }
  // Celery-offloaded mutations (regroup/reassign/regenerate) surface the same
  // conflict as a completed task result {status:"conflict"} rather than a 409.
  function conflictResult(data, setMsg) {
    if (!data || data.status !== 'conflict') return false;
    setMsg((data.detail ? data.detail + ' ' : '') + 'Reloading to the latest…');
    setTimeout(() => location.reload(), 2000);
    return true;
  }
  function render(data) {
    if (data && data.revision !== undefined) planRevision = data.revision;
    WAS = data.work_areas || [];
    renderSummary(data.summary || {});
    renderKpis(data.kpis || {});
    renderTable();
    refreshMap();
    setSelState();
    syncPlanTools();
  }

  // Grey-out: grouping / assignment / bulk / export all act on a plan's work areas,
  // so they stay visible-but-disabled (and the hint shows) until work areas exist —
  // i.e. until Generate plan has run — then enable. Driven off WAS.
  function syncPlanTools() {
    const hasWAs = Array.isArray(WAS) && WAS.length > 0;
    [
      'btn-regroup',
      'btn-reassign',
      'bulk-exclude',
      'bulk-regroup',
      'bulk-reassign',
      'btn-export',
      'btn-apply-filters',
    ].forEach((id) => {
      const el = $(id);
      if (el) el.disabled = !hasWAs;
    });
    [
      'group-strategy-card',
      'assign-workers-card',
      'bulk-card',
      'filter-card',
    ].forEach((id) => {
      const el = $(id);
      if (el) {
        el.classList.toggle('opacity-50', !hasWAs);
        el.classList.toggle('pointer-events-none', !hasWAs);
      }
    });
    const hint = $('plan-tools-hint');
    if (hint) hint.classList.toggle('hidden', hasWAs);
    if (typeof previewFilters === 'function') previewFilters();
  }

  // --- sampling overlay replay (load / create-in-place / regenerate) -----------
  // A saved sampling plan stores its ward boundaries (input_areas) + selected-PSU
  // hulls + per-arm stats. Replaying them makes a reopened plan look exactly like
  // the just-created one. Deferred to map-ready (the plan fetch may resolve first).
  let pendingSampling = null;
  function drawSamplingOverlay() {
    const d = pendingSampling;
    if (!d || !map || !mapReady || !draw) return;
    pendingSampling = null;
    if (d.mode !== 'sampling') return;
    // Re-add the picked ward boundaries to the draw surface so they're visible AND
    // collectArmAreas (Regenerate) can read them back.
    const inputs = Array.isArray(d.input_areas) ? d.input_areas : [];
    if (inputs.length) {
      try {
        draw.deleteAll();
        inputs.forEach((a) => {
          const g = a && a.geometry;
          if (!g) return;
          const polys =
            g.type === 'MultiPolygon'
              ? g.coordinates.map((c) => ({ type: 'Polygon', coordinates: c }))
              : [g];
          polys.forEach((poly) =>
            draw.add({
              type: 'Feature',
              geometry: poly,
              properties: { arm: a.arm || 'intervention' },
            }),
          );
        });
        if (typeof refreshAreaStats === 'function') refreshAreaStats();
        // Rehydrate the left-rail boundary list so a reopened plan shows its picked
        // wards (name + arm pill) like during creation. Rail-only — the draw
        // features added above already render the wards on the map. The admin layer
        // may not be registered yet, so stash + try (it also fires post-register).
        pendingBoundaryRestore = inputs;
        tryRestoreBoundaryRail();
      } catch (_) {
        /* draw not ready — map-load will retry */
      }
    }
    // Redraw the selected-PSU hulls + restore Sample details. No pins: the work
    // areas already represent the surveyed houses (keeps created == opened).
    const hulls = d.psu_hulls || { type: 'FeatureCollection', features: [] };
    if (hulls.features && hulls.features.length) {
      renderSample({
        hulls,
        pins: { type: 'FeatureCollection', features: [] },
        stats: d.sampling_stats || [],
      });
    }
  }

  // Apply a plan payload (from load, create-in-place, or regenerate): forms + work
  // areas + KPIs, then the sampling overlay, then tool enablement — one path so the
  // three entry points can never diverge.
  function applyPlanData(d) {
    prefillSetupForm(d);
    render(d);
    pendingSampling = d;
    drawSamplingOverlay();
  }
  function chip(label, value, hint) {
    // A KPI tile: small uppercase label over a large display-font figure. value/hint
    // may carry plan/territory data — escape before innerHTML.
    return `<div class="border border-gray-200 rounded-lg px-3 py-2 bg-white" title="${esc(
      hint || '',
    )}">
      <div class="text-[9.5px] uppercase tracking-wide text-gray-400 font-semibold">${esc(
        label,
      )}</div>
      <div class="text-[18px] font-extrabold tracking-tight text-gray-900 tabular-nums" style="font-family:'Bricolage Grotesque','Work Sans',sans-serif">${esc(
        value,
      )}</div></div>`;
  }
  function renderKpis(k) {
    const p = k.plan || {};
    const balLabel = p.has_population ? 'Pop imbalance' : 'Bldg imbalance';
    const balVal = p.has_population
      ? p.pop_imbalance_pct
      : p.building_imbalance_pct;
    // The KPI strip only exists once the plan-review DOM is present. During
    // create-in-place (Generate plan on a fresh /new/ page) renderKpis can fire
    // before that DOM is adopted — no-op rather than throw (which would surface a
    // spurious "Failed" even though the plan was created fine).
    const strip = $('kpi-strip');
    if (!strip) return;
    strip.innerHTML = [
      chip(
        'Worst travel',
        (p.max_spread_km ?? 0) + ' km',
        'Largest FLW territory diameter — the minimax objective',
      ),
      chip(
        'Mean travel',
        (p.mean_spread_km ?? 0) + ' km',
        'Mean FLW territory diameter (±std ' + (p.std_spread_km ?? 0) + ')',
      ),
      chip(
        balLabel,
        balVal == null ? '—' : balVal + ' %',
        '(max − min) / target × 100',
      ),
      chip(
        p.has_population ? 'Pop std' : 'Bldg std',
        (p.has_population ? p.pop_std : p.building_std) ?? '—',
        'Std of per-FLW ' + (p.has_population ? 'population' : 'buildings'),
      ),
      chip(
        'Coverage',
        (k.coverage_pct ?? 100) + ' %',
        'Active buildings / (active + excluded)',
      ),
      chip(
        'Excluded',
        (k.excluded ? k.excluded.count : 0) + ' areas',
        (k.excluded ? k.excluded.buildings : 0) + ' buildings dropped',
      ),
      chip(
        k.dimension === 'worker' ? 'Workers' : 'Groups',
        p.territory_count ?? 0,
        k.dimension === 'worker'
          ? ''
          : 'No workers assigned yet — metrics shown by group',
      ),
    ].join('');
    $('flw-dim').textContent = k.dimension === 'worker' ? 'Worker' : 'Group';
    const showPop = !!p.has_population;
    $('flw-pop-col').style.display = showPop ? '' : 'none';
    $('flw-body').innerHTML = (k.territories || [])
      .map(
        (t) =>
          `<tr class="border-b"><td class="p-1.5 font-medium">${esc(
            t.name,
          )}</td>
        <td class="p-1.5">${t.work_areas}</td>
        <td class="p-1.5">${(t.buildings || 0).toLocaleString()}</td>
        ${
          showPop
            ? `<td class="p-1.5">${
                t.population ? t.population.toLocaleString() : '—'
              }</td>`
            : ''
        }
        <td class="p-1.5">${(t.expected_visits || 0).toLocaleString()}</td>
        <td class="p-1.5">${t.spread_km}</td></tr>`,
      )
      .join('');
  }
  function renderSummary(s) {
    $(
      'summary',
    ).innerHTML = `<div class="flex justify-between"><dt class="text-gray-500">Active areas</dt><dd class="font-medium">${
      s.active ?? 0
    }</dd></div>
       <div class="flex justify-between"><dt class="text-gray-500">Excluded</dt><dd class="font-medium">${
         s.excluded ?? 0
       }</dd></div>
       <div class="flex justify-between"><dt class="text-gray-500">Buildings (active)</dt><dd class="font-medium">${(
         s.buildings_active ?? 0
       ).toLocaleString()}</dd></div>`;
    renderDimSidebar(s);
  }
  function renderDimSidebar(s) {
    // Sidebar contents follow the active dimension (worker | group).
    const bucket = (colorDim === 'worker' ? s.by_worker : s.by_group) || {};
    // Don't list the synthetic "(unassigned)" bucket — empty key is the visual.
    const rows = Object.keys(bucket)
      .filter((k) => k && k !== '(unassigned)')
      .sort((a, b) => bucket[b].work_areas - bucket[a].work_areas);
    if (!rows.length) {
      $('by-dim').innerHTML = `<span class="text-gray-400 text-xs">no ${
        colorDim === 'worker' ? 'workers assigned' : 'groups defined'
      }</span>`;
    } else {
      $('by-dim').innerHTML = rows
        .map((name) => {
          const cls =
            'worker-row text-xs ' + (activeDim === name ? 'is-active' : '');
          return `<div class="${cls}" data-dim="${esc(name)}">
          <span class="sw" style="background:${colorFor(name)}"></span>
          <span class="nm">${esc(name)}</span>
          <span class="ct">${bucket[name].work_areas} · ${bucket[
            name
          ].buildings.toLocaleString()}</span>
        </div>`;
        })
        .join('');
    }
    $('dim-clear').classList.toggle('hidden', activeDim === null);
  }
  // ---- sort + collapse state for the work-area table ----
  let sortKey = 'work_area_group'; // start grouped by group name
  let sortDir = 1; // 1 = asc, -1 = desc
  const collapsedGroups = new Set();
  const SORT_GETTERS = {
    id: (w) => String(w.id || ''),
    work_area_group: (w) => String(w.work_area_group || ''),
    opportunity_access: (w) => String(w.opportunity_access || ''),
    building_count: (w) => Number(w.building_count || 0),
    expected_visit_count: (w) => Number(w.expected_visit_count || 0),
    status: (w) => String(w.status || ''),
  };

  // In the planning phase every active area is "UNASSIGNED" — the execution
  // default that flips to VISITED once Connect runs the plan. Shown next to an
  // assigned worker the raw word reads as "no worker", so render it as the
  // planning-phase "Planned" (muted); execution states pass through verbatim.
  function statusLabel(status) {
    const s = String(status || 'UNASSIGNED');
    if (s === 'UNASSIGNED') return '<span class="text-gray-400">Planned</span>';
    return esc(s);
  }

  function _cellRow(w) {
    const excluded = w.status === 'EXCLUDED';
    const id = esc(w.id);
    const field = DIM_FIELD[colorDim];
    const isHl = activeDim !== null && (w[field] || '') === activeDim;
    const tr = document.createElement('tr');
    tr.className =
      'wa-row border-b ' +
      (excluded ? 'excluded ' : '') +
      (selected.has(w.id) ? 'sel ' : '') +
      (isHl ? 'hl-worker ' : '');
    tr.innerHTML = `
      <td class="p-2"><input type="checkbox" class="rowsel" data-id="${id}" ${
        selected.has(w.id) ? 'checked' : ''
      }></td>
      <td class="p-2"><input class="cell-input rounded border-gray-200 text-xs" data-act="regroup" data-field="work_area_group" data-id="${id}" value="${esc(
        w.work_area_group || '',
      )}"></td>
      <td class="p-2 font-mono text-xs">${id}</td>
      <td class="p-2"><input class="cell-input rounded border-gray-200 text-xs" data-act="reassign" data-field="opportunity_access" data-id="${id}" value="${esc(
        w.opportunity_access || '',
      )}"></td>
      <td class="p-2">${(w.building_count ?? 0).toLocaleString()}</td>
      <td class="p-2"><input type="number" class="cell-input rounded border-gray-200 text-xs" data-act="resize" data-field="expected_visit_count" data-id="${id}" value="${
        w.expected_visit_count ?? 0
      }"></td>
      <td class="p-2 text-xs">${
        excluded
          ? `<span class="text-gray-500" title="${esc(
              w.excluded_reason || '',
            )}">Excluded</span>`
          : statusLabel(w.status)
      }</td>
      <td class="p-2">${
        excluded
          ? `<button class="text-xs text-brand-cornflower-blue" data-restore="${id}">restore</button>`
          : `<button class="text-xs text-red-600" data-exclude="${id}">exclude</button>`
      }</td>`;
    return tr;
  }

  function _groupHeaderRow(name, items) {
    const buildings = items.reduce((s, w) => s + (w.building_count || 0), 0);
    const visits = items.reduce((s, w) => s + (w.expected_visit_count || 0), 0);
    const workers = Array.from(
      new Set(items.map((w) => w.opportunity_access).filter(Boolean)),
    );
    const workerTxt =
      workers.length === 0
        ? '—'
        : workers.length === 1
        ? esc(workers[0])
        : `${workers.length} workers`;
    const collapsed = collapsedGroups.has(name);
    const selInGroup = items.reduce(
      (n, w) => n + (selected.has(w.id) ? 1 : 0),
      0,
    );
    const allSelected = selInGroup === items.length && items.length > 0;
    const noneSelected = selInGroup === 0;
    // Color swatch ties the row to the map's group fill color (same `colorFor`).
    const swatch = colorFor(name);
    const tr = document.createElement('tr');
    tr.className = 'wa-group-hdr' + (collapsed ? ' is-collapsed' : '');
    tr.dataset.groupToggle = name;
    tr.innerHTML = `<td colspan="8">
      <label class="ghdr-check" title="${
        allSelected ? 'Deselect all in group' : 'Select all in group'
      }">
        <input type="checkbox" class="grp-sel" data-group-sel="${esc(name)}" ${
          allSelected ? 'checked' : ''
        }>
      </label>
      <span class="chev">${collapsed ? '▶' : '▼'}</span>
      <span class="gn-sw" style="background:${swatch}"></span>
      <span class="gn">${esc(name)}</span>
      <span class="meta">${
        items.length
      } work areas · ${buildings.toLocaleString()} buildings · ${visits.toLocaleString()} visits · ${workerTxt}${
        noneSelected ? '' : ` · ${selInGroup} selected`
      }</span>
    </td>`;
    // .indeterminate isn't a serializable attribute; set it after construct.
    const cb = tr.querySelector('.grp-sel');
    if (cb) cb.indeterminate = !allSelected && !noneSelected;
    return tr;
  }

  function renderTable() {
    const body = $('wa-body');
    body.innerHTML = '';
    const field = DIM_FIELD[colorDim];
    let rows = WAS.slice();
    if (activeDim !== null)
      rows = rows.filter((w) => (w[field] || '') === activeDim);
    // Sort rows by the active column.
    const getter = SORT_GETTERS[sortKey] || SORT_GETTERS.id;
    rows.sort((a, b) => {
      const va = getter(a),
        vb = getter(b);
      if (va < vb) return -1 * sortDir;
      if (va > vb) return 1 * sortDir;
      return 0;
    });
    // Bucket into groups preserving the sorted within-group order.
    const groups = new Map();
    rows.forEach((w) => {
      const g = w.work_area_group || '(no group)';
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(w);
    });
    // Group header ordering follows the Group column sort direction; otherwise
    // alphabetical so the layout is stable across other sorts.
    const dir = sortKey === 'work_area_group' ? sortDir : 1;
    const groupOrder = Array.from(groups.keys()).sort((a, b) =>
      a < b ? -dir : a > b ? dir : 0,
    );
    groupOrder.forEach((name) => {
      const items = groups.get(name);
      body.appendChild(_groupHeaderRow(name, items));
      if (collapsedGroups.has(name)) return;
      items.forEach((w) => body.appendChild(_cellRow(w)));
    });
    // Sort indicators on the table head.
    document.querySelectorAll('.wa-th').forEach((th) => {
      const active = th.dataset.sort === sortKey;
      th.classList.toggle('is-sorted', active);
      const ind = th.querySelector('.sort-ind');
      if (ind) ind.textContent = active ? (sortDir === 1 ? '↑' : '↓') : '';
    });
    $('sel-count').textContent = `${selected.size} selected`;
  }

  // ---- column sort: click toggles asc/desc, clicking a new column starts asc.
  document.querySelectorAll('.wa-th').forEach((th) => {
    th.addEventListener('click', () => {
      const k = th.dataset.sort;
      if (sortKey === k) sortDir = -sortDir;
      else {
        sortKey = k;
        sortDir = 1;
      }
      renderTable();
    });
  });

  // Select/deselect every cell whose work_area_group matches `name`.
  // Partial state (some-but-not-all selected) promotes to all-selected first
  // — matches how a click on a tri-state checkbox resolves the `indeterminate`
  // visual into a definite checked.
  function toggleGroupSelect(name) {
    const cellsInGroup = WAS.filter((w) => (w.work_area_group || '') === name);
    if (!cellsInGroup.length) return;
    const allOn = cellsInGroup.every((w) => selected.has(w.id));
    cellsInGroup.forEach((w) => {
      if (allOn) selected.delete(w.id);
      else selected.add(w.id);
    });
    renderTable();
    setSelState();
  }
  function toggleGroupCollapse(name) {
    if (collapsedGroups.has(name)) collapsedGroups.delete(name);
    else collapsedGroups.add(name);
    renderTable();
  }

  // ---- dimension filter (worker | group) ----
  function setActiveDim(name) {
    activeDim = name === activeDim ? null : name;
    renderDimSidebar(lastSummary);
    renderTable();
    setSelState();
  }
  function setColorDim(d) {
    colorDim = d;
    activeDim = null; // clear filter when switching dimensions
    $('dim-worker').classList.toggle('is-on', d === 'worker');
    $('dim-group').classList.toggle('is-on', d === 'group');
    $('dim-label').textContent = d === 'worker' ? 'Workers' : 'Groups';
    $('dim-label2').textContent = d === 'worker' ? 'worker' : 'group';
    // Legend reflects which dim is in the fill vs the outline.
    const fillLbl = $('legend-fill-dim'),
      outLbl = $('legend-outline-dim');
    if (fillLbl) fillLbl.textContent = d;
    if (outLbl) outLbl.textContent = d === 'worker' ? 'group' : 'worker';
    // Recompute per-feature fill + outline colors and push to the source.
    if (map && mapReady) {
      const src = map.getSource('wa');
      if (src) src.setData(fc());
    }
    renderDimSidebar(lastSummary);
    renderTable();
    setSelState();
  }
  let lastSummary = {};
  const origRenderSummary = renderSummary;
  renderSummary = function (s) {
    lastSummary = s;
    origRenderSummary(s);
  };
  on('by-dim', 'click', (e) => {
    const row = e.target.closest('.worker-row');
    if (!row) return;
    setActiveDim(row.dataset.dim);
  });
  on('dim-clear', 'click', () => {
    activeDim = null;
    renderDimSidebar(lastSummary);
    renderTable();
    setSelState();
  });
  on('dim-worker', 'click', () => setColorDim('worker'));
  on('dim-group', 'click', () => setColorDim('group'));

  // ---- edits ----
  async function edit(body) {
    $('status').textContent = 'Saving…';
    try {
      const resp = await post(
        EDIT_URL,
        Object.assign({ revision: planRevision }, body),
      );
      const data = await resp.json();
      if (handleConflict(resp, data, (m) => ($('status').textContent = m)))
        return;
      if (!resp.ok || data.status !== 'ok') {
        $('status').textContent = data.detail || 'HTTP ' + resp.status;
        return;
      }
      $('status').textContent = 'Saved.';
      render(data);
    } catch (e) {
      $('status').textContent = 'Failed: ' + e;
    }
  }
  function toggleSelect(id) {
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    renderTable();
    setSelState();
  }

  // table interactions (delegated)
  on('wa-body', 'click', (e) => {
    const ex = e.target.closest('[data-exclude]');
    const rs = e.target.closest('[data-restore]');
    if (ex) {
      const reason = prompt('Reason for excluding this work area?');
      if (reason !== null)
        edit({ action: 'exclude', wa_id: ex.dataset.exclude, reason });
      return;
    }
    if (rs) {
      edit({ action: 'unexclude', wa_id: rs.dataset.restore });
      return;
    }
    const cb = e.target.closest('.rowsel');
    if (cb) {
      toggleSelect(cb.dataset.id);
      return;
    }
    // Click on a group header row → toggle collapse for that group.
    // Group-header checkbox: select/deselect all cells in this group. Sits
    // inside [data-group-toggle], so catch it FIRST and stop the bubble so the
    // collapse handler below doesn't also fire.
    const gsel = e.target.closest('[data-group-sel]');
    if (gsel) {
      e.stopPropagation();
      toggleGroupSelect(gsel.dataset.groupSel);
      return;
    }
    // Click on any other part of the group header row → toggle collapse.
    const hdr = e.target.closest('[data-group-toggle]');
    if (hdr) {
      toggleGroupCollapse(hdr.dataset.groupToggle);
    }
  });
  on('wa-body', 'change', (e) => {
    const inp = e.target.closest('[data-act]');
    if (!inp) return;
    const body = { action: inp.dataset.act, wa_id: inp.dataset.id };
    body[inp.dataset.field] =
      inp.dataset.act === 'resize' ? +inp.value : inp.value;
    edit(body);
  });
  on('sel-all', 'change', (e) => {
    selected.clear();
    if (e.target.checked) WAS.forEach((w) => selected.add(w.id));
    renderTable();
    setSelState();
  });

  // Collapse / expand every group at once — a 1444-row plan reads as ~N group
  // summary rows (each carrying its worker + counts) when collapsed.
  on('toggle-collapse-all', 'click', () => {
    const names = new Set(
      (WAS || []).map((w) => w.work_area_group || '(no group)'),
    );
    const anyExpanded = [...names].some((n) => !collapsedGroups.has(n));
    if (anyExpanded) {
      names.forEach((n) => collapsedGroups.add(n));
    } else {
      collapsedGroups.clear();
    }
    const btn = $('toggle-collapse-all');
    if (btn)
      btn.textContent = anyExpanded
        ? 'Expand all groups'
        : 'Collapse all groups';
    renderTable();
  });

  // bulk
  on('bulk-exclude', 'click', () => {
    if (!selected.size) return;
    const reason = prompt(`Reason for excluding ${selected.size} work areas?`);
    if (reason !== null)
      edit({ action: 'exclude', wa_ids: [...selected], reason });
  });
  on('bulk-regroup', 'click', () => {
    if (selected.size && $('bulk-group').value)
      edit({
        action: 'regroup',
        wa_ids: [...selected],
        work_area_group: $('bulk-group').value,
      });
  });
  on('bulk-reassign', 'click', () => {
    if (selected.size)
      edit({
        action: 'reassign',
        wa_ids: [...selected],
        opportunity_access: $('bulk-worker').value,
      });
  });

  // export
  // ---- Phase-1: re-group cells ----
  on('grp-strategy', 'change', () => {
    const s = $('grp-strategy').value;
    $('grp-bfs-params').classList.toggle('hidden', s !== 'bfs_adjacency');
    $('grp-bbox-params').classList.toggle('hidden', s !== 'bbox');
  });
  on('btn-regroup', 'click', async () => {
    if (!REGROUP_URL) {
      $('status').textContent = 'Regroup URL not available.';
      return;
    }
    const strategy = $('grp-strategy').value;
    const body = { strategy };
    if (strategy === 'bfs_adjacency') {
      body.max_buildings = +$('grp-max-buildings').value;
      body.buffer_distance_m = +$('grp-buffer-m').value;
    } else {
      body.target_size = +$('grp-target-size').value;
    }
    $('btn-regroup').disabled = true;
    $('status').textContent = `Re-grouping (${strategy})…`;
    const t0 = performance.now();
    try {
      body.revision = planRevision;
      // Offloaded to Celery (BFS grouping over all cells) — enqueue + poll.
      const data = await Microplans.enqueueAndPoll(REGROUP_URL, body, {
        csrf: CSRF,
        onProgress: (m) => ($('status').textContent = m),
      });
      if (conflictResult(data, (m) => ($('status').textContent = m))) return;
      if (data.status !== 'ok') {
        $('status').textContent = data.detail || 'Re-group failed.';
      } else {
        const dt = ((performance.now() - t0) / 1000).toFixed(1);
        // Count distinct groups from the new work_areas response.
        const groups = new Set(
          (data.work_areas || []).map((w) => w.work_area_group),
        );
        $(
          'status',
        ).textContent = `Re-grouped into ${groups.size} groups in ${dt}s.`;
        // Switch the map color dimension to Group so the new layout is
        // immediately visible. setColorDim also re-renders sidebar + table.
        setColorDim('group');
        render(data);
        // The form now matches the applied state; restamp baseline so the
        // Apply button greys back out until the user changes something again.
        grpBaseline = snapshotGrp();
      }
    } catch (e) {
      $('status').textContent = 'Re-group failed: ' + e;
    } finally {
      syncApplyButtons();
    }
  });

  // ---- Phase-2: re-assign CHWs ----
  on('asg-strategy', 'change', () => {
    const s = $('asg-strategy').value;
    $('asg-restarts-wrap').classList.toggle('hidden', s !== 'minimax_spread');
  });
  on('btn-reassign', 'click', async () => {
    if (!REASSIGN_URL) {
      $('status').textContent = 'Reassign URL not available.';
      return;
    }
    const strategy = $('asg-strategy').value;
    const workers = ($('asg-workers').value || '').trim();
    if (!workers) {
      $('status').textContent = 'Enter at least one worker name.';
      return;
    }
    const body = { strategy, workers };
    if (strategy === 'minimax_spread') body.restarts = +$('asg-restarts').value;
    $('btn-reassign').disabled = true;
    $('status').textContent = `Re-assigning (${strategy})…`;
    const t0 = performance.now();
    try {
      body.revision = planRevision;
      // Offloaded to Celery (minimax assignment over all groups) — enqueue + poll.
      const data = await Microplans.enqueueAndPoll(REASSIGN_URL, body, {
        csrf: CSRF,
        onProgress: (m) => ($('status').textContent = m),
      });
      if (conflictResult(data, (m) => ($('status').textContent = m))) return;
      if (data.status !== 'ok') {
        $('status').textContent = data.detail || 'Re-assign failed.';
      } else {
        const dt = ((performance.now() - t0) / 1000).toFixed(1);
        const assigned = new Set(
          (data.work_areas || [])
            .map((w) => w.opportunity_access)
            .filter(Boolean),
        );
        $(
          'status',
        ).textContent = `Assigned to ${assigned.size} CHWs in ${dt}s.`;
        // Switch the map color dimension to Worker so the new assignment is visible.
        setColorDim('worker');
        render(data);
        asgBaseline = snapshotAsg();
      }
    } catch (e) {
      $('status').textContent = 'Re-assign failed: ' + e;
    } finally {
      syncApplyButtons();
    }
  });

  on('btn-export', 'click', async () => {
    const resp = await post(CSV_URL, {});
    if (!resp.ok) {
      $('status').textContent = 'Export failed.';
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `microplan_plan${PLAN_ID}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    // Connect's importer rejects rows with a blank LGA/State. The server flags
    // that here so we can warn rather than let the upload fail downstream.
    if (resp.headers.get('X-Microplan-Connect-Ready') === 'false') {
      const missing = resp.headers.get('X-Microplan-Missing') || 'LGA/State';
      $(
        'status',
      ).textContent = `Downloaded — but Connect needs ${missing}. Set ${missing} on the plan, then re-download before importing.`;
    } else {
      $('status').textContent = 'Downloaded Connect-ready CSV.';
    }
  });

  // ---- map resize (drag the handle on the bottom edge) ----
  (() => {
    const pane = $('map-pane'),
      handle = $('map-resizer');
    if (!pane || !handle) return;
    let startY = 0,
      startHeight = 0;
    const onMove = (e) => {
      const dy = e.clientY - startY;
      const h = Math.max(
        160,
        Math.min(window.innerHeight - 200, startHeight + dy),
      );
      pane.style.height = h + 'px';
      if (map) map.resize();
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    handle.addEventListener('mousedown', (e) => {
      startY = e.clientY;
      startHeight = pane.getBoundingClientRect().height;
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
      document.body.style.cursor = 'row-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
  })();

  // ---- pre-fill the sidebar form from the plan's stored config ----
  // So opening an existing plan shows the SAME params that produced its
  // current grouping/assignment — the user can see "what was used" by just
  // reading the form, and re-apply with one click after tweaking anything.
  function prefillSetupForm(data) {
    // Restore the saved plan's mode so the editor shows its real tools (a sampling
    // plan must load with the sampling controls + PSU grouping, not coverage's).
    if (data.mode) setMode(data.mode);
    const g = data.grouping || {};
    const a = data.assignment || {};
    if (g.strategy) {
      $('grp-strategy').value = g.strategy;
      $('grp-strategy').dispatchEvent(new Event('change'));
    }
    if (g.max_buildings) $('grp-max-buildings').value = g.max_buildings;
    if (g.buffer_distance_m) $('grp-buffer-m').value = g.buffer_distance_m;
    if (g.target_size) $('grp-target-size').value = g.target_size;
    if (a.strategy) {
      $('asg-strategy').value = a.strategy;
      $('asg-strategy').dispatchEvent(new Event('change'));
    }
    if (a.workers) {
      const w = Array.isArray(a.workers)
        ? a.workers.join('\n')
        : String(a.workers);
      $('asg-workers').value = w;
    }
    if (a.restarts) $('asg-restarts').value = a.restarts;
    // Pre-fill the cell-size from the first work area's properties.cell_size_m
    // (coverage frame stores it on every feature). So the Area definition
    // section shows the size used to generate the current layout.
    const was = data.work_areas || [];
    const cellSize =
      was.length && was[0].properties && was[0].properties.cell_size_m;
    if (cellSize) setCellSize(cellSize, true);
    // Stamp baselines so Apply buttons grey out until something actually
    // changes. If the plan had nothing stored for that phase, baseline stays
    // null → button is enabled for the first-ever apply.
    grpBaseline = Object.keys(g).length ? snapshotGrp() : null;
    asgBaseline = Object.keys(a).length ? snapshotAsg() : null;
    syncApplyButtons();
  }

  // ---- Apply-button dirty tracking: grey the button when the current form
  // matches the last-applied config, so a no-op Apply can't masquerade as
  // work done. Re-enable as soon as anything in the section is edited.
  let grpBaseline = null;
  let asgBaseline = null;
  const GRP_FIELDS = [
    'grp-strategy',
    'grp-max-buildings',
    'grp-buffer-m',
    'grp-target-size',
  ];
  const ASG_FIELDS = ['asg-strategy', 'asg-workers', 'asg-restarts'];
  function snapshotFields(ids) {
    return ids.map((id) => ($(id) ? String($(id).value) : '')).join('|');
  }
  function snapshotGrp() {
    return snapshotFields(GRP_FIELDS);
  }
  function snapshotAsg() {
    return snapshotFields(ASG_FIELDS);
  }
  function syncApplyButtons() {
    if (!PLAN_ID) return; // no plan yet: action buttons stay disabled (set in template)
    const gb = $('btn-regroup');
    if (gb) gb.disabled = grpBaseline !== null && snapshotGrp() === grpBaseline;
    const ab = $('btn-reassign');
    if (ab) {
      const noWorkers = !($('asg-workers').value || '').trim();
      ab.disabled =
        noWorkers || (asgBaseline !== null && snapshotAsg() === asgBaseline);
    }
  }
  GRP_FIELDS.forEach((id) => {
    const el = $(id);
    if (el) {
      el.addEventListener('input', syncApplyButtons);
      el.addEventListener('change', syncApplyButtons);
    }
  });
  ASG_FIELDS.forEach((id) => {
    const el = $(id);
    if (el) {
      el.addEventListener('input', syncApplyButtons);
      el.addEventListener('change', syncApplyButtons);
    }
  });

  // ----------------------------------------------------------------------
  //  Area definition (Edit area & work area size) — destructive regenerate flow.
  //  Adds a MapboxDraw control to the existing map so the LLO can draw a new
  //  boundary; offers admin-area picker + pin-and-radius as alternatives;
  //  Apply geographic frame posts to /plan/<id>/regenerate/ and re-renders.
  // ----------------------------------------------------------------------
  let draw = null;
  let areaInput = 'admin'; // "draw" | "admin" | "pin" — Boundaries is the default
  let circleAreas = []; // [{circle:{lon,lat,radius_m}}]
  let dropPinArmed = false;
  let cellSizeM = 100;

  // Read-only display mode: renders draw features (picked admin boundaries,
  // SD-derived + freehand polygons) but wires NO click/drag handlers, so a ward
  // boundary can't be accidentally reshaped or translated. We park the control in
  // this mode by default and only enter draw_polygon while the LLO is actively
  // drawing a freehand area (returning to 'static' on draw.create). Collection
  // (draw.getAll) and rail x-delete (draw.delete) work in any mode.
  const StaticDrawMode = {
    onSetup() {
      this.setActionableState();
      return {};
    },
    toDisplayFeatures(state, geojson, display) {
      display(geojson);
    },
  };

  function setupAreaDef() {
    if (!map) return;
    // MapboxDraw is added top-right; works alongside the existing wa-fill /
    // wa-line layers because those layers are below the draw layer ordering.
    try {
      draw = new MapboxDraw({
        displayControlsDefault: false,
        controls: { polygon: true, trash: true },
        // Default to the read-only mode so picked boundaries aren't editable; the
        // polygon control still switches into draw_polygon on demand.
        defaultMode: 'static',
        modes: Object.assign({}, MapboxDraw.modes, { static: StaticDrawMode }),
        styles: [
          {
            id: 'gl-draw-polygon-fill',
            type: 'fill',
            filter: ['all', ['==', '$type', 'Polygon']],
            paint: { 'fill-color': '#10b981', 'fill-opacity': 0.15 },
          },
          {
            id: 'gl-draw-polygon-stroke',
            type: 'line',
            filter: ['all', ['==', '$type', 'Polygon']],
            paint: { 'line-color': '#10b981', 'line-width': 2 },
          },
          {
            id: 'gl-draw-vertex',
            type: 'circle',
            filter: ['all', ['==', 'meta', 'vertex'], ['==', '$type', 'Point']],
            paint: {
              'circle-radius': 4,
              'circle-color': '#fff',
              'circle-stroke-color': '#111',
              'circle-stroke-width': 1,
            },
          },
        ],
      });
      map.addControl(draw, 'top-right');
      map.on('draw.create', refreshAreaStats);
      map.on('draw.update', refreshAreaStats);
      map.on('draw.delete', refreshAreaStats);
      // Once a freehand polygon is finished, drop back to the read-only mode so it
      // (and every picked boundary) can't be dragged/reshaped by a stray click.
      map.on('draw.create', () => {
        if (draw) draw.changeMode('static');
      });
    } catch (e) {
      /* draw plugin unavailable — fall back gracefully */
    }
  }

  function setAreaInput(i) {
    areaInput = i;
    [
      ['draw', 'btn-area-draw', 'area-draw'],
      ['admin', 'btn-area-admin', 'area-admin'],
      ['pin', 'btn-area-pin', 'area-pin'],
    ].forEach(([k, btnId, panelId]) => {
      $(btnId).classList.toggle('is-on', k === i);
      $(panelId).classList.toggle('hidden', k !== i);
    });
    if (i !== 'pin') disarmPin();
  }
  $('btn-area-draw').addEventListener('click', () => setAreaInput('draw'));
  // "Boundaries" mode = reveal the boundary controls in the rail (#area-admin) and
  // turn on the map layer (lines + click-to-select). Controls live in the rail now.
  $('btn-area-admin').addEventListener('click', () => {
    setAreaInput('admin');
    if (adminBoundaries) adminBoundaries.enable();
  });
  $('btn-area-pin').addEventListener('click', () => setAreaInput('pin'));

  // ---- cell-size chips (same pattern as setup.html) ----
  function setCellSize(m, fromChip) {
    cellSizeM = Math.max(10, +m || 100);
    document
      .querySelectorAll('#cellsize-chips .chip')
      .forEach((c) =>
        c.classList.toggle('is-on', +c.dataset.cellsize === cellSizeM),
      );
    if (fromChip) $('cfg-cellsize').value = cellSizeM;
  }
  document
    .querySelectorAll('#cellsize-chips .chip')
    .forEach((c) =>
      c.addEventListener('click', () => setCellSize(+c.dataset.cellsize, true)),
    );
  $('cfg-cellsize').addEventListener('input', () =>
    setCellSize(+$('cfg-cellsize').value, false),
  );

  // Admin-area selection now lives in the "Boundaries" map-panel layer
  // (admin_boundaries_layer.js): outline render, source picker, name search, and
  // Shift/⌘-click area-select. It calls back into the draw control via the
  // onAreaAdd / onAreaRemove handlers wired where the layer is registered above.

  // ---- pin + radius ----
  function disarmPin() {
    dropPinArmed = false;
    map.getCanvas().style.cursor = '';
    $('btn-drop-pin').classList.remove('seg-on');
  }
  $('btn-drop-pin').addEventListener('click', () => {
    dropPinArmed = !dropPinArmed;
    $('btn-drop-pin').classList.toggle('seg-on', dropPinArmed);
    map.getCanvas().style.cursor = dropPinArmed ? 'crosshair' : '';
    $('pin-status').textContent = dropPinArmed
      ? 'Click the map to place the pin.'
      : '';
  });
  map.on('click', (e) => {
    if (!dropPinArmed) return;
    const r = Math.max(20, +$('inp-pin-radius').value || 500);
    circleAreas.push({
      circle: { lon: e.lngLat.lng, lat: e.lngLat.lat, radius_m: r },
    });
    $('pin-status').textContent = `${circleAreas.length} pin area(s).`;
    refreshAreaStats();
  });

  // ---- stats (count + approx area) ----
  function polyArea(g) {
    if (!g || g.type !== 'Polygon') return 0;
    const R = 6378137,
      toRad = (d) => (d * Math.PI) / 180,
      coords = g.coordinates[0];
    let total = 0;
    for (let i = 0; i < coords.length; i++) {
      const [lon1, lat1] = coords[i],
        [lon2, lat2] = coords[(i + 1) % coords.length];
      total +=
        (toRad(lon2) - toRad(lon1)) *
        (2 + Math.sin(toRad(lat1)) + Math.sin(toRad(lat2)));
    }
    return Math.abs((total * R * R) / 2);
  }
  function refreshAreaStats() {
    const drawn = draw ? draw.getAll().features : [];
    const total = drawn.length + circleAreas.length;
    let areaM2 = 0;
    drawn.forEach((f) => {
      try {
        areaM2 += polyArea(f.geometry);
      } catch (e) {}
    });
    circleAreas.forEach((c) => {
      areaM2 += Math.PI * c.circle.radius_m * c.circle.radius_m;
    });
    $('stat-areas-drawn').textContent = total;
    $('stat-area-km2').textContent = areaM2
      ? (areaM2 / 1e6).toFixed(2) + ' km²'
      : '—';
    // Boundaries changed → refresh the building footprints for the new area.
    if (footprintsOn && !FOOTPRINTS_URL) reloadFootprintsDebounced();
  }

  // ---- collect + apply geographic frame ----
  function collectAreas() {
    const polys = (draw ? draw.getAll().features : []).map((f) => ({
      geometry: f.geometry,
    }));
    return polys.concat(circleAreas);
  }
  on('btn-apply-area', 'click', async () => {
    const areas = collectAreas();
    if (!areas.length) {
      $('apply-area-status').textContent =
        'Define an area first (draw / admin / pin).';
      return;
    }
    // Existing plan: confirm before destroying. New plan: just go.
    if (PLAN_ID) {
      if (!REGENERATE_URL) {
        $('apply-area-status').textContent = 'Regenerate URL not available.';
        return;
      }
      if (
        !confirm(
          'Regenerate work areas? CHW assignments and per-area edits will be wiped.',
        )
      )
        return;
    } else {
      if (!CREATE_PLAN_URL) {
        $('apply-area-status').textContent = 'Create URL not available.';
        return;
      }
    }
    $('btn-apply-area').disabled = true;
    $('apply-area-status').textContent = 'Previewing work areas…';
    try {
      // Generation runs on the Celery worker (cold Overture fetch is tens of
      // seconds) — enqueue then poll, surfacing progress on the status line.
      const prev = await Microplans.enqueueAndPoll(
        PREVIEW_COVERAGE_URL,
        { areas, config: coverageConfig() },
        {
          csrf: CSRF,
          onProgress: (m) => {
            $('apply-area-status').textContent = m;
          },
        },
      );
      if (prev.status !== 'ok') {
        $('apply-area-status').textContent = prev.detail || 'preview failed';
        return;
      }
      renderCoverageReadout(prev.stats);
      const n = prev.areas.features.length;
      $('apply-area-status').textContent = `Generating ${n} work areas…`;
      // Grouping fields only exist when there's a plan UI on the page (review
      // mode); on new-plan mode the backend falls back to its defaults.
      const grpStrategy = $('grp-strategy');
      const grpMax = $('grp-max-buildings');
      const grpBuf = $('grp-buffer-m');
      const grouping = grpStrategy
        ? {
            strategy: grpStrategy.value,
            max_buildings: +grpMax.value,
            buffer_distance_m: +grpBuf.value,
          }
        : {};
      if (PLAN_ID) {
        // Offloaded to Celery (re-materialize + BFS grouping over all cells) — enqueue + poll.
        const data = await Microplans.enqueueAndPoll(
          REGENERATE_URL,
          {
            mode: 'coverage',
            coverage_areas: prev.areas,
            input_areas: areas,
            grouping,
            revision: planRevision,
          },
          {
            csrf: CSRF,
            onProgress: (m) => ($('apply-area-status').textContent = m),
          },
        );
        if (
          conflictResult(data, (m) => ($('apply-area-status').textContent = m))
        )
          return;
        if (data.status !== 'ok') {
          $('apply-area-status').textContent =
            data.detail || 'Regenerate failed.';
          return;
        }
        $(
          'apply-area-status',
        ).textContent = `Regenerated ${data.work_areas.length} work areas.`;
        if (draw) draw.deleteAll();
        circleAreas = [];
        refreshAreaStats();
        prefillSetupForm(data);
        render(data);
        // Work areas changed — the building overlay (if shown) is now stale and
        // would misalign. render() already restamped planRevision, so re-fetching
        // gets the new layout's footprints (and misses the old HTTP cache entry).
        if (footprintsLoaded) loadFootprints();
      } else {
        // New plan: name + region from the inputs (fall back to picked area's
        // label or a sensible default). The server requires at least one of
        // name/region to be non-empty.
        const name = ($('inp-plan-name').value || '').trim();
        const region = ($('inp-plan-region').value || '').trim();
        const state = ($('inp-plan-state')?.value || '').trim();
        if (!name && !region) {
          $('apply-area-status').textContent =
            'Give the plan a name (or region label) first.';
          return;
        }
        // lga/state captured here so the Connect-import CSV export is upload-ready
        // (Connect requires both non-empty). lga defaults to region server-side.
        const resp = await post(CREATE_PLAN_URL, {
          name,
          region,
          lga: region,
          state,
          mode: 'coverage',
          coverage_areas: prev.areas,
          input_areas: areas,
          grouping,
          group_id: GROUP_ID || undefined,
        });
        const data = await resp.json();
        if (!resp.ok || data.status !== 'ok') {
          $('apply-area-status').textContent =
            data.detail || 'HTTP ' + resp.status;
          return;
        }
        // Navigate to the new plan's review URL. The base URL is the
        // back-to-program link's parent + "plan/<id>/review/".
        window.location = PROGRAM_URL + 'plan/' + data.plan_id + '/review/';
      }
    } catch (e) {
      $('apply-area-status').textContent = 'Apply failed: ' + e;
    } finally {
      $('btn-apply-area').disabled = false;
    }
  });

  // initial load — only fetch the plan when one exists.
  if (PLAN_URL) {
    fetch(PLAN_URL)
      .then((r) => r.json())
      .then((d) => {
        if (d.status === 'ok') {
          // Keep the original order so work-area layers stay BELOW the draw control:
          // render (wa-fill/wa-line) → setupAreaDef (draw on top) → overlay (re-adds
          // the ward boundaries into draw + redraws the PSU hulls).
          prefillSetupForm(d);
          render(d);
          setupAreaDef();
          pendingSampling = d;
          drawSamplingOverlay();
        } else {
          const s = $('status');
          if (s) s.textContent = d.detail || 'load failed';
          setupAreaDef();
        }
      })
      .catch((e) => {
        const s = $('status');
        if (s) s.textContent = 'load failed: ' + e;
      });
  } else {
    // No plan yet: init the area-definition surface; tools start disabled.
    setupAreaDef();
    syncPlanTools();
  }

  // ---- sampling mode (two-arm rooftop study) -------------------------------
  // Reuses the existing draw surface + the offloaded preview_frame engine. Each
  // drawn/derived polygon is tagged to a study arm; Generate runs the rooftop
  // sampling (PPS PSUs → primary/alternate pins) per arm and renders both.
  function setMode(m) {
    mpMode = m;
    const samp = m === 'sampling';
    $('btn-mode-coverage')?.classList.toggle('is-on', !samp);
    $('btn-mode-sampling')?.classList.toggle('is-on', samp);
    $('coverage-config')?.classList.toggle('hidden', samp);
    $('sampling-config')?.classList.toggle('hidden', !samp);
    // Coverage commits on "Create plan / Apply"; sampling previews on "Generate".
    $('btn-apply-area')?.classList.toggle('hidden', samp);
    // Sampling groups are the PSUs from generation — geometric auto-grouping is a
    // coverage tool, so swap it for the PSU note in sampling mode. (Assign workers
    // + Bulk regroup stay available for both.)
    $('group-strategy-card')?.classList.toggle('hidden', samp);
    $('sampling-group-note')?.classList.toggle('hidden', !samp);
    // Post-creation exclusion filters are a coverage concept (grid cells).
    $('filter-card')?.classList.toggle('hidden', samp);
    // Show/hide the per-boundary arm pills as we enter/leave two-arm sampling.
    try {
      adminBoundaries?.renderSelected?.();
    } catch (_) {}
  }
  on('btn-mode-coverage', 'click', () => setMode('coverage'));
  on('btn-mode-sampling', 'click', () => setMode('sampling'));
  // Post-creation exclusion filters: live preview as inputs change, persist on Apply.
  ['flt-min-roof', 'flt-exclude-isolated', 'flt-isolation-dist'].forEach((id) =>
    on(id, 'input', previewFilters),
  );
  on('btn-apply-filters', 'click', applyFilters);
  // Clicking the suggestion fills the population field with the picked wards' total,
  // overriding whatever's there (the user asked for this number explicitly).
  on('cfg-pop-suggest', 'click', () => {
    const inp = $('cfg-population');
    const btn = $('cfg-pop-suggest');
    if (inp && btn && btn.dataset.pop) inp.value = btn.dataset.pop;
  });
  // Changing the population source re-totals the picked wards for that source.
  on('cfg-pop-source', 'change', () => {
    const inp = $('cfg-population');
    if (inp) inp.value = ''; // let the new source's total pre-fill
    refreshPopulationSuggestion();
  });

  function setArm(a) {
    currentArm = a;
    $('btn-arm-intervention')?.classList.toggle('is-on', a === 'intervention');
    $('btn-arm-comparison')?.classList.toggle('is-on', a === 'comparison');
  }
  on('btn-arm-intervention', 'click', () => setArm('intervention'));
  on('btn-arm-comparison', 'click', () => setArm('comparison'));

  // Tag freshly user-drawn polygons with the active arm (derived ones are tagged
  // at draw.add above).
  if (map) {
    map.on('draw.create', (e) => {
      if (mpMode !== 'sampling' || !draw) return;
      (e.features || []).forEach((f) => {
        try {
          draw.setFeatureProperty(f.id, 'arm', currentArm);
        } catch (_) {}
      });
    });
  }

  function collectArmAreas() {
    const feats = draw ? draw.getAll().features : [];
    return feats
      .filter(
        (f) =>
          f.geometry &&
          (f.geometry.type === 'Polygon' || f.geometry.type === 'MultiPolygon'),
      )
      .map((f) => ({
        arm: (f.properties && f.properties.arm) || 'intervention',
        geometry: f.geometry,
      }));
  }

  // Picked wards carry a `population` from their boundary source (e.g. GeoPoDe).
  // We sum it across the selected wards and offer it as a one-click fill for the
  // coverage population field, so visits are driven by real numbers instead of a
  // blind guess. The field stays freely editable (manual override always wins).
  // boundaryId -> { name, pops: {source -> number} }. `pops` merges the boundary's
  // per-source bag (extra.populations) with its GeoPoDe under-5 (the population field).
  const pickedWardPops = new Map();
  const POP_SOURCE_LABELS = {
    geopode_u5: 'GeoPoDe (under-5)',
    worldpop_u5: 'WorldPop (under-5)',
    meta_u5: 'Meta (under-5)',
    worldpop_total: 'WorldPop (total)',
    meta_total: 'Meta (total)',
    grid3_v3_total: 'GRID3 v3 (total)',
  };
  const POP_SOURCE_ORDER = Object.keys(POP_SOURCE_LABELS);

  function recordWardPopulation(boundaryId, feature) {
    const f = feature || {};
    const pops = Object.assign({}, f.populations || {});
    if (
      pops.geopode_u5 == null &&
      f.population != null &&
      !isNaN(+f.population)
    )
      pops.geopode_u5 = +f.population;
    if (Object.keys(pops).length)
      pickedWardPops.set(boundaryId, { name: f.name || '', pops });
    refreshPopulationSuggestion();
  }

  function forgetWardPopulation(boundaryId) {
    pickedWardPops.delete(boundaryId);
    refreshPopulationSuggestion();
  }

  // Sources available across the picked wards, in canonical order.
  function availablePopSources() {
    const seen = new Set();
    pickedWardPops.forEach((r) =>
      Object.keys(r.pops).forEach((k) => seen.add(k)),
    );
    return POP_SOURCE_ORDER.filter((k) => seen.has(k));
  }

  function refreshPopulationSuggestion() {
    const sel = $('cfg-pop-source');
    const btn = $('cfg-pop-suggest');
    if (!sel || !btn) return;
    const avail = availablePopSources();
    // Rebuild the dropdown options (keep the current pick if still available).
    const prev = sel.value;
    sel.innerHTML =
      '<option value="">— pick a population source —</option>' +
      avail
        .map((k) => `<option value="${k}">${POP_SOURCE_LABELS[k]}</option>`)
        .join('');
    if (avail.includes(prev)) sel.value = prev;
    else if (avail.includes('geopode_u5')) sel.value = 'geopode_u5';
    sel.parentElement.style.display = avail.length ? '' : 'none';

    const src = sel.value;
    if (!src) {
      btn.classList.add('hidden');
      return;
    }
    let total = 0;
    let n = 0;
    pickedWardPops.forEach((r) => {
      if (r.pops[src] != null) {
        total += r.pops[src];
        n += 1;
      }
    });
    total = Math.round(total);
    btn.dataset.pop = String(total);
    btn.textContent = `Use ${total.toLocaleString()} — ${
      POP_SOURCE_LABELS[src]
    } across ${n} ward${n === 1 ? '' : 's'}`;
    btn.classList.remove('hidden');
    const inp = $('cfg-population');
    if (inp && !String(inp.value || '').trim()) inp.value = total;
  }

  // Coverage config: cell size + the two cell-level exclusion filters + an optional
  // population for visit-weighting. Sent to the coverage preview; the backend
  // (CoverageConfig.from_payload) clamps/validates and is the single source of truth
  // for the filter + expected-visit math.
  function coverageConfig() {
    const pop = parseFloat($('cfg-population')?.value);
    const sources = [...document.querySelectorAll('.cov-src-cb:checked')].map(
      (c) => c.value,
    );
    const conf = parseFloat($('cfg-cov-min-confidence')?.value);
    return {
      cell_size_m: cellSizeM,
      // Empty/all-checked → null = every provider (the backend default).
      sources: sources.length && sources.length < 3 ? sources : null,
      min_confidence: isNaN(conf) ? null : conf,
      // Generation produces ALL occupied cells; the exclusion filters are applied
      // AFTER creation (the "Exclude work areas" card) so they can update live.
      population: isNaN(pop) || pop <= 0 ? null : pop,
    };
  }

  // ---- Post-creation exclusion filters (#7) --------------------------------
  // Each coverage work area carries properties.roof_area_m2 + .dist_to_multi_m
  // (persisted at creation). A work area is a filter match when its total rooftop
  // area is below the threshold, or it's a lone building too far from any cluster.
  const FILTER_REASON = 'auto-filter';
  function filterParams() {
    return {
      minRoof: parseFloat($('flt-min-roof')?.value || '0') || 0,
      isoOn: !!$('flt-exclude-isolated')?.checked,
      isoDist: parseFloat($('flt-isolation-dist')?.value || '150') || 150,
    };
  }
  function matchesExclusion(w, p) {
    const props = w.properties || {};
    const roof = parseFloat(props.roof_area_m2);
    const dist = parseFloat(props.dist_to_multi_m);
    if (p.minRoof > 0 && !isNaN(roof) && roof < p.minRoof) return true;
    if (p.isoOn && w.building_count === 1 && !isNaN(dist) && dist > p.isoDist)
      return true;
    return false;
  }
  function previewFilters() {
    const el = $('filter-preview');
    if (!el) return;
    const p = filterParams();
    const n = (WAS || []).filter((w) => matchesExclusion(w, p)).length;
    const total = (WAS || []).length;
    el.textContent = total
      ? `${n} of ${total} work areas would be excluded (${total - n} kept).`
      : '';
  }
  async function applyFilters() {
    const p = filterParams();
    // Cells that should now be excluded vs auto-excluded cells that no longer match
    // (filter loosened) → re-include. Manual exclusions (other reasons) are untouched.
    const toExclude = (WAS || [])
      .filter((w) => w.status !== 'EXCLUDED' && matchesExclusion(w, p))
      .map((w) => w.id);
    const toInclude = (WAS || [])
      .filter(
        (w) =>
          w.status === 'EXCLUDED' &&
          String(w.excluded_reason || '').startsWith(FILTER_REASON) &&
          !matchesExclusion(w, p),
      )
      .map((w) => w.id);
    if (!toExclude.length && !toInclude.length) {
      $('filter-status').textContent = 'No change.';
      return;
    }
    if (toInclude.length)
      await edit({ action: 'unexclude', wa_ids: toInclude });
    if (toExclude.length)
      await edit({
        action: 'exclude',
        wa_ids: toExclude,
        reason: `${FILTER_REASON}: rooftop<${p.minRoof || 0}m² / isolated`,
      });
    $(
      'filter-status',
    ).textContent = `Excluded ${toExclude.length}, re-included ${toInclude.length}.`;
  }

  // Summarise the coverage preview's exclusion + visit stats under the controls.
  function renderCoverageReadout(stats) {
    const el = $('coverage-visit-readout');
    if (!el) return;
    const s = (stats && stats[0]) || null;
    if (!s) {
      el.textContent = '';
      return;
    }
    const parts = [`${s.work_areas} work areas`];
    const excluded = (s.removed_small_area || 0) + (s.removed_isolated || 0);
    if (excluded)
      parts.push(
        `${excluded} excluded (${s.removed_small_area || 0} small, ${
          s.removed_isolated || 0
        } isolated)`,
      );
    if (s.people_per_building != null)
      parts.push(`${s.people_per_building} people/building`);
    el.textContent = parts.join(' · ');
  }

  // Canonical sampling defaults injected by the page (microplans/sampling/defaults.py
  // via the {% sampling_defaults_json %} tag) — the single source for empty-input
  // fallbacks, so this file never hardcodes a default that could drift from the engine.
  const SAMPLING_DEFAULTS = (function () {
    try {
      return JSON.parse(
        document.getElementById('sampling-defaults').textContent,
      );
    } catch (e) {
      return {};
    }
  })();

  function samplingConfig() {
    const sources = [...document.querySelectorAll('.src-cb:checked')].map(
      (c) => c.value,
    );
    const conf = parseFloat($('cfg-min-confidence')?.value);
    const SD = SAMPLING_DEFAULTS;
    return {
      target_clusters: parseInt(
        $('cfg-target-clusters')?.value || SD.target_clusters,
        10,
      ),
      primary_per_psu: parseInt(
        $('cfg-primary')?.value || SD.primary_per_psu,
        10,
      ),
      alternates_per_psu: parseInt(
        $('cfg-alternate')?.value || SD.alternates_per_psu,
        10,
      ),
      // Size-stratified PPS bands (0/1 = plain PPS). Sent explicitly now that the UI
      // exposes it; ?? not || so an explicit 0 (plain PPS) isn't overridden.
      size_balance_bands: parseInt(
        $('cfg-bands')?.value ?? SD.size_balance_bands,
        10,
      ),
      sources: sources,
      min_confidence: isNaN(conf) ? null : conf,
      // Footprint-size filter (m²); from_payload clamps to sane bounds.
      area_min_m2: parseFloat($('cfg-area-min')?.value || SD.area_min_m2),
      area_max_m2: parseFloat($('cfg-area-max')?.value || SD.area_max_m2),
    };
  }

  // After a sample, show how many buildings each provider contributed (totalled
  // across arms) next to the source checkboxes, so the next pick is informed.

  function renderSample(result) {
    if (!map || !mapReady) return;
    const hulls = result.hulls ||
      result.hulls_geojson || { type: 'FeatureCollection', features: [] };
    const pins = result.pins ||
      result.pins_geojson || { type: 'FeatureCollection', features: [] };
    lastSample = { pins, hulls, stats: result.stats || [] };
    // PSU hulls + sampled pins via the shared PlanLayers component (same paint the
    // monitoring render uses). Interactivity / fitting stays here.
    window.PlanLayers.hulls(map, { data: hulls });
    window.PlanLayers.pins(map, { data: pins });
    const fitData = pins.features && pins.features.length ? pins : hulls;
    if (fitData.features && fitData.features.length)
      Microplans.fitTo(map, fitData, {
        maxZoom: 16,
        animate: false,
        duration: 0,
      });
    renderArmStats(result.stats || []);
    renderRationale(result.stats || []);
    renderSourceCounts(result.stats || []);
    // Two-arm plan → render the shared comparability panel. One call site here means
    // it shows on page load, after Generate, and after Regenerate alike.
    updateComparability(result.stats || []);
  }

  // One action: draw a fresh random sample AND commit it in a single click — no
  // separate Save step. New plan → create it + open its page; existing plan →
  // rebuild its work areas in place. The draw is non-deterministic (the server
  // reseeds each call), so every "Regenerate plan" yields different PSUs +
  // households. mode="sampling" → the server materializes one tiny work area per
  // pin, with arm stored labs-side, so the shared plan stays blind to arm.
  on('btn-generate-sample', 'click', async () => {
    const statusEl = $('generate-sample-status');
    if (!PREVIEW_FRAME_URL) {
      if (statusEl) statusEl.textContent = 'Sampling endpoint unavailable.';
      return;
    }
    const areas = collectArmAreas();
    if (!areas.length) {
      if (statusEl)
        statusEl.textContent = 'Draw or derive at least one area first.';
      return;
    }
    const btn = $('btn-generate-sample');
    if (btn) btn.disabled = true;
    if (statusEl)
      statusEl.textContent =
        'Sampling… fetching buildings + selecting (this can take ~30s).';
    try {
      // 1. Draw the sample (PSUs + pins) and paint it on the map.
      const result = await Microplans.enqueueAndPoll(
        PREVIEW_FRAME_URL,
        { areas, config: samplingConfig() },
        { csrf: CSRF },
      );
      renderSample(result);
      if (!lastSample || !(lastSample.pins.features || []).length) {
        if (statusEl)
          statusEl.textContent =
            'No buildings sampled — try a larger area or different sources.';
        return;
      }
      // 2. Commit it in the same click.
      if (PLAN_ID) {
        // Existing sampling plan → rebuild its work areas in place from this draw.
        // No confirm: the click IS the action (re-rolling a plan under design).
        if (!REGENERATE_URL) {
          if (statusEl) statusEl.textContent = 'Regenerate URL not available.';
          return;
        }
        if (statusEl) statusEl.textContent = 'Rebuilding work areas…';
        const data = await Microplans.enqueueAndPoll(
          REGENERATE_URL,
          {
            mode: 'sampling',
            pins: lastSample.pins,
            hulls: lastSample.hulls,
            input_areas: areas,
            stats: lastSample.stats,
            grouping: {},
            revision: planRevision,
          },
          {
            csrf: CSRF,
            onProgress: (m) => {
              if (statusEl) statusEl.textContent = m;
            },
          },
        );
        if (
          conflictResult(data, (m) => {
            if (statusEl) statusEl.textContent = m;
          })
        )
          return;
        if (data.status !== 'ok') {
          if (statusEl)
            statusEl.textContent = data.detail || 'Regenerate failed.';
          return;
        }
        applyPlanData(data); // work areas + replayed boundaries/hulls + Sample details
        if (footprintsLoaded) loadFootprints();
        if (statusEl)
          statusEl.textContent = `Rebuilt ${data.work_areas.length} work areas from the new draw.`;
        return;
      }
      // New plan → create it from this draw, then hydrate the page in place (no
      // reload): adopt the new plan's id + URLs, flip the button to "Regenerate
      // plan", and render it exactly like an opened plan.
      if (!CREATE_PLAN_URL) {
        if (statusEl) statusEl.textContent = 'Save unavailable on this page.';
        return;
      }
      const name = ($('inp-plan-name')?.value || '').trim();
      const region = ($('inp-plan-region')?.value || '').trim();
      if (!name && !region) {
        if (statusEl)
          statusEl.textContent = 'Give the plan a name (or region) first.';
        return;
      }
      if (statusEl) statusEl.textContent = 'Creating plan…';
      const resp = await post(CREATE_PLAN_URL, {
        name,
        region,
        lga: region,
        state: ($('inp-plan-state')?.value || '').trim(),
        mode: 'sampling',
        pins: lastSample.pins,
        hulls: lastSample.hulls,
        input_areas: areas,
        stats: lastSample.stats,
        grouping: {},
        group_id: GROUP_ID || undefined,
      });
      const data = await resp.json();
      if (!resp.ok || data.status !== 'ok') {
        if (statusEl)
          statusEl.textContent = data.detail || 'HTTP ' + resp.status;
        return;
      }
      // Adopt the created plan's identity + plan-scoped URLs without navigating.
      PLAN_ID = String(data.plan_id);
      const u = data.urls || {};
      if (u.regenerate) REGENERATE_URL = u.regenerate;
      if (u.plan) PLAN_URL = u.plan;
      if (u.footprints) FOOTPRINTS_URL = u.footprints;
      if (u.regroup) REGROUP_URL = u.regroup;
      if (u.reassign) REASSIGN_URL = u.reassign;
      if (u.csv) CSV_URL = u.csv;
      if (u.edit) EDIT_URL = u.edit;
      if (u.review) {
        try {
          history.pushState({}, '', u.review);
        } catch (_) {}
      }
      const gbtn = $('btn-generate-sample');
      if (gbtn) gbtn.textContent = 'Regenerate plan';
      const ghint = $('generate-plan-hint');
      if (ghint)
        ghint.textContent =
          "Re-rolls a fresh random sample and rebuilds this plan's work areas.";
      applyPlanData(data); // boundaries + hulls + work areas + enabled tools (== opened)
      if (statusEl)
        statusEl.textContent = `Plan created — ${
          (data.work_areas || []).length
        } work areas.`;
    } catch (e) {
      if (statusEl)
        statusEl.textContent = 'Failed: ' + (e && e.message ? e.message : e);
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  window.__review = {
    get was() {
      return WAS;
    },
    get selected() {
      return [...selected];
    },
    edit,
    get activeDim() {
      return activeDim;
    },
    get colorDim() {
      return colorDim;
    },
    get map() {
      return map;
    },
    get panel() {
      return mapPanel;
    },
    // Diagnostic handles for the boundary-rail rehydration path.
    get adminBoundaries() {
      return adminBoundaries;
    },
    get pendingBoundaryRestore() {
      return pendingBoundaryRestore;
    },
    get mode() {
      return mpMode;
    },
    get arm() {
      return currentArm;
    },
    setMode,
    setArm,
    setActiveDim,
    setColorDim,
  };
})();
