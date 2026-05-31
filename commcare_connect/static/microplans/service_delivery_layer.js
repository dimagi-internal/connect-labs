/*
 * ServiceDeliveryLayer — a reusable Mapbox GL overlay for an opportunity's
 * service-delivery GPS points, plus "derive a boundary polygon from the point
 * cloud". Self-contained: it renders its own control panel into a mount element,
 * owns its map source/layer/popup, and calls back to the host to hand off the
 * derived boundary (so the host can drop it into a draw layer / area input).
 *
 * Host contract:
 *   ServiceDeliveryLayer.create({
 *     map,                       // a mapboxgl.Map
 *     mount,                     // an element to render the panel into
 *     csrf,                      // CSRF token string
 *     opps: [{id, name, program_name?, visit_count?}], // searchable opportunities
 *     currentOppId,              // pre-selected opp (added as the first chip)
 *     urls: { preview, pipelines, derive },
 *     onBoundary: (feature) => {}, // optional: receives the derived GeoJSON Feature
 *   }) -> controller { destroy() }
 *
 * Used by microplans/setup.html today; designed to be dropped into a feature
 * report page unchanged.
 */
(function (global) {
  'use strict';

  const SRC = 'sd-points';
  const LAYER = 'sd-points-circle';

  // Mirrors OPP_COLORS in microplans/service_delivery/points.py so the chip
  // swatch a user sees matches the server-assigned layer color (both index by
  // selection order).
  const OPP_COLORS = [
    '#2563eb',
    '#dc2626',
    '#16a34a',
    '#d97706',
    '#7c3aed',
    '#0891b2',
    '#db2777',
    '#65a30d',
    '#ea580c',
    '#4f46e5',
  ];
  const colorFor = (i) => OPP_COLORS[i % OPP_COLORS.length];

  const esc = Microplans.esc;

  function el(tag, attrs, html) {
    const e = document.createElement(tag);
    if (attrs)
      Object.entries(attrs).forEach(([k, v]) =>
        k === 'class' ? (e.className = v) : e.setAttribute(k, v),
      );
    if (html != null) e.innerHTML = html;
    return e;
  }

  function create(opts) {
    const { map, mount, csrf, urls } = opts;
    const opps = opts.opps || [];
    const currentOppId =
      opts.currentOppId != null ? Number(opts.currentOppId) : null;
    const onBoundary = opts.onBoundary || function () {};

    const post = (url, body, o) =>
      Microplans.post(url, body, Object.assign({ csrf }, o || {}));

    let loadedFeatures = []; // last-rendered point features (source of derive coords)
    let pipelinesLoaded = false;

    // ---- opp selection state (searchable multiselect) -----------------------
    const oppById = new Map(opps.map((o) => [Number(o.id), o]));
    const selected = []; // opp ids, in selection order (drives server colors)
    if (currentOppId != null && oppById.has(currentOppId))
      selected.push(currentOppId);

    // ---- panel markup -------------------------------------------------------
    const panel = el('div', { class: 'sd-panel' });
    panel.innerHTML = `
      <div class="mp-section-label">Service delivery data</div>
      <div class="text-xs text-gray-500 mb-1">Search opportunities to overlay their visit GPS. Add as many as you like.</div>
      <div class="sd-picker relative">
        <input type="text" class="sd-search base-input text-xs w-full" placeholder="Search by name, program, or id…" autocomplete="off">
        <div class="sd-results hidden absolute z-20 left-0 right-0 mt-1 max-h-56 overflow-auto bg-white border rounded shadow-lg"></div>
      </div>
      <div class="sd-chips flex flex-wrap gap-1 mt-2"></div>
      <label class="block mt-2"><span class="text-gray-600 text-xs">Data source</span>
        <select class="sd-pipeline base-input mt-1 text-xs"><option value="default">Default — device GPS (any app)</option></select>
      </label>
      <button type="button" class="sd-load button mt-2 w-full text-sm">Show service delivery points</button>
      <div class="sd-status text-xs text-gray-600 mt-2"></div>
      <div class="sd-legend mt-2 space-y-1"></div>

      <div class="sd-derive hidden mt-3 pt-3 border-t">
        <div class="mp-section-label">Boundary from points</div>
        <div class="text-xs text-gray-500 mb-1">Use the border of the delivery data as a planning area.</div>
        <div class="flex items-center gap-3 text-xs mb-1">
          <label class="flex items-center gap-1 cursor-pointer"><input type="radio" name="sd-method" value="concave" checked> Concave</label>
          <label class="flex items-center gap-1 cursor-pointer"><input type="radio" name="sd-method" value="convex"> Convex</label>
        </div>
        <label class="block text-xs sd-tightness-wrap">Tightness
          <input type="range" class="sd-tightness w-full" min="0.05" max="1" step="0.05" value="0.3">
        </label>
        <label class="block text-xs">Outward buffer (m)
          <input type="number" class="sd-buffer base-input mt-1 text-xs" value="25" min="0" step="5">
        </label>
        <button type="button" class="sd-derive-btn button mt-2 w-full text-sm">Add boundary as planning area</button>
      </div>
    `;
    mount.appendChild(panel);

    const q = (sel) => panel.querySelector(sel);
    const status = (t) => (q('.sd-status').textContent = t || '');
    const selectedOppIds = () => selected.slice();

    // ---- searchable multiselect (mirrors the labs context picker) -----------
    function oppMatches(o, term) {
      if (!term) return true;
      const hay = `${o.name || ''} ${o.program_name || ''} ${
        o.id
      }`.toLowerCase();
      return term.split(/\s+/).every((t) => hay.includes(t));
    }
    function renderChips() {
      const box = q('.sd-chips');
      if (!selected.length) {
        box.innerHTML =
          '<span class="text-xs text-gray-400">No opportunities selected.</span>';
        return;
      }
      box.innerHTML = selected
        .map((id, i) => {
          const o = oppById.get(id) || { name: `Opp #${id}`, id };
          return `<span class="sd-chip inline-flex items-center gap-1 pl-1 pr-1.5 py-0.5 rounded text-xs bg-gray-100 border" data-id="${id}">
            <span style="background:${colorFor(
              i,
            )};width:9px;height:9px;border-radius:9999px;display:inline-block"></span>
            <span class="truncate max-w-[9rem]" title="${esc(
              o.name,
            )} (id ${id})">${esc(o.name)}</span>
            <button type="button" class="sd-chip-x text-gray-400 hover:text-red-600 leading-none" data-id="${id}" title="Remove">&times;</button>
          </span>`;
        })
        .join('');
      box.querySelectorAll('.sd-chip-x').forEach((b) =>
        b.addEventListener('click', () => {
          const id = Number(b.dataset.id);
          const idx = selected.indexOf(id);
          if (idx > -1) selected.splice(idx, 1);
          renderChips();
        }),
      );
    }
    function renderResults() {
      const term = q('.sd-search').value.trim().toLowerCase();
      const box = q('.sd-results');
      const hits = opps
        .filter((o) => !selected.includes(Number(o.id)) && oppMatches(o, term))
        .slice(0, 40);
      if (!hits.length) {
        box.innerHTML =
          '<div class="px-3 py-2 text-xs text-gray-400">No matches.</div>';
      } else {
        box.innerHTML = hits
          .map(
            (o) =>
              `<button type="button" class="sd-result w-full text-left px-3 py-1.5 text-xs hover:bg-purple-50" data-id="${
                o.id
              }">
                <div class="font-medium truncate" title="${esc(o.name)}">${esc(
                  o.name,
                )}</div>
                <div class="text-gray-500">ID ${o.id}${
                  o.program_name ? ' · ' + esc(o.program_name) : ''
                } · ${o.visit_count || 0} visits</div>
              </button>`,
          )
          .join('');
        box.querySelectorAll('.sd-result').forEach((r) =>
          r.addEventListener('click', () => {
            const id = Number(r.dataset.id);
            if (!selected.includes(id)) selected.push(id);
            q('.sd-search').value = '';
            box.classList.add('hidden');
            renderChips();
          }),
        );
      }
      box.classList.remove('hidden');
    }
    q('.sd-search').addEventListener('input', renderResults);
    q('.sd-search').addEventListener('focus', renderResults);
    const onDocClick = (e) => {
      if (!q('.sd-picker').contains(e.target))
        q('.sd-results').classList.add('hidden');
    };
    document.addEventListener('click', onDocClick);
    renderChips();

    // ---- pipeline dropdown (lazy) ------------------------------------------
    async function loadPipelines() {
      if (pipelinesLoaded || !urls.pipelines) return;
      pipelinesLoaded = true;
      try {
        const resp = await fetch(urls.pipelines, {
          headers: { 'X-CSRFToken': csrf },
        });
        const data = await resp.json();
        if (data.status === 'ok' && data.pipelines) {
          const sel = q('.sd-pipeline');
          sel.innerHTML = data.pipelines
            .map((p) => `<option value="${p.id}">${p.name}</option>`)
            .join('');
        }
      } catch (e) {
        /* keep the default option */
      }
    }
    q('.sd-pipeline').addEventListener('mousedown', loadPipelines, {
      once: true,
    });

    // ---- map layer ----------------------------------------------------------
    function ensureLayer() {
      if (!map.getSource(SRC)) {
        map.addSource(SRC, {
          type: 'geojson',
          data: { type: 'FeatureCollection', features: [] },
        });
      }
      if (!map.getLayer(LAYER)) {
        map.addLayer({
          id: LAYER,
          type: 'circle',
          source: SRC,
          paint: {
            'circle-radius': [
              'interpolate',
              ['linear'],
              ['zoom'],
              8,
              2.5,
              14,
              5,
            ],
            'circle-color': ['coalesce', ['get', 'color'], '#2563eb'],
            'circle-stroke-color': '#fff',
            'circle-stroke-width': 0.6,
            'circle-opacity': 0.85,
          },
        });
        wirePopup();
      } else {
        map.setLayoutProperty(LAYER, 'visibility', 'visible');
      }
    }

    let popup = null;
    function wirePopup() {
      map.on(
        'mouseenter',
        LAYER,
        () => (map.getCanvas().style.cursor = 'pointer'),
      );
      map.on('mouseleave', LAYER, () => {
        map.getCanvas().style.cursor = '';
        if (popup) {
          popup.remove();
          popup = null;
        }
      });
      map.on('click', LAYER, (e) => {
        const f = e.features && e.features[0];
        if (!f) return;
        const p = f.properties || {};
        const rows = [
          ['Opportunity', p.name || p.opportunity_id],
          ['FLW', p.username],
          ['Status', p.status],
          ['Date', p.visit_date],
          ['Entity', p.entity_name],
        ].filter(([, v]) => v != null && v !== '');
        const html = rows
          .map(
            ([k, v]) => `<div><span style="color:#888">${k}:</span> ${v}</div>`,
          )
          .join('');
        if (popup) popup.remove();
        popup = new mapboxgl.Popup({ closeButton: false })
          .setLngLat(f.geometry.coordinates)
          .setHTML(
            `<div style="font-size:11px;line-height:1.4">${
              html || 'visit'
            }</div>`,
          )
          .addTo(map);
      });
    }

    const fitTo = (features) =>
      Microplans.fitTo(map, features, { padding: 60, maxZoom: 15 });

    function renderLegend(layers) {
      q('.sd-legend').innerHTML = layers
        .map((L) => {
          const s = L.stats || {};
          const err = L.error
            ? `<div class="text-red-600">${L.error}</div>`
            : `<div class="text-gray-500">${(
                s.with_gps || 0
              ).toLocaleString()} pts · ${s.gps_pct || 0}% w/ GPS</div>`;
          return `<div class="flex items-start gap-2 text-xs">
            <span style="background:${L.color};width:10px;height:10px;border-radius:9999px;display:inline-block;margin-top:3px;flex:0 0 auto"></span>
            <div><div class="font-medium truncate">${L.name}</div>${err}</div>
          </div>`;
        })
        .join('');
    }

    // ---- load points --------------------------------------------------------
    async function loadPoints() {
      const opp_ids = selectedOppIds();
      if (!opp_ids.length) {
        status('Select at least one opportunity.');
        return;
      }
      status('Fetching service-delivery points…');
      q('.sd-load').disabled = true;
      try {
        const resp = await post(urls.preview, {
          opp_ids,
          pipeline_id: q('.sd-pipeline').value,
        });
        const data = await resp.json();
        if (data.auth_error === 'commcare_hq') {
          status('CommCare HQ authorization required.');
          if (data.auth_authorize_url)
            window.open(data.auth_authorize_url, '_blank');
          return;
        }
        if (!resp.ok || data.status !== 'ok') {
          status(data.detail || 'HTTP ' + resp.status);
          return;
        }
        ensureLayer();
        map.getSource(SRC).setData(data.points);
        loadedFeatures = data.points.features || [];
        renderLegend(data.layers || []);
        fitTo(loadedFeatures);
        q('.sd-derive').classList.toggle('hidden', loadedFeatures.length === 0);
        status(
          `${data.count.toLocaleString()} points across ${
            (data.layers || []).length
          } opp(s).`,
        );
      } catch (e) {
        status('Failed: ' + e);
      } finally {
        q('.sd-load').disabled = false;
      }
    }
    q('.sd-load').addEventListener('click', loadPoints);

    // tightness only meaningful for concave
    function syncMethodUI() {
      const concave =
        panel.querySelector('input[name="sd-method"]:checked').value ===
        'concave';
      q('.sd-tightness-wrap').style.opacity = concave ? '1' : '0.4';
      q('.sd-tightness').disabled = !concave;
    }
    panel
      .querySelectorAll('input[name="sd-method"]')
      .forEach((r) => r.addEventListener('change', syncMethodUI));

    // ---- derive boundary ----------------------------------------------------
    async function deriveBoundary() {
      if (!loadedFeatures.length) {
        status('Load points first.');
        return;
      }
      const coords = loadedFeatures.map((f) => f.geometry.coordinates);
      const method = panel.querySelector(
        'input[name="sd-method"]:checked',
      ).value;
      status('Deriving boundary…');
      q('.sd-derive-btn').disabled = true;
      try {
        const resp = await post(urls.derive, {
          coords,
          method,
          concavity: Number(q('.sd-tightness').value),
          buffer_m: Number(q('.sd-buffer').value),
        });
        const data = await resp.json();
        if (!resp.ok || data.status !== 'ok') {
          status(data.detail || 'HTTP ' + resp.status);
          return;
        }
        onBoundary(data.boundary);
        status(
          `Boundary added from ${data.point_count.toLocaleString()} points.`,
        );
      } catch (e) {
        status('Derive failed: ' + e);
      } finally {
        q('.sd-derive-btn').disabled = false;
      }
    }
    q('.sd-derive-btn').addEventListener('click', deriveBoundary);

    return {
      destroy() {
        document.removeEventListener('click', onDocClick);
        if (map.getLayer(LAYER)) map.removeLayer(LAYER);
        if (map.getSource(SRC)) map.removeSource(SRC);
        if (popup) popup.remove();
        panel.remove();
      },
      loadPoints,
    };
  }

  global.ServiceDeliveryLayer = { create };
})(window);
