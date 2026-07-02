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
  // Two-arm sampling: per-boundary study arm. Matches review.html ARM_COLOR.
  const ARM_COLOR = { intervention: '#10b981', comparison: '#3b82f6' };
  const ARM_LABEL = { intervention: 'Interv', comparison: 'Match' };
  // Full chip labels for the committed-arm chip in the rail (the two-segment
  // Interv/Match toggle was misread as "this ward is in both arms").
  // "Match" not "Control": a matched quasi-experimental design isn't a
  // randomized control trial, so the non-intervention arm shouldn't imply one.
  const ARM_CHIP_LABEL = {
    intervention: 'Intervention',
    comparison: 'Match',
  };
  const SRC = 'mp-admin';
  const LINE = 'mp-admin-line';
  const FILL = 'mp-admin-fill';
  const FILL_HOVER = 'mp-admin-fill-hover';
  const HOVER = 'mp-admin-hover';
  const SEL_SRC = 'mp-admin-sel';
  const SEL_FILL = 'mp-admin-sel-fill';
  const SEL_LINE = 'mp-admin-sel-line';
  // Surrounding-ward compare overlay — per-candidate coloured fills so each row in
  // the results panel maps to a boundary on the map.
  const CMP_SRC = 'mp-admin-cmp';
  const CMP_FILL = 'mp-admin-cmp-fill';
  const CMP_LINE = 'mp-admin-cmp-line';
  const CMP_LABEL = 'mp-admin-cmp-label';

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
    // Two-arm sampling support. When armEnabled() is true, each selected boundary
    // carries its own intervention/comparison arm, set via a per-row pill in the
    // rail. onAreaAdd's 4th arg + onArmChange let the host tag the boundary's draw
    // feature with the arm so collectArmAreas + the sample paint read it.
    const onArmChange = opts.onArmChange || function () {};
    const armEnabled = opts.armEnabled || (() => false);
    const post = (url, body) => M.post(url, body, { csrf: opts.csrf });
    // When a controlsHost (a left-rail element) is provided, the source picker /
    // search / selected list mount THERE and the map-panel "Boundaries" layer is
    // left as a pure visibility toggle. Keeps every plan-building control in the
    // rail; the map only owns the lines + the click-to-select gesture.
    const controlsHost = opts.controlsHost || null;
    // Surrounding-ward control finder: the enqueue URL, the panel element (below
    // the map) the ranked results render into, and a getter for the sampling
    // config so neighbours are analysed with the same frame settings as the plan.
    const compareUrl = (opts.urls || {}).compareSurrounding || null;
    const comparePanel = opts.comparePanel || null;
    const getSamplingConfig = opts.getSamplingConfig || (() => ({}));

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

    // Map-navigation controls (Source + place Search + results) are properties of
    // the map layer, so they live in the map-panel layer body. The rail
    // (controlsHost) keeps only the plan-building output: the selected-boundary
    // summary + x-deletable list.
    const sourceBody = document.createElement('div');
    sourceBody.innerHTML = `
      <label class="block mb-1.5">
        <span class="text-gray-600">Source</span>
        <select class="mp-ab-source base-input mt-0.5 text-xs"></select>
      </label>
      <input class="mp-ab-search base-input text-xs w-full" type="text"
             placeholder="search a ward / LGA / state by name, then click to add…">
      <div class="mp-ab-status text-[10px] text-gray-500 mt-1"></div>
      <div class="mp-ab-results max-h-32 overflow-y-auto"></div>`;
    const body = document.createElement('div');
    body.innerHTML = `
      <div class="mp-ab-summary text-[11px] font-medium text-purple-700 mt-1"></div>
      <div class="mp-ab-selected space-y-0.5 mt-1"></div>
      <p class="mp-ab-hint text-[10px] text-gray-400 mt-1"></p>`;
    const sourceSel = sourceBody.querySelector('.mp-ab-source');
    const searchEl = sourceBody.querySelector('.mp-ab-search');
    const statusEl = sourceBody.querySelector('.mp-ab-status');
    const resultsEl = sourceBody.querySelector('.mp-ab-results');
    const summaryEl = body.querySelector('.mp-ab-summary');
    const hintEl = body.querySelector('.mp-ab-hint');
    const selectedListEl = body.querySelector('.mp-ab-selected');

    if (controlsHost) {
      // The selected-boundary list now lives in the below-map planning table (with
      // per-row ✕). Keep this node for its state/handlers but hide it in the rail —
      // the rail is for configuration actions, not a data overview.
      body.style.display = 'none';
      controlsHost.appendChild(body);
    } else {
      body.insertBefore(sourceBody, body.firstChild); // no host: source + controls together
    }

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
      } else {
        let km2 = 0;
        selected.forEach((v) => {
          km2 += (v.desc && v.desc.area_km2) || 0;
        });
        summaryEl.textContent = `${selected.size} selected · ${Math.round(
          km2,
        ).toLocaleString()} km²`;
      }
      // x-deletable list of the selected boundaries (lives in the rail). In
      // sampling mode each row also carries a per-boundary arm pill.
      if (!selectedListEl) return;
      selectedListEl.innerHTML = '';
      const showArm = armEnabled();
      selected.forEach((v, id) => {
        const row = document.createElement('div');
        row.className =
          'flex items-start justify-between gap-1.5 text-[11px] px-1.5 py-1 rounded bg-gray-50 border border-gray-200';
        // Two-line label so the ward NAME is never clipped by the arm pill: the ward
        // name wraps onto its own line (always fully readable — the meaningful axis),
        // and the parent path (LGA › state, the disambiguator) sits below it muted and
        // truncates if it must. Previously name + parent shared one truncated line, so a
        // wide "Intervention" pill cut the row mid-parent ("Riyom › Pl…").
        const nameWrap = document.createElement('span');
        nameWrap.className = 'min-w-0 flex-1 leading-tight';
        const wardName = (v.desc && v.desc.name) || '(area)';
        const parentName = v.desc && v.desc.parent_name;
        const ward = document.createElement('div');
        ward.className = 'font-medium text-gray-800 break-words';
        ward.textContent = wardName;
        nameWrap.appendChild(ward);
        if (parentName) {
          const parent = document.createElement('div');
          parent.className = 'truncate text-[10px] text-gray-500';
          parent.textContent = parentName;
          nameWrap.appendChild(parent);
          nameWrap.title = `${wardName} — ${parentName}`;
        }
        row.appendChild(nameWrap);
        if (showArm) row.appendChild(armPill(id, v));
        const x = document.createElement('button');
        x.type = 'button';
        x.className =
          'text-gray-400 hover:text-red-600 leading-none px-1 shrink-0';
        x.textContent = '×';
        x.title = 'Remove from plan area';
        x.addEventListener('click', () => toggleSelect(v.desc));
        row.appendChild(x);
        selectedListEl.appendChild(row);
      });
      updateCompareBtn();
      // Keep the compare table's per-row "Add boundary / ✓ added" in sync with the
      // live selection (e.g. when a ward is removed from the rail after being added).
      if (
        lastCompareState &&
        comparePanel &&
        !comparePanel.classList.contains('hidden')
      )
        renderComparePanel(lastCompareState);
    }
    // Slick per-boundary arm selector: a two-segment Interv / Match pill, the
    // active half filled with its arm colour. Changing it re-tags the boundary's
    // draw feature via the host's onArmChange.
    function setArm(id, arm) {
      const v = selected.get(id);
      if (!v || v.arm === arm) return;
      v.arm = arm;
      onArmChange(id, arm);
      renderSummary();
    }
    // A SINGLE committed-arm chip (not a two-segment toggle, which read as "this
    // ward is in both arms"). Shows the one arm this ward is committed to, coloured
    // by arm; clicking it flips to the other arm (a one-tap correction), with the
    // affordance spelled out in the title so it doesn't look like a static badge.
    function armPill(id, v) {
      const arm = v.arm || 'intervention';
      const other = arm === 'intervention' ? 'comparison' : 'intervention';
      const b = document.createElement('button');
      b.type = 'button';
      b.className =
        'inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9px] font-semibold leading-none shrink-0 transition-colors';
      b.style.background = ARM_COLOR[arm];
      b.style.borderColor = ARM_COLOR[arm];
      b.style.color = '#fff';
      b.textContent = ARM_CHIP_LABEL[arm] || ARM_LABEL[arm];
      b.title = `Study arm: ${ARM_CHIP_LABEL[arm]}. Click to switch to ${ARM_CHIP_LABEL[other]}.`;
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        setArm(id, other);
      });
      return b;
    }
    function renderHint() {
      hintEl.textContent = isAreaPhase()
        ? 'Click a boundary on the map, OR use the search box above (by ward/LGA/state name) and click a result, to add it to the plan area.'
        : 'Click a boundary to inspect it.';
    }

    // ---- map layers ----
    function ensureLayers() {
      if (!map.getSource(SRC)) {
        map.addSource(SRC, { type: 'geojson', data: empty() });
        // Invisible fill over every boundary so a click/hover INSIDE a polygon
        // (not only on its line) hits the layer and can toggle selection.
        map.addLayer({
          id: FILL,
          type: 'fill',
          source: SRC,
          paint: { 'fill-color': COLOR, 'fill-opacity': 0 },
        });
        // Fill the hovered boundary so it's visible even when you're zoomed
        // INSIDE it (its outline off-screen). Filtered to the hovered id.
        map.addLayer({
          id: FILL_HOVER,
          type: 'fill',
          source: SRC,
          filter: ['==', ['get', 'boundary_id'], '__none__'],
          paint: { 'fill-color': COLOR, 'fill-opacity': 0.22 },
        });
        map.addLayer({
          id: LINE,
          type: 'line',
          source: SRC,
          paint: {
            'line-color': COLOR,
            // coarser level (lower number) = thicker; finer = thinner
            // Scale with zoom so fine (ward) outlines stay clearly visible when
            // zoomed in, instead of staying hair-thin on the satellite imagery.
            'line-width': [
              'interpolate',
              ['linear'],
              ['zoom'],
              6,
              [
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
              13,
              [
                'interpolate',
                ['linear'],
                ['coalesce', ['get', 'admin_level'], 1],
                1,
                4,
                2,
                3,
                4,
                2,
              ],
            ],
            'line-opacity': 1,
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
          paint: { 'fill-color': COLOR, 'fill-opacity': 0.3 },
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
      [FILL, FILL_HOVER, LINE, HOVER, SEL_FILL, SEL_LINE].forEach((id) => {
        if (map.getLayer(id))
          map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
      });
    }
    function teardown() {
      M.removeSourceAndLayers(map, SRC, [FILL, FILL_HOVER, LINE, HOVER]);
      M.removeSourceAndLayers(map, SEL_SRC, [SEL_FILL, SEL_LINE]);
      M.removeSourceAndLayers(map, CMP_SRC, [CMP_FILL, CMP_LINE, CMP_LABEL]);
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
      const hostIso = getCountryIso();
      const iso = hostIso || detectedIso;
      // Send iso when it's the reliable host/program country, or for Overture
      // (which needs it for partition pruning). Do NOT filter the labs source by
      // a *detected* country: it's inferred from a wide first view and can be a
      // neighbour, which then wrongly empties the map when you zoom into the
      // program's real country (the bbox already scopes labs geographically).
      if (iso && (hostIso || source !== 'labs')) params.set('iso', iso);
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
        if (!detectedIso && features.length) {
          // Dominant country in view, not just features[0] — a wide, multi-country
          // zoom can list a neighbour first, which then mis-scopes later fetches.
          const counts = {};
          for (const f of features) {
            const c = f.properties && f.properties.iso_code;
            if (c) counts[c] = (counts[c] || 0) + 1;
          }
          detectedIso =
            Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0] ||
            null;
        }
        availableSources = data.available_sources || [];
        sourceLabels = data.source_labels || {};
        source = data.source || source;
        ensureLayers();
        map.getSource(SRC).setData({ type: 'FeatureCollection', features });
        renderSourceOptions();
        // search is country-wide + user-driven, so it doesn't re-run on viewport refresh
        setStatus(
          `${features.length.toLocaleString()} boundaries from ${
            sourceLabels[source] || source || 'default'
          }${truncated ? ' · zoom in to see all' : ''}`,
        );
      } catch (e) {
        if (e && e.name === 'AbortError') return;
        setStatus('Failed: ' + e);
      }
    }
    const refreshDebounced = M.debounce(refresh, 350);

    // ---- smallest-wins resolution ----
    function smallestAt(point) {
      const hits = map.queryRenderedFeatures(point, { layers: [FILL, LINE] });
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
      const hits = map.queryRenderedFeatures(point, { layers: [FILL, LINE] });
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
      const filt = ['==', ['get', 'boundary_id'], f.properties.boundary_id];
      if (map.getLayer(HOVER)) map.setFilter(HOVER, filt);
      if (map.getLayer(FILL_HOVER)) map.setFilter(FILL_HOVER, filt);
      panel.setInspect(inspectHTML(f, point), false);
    }
    function clearHover() {
      const none = ['==', ['get', 'boundary_id'], '__none__'];
      if (map.getLayer(HOVER)) map.setFilter(HOVER, none);
      if (map.getLayer(FILL_HOVER)) map.setFilter(FILL_HOVER, none);
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
      // Mapbox serializes nested feature properties to JSON strings when queried,
      // so `ref` (a dict) comes back as a string from queryRenderedFeatures.
      // Parse it back, else the geometry lookup 404s ("Area not found").
      let ref = p.ref || {};
      if (typeof ref === 'string') {
        try {
          ref = JSON.parse(ref);
        } catch (e) {
          ref = {};
        }
      }
      // Same JSON-string round-trip for the per-source population bag, so a CLICKED
      // boundary carries the numbers the setup planning table needs (Total/U5).
      let populations = p.populations || null;
      if (typeof populations === 'string') {
        try {
          populations = JSON.parse(populations);
        } catch (e) {
          populations = null;
        }
      }
      return {
        key: p.boundary_id,
        name: p.name,
        level: p.admin_level,
        parent_name: p.parent_name || '',
        source: p.source,
        country: p.iso_code,
        ref: ref,
        area_km2: p.area_km2,
        population: p.population != null ? p.population : null,
        populations: populations,
      };
    }
    function rowToDesc(a) {
      const ref = a.ref || {};
      const key = ref.boundary_id || `${a.source}:${a.region || ''}:${a.name}`;
      return {
        key,
        name: a.name,
        level: a.level,
        parent_name: a.parent_name || ref.parent_name || '',
        source: a.source,
        country: a.country,
        ref,
        area_km2: a.area_km2,
        population: a.population != null ? a.population : null,
        populations: a.populations || null,
      };
    }
    // Fetch geometry + add a boundary to the selected set under a given arm. The
    // shared core of a plain click-to-select (intervention) and the surrounding
    // control finder's "Set as control" (comparison). Already-selected → just
    // re-tag the arm. Returns true on success.
    async function addBoundary(desc, arm) {
      const id = desc.key;
      arm = arm || 'intervention';
      if (selected.has(id)) {
        setArm(id, arm);
        return true;
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
          return false;
        }
        selected.set(id, { desc, geometry: d.geometry, arm });
        onAreaAdd(id, d.geometry, desc, arm);
        syncSelectedSource();
        renderSummary();
        setStatus('');
        return true;
      } catch (e) {
        setStatus('Failed: ' + e);
        return false;
      }
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
      // New picks default to the intervention arm; the per-row pill changes it.
      await addBoundary(desc, 'intervention');
    }
    // Public removal by boundary id — used by the host's below-map planning table
    // (its per-row ✕). Mirrors the deselect branch of toggleSelect so the map
    // highlight, draw geometry, and population bag all clear together.
    function removeArea(id) {
      if (!selected.has(id)) return;
      selected.delete(id);
      onAreaRemove(id);
      syncSelectedSource();
      renderSummary();
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

    // ---- surrounding-ward control finder -------------------------------------
    // The reference (intervention) ward neighbours are compared against: the
    // intervention-arm selection, or the only selection when there's just one.
    function referenceForCompare() {
      let interv = null;
      let only = null;
      let count = 0;
      selected.forEach((v, id) => {
        count += 1;
        only = { v, id };
        if (!interv && (v.arm || 'intervention') === 'intervention')
          interv = { v, id };
      });
      const pick = interv || (count === 1 ? only : null);
      if (!pick) return null;
      const d = pick.v.desc || {};
      const ref = d.ref || {};
      const bid = ref.boundary_id || d.boundary_id || pick.id;
      if (!bid) return null;
      return {
        boundary_id: bid,
        name: d.name || '',
        source: d.source || '',
        country: d.country || '',
        level: d.level,
        ref: ref.boundary_id ? ref : { boundary_id: bid, source: d.source },
      };
    }
    // Add a ranked candidate as the control arm — same level/source as the
    // reference, so a single boundary_id is enough to fetch its geometry.
    function selectCandidateAsControl(cand, ref) {
      return addBoundary(
        {
          key: cand.boundary_id,
          name: cand.name,
          level: ref.level,
          parent_name: '',
          source: ref.source,
          country: ref.country,
          ref: { boundary_id: cand.boundary_id, source: ref.source },
          area_km2: null,
          population: cand.population != null ? cand.population : null,
        },
        'comparison',
      );
    }

    const compareBtn = document.createElement('button');
    compareBtn.type = 'button';
    compareBtn.className =
      'mp-ab-compare hidden w-full mt-2 text-[11px] font-medium px-2 py-1.5 rounded border ' +
      'border-purple-200 text-purple-700 bg-purple-50 hover:bg-purple-100 disabled:opacity-60';
    compareBtn.textContent = 'Compare surrounding boundaries';
    compareBtn.addEventListener('click', runCompare);
    body.insertBefore(compareBtn, hintEl);

    function updateCompareBtn() {
      if (!compareBtn) return;
      const ok = !!compareUrl && armEnabled() && !!referenceForCompare();
      compareBtn.classList.toggle('hidden', !ok);
    }

    // ---- map overlay: one coloured fill per candidate boundary ----
    function ensureCompareLayers() {
      if (map.getSource(CMP_SRC)) return;
      map.addSource(CMP_SRC, { type: 'geojson', data: empty() });
      map.addLayer({
        id: CMP_FILL,
        type: 'fill',
        source: CMP_SRC,
        paint: {
          'fill-color': ['coalesce', ['get', 'color'], '#9ca3af'],
          // solid once scored, faint while still analysing
          'fill-opacity': ['case', ['get', 'scored'], 0.32, 0.1],
        },
      });
      map.addLayer({
        id: CMP_LINE,
        type: 'line',
        source: CMP_SRC,
        paint: {
          'line-color': ['coalesce', ['get', 'color'], '#9ca3af'],
          'line-width': 2.2,
        },
      });
      try {
        map.addLayer({
          id: CMP_LABEL,
          type: 'symbol',
          source: CMP_SRC,
          layout: {
            'text-field': ['get', 'label'],
            'text-size': 11,
            'text-font': ['Open Sans Bold', 'Arial Unicode MS Bold'],
          },
          paint: {
            'text-color': ['coalesce', ['get', 'color'], '#374151'],
            'text-halo-color': '#ffffff',
            'text-halo-width': 1.4,
          },
        });
      } catch (e) {
        /* style has no glyphs → skip labels; fills + lines still draw */
      }
    }
    // Fit the map to the candidate wards ONCE per compare run, the first time they
    // have geometry — so the colour-fills are actually visible (the map may have
    // been zoomed out to the country) without re-fitting (jumping) on every poll.
    let compareOverlayFitted = false;
    function renderCompareOverlay(results) {
      try {
        ensureCompareLayers();
        const feats = (results || [])
          .filter((r) => r && r.geometry)
          .map((r) => ({
            type: 'Feature',
            geometry: r.geometry,
            properties: {
              color: r.color || '#9ca3af',
              scored: r.status !== 'error' && r.overlap != null,
              label:
                r.overlap != null
                  ? `${r.name} · ${Math.round(r.overlap * 100)}%`
                  : r.name || '',
            },
          }));
        const src = map.getSource(CMP_SRC);
        if (src) {
          src.setData({ type: 'FeatureCollection', features: feats });
          if (!compareOverlayFitted && feats.length) {
            compareOverlayFitted = true;
            // Include the selected (intervention) ward so it stays centred.
            const fitFeats = feats.slice();
            selected.forEach((v) => {
              if (v && v.geometry)
                fitFeats.push({
                  type: 'Feature',
                  geometry: v.geometry,
                  properties: {},
                });
            });
            M.fitTo(
              map,
              { type: 'FeatureCollection', features: fitFeats },
              { padding: 60, maxZoom: 12, duration: 600 },
            );
          }
        }
      } catch (e) {
        /* map/style not ready — the panel still renders */
      }
    }
    function clearCompareOverlay() {
      compareOverlayFitted = false;
      const src = map.getSource(CMP_SRC);
      if (src) src.setData(empty());
    }

    // Overlaid mini-histogram: reference (grey) behind, this candidate (its colour)
    // in front, over the SAME bins the overlap score uses — so the picture IS the score.
    function sparkline(spark, color) {
      if (!spark || !spark.ref || !spark.cand || !spark.ref.length) return '';
      const n = spark.ref.length;
      const w = 124;
      const h = 26;
      const bw = w / n;
      let max = 0;
      for (let i = 0; i < n; i++)
        max = Math.max(max, spark.ref[i] || 0, spark.cand[i] || 0);
      max = max || 1;
      let bars = '';
      for (let i = 0; i < n; i++) {
        const x = i * bw;
        const rh = ((spark.ref[i] || 0) / max) * (h - 1);
        const ch = ((spark.cand[i] || 0) / max) * (h - 1);
        bars +=
          `<rect x="${x.toFixed(1)}" y="${(h - rh).toFixed(1)}" width="${(
            bw - 0.6
          ).toFixed(1)}" height="${rh.toFixed(1)}" fill="#cbd5e1"></rect>` +
          `<rect x="${(x + bw * 0.22).toFixed(1)}" y="${(h - ch).toFixed(
            1,
          )}" width="${(bw * 0.56).toFixed(1)}" height="${ch.toFixed(
            1,
          )}" fill="${color}" opacity="0.9"></rect>`;
      }
      return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" class="block shrink-0">${bars}</svg>`;
    }

    // Full per-column explanations (algorithm-level where relevant), shown in a
    // click-toggled popover on each header.
    const COL_INFO = {
      ward:
        'The candidate match ward. It’s filled on the map in this row’s colour, so you can see which ' +
        'boundary it is. The ward you’re matching against is the green <b>baseline</b> row at the top.',
      pop:
        'Estimated population of the ward, from the admin-boundary dataset (Enriched Boundaries). A rough ' +
        'size indicator only — it is <b>not</b> part of the match score, which is based on building density.',
      buildings:
        'Every building footprint inside the ward, from Google Open Buildings (via Overture Maps). Very small ' +
        'or oversized footprints are dropped as noise / non-residential first. What remains is the ' +
        '<b>sampling frame</b> — the structures the survey design draws from.',
      clusters:
        '<b>What it is.</b> The candidate PSUs (primary sampling units) — the units a survey would actually ' +
        'visit. <b>How they’re formed.</b> The ward’s buildings are grouped by location with k-means ' +
        'clustering: we deliberately over-cluster into 3× the target number of PSUs, then merge any cluster ' +
        'with fewer than 16 buildings into its nearest neighbour, so every final cluster is big enough to be a ' +
        'viable survey unit. When you draw the sample, a subset of these clusters are selected with probability ' +
        'proportional to their building count (PPS). This column is shown for context — the density column is ' +
        'measured per building, independent of this grouping.',
      median:
        '<b>The number.</b> The typical building’s local density, in buildings per km². ' +
        '<b>How it’s computed.</b> For every building we find its 8 nearest neighbours and measure d₈, the ' +
        'distance to the 8th; the local density there is 8 ÷ (π · d₈²) — eight buildings spread over the circle ' +
        'that reaches them — converted to per-km². The median is the middle of all those per-building values: ' +
        'half the ward’s buildings sit somewhere denser, half sparser. This k-nearest-neighbour intensity is ' +
        'the standard, robust point-density estimator: because it’s measured locally at each building it isn’t ' +
        'thrown off by a cluster’s outline shape or a few stray edge buildings (the weakness of the old ' +
        'convex-hull density).',
      distribution:
        '<b>What it shows.</b> A histogram of <b>every</b> building’s local density (computed as in “Median ' +
        'density”), so you see the whole spread, not just the middle. ' +
        '<b>How to read it.</b> Both wards are binned on one shared axis — anchored to the intervention ward’s ' +
        '2nd–98th percentile — so the <span class="text-gray-400">grey bars (intervention)</span> are identical ' +
        'in every row and the coloured bars (this ward) line up for comparison. ' +
        '<b>The score.</b> Each histogram is normalised to sum to 1; we add up the smaller of the two bars in ' +
        'every bin, and that shared (overlapping) area is the Overlap %. A wide spread = a mixed ward (some ' +
        'sparse, some dense).',
      match:
        'How much this ward’s local-density distribution <b>overlaps</b> the intervention’s (0–100%, the shared ' +
        'area of the bars), banded as strong (≥ 70%) / partial (≥ 50%) / poor. High overlap = a similar ' +
        'built-up texture overall — context for how alike the two wards look. For the balance a study would ' +
        'actually get, see <b>Matched balance</b>.',
      balance:
        'The density SMD you’d get if you matched-sample this ward — the standardized mean difference in ' +
        'settlement density between the two arms after the matched draw (0 = identical, higher = more ' +
        'different). We simulate the draw: both arms are reweighted to the intervention’s density-band mix, so ' +
        'only the within-band density difference remains. This is the figure the study’s comparability check ' +
        'reports. “— incomparable” means the two wards share no common density range, so a matched draw isn’t ' +
        'possible.',
      common:
        'Share of settlements in the density range both wards have in <b>common</b> — matching only uses this ' +
        'shared range. A great matched balance over a tiny shared range is a weaker match, so read this ' +
        'alongside Matched balance.',
    };

    // One persistent column-info popover + a single outside-click closer (declared
    // here, once per register, so re-renders don't stack document listeners).
    let activeColPop = null;
    // Column highlighting tied to the info popover: opening a column's ⓘ tints that
    // whole column so it's obvious which metric you're reading; closing clears it.
    const MP_AB_COL_HL = ['bg-indigo-50', 'ring-1', 'ring-indigo-300'];
    function clearColHighlight() {
      document
        .querySelectorAll('.mp-ab-col-hl')
        .forEach((el) => el.classList.remove('mp-ab-col-hl', ...MP_AB_COL_HL));
    }
    function highlightCol(key) {
      clearColHighlight();
      if (!key) return;
      document
        .querySelectorAll(`th[data-col="${key}"], td[data-col="${key}"]`)
        .forEach((el) => el.classList.add('mp-ab-col-hl', ...MP_AB_COL_HL));
    }
    document.addEventListener('click', (e) => {
      if (
        activeColPop &&
        !activeColPop.classList.contains('hidden') &&
        e.target.closest &&
        !e.target.closest('.mp-ab-col-i') &&
        !e.target.closest('.mp-ab-col-popover')
      ) {
        activeColPop.classList.add('hidden');
        clearColHighlight();
      }
    });
    // The last rendered compare state, so a selection change (add/remove a boundary
    // anywhere) can re-render the table and keep each row's "Add / added" accurate.
    let lastCompareState = null;

    function renderComparePanel(state) {
      if (!comparePanel) return;
      const st = state || {};
      comparePanel.classList.remove('hidden');
      if (st.kind === 'error') {
        comparePanel.innerHTML =
          '<div class="px-3 py-2.5 text-[12px] text-red-700 bg-red-50 border border-red-200 rounded-lg">' +
          esc(st.detail || 'Comparison failed.') +
          '</div>';
        return;
      }
      const ref = st.ref || {};
      const reference = st.reference || {};
      const refName = reference.name || ref.name || 'selected ward';
      const running = st.kind === 'running';
      // De-duplicate the candidate list to ONE canonical row per ward and drop the
      // intervention ward from its own candidate list. The admin-boundary table can
      // hold several rows that resolve to the same ward name (and occasionally a row
      // for the reference ward itself), so without this the panel shows duplicate,
      // conflicting rows (e.g. a ward twice) and lists the intervention ward against
      // itself at 100%. Presentation-only: the underlying results data is untouched.
      const refId = reference.boundary_id || ref.boundary_id || null;
      const refKey = String(refName || '')
        .trim()
        .toLowerCase();
      const results = (function dedupeCandidates(rows) {
        const byName = new Map();
        const order = [];
        (rows || []).forEach((r) => {
          if (!r) return;
          // Exclude the intervention ward itself (by id or by name).
          if (refId && r.boundary_id === refId) return;
          const nameKey = String(r.name || '')
            .trim()
            .toLowerCase();
          if (nameKey && nameKey === refKey) return;
          const key = nameKey || 'id:' + r.boundary_id;
          const prev = byName.get(key);
          if (!prev) {
            byName.set(key, r);
            order.push(key);
            return;
          }
          // Keep the better row: an analysed (ok) row beats a pending/errored one;
          // among analysed rows keep the higher overlap. Ranking already sorted by
          // overlap, so first-seen-ok wins ties.
          const prevOk = prev.status === 'ok' && prev.overlap != null;
          const curOk = r.status === 'ok' && r.overlap != null;
          if (curOk && !prevOk) byName.set(key, r);
          else if (curOk && prevOk && (r.overlap || 0) > (prev.overlap || 0))
            byName.set(key, r);
        });
        return order.map((k) => byName.get(k));
      })(st.results || []);
      const num = (x) => (x == null ? null : Math.round(x).toLocaleString());
      const dash = '<span class="text-gray-300">—</span>';
      const cell = (v) => (v == null ? dash : v);
      // Population is "—" only when NO source (scalar or populations bag) has a
      // value for the ward — make that honest with a tooltip rather than a bare
      // blank the reader might mistake for "we forgot to look it up".
      const popCell = (p) =>
        p == null
          ? '<span class="text-gray-300" title="No population source available for this ward">—</span>'
          : num(p);

      // Up-front scope: as soon as the neighbours are known, say how many we're
      // processing (and progress through them), instead of a vague "analysing…".
      const total = st.total != null ? st.total : results.length;
      const doneCount = results.filter(
        (r) => r.status === 'ok' || r.status === 'error',
      ).length;
      const runMsg =
        total > 0
          ? `Processing ${total} surrounding area${total === 1 ? '' : 's'}${
              doneCount ? ` · ${doneCount}/${total} done` : ''
            }`
          : st.message || 'Analysing…';
      const head =
        '<div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-gray-100">' +
        '<div class="text-[12px] font-semibold text-gray-700 flex items-center gap-1.5">Surrounding wards vs ' +
        `<span class="text-gray-900">${esc(refName)}</span>` +
        '<button type="button" class="mp-ab-method-toggle w-4 h-4 rounded-full border border-gray-300 text-gray-400 ' +
        'text-[10px] leading-none hover:bg-gray-100" title="How this comparison works">?</button></div>' +
        (running
          ? `<span class="text-[11px] text-gray-400">${esc(runMsg)}</span>`
          : '<span class="text-[11px] text-gray-400">best match first</span>') +
        '</div>';

      // Methodology popup (toggled by the "?"), hidden by default.
      const method =
        '<div class="mp-ab-method hidden px-3 py-2.5 text-[11px] text-gray-600 leading-snug bg-gray-50 border-b border-gray-100">' +
        '<b>What this measures.</b> We pull every building footprint in each ward and, for <b>each building</b>, estimate the local ' +
        'building density around it — from the distance to its nearest neighbours (a k-nearest-neighbour intensity, k = 8). ' +
        'We then compare the whole <b>distribution</b> of those local densities — not just the average — between the intervention ward ' +
        'and each neighbour. <b>Overlap</b> is how much the two distributions coincide (the shared area in the bars); a high overlap ' +
        'means a similar built-up texture, so a fairer match. Measuring per building (not per cluster) keeps it robust to settlement ' +
        'shape and stray edge buildings.' +
        '<br><br><b>Clusters / PSUs.</b> Separately, we group nearby buildings into <b>clusters</b> — the candidate PSUs you’d actually ' +
        'sample (the “Clusters” column counts them). The density above is measured per building, independent of this grouping. They’re ' +
        'algorithmic clusters, not official settlements.' +
        '<br><br><b>Where it’s from.</b> Building-footprint frames + two-stage PPS cluster sampling are standard household-survey ' +
        'practice (DHS / MICS / LSMS); k-NN intensity is the standard nonparametric density estimator for point patterns. Ranking ' +
        'match wards by density-distribution overlap is our own heuristic, grounded in matched-design / covariate-balance methods.</div>';

      // Table-row builder shared by the intervention baseline + each candidate.
      // Each <td> is tagged with data-col="<key>" (matching the header order) so a
      // whole column can be selected + highlighted from its ⓘ button.
      const sparkCell = (spark, color) =>
        spark
          ? `<td data-col="distribution" class="px-1.5 py-1">${sparkline(
              spark,
              color,
            )}</td>`
          : `<td data-col="distribution" class="px-1.5 py-1">${dash}</td>`;
      const wardCell = (color, name, suffix) =>
        '<td data-col="ward" class="px-1.5 py-1"><div class="flex items-center gap-1.5">' +
        `<span class="inline-block w-3 h-3 rounded-sm shrink-0" style="background:${color}"></span>` +
        `<span class="text-gray-800 font-medium truncate">${esc(
          name || '(ward)',
        )}</span>` +
        (suffix
          ? `<span class="text-[10px] text-gray-400">${esc(suffix)}</span>`
          : '') +
        '</div></td>';

      // Intervention baseline row, so every candidate number has something to read against.
      const refQ = reference.q || null;
      const baseRow =
        '<tr class="bg-emerald-50/50 border-t border-gray-100 align-middle">' +
        wardCell('#10b981', refName, 'intervention') +
        `<td data-col="pop" class="px-1.5 py-1 text-right tabular-nums">${popCell(
          reference.population,
        )}</td>` +
        `<td data-col="buildings" class="px-1.5 py-1 text-right tabular-nums">${cell(
          num(reference.buildings),
        )}</td>` +
        `<td data-col="clusters" class="px-1.5 py-1 text-right tabular-nums">${cell(
          reference.n_clusters,
        )}</td>` +
        `<td data-col="median" class="px-1.5 py-1 text-right tabular-nums">${cell(
          refQ ? num(refQ[1]) : null,
        )}</td>` +
        sparkCell(reference.spark, '#9ca3af') +
        '<td data-col="match" class="px-1.5 py-1 text-[11px] text-gray-400">baseline</td>' +
        '<td data-col="balance" class="px-1.5 py-1 text-[11px] text-gray-400">baseline</td>' +
        '<td data-col="common" class="px-1.5 py-1"></td>' +
        '<td class="px-1.5 py-1"></td>' +
        '</tr>';

      const candRows = results
        .map((r) => {
          const isErr = r.status === 'error';
          const ok = !isErr && r.overlap != null;
          // Live selection state (re-rendered on every add/remove), so it can't go stale.
          const inPlan = selected.has(r.boundary_id);
          const color = r.color || '#9ca3af';
          const cq = r.q_cand;
          const action = !ok
            ? ''
            : inPlan
            ? '<span class="text-[11px] text-emerald-600 whitespace-nowrap">✓ added</span>'
            : '<button type="button" class="mp-ab-addbnd text-[11px] font-medium px-2 py-0.5 rounded ' +
              'border border-gray-300 text-gray-700 bg-white hover:bg-gray-50" ' +
              `data-bid="${esc(r.boundary_id)}" data-name="${esc(r.name)}" ` +
              `data-pop="${
                r.population != null ? esc(r.population) : ''
              }">Add boundary</button>`;
          let matchCell;
          if (isErr)
            matchCell = `<td data-col="match" class="px-1.5 py-1 text-[11px] text-red-500">${esc(
              r.detail || 'failed',
            )}</td>`;
          else if (!ok)
            matchCell =
              '<td data-col="match" class="px-1.5 py-1 text-[11px] text-gray-400">analysing…</td>';
          else
            matchCell =
              '<td data-col="match" class="px-1.5 py-1 whitespace-nowrap">' +
              `<b class="text-gray-700">${Math.round(
                (r.overlap || 0) * 100,
              )}%</b></td>`;
          // MATCHED BALANCE — the density SMD the matched draw would actually realise
          // (lower = better; the inverse direction of Match %). "—" when the two wards
          // share no density support (incomparable) or it couldn't be scored.
          let balanceCell;
          if (isErr || !ok)
            balanceCell = '<td data-col="balance" class="px-1.5 py-1"></td>';
          else if (r.incomparable || r.matched_smd == null)
            balanceCell =
              '<td data-col="balance" class="px-1.5 py-1 whitespace-nowrap text-[11px] text-gray-400">— incomparable</td>';
          else
            balanceCell =
              '<td data-col="balance" class="px-1.5 py-1 whitespace-nowrap">' +
              `<b class="text-gray-700">${r.matched_smd.toFixed(2)}</b></td>`;
          // COMMON SUPPORT — share of settlements in the density range both wards have
          // in common (the range matching can actually use).
          const commonCell =
            isErr || !ok
              ? '<td data-col="common" class="px-1.5 py-1"></td>'
              : `<td data-col="common" class="px-1.5 py-1 text-right tabular-nums">${
                  r.common_fraction == null
                    ? dash
                    : Math.round(r.common_fraction * 100) + '%'
                }</td>`;
          return (
            '<tr class="border-t border-gray-100 align-middle hover:bg-gray-50">' +
            wardCell(color, r.name) +
            `<td data-col="pop" class="px-1.5 py-1 text-right tabular-nums">${popCell(
              r.population,
            )}</td>` +
            `<td data-col="buildings" class="px-1.5 py-1 text-right tabular-nums">${cell(
              num(r.buildings),
            )}</td>` +
            `<td data-col="clusters" class="px-1.5 py-1 text-right tabular-nums">${cell(
              r.n_clusters,
            )}</td>` +
            `<td data-col="median" class="px-1.5 py-1 text-right tabular-nums" style="color:${color}">${
              cq ? num(cq[1]) : dash
            }</td>` +
            sparkCell(r.spark, color) +
            matchCell +
            balanceCell +
            commonCell +
            `<td class="px-1.5 py-1 text-right">${action}</td>` +
            '</tr>'
          );
        })
        .join('');

      // Each header shows the label + an ⓘ that opens a full-explanation popover (the
      // text lives in COL_INFO, keyed by colKey).
      const th = (label, colKey, extra) =>
        `<th${
          colKey ? ` data-col="${colKey}"` : ''
        } class="px-1.5 py-1 font-semibold text-gray-500 ${
          extra || 'text-right'
        }">` +
        (colKey
          ? `<span class="inline-flex items-center gap-1">${label}` +
            `<button type="button" class="mp-ab-col-i text-gray-300 hover:text-gray-600 cursor-pointer leading-none" ` +
            `data-col="${colKey}" aria-label="Explain ${esc(
              label,
            )}">ⓘ</button></span>`
          : label) +
        '</th>';
      const table =
        '<div class="overflow-x-auto"><table class="w-full text-[12px]">' +
        '<thead class="bg-gray-50 text-[10px] uppercase tracking-wide"><tr>' +
        th('Ward', 'ward', 'text-left') +
        th('Pop.', 'pop') +
        th('Buildings', 'buildings') +
        th('Clusters', 'clusters') +
        th('Median', 'median') +
        th('Distribution', 'distribution', 'text-left') +
        th('Overlap', 'match', 'text-left') +
        th('Matched balance', 'balance', 'text-left') +
        th('Common support', 'common') +
        th('', '', 'text-left') +
        '</tr></thead><tbody>' +
        baseRow +
        candRows +
        '</tbody></table></div>';

      const empty =
        !results.length && !running
          ? `<p class="px-3 py-3 text-[12px] text-gray-500">${esc(
              st.detail ||
                'No neighbouring wards at the same level were found.',
            )}</p>`
          : '';

      // Remember this state so a selection change elsewhere can re-render (keeping
      // each row's Add / ✓ added accurate).
      lastCompareState = st;

      comparePanel.innerHTML =
        '<div class="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">' +
        head +
        method +
        (results.length ? table : empty) +
        '</div>';

      // Colour-fill each candidate's boundary on the map to match the rows.
      renderCompareOverlay(results);

      const methodEl = comparePanel.querySelector('.mp-ab-method');
      const methodBtn = comparePanel.querySelector('.mp-ab-method-toggle');
      if (methodEl && methodBtn)
        methodBtn.addEventListener('click', () =>
          methodEl.classList.toggle('hidden'),
        );

      // Per-column info popover — absolutely positioned within comparePanel (which
      // is in normal flow), so it stays pinned to the header it opened from and
      // scrolls WITH the page (a fixed/viewport one runs off-screen with no way to
      // reach its bottom). comparePanel is the table's sibling container, so its
      // overflow-x doesn't clip this.
      comparePanel.classList.add('relative');
      const colPop = document.createElement('div');
      colPop.className =
        'mp-ab-col-popover hidden absolute z-50 w-64 max-w-[88vw] bg-white border border-gray-200 rounded-lg ' +
        'shadow-xl p-3 text-[11px] font-normal normal-case tracking-normal text-left text-gray-600 leading-snug';
      comparePanel.appendChild(colPop);
      activeColPop = colPop;
      comparePanel.querySelectorAll('.mp-ab-col-i').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const key = btn.dataset.col;
          if (
            !colPop.classList.contains('hidden') &&
            colPop.dataset.col === key
          ) {
            colPop.classList.add('hidden'); // toggle off
            clearColHighlight();
            return;
          }
          colPop.dataset.col = key;
          colPop.innerHTML = COL_INFO[key] || '';
          colPop.classList.remove('hidden');
          highlightCol(key);
          // Position relative to comparePanel so it tracks the header on scroll.
          // Anchor BELOW the table (not below the header) so the popover never
          // occludes the comparison data — a popover dropped below a header sits
          // on top of the score cells for the top rows (e.g. the Common-support
          // popover covering the Matched-balance column it's explaining). The
          // column tint (highlightCol) is the cue for WHICH column; the popover
          // explains it in clear space under the table.
          const panelRect = comparePanel.getBoundingClientRect();
          const rect = btn.getBoundingClientRect();
          const w = colPop.offsetWidth || 256;
          let left = rect.left - panelRect.left;
          left = Math.max(4, Math.min(left, comparePanel.clientWidth - w - 4));
          colPop.style.left = left + 'px';
          const tbl = comparePanel.querySelector('table');
          const below = tbl
            ? tbl.getBoundingClientRect().bottom - panelRect.top + 6
            : rect.bottom - panelRect.top + 4;
          colPop.style.top = below + 'px';
        });
      });

      comparePanel.querySelectorAll('.mp-ab-addbnd').forEach((btn) => {
        btn.addEventListener('click', async () => {
          btn.disabled = true;
          btn.textContent = 'Adding…';
          // On success addBoundary() calls renderSummary(), which re-renders this
          // panel → the row flips to "✓ added". Only reset the button on failure.
          const done = await selectCandidateAsControl(
            {
              boundary_id: btn.dataset.bid,
              name: btn.dataset.name,
              population: btn.dataset.pop ? Number(btn.dataset.pop) : null,
            },
            ref,
          );
          if (!done) {
            btn.disabled = false;
            btn.textContent = 'Add boundary';
          }
        });
      });
    }

    let comparing = false;
    async function runCompare() {
      if (comparing || !compareUrl) return;
      const ref = referenceForCompare();
      if (!ref) {
        renderComparePanel({
          kind: 'error',
          detail: 'Select an intervention ward first.',
        });
        return;
      }
      comparing = true;
      compareBtn.disabled = true;
      clearCompareOverlay();
      renderComparePanel({
        kind: 'running',
        message: 'Finding neighbouring wards…',
        results: [],
        reference: { name: ref.name },
        ref,
      });
      const deadline = Date.now() + 8 * 60 * 1000;
      try {
        const enqResp = await post(compareUrl, {
          selected: ref,
          config: getSamplingConfig(),
        });
        const enq = await enqResp.json();
        const pollUrl = enq && enq.poll_url;
        if (!enqResp.ok || !pollUrl) {
          renderComparePanel({
            kind: 'error',
            detail: (enq && enq.detail) || 'Could not start.',
            ref,
          });
          return;
        }
        for (;;) {
          await new Promise((r) => setTimeout(r, 1200));
          if (Date.now() > deadline) {
            renderComparePanel({
              kind: 'error',
              detail: 'Timed out analysing wards.',
              ref,
            });
            return;
          }
          const stt = await M.apiGet(pollUrl);
          if (stt.state === 'completed') {
            const res = stt.result || {};
            if (res.status === 'error') {
              renderComparePanel({
                kind: 'error',
                detail: res.detail || 'Comparison failed.',
                ref,
              });
            } else {
              renderComparePanel(Object.assign({ kind: 'done', ref }, res));
            }
            return;
          }
          if (stt.state === 'failed') {
            renderComparePanel({
              kind: 'error',
              detail: stt.detail || 'Comparison failed.',
              ref,
            });
            return;
          }
          renderComparePanel({
            kind: 'running',
            message: stt.message,
            results: stt.results || [],
            reference: stt.reference || { name: ref.name },
            ref,
          });
        }
      } catch (e) {
        renderComparePanel({ kind: 'error', detail: String(e), ref });
      } finally {
        comparing = false;
        compareBtn.disabled = false;
      }
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
    // Place search (Mapbox geocoding) used on a cold map with no country yet:
    // type a place name → results → click one → fly the map there so boundaries
    // load. Independent of the admin-area resolver (which needs a country), so it
    // works from a blank new-plan page.
    let geocodeCtrl = null;
    async function geocodeAndFly(q) {
      const token = (window.mapboxgl && mapboxgl.accessToken) || '';
      if (!token) {
        renderResults(clientFilter(q.toLowerCase()));
        return;
      }
      if (geocodeCtrl) geocodeCtrl.abort();
      geocodeCtrl = new AbortController();
      setStatus('Searching places…');
      try {
        const url =
          'https://api.mapbox.com/geocoding/v5/mapbox.places/' +
          encodeURIComponent(q) +
          '.json?limit=6&types=region,district,place,locality&access_token=' +
          encodeURIComponent(token);
        const r = await fetch(url, { signal: geocodeCtrl.signal });
        const d = await r.json();
        const feats = (d && d.features) || [];
        resultsEl.innerHTML = '';
        if (!feats.length) {
          setStatus('No places found');
          return;
        }
        feats.forEach((f) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className =
            'block w-full text-left truncate text-purple-700 text-xs px-1 py-0.5 hover:bg-purple-50';
          b.textContent = f.place_name || f.text;
          b.addEventListener('click', () => {
            // Seed the country from the geocode result so the boundary layer can
            // load on arrival — the global Overture source needs an iso, and it's
            // otherwise only inferred from boundaries that haven't loaded yet. The
            // viewport endpoint normalizes alpha-2 (Mapbox) → alpha-3 (resolver).
            const ctry = (f.context || []).find((c) =>
              (c.id || '').startsWith('country'),
            );
            const code =
              (ctry && ctry.short_code) ||
              (f.properties && f.properties.short_code) ||
              '';
            if (code && !detectedIso) detectedIso = code.toUpperCase();
            map.flyTo({ center: f.center, zoom: 9 });
            searchEl.value = '';
            resultsEl.innerHTML = '';
            setStatus('Moved — click a boundary on the map to add it.');
          });
          resultsEl.appendChild(b);
        });
        setStatus(feats.length + ' place(s) — click to go there');
      } catch (e) {
        if (e && e.name === 'AbortError') return;
        renderResults(clientFilter(q.toLowerCase()));
      }
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
        // Cold start (e.g. a fresh new-plan map): no country detected yet, so the
        // admin-area search can't run and there's nothing in view to filter. Treat
        // the query as a PLACE search instead — geocode it and fly the map there.
        // Boundaries then load for that view and the country auto-detects, after
        // which this box reverts to the admin-area name search below and the user
        // clicks a boundary on the map.
        return geocodeAndFly(q);
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
                // Lead with the admin hierarchy (e.g. "Dabi — Nigeria › Jigawa ›
                // Gwiwa") so same-named wards across different LGAs/states are
                // distinguishable; fall back to the level label only when no chain.
                label:
                  `${a.name}` +
                  (a.parent_name
                    ? ` — ${a.parent_name}`
                    : ` · ${a.level_label || 'ADM' + a.level}`) +
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
      map.on('mousemove', FILL, (e) => {
        if (!layer.on || !e.features.length) return;
        map.getCanvas().style.cursor = 'pointer';
        const f = smallestAt(e.point) || e.features[0];
        showHover(f, e.point);
      });
      map.on('mouseleave', FILL, () => {
        if (!layer.on) return;
        map.getCanvas().style.cursor = '';
        clearHover();
      });
      map.on('click', FILL, (e) => {
        if (!layer.on) return;
        const f = smallestAt(e.point);
        if (!f) return;
        // Plain click anywhere INSIDE a boundary toggles it — select (fills it)
        // on first click, deselect (clears) on the next. The inspector still
        // pins so you see what you picked.
        if (isAreaPhase()) {
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
          layer.setBody(controlsHost ? sourceBody : body);
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

    // Rehydrate the selected-boundary rail when a saved plan reopens, so the left
    // panel shows its picked wards (name + arm pill + remove) exactly like during
    // creation. Rail-only: the host (review.js) already re-adds the geometries to
    // the draw surface for the map + collection, so we don't re-add to draw here.
    function restore(items) {
      let added = 0;
      (items || []).forEach((a) => {
        if (!a || a.boundary_id == null || selected.has(a.boundary_id)) return;
        selected.set(a.boundary_id, {
          desc: {
            name: a.name,
            boundary_id: a.boundary_id,
            level: a.level,
            source: a.source,
            country: a.country,
            ref: a.ref || {},
          },
          geometry: a.geometry,
          arm: a.arm || 'intervention',
        });
        added++;
      });
      if (added) renderSummary();
    }

    return {
      layer,
      refresh,
      // Remove a selected boundary by id (host's below-map table ✕).
      removeArea,
      // Re-render the selected-boundary list (e.g. when the host toggles sampling
      // mode on/off, so the per-boundary arm pills appear/disappear).
      renderSelected: renderSummary,
      // Repopulate the selected list from a saved plan's input_areas on load.
      restore,
      enable() {
        layer.setEnabled(true);
      },
      teardown,
      selectedCount: () => selected.size,
    };
  }

  global.MicroplansAdminBoundaries = { register };
})(window);
