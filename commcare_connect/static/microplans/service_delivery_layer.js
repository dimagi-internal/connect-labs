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
 *     opps: [{id, name}],        // selectable opportunities
 *     currentOppId,              // pre-checked opp
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

    const post = (url, body) =>
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify(body),
      });

    let loadedFeatures = []; // last-rendered point features (source of derive coords)
    let pipelinesLoaded = false;

    // ---- panel markup -------------------------------------------------------
    const panel = el('div', { class: 'sd-panel' });
    const oppRows = opps
      .map(
        (o) =>
          `<label class="flex items-center gap-2 text-xs cursor-pointer py-0.5">
             <input type="checkbox" class="sd-opp rounded" value="${o.id}" ${
               o.id === currentOppId ? 'checked' : ''
             }>
             <span class="truncate" title="${o.name.replace(/"/g, '&quot;')}">${
               o.name
             }</span>
           </label>`,
      )
      .join('');

    panel.innerHTML = `
      <div class="mp-section-label">Service delivery data</div>
      <div class="text-xs text-gray-500 mb-1">Overlay visit GPS for one or more opportunities.</div>
      <div class="sd-opps max-h-32 overflow-auto border rounded p-1 bg-white">${
        oppRows ||
        '<div class="text-xs text-gray-400 p-1">No opportunities available.</div>'
      }</div>
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
    const selectedOppIds = () =>
      Array.from(panel.querySelectorAll('.sd-opp:checked')).map((c) =>
        Number(c.value),
      );

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

    function fitTo(features) {
      if (!features.length) return;
      const b = new mapboxgl.LngLatBounds();
      features.forEach((f) => b.extend(f.geometry.coordinates));
      if (!b.isEmpty()) map.fitBounds(b, { padding: 60, maxZoom: 15 });
    }

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
