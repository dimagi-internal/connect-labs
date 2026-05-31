/*
 * Microplans "Boundaries" layer — admin boundaries as a map-panel layer.
 *
 * Renders the admin boundaries we have for the opp's region as outlines, across
 * ALL available levels at once (generic over each country's hierarchy depth).
 * Toggle on -> fetch the viewport endpoint for the current map bounds and draw
 * one purple outline layer (finer levels thinner). Re-fetches on moveend.
 *
 *  - Smallest-wins inspect: hover/click hit-tests to the smallest (highest
 *    admin_level) boundary at the point and shows it in the panel's Inspect tab.
 *    Hover = transient preview; click = pin. No floating popup.
 *  - One active SOURCE at a time (labs / overture) via a dropdown in the body.
 *  - Area phase only: Shift/Cmd-click (or a search-result click) toggles the
 *    boundary into/out of the plan's selected area set; the host adds/removes the
 *    geometry on the draw control via onAreaAdd / onAreaRemove.
 *
 *   MicroplansAdminBoundaries.register({
 *     panel, map, csrf,
 *     urls: { viewport: BOUNDARY_VIEWPORT_URL, geometry: ADMIN_AREA_GEOMETRY_URL },
 *     getCountryIso: () => 'NGA' | null,     // opp's country, optional (labs); needed for Overture
 *     isAreaPhase: () => true,               // gate area mutation
 *     onAreaAdd: (boundaryId, geometry, feature) => {},   // host: draw.add + refreshAreaStats
 *     onAreaRemove: (boundaryId) => {},                   // host: remove from draw + refresh
 *   });
 */
(function (global) {
  'use strict';

  const COLOR = '#a855f7';
  const SRC = 'mp-admin';
  const LINE = 'mp-admin-line';
  const HOVER = 'mp-admin-hover';
  const SEL_SRC = 'mp-admin-sel';
  const SEL_FILL = 'mp-admin-sel-fill';
  const SEL_LINE = 'mp-admin-sel-line';

  function register(opts) {
    const map = opts.map;
    const panel = opts.panel;
    const urls = opts.urls || {};
    const M = global.Microplans;
    const esc = (M && M.esc) || ((s) => String(s == null ? '' : s));
    const getCountryIso = opts.getCountryIso || (() => null);
    const isAreaPhase = opts.isAreaPhase || (() => false);
    const onAreaAdd = opts.onAreaAdd || function () {};
    const onAreaRemove = opts.onAreaRemove || function () {};
    const post = (url, body) => M.post(url, body, { csrf: opts.csrf });

    let source = null; // active source; null = let the server pick the country default
    let detectedIso = null; // country inferred from returned features (labs carry iso_code)
    let availableSources = [];
    let sourceLabels = {};
    let features = []; // current viewport features
    let truncated = false;
    let pinned = null; // pinned inspect HTML (click)
    let fetchCtrl = null;
    // boundaryId -> { feature, geometry } for the selected-area set
    const selected = new Map();

    // ---- panel body: source picker + search + summary ----
    const body = document.createElement('div');
    body.innerHTML = `
      <label class="block mb-1.5">
        <span class="text-gray-600">Source</span>
        <select class="mp-ab-source base-input mt-0.5 text-xs"></select>
      </label>
      <input class="mp-ab-search base-input text-xs w-full" type="text"
             placeholder="search boundaries in view…">
      <div class="mp-ab-status text-[10px] text-gray-500 mt-1"></div>
      <div class="mp-ab-results max-h-32 overflow-y-auto"></div>
      <div class="mp-ab-summary text-[11px] font-medium text-purple-700 mt-1"></div>
      <p class="mp-ab-hint text-[10px] text-gray-400 mt-1"></p>`;
    const sourceSel = body.querySelector('.mp-ab-source');
    const searchEl = body.querySelector('.mp-ab-search');
    const statusEl = body.querySelector('.mp-ab-status');
    const resultsEl = body.querySelector('.mp-ab-results');
    const summaryEl = body.querySelector('.mp-ab-summary');
    const hintEl = body.querySelector('.mp-ab-hint');

    function setStatus(t) {
      statusEl.textContent = t || '';
    }
    function renderSourceOptions() {
      sourceSel.innerHTML = (availableSources || [])
        .map(
          (n) =>
            `<option value="${esc(n)}"${n === source ? ' selected' : ''}>${esc(
              sourceLabels[n] || n,
            )}</option>`,
        )
        .join('');
      sourceSel.parentElement.style.display =
        availableSources.length > 1 ? '' : 'none';
    }
    function renderSummary() {
      if (!selected.size) {
        summaryEl.textContent = '';
        return;
      }
      let km2 = 0;
      selected.forEach((v) => {
        km2 += (v.desc && v.desc.area_km2) || 0;
      });
      summaryEl.textContent = `${selected.size} selected · ${Math.round(
        km2,
      ).toLocaleString()} km²`;
    }
    function renderHint() {
      hintEl.textContent = isAreaPhase()
        ? 'Shift/⌘-click a boundary to add it to the plan area, or click a search result.'
        : 'Click a boundary to inspect it.';
    }

    // ---- map layers ----
    function ensureLayers() {
      if (!map.getSource(SRC)) {
        map.addSource(SRC, { type: 'geojson', data: empty() });
        map.addLayer({
          id: LINE,
          type: 'line',
          source: SRC,
          paint: {
            'line-color': COLOR,
            // coarser level (lower number) = thicker; finer = thinner
            'line-width': [
              'interpolate',
              ['linear'],
              ['coalesce', ['get', 'admin_level'], 1],
              1,
              2.4,
              2,
              1.4,
              4,
              0.7,
            ],
            'line-opacity': 0.85,
          },
        });
        map.addLayer({
          id: HOVER,
          type: 'line',
          source: SRC,
          filter: ['==', ['get', 'boundary_id'], '__none__'],
          paint: { 'line-color': COLOR, 'line-width': 3.2, 'line-opacity': 1 },
        });
      }
      if (!map.getSource(SEL_SRC)) {
        map.addSource(SEL_SRC, { type: 'geojson', data: empty() });
        map.addLayer({
          id: SEL_FILL,
          type: 'fill',
          source: SEL_SRC,
          paint: { 'fill-color': COLOR, 'fill-opacity': 0.18 },
        });
        map.addLayer({
          id: SEL_LINE,
          type: 'line',
          source: SEL_SRC,
          paint: { 'line-color': COLOR, 'line-width': 1.6 },
        });
      }
    }
    function empty() {
      return { type: 'FeatureCollection', features: [] };
    }
    function setVisible(on) {
      [LINE, HOVER, SEL_FILL, SEL_LINE].forEach((id) => {
        if (map.getLayer(id))
          map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
      });
    }
    function teardown() {
      M.removeSourceAndLayers(map, SRC, [LINE, HOVER]);
      M.removeSourceAndLayers(map, SEL_SRC, [SEL_FILL, SEL_LINE]);
    }

    // ---- viewport fetch ----
    async function refresh() {
      if (!layer.on || !urls.viewport) return;
      const b = map.getBounds();
      const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
        .map((n) => n.toFixed(5))
        .join(',');
      const params = new URLSearchParams({
        bbox,
        zoom: map.getZoom().toFixed(1),
      });
      // Prefer the host-supplied country; otherwise reuse the one we inferred from a
      // prior labs response so switching to Overture (which needs an iso) works.
      const iso = getCountryIso() || detectedIso;
      if (iso) params.set('iso', iso);
      if (source) params.set('source', source);
      if (fetchCtrl) fetchCtrl.abort();
      fetchCtrl = new AbortController();
      setStatus('Loading boundaries…');
      try {
        const data = await M.apiGet(urls.viewport + '?' + params.toString(), {
          signal: fetchCtrl.signal,
        });
        if (data.status !== 'ok') {
          setStatus(data.detail || 'Could not load boundaries');
          return;
        }
        features = data.features || [];
        truncated = !!data.truncated;
        if (!detectedIso && features.length)
          detectedIso = features[0].properties.iso_code || null;
        availableSources = data.available_sources || [];
        sourceLabels = data.source_labels || {};
        source = data.source || source;
        ensureLayers();
        map.getSource(SRC).setData({ type: 'FeatureCollection', features });
        renderSourceOptions();
        applySearch();
        setStatus(
          `${features.length.toLocaleString()} boundaries${
            truncated ? ' · zoom in to see all' : ''
          }`,
        );
      } catch (e) {
        if (e && e.name === 'AbortError') return;
        setStatus('Failed: ' + e);
      }
    }
    const refreshDebounced = M.debounce(refresh, 350);

    // ---- smallest-wins resolution ----
    function smallestAt(point) {
      const hits = map.queryRenderedFeatures(point, { layers: [LINE] });
      if (!hits.length) return null;
      // highest admin_level = smallest (most granular) boundary
      return hits.reduce((a, b) =>
        (b.properties.admin_level || 0) > (a.properties.admin_level || 0)
          ? b
          : a,
      );
    }
    function parentOf(point, level) {
      // next-coarser feature at the same point, for the "select parent" affordance
      const hits = map.queryRenderedFeatures(point, { layers: [LINE] });
      const coarser = hits.filter(
        (f) => (f.properties.admin_level || 0) < level,
      );
      if (!coarser.length) return null;
      return coarser.reduce((a, b) =>
        (b.properties.admin_level || 0) > (a.properties.admin_level || 0)
          ? b
          : a,
      );
    }

    function inspectHTML(f, point) {
      const p = f.properties || {};
      const rows = [];
      if (p.parent_name) rows.push(['Parent', p.parent_name]);
      rows.push([
        'Admin level',
        'ADM' + (p.admin_level != null ? p.admin_level : '?'),
      ]);
      if (p.iso_code) rows.push(['Country', p.iso_code]);
      if (p.area_km2 != null)
        rows.push(['Area', Math.round(p.area_km2).toLocaleString() + ' km²']);
      if (p.population != null)
        rows.push(['Population', Math.round(p.population).toLocaleString()]);
      const isSel = selected.has(p.boundary_id);
      const body = [
        `<div style="font-weight:700;margin-bottom:.25rem">${esc(
          p.name || 'Boundary',
        )}</div>`,
        `<div style="font-size:.62rem;color:#6b7280">via ${esc(
          sourceLabels[p.source] || p.source || '',
        )}</div>`,
        '<table style="margin-top:.35rem;font-size:.66rem;color:#374151">' +
          rows
            .map(
              ([k, v]) =>
                `<tr><td style="color:#9ca3af;padding-right:.5rem">${esc(
                  k,
                )}</td><td>${esc(v)}</td></tr>`,
            )
            .join('') +
          '</table>',
      ];
      if (isAreaPhase()) {
        body.push(
          `<button type="button" class="mp-ab-act button button-sm ${
            isSel ? 'outline-style' : 'primary-dark'
          } text-xs mt-2 w-full">` +
            `${isSel ? 'Remove from area' : 'Add to area'}</button>`,
        );
        const par = point ? parentOf(point, p.admin_level || 0) : null;
        if (par) {
          body.push(
            `<button type="button" class="mp-ab-parent text-[11px] text-purple-700 mt-1.5 block">↑ Select parent: ${esc(
              par.properties.name,
            )}</button>`,
          );
        }
      }
      const node = document.createElement('div');
      node.innerHTML = body.join('');
      const act = node.querySelector('.mp-ab-act');
      if (act) act.addEventListener('click', () => toggleSelect(featToDesc(f)));
      const par = node.querySelector('.mp-ab-parent');
      if (par && point) {
        par.addEventListener('click', () => {
          const pf = parentOf(point, p.admin_level || 0);
          if (pf) pinInspect(pf, point);
        });
      }
      return node;
    }

    function showHover(f, point) {
      if (map.getLayer(HOVER))
        map.setFilter(HOVER, [
          '==',
          ['get', 'boundary_id'],
          f.properties.boundary_id,
        ]);
      panel.setInspect(inspectHTML(f, point), false);
    }
    function clearHover() {
      if (map.getLayer(HOVER))
        map.setFilter(HOVER, ['==', ['get', 'boundary_id'], '__none__']);
      if (pinned) panel.setInspect(pinned, false);
      else panel.clearInspect();
    }
    function pinInspect(f, point) {
      const node = inspectHTML(f, point);
      pinned = node.cloneNode(true);
      // re-bind handlers on the pinned clone
      const act = pinned.querySelector('.mp-ab-act');
      if (act) act.addEventListener('click', () => toggleSelect(featToDesc(f)));
      panel.setInspect(node, true);
    }

    // ---- area selection ----
    // Both map features and country-wide search rows funnel through a normalised
    // "descriptor" so selection + geometry-fetch is identical for either path.
    function featToDesc(f) {
      const p = f.properties || {};
      return {
        key: p.boundary_id,
        name: p.name,
        level: p.admin_level,
        source: p.source,
        country: p.iso_code,
        ref: p.ref || {},
        area_km2: p.area_km2,
      };
    }
    function rowToDesc(a) {
      const ref = a.ref || {};
      const key = ref.boundary_id || `${a.source}:${a.region || ''}:${a.name}`;
      return {
        key,
        name: a.name,
        level: a.level,
        source: a.source,
        country: a.country,
        ref,
        area_km2: a.area_km2,
      };
    }
    async function toggleSelect(desc) {
      const id = desc.key;
      if (selected.has(id)) {
        selected.delete(id);
        onAreaRemove(id);
        syncSelectedSource();
        renderSummary();
        return;
      }
      setStatus('Fetching geometry…');
      try {
        const area = {
          name: desc.name,
          level: desc.level,
          source: desc.source,
          country: desc.country,
          ref: desc.ref || {},
        };
        const resp = await post(urls.geometry, { area });
        const d = await resp.json();
        if (!resp.ok || d.status !== 'ok' || !d.geometry) {
          setStatus(d.detail || 'Geometry lookup failed');
          return;
        }
        selected.set(id, { desc, geometry: d.geometry });
        onAreaAdd(id, d.geometry, desc);
        syncSelectedSource();
        renderSummary();
        setStatus('');
      } catch (e) {
        setStatus('Failed: ' + e);
      }
    }
    function syncSelectedSource() {
      ensureLayers();
      const feats = [];
      selected.forEach((v) =>
        feats.push({ type: 'Feature', geometry: v.geometry, properties: {} }),
      );
      map
        .getSource(SEL_SRC)
        .setData({ type: 'FeatureCollection', features: feats });
    }

    // ---- search ----
    // Country-wide by name across all levels via the resolver-backed areas endpoint
    // (reuses urls.areas), scoped to the active source + the resolved country. Falls
    // back to a client-side filter over the in-view features when we don't yet know
    // the country (e.g. before the first viewport load) or the endpoint is absent.
    let searchCtrl = null;
    function renderResults(items) {
      resultsEl.innerHTML = '';
      items.slice(0, 40).forEach(({ desc, label }) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className =
          'block w-full text-left truncate text-purple-700 text-xs px-1 py-0.5 hover:bg-purple-50';
        b.textContent = label;
        b.addEventListener('click', () => {
          if (isAreaPhase()) toggleSelect(desc);
          else setStatus('Switch to the area phase to add boundaries.');
        });
        resultsEl.appendChild(b);
      });
    }
    function clientFilter(q) {
      return features
        .filter((f) => (f.properties.name || '').toLowerCase().includes(q))
        .map((f) => {
          const p = f.properties;
          return {
            desc: featToDesc(f),
            label:
              `${p.name} · ADM${p.admin_level}` +
              (p.area_km2 ? ` · ${Math.round(p.area_km2)} km²` : ''),
          };
        });
    }
    async function runSearch() {
      const q = (searchEl.value || '').trim();
      resultsEl.innerHTML = '';
      if (!q) {
        setStatus('');
        return;
      }
      const country = getCountryIso() || detectedIso;
      if (!country || !urls.areas) {
        renderResults(clientFilter(q.toLowerCase()));
        return;
      }
      if (searchCtrl) searchCtrl.abort();
      searchCtrl = new AbortController();
      setStatus('Searching…');
      try {
        const perLevel = await Promise.all(
          [1, 2, 3].map((level) =>
            M.post(
              urls.areas,
              { country, level, q, source: source || undefined },
              { csrf: opts.csrf, signal: searchCtrl.signal },
            )
              .then((r) => r.json())
              .catch(() => null),
          ),
        );
        const items = [];
        perLevel.forEach((d) => {
          if (d && d.status === 'ok')
            (d.areas || []).forEach((a) =>
              items.push({
                desc: rowToDesc(a),
                label:
                  `${a.name} · ${a.level_label || 'ADM' + a.level}` +
                  (a.area_km2 ? ` · ${Math.round(a.area_km2)} km²` : ''),
              }),
            );
        });
        renderResults(items);
        setStatus(items.length ? `${items.length} match(es)` : 'No matches');
      } catch (e) {
        if (e && e.name === 'AbortError') return;
        renderResults(clientFilter(q.toLowerCase())); // network error → best-effort
      }
    }
    searchEl.addEventListener('input', M.debounce(runSearch, 250));

    sourceSel.addEventListener('change', () => {
      source = sourceSel.value || null;
      refresh();
    });

    // ---- map interaction wiring (bound once, guarded by layer.on) ----
    let wired = false;
    function wireMap() {
      if (wired) return;
      wired = true;
      map.on('mousemove', LINE, (e) => {
        if (!layer.on || !e.features.length) return;
        map.getCanvas().style.cursor = 'pointer';
        const f = smallestAt(e.point) || e.features[0];
        showHover(f, e.point);
      });
      map.on('mouseleave', LINE, () => {
        if (!layer.on) return;
        map.getCanvas().style.cursor = '';
        clearHover();
      });
      map.on('click', LINE, (e) => {
        if (!layer.on) return;
        const f = smallestAt(e.point);
        if (!f) return;
        if (
          (e.originalEvent.shiftKey || e.originalEvent.metaKey) &&
          isAreaPhase()
        ) {
          toggleSelect(featToDesc(f));
        }
        pinInspect(f, e.point);
      });
      map.on('moveend', () => {
        if (layer.on) refreshDebounced();
      });
    }

    const layer = panel.registerLayer({
      id: 'admin',
      label: 'Boundaries',
      color: COLOR,
      onToggle: (on) => {
        if (on) {
          layer.setBody(body);
          renderHint();
          renderSummary();
          ensureLayers();
          setVisible(true);
          wireMap();
          refresh();
        } else {
          setVisible(false);
        }
      },
    });

    return {
      layer,
      refresh,
      enable() {
        layer.setEnabled(true);
      },
      teardown,
      selectedCount: () => selected.size,
    };
  }

  global.MicroplansAdminBoundaries = { register };
})(window);
