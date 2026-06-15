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
  "use strict";

  const COLOR = "#a855f7";
  // Two-arm sampling: per-boundary study arm. Matches review.html ARM_COLOR.
  const ARMS = ["intervention", "comparison"];
  const ARM_COLOR = { intervention: "#10b981", comparison: "#3b82f6" };
  const ARM_LABEL = { intervention: "Interv", comparison: "Control" };
  const SRC = "mp-admin";
  const LINE = "mp-admin-line";
  const FILL = "mp-admin-fill";
  const FILL_HOVER = "mp-admin-fill-hover";
  const HOVER = "mp-admin-hover";
  const SEL_SRC = "mp-admin-sel";
  const SEL_FILL = "mp-admin-sel-fill";
  const SEL_LINE = "mp-admin-sel-line";

  function register(opts) {
    const map = opts.map;
    const panel = opts.panel;
    const urls = opts.urls || {};
    const M = global.Microplans;
    const esc = (M && M.esc) || ((s) => String(s == null ? "" : s));
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
    const sourceBody = document.createElement("div");
    sourceBody.innerHTML = `
      <label class="block mb-1.5">
        <span class="text-gray-600">Source</span>
        <select class="mp-ab-source base-input mt-0.5 text-xs"></select>
      </label>
      <input class="mp-ab-search base-input text-xs w-full" type="text"
             placeholder="search a ward / LGA / state by name, then click to add…">
      <div class="mp-ab-status text-[10px] text-gray-500 mt-1"></div>
      <div class="mp-ab-results max-h-32 overflow-y-auto"></div>`;
    const body = document.createElement("div");
    body.innerHTML = `
      <div class="mp-ab-summary text-[11px] font-medium text-purple-700 mt-1"></div>
      <div class="mp-ab-selected space-y-0.5 mt-1"></div>
      <p class="mp-ab-hint text-[10px] text-gray-400 mt-1"></p>`;
    const sourceSel = sourceBody.querySelector(".mp-ab-source");
    const searchEl = sourceBody.querySelector(".mp-ab-search");
    const statusEl = sourceBody.querySelector(".mp-ab-status");
    const resultsEl = sourceBody.querySelector(".mp-ab-results");
    const summaryEl = body.querySelector(".mp-ab-summary");
    const hintEl = body.querySelector(".mp-ab-hint");
    const selectedListEl = body.querySelector(".mp-ab-selected");

    if (controlsHost) {
      controlsHost.appendChild(body); // search + selected list mount in the rail
    } else {
      body.insertBefore(sourceBody, body.firstChild); // no host: source + controls together
    }

    function setStatus(t) {
      statusEl.textContent = t || "";
    }
    function renderSourceOptions() {
      sourceSel.innerHTML = (availableSources || [])
        .map(
          (n) =>
            `<option value="${esc(n)}"${n === source ? " selected" : ""}>${esc(
              sourceLabels[n] || n,
            )}</option>`,
        )
        .join("");
      sourceSel.parentElement.style.display =
        availableSources.length > 1 ? "" : "none";
    }
    function renderSummary() {
      if (!selected.size) {
        summaryEl.textContent = "";
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
      selectedListEl.innerHTML = "";
      const showArm = armEnabled();
      selected.forEach((v, id) => {
        const row = document.createElement("div");
        row.className =
          "flex items-center justify-between gap-1.5 text-[11px] px-1.5 py-0.5 rounded bg-gray-50 border border-gray-200";
        const name = document.createElement("span");
        name.className = "truncate flex-1";
        name.textContent = (v.desc && v.desc.name) || "(area)";
        row.appendChild(name);
        if (showArm) row.appendChild(armPill(id, v));
        const x = document.createElement("button");
        x.type = "button";
        x.className =
          "text-gray-400 hover:text-red-600 leading-none px-1 shrink-0";
        x.textContent = "×";
        x.title = "Remove from plan area";
        x.addEventListener("click", () => toggleSelect(v.desc));
        row.appendChild(x);
        selectedListEl.appendChild(row);
      });
    }
    // Slick per-boundary arm selector: a two-segment Interv / Control pill, the
    // active half filled with its arm colour. Changing it re-tags the boundary's
    // draw feature via the host's onArmChange.
    function setArm(id, arm) {
      const v = selected.get(id);
      if (!v || v.arm === arm) return;
      v.arm = arm;
      onArmChange(id, arm);
      renderSummary();
    }
    function armPill(id, v) {
      const arm = v.arm || "intervention";
      const wrap = document.createElement("div");
      wrap.className =
        "inline-flex rounded overflow-hidden border border-gray-200 text-[9px] font-semibold leading-none shrink-0";
      wrap.title = "Study arm for this ward (intervention vs control)";
      ARMS.forEach((a) => {
        const b = document.createElement("button");
        b.type = "button";
        const on = a === arm;
        b.className = "px-1.5 py-0.5 transition-colors";
        b.style.background = on ? ARM_COLOR[a] : "#fff";
        b.style.color = on ? "#fff" : "#6b7280";
        b.textContent = ARM_LABEL[a];
        b.addEventListener("click", (e) => {
          e.stopPropagation();
          setArm(id, a);
        });
        wrap.appendChild(b);
      });
      return wrap;
    }
    function renderHint() {
      hintEl.textContent = isAreaPhase()
        ? "Click a boundary on the map, OR use the search box above (by ward/LGA/state name) and click a result, to add it to the plan area."
        : "Click a boundary to inspect it.";
    }

    // ---- map layers ----
    function ensureLayers() {
      if (!map.getSource(SRC)) {
        map.addSource(SRC, { type: "geojson", data: empty() });
        // Invisible fill over every boundary so a click/hover INSIDE a polygon
        // (not only on its line) hits the layer and can toggle selection.
        map.addLayer({
          id: FILL,
          type: "fill",
          source: SRC,
          paint: { "fill-color": COLOR, "fill-opacity": 0 },
        });
        // Fill the hovered boundary so it's visible even when you're zoomed
        // INSIDE it (its outline off-screen). Filtered to the hovered id.
        map.addLayer({
          id: FILL_HOVER,
          type: "fill",
          source: SRC,
          filter: ["==", ["get", "boundary_id"], "__none__"],
          paint: { "fill-color": COLOR, "fill-opacity": 0.22 },
        });
        map.addLayer({
          id: LINE,
          type: "line",
          source: SRC,
          paint: {
            "line-color": COLOR,
            // coarser level (lower number) = thicker; finer = thinner
            // Scale with zoom so fine (ward) outlines stay clearly visible when
            // zoomed in, instead of staying hair-thin on the satellite imagery.
            "line-width": [
              "interpolate",
              ["linear"],
              ["zoom"],
              6,
              [
                "interpolate",
                ["linear"],
                ["coalesce", ["get", "admin_level"], 1],
                1,
                2.4,
                2,
                1.4,
                4,
                0.7,
              ],
              13,
              [
                "interpolate",
                ["linear"],
                ["coalesce", ["get", "admin_level"], 1],
                1,
                4,
                2,
                3,
                4,
                2,
              ],
            ],
            "line-opacity": 1,
          },
        });
        map.addLayer({
          id: HOVER,
          type: "line",
          source: SRC,
          filter: ["==", ["get", "boundary_id"], "__none__"],
          paint: { "line-color": COLOR, "line-width": 3.2, "line-opacity": 1 },
        });
      }
      if (!map.getSource(SEL_SRC)) {
        map.addSource(SEL_SRC, { type: "geojson", data: empty() });
        map.addLayer({
          id: SEL_FILL,
          type: "fill",
          source: SEL_SRC,
          paint: { "fill-color": COLOR, "fill-opacity": 0.3 },
        });
        map.addLayer({
          id: SEL_LINE,
          type: "line",
          source: SEL_SRC,
          paint: { "line-color": COLOR, "line-width": 1.6 },
        });
      }
    }
    function empty() {
      return { type: "FeatureCollection", features: [] };
    }
    function setVisible(on) {
      [FILL, FILL_HOVER, LINE, HOVER, SEL_FILL, SEL_LINE].forEach((id) => {
        if (map.getLayer(id))
          map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
      });
    }
    function teardown() {
      M.removeSourceAndLayers(map, SRC, [FILL, FILL_HOVER, LINE, HOVER]);
      M.removeSourceAndLayers(map, SEL_SRC, [SEL_FILL, SEL_LINE]);
    }

    // ---- viewport fetch ----
    async function refresh() {
      if (!layer.on || !urls.viewport) return;
      const b = map.getBounds();
      const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
        .map((n) => n.toFixed(5))
        .join(",");
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
      if (iso && (hostIso || source !== "labs")) params.set("iso", iso);
      if (source) params.set("source", source);
      if (fetchCtrl) fetchCtrl.abort();
      fetchCtrl = new AbortController();
      setStatus("Loading boundaries…");
      try {
        const data = await M.apiGet(urls.viewport + "?" + params.toString(), {
          signal: fetchCtrl.signal,
        });
        if (data.status !== "ok") {
          setStatus(data.detail || "Could not load boundaries");
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
        map.getSource(SRC).setData({ type: "FeatureCollection", features });
        renderSourceOptions();
        // search is country-wide + user-driven, so it doesn't re-run on viewport refresh
        setStatus(
          `${features.length.toLocaleString()} boundaries from ${
            sourceLabels[source] || source || "default"
          }${truncated ? " · zoom in to see all" : ""}`,
        );
      } catch (e) {
        if (e && e.name === "AbortError") return;
        setStatus("Failed: " + e);
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
      if (p.parent_name) rows.push(["Parent", p.parent_name]);
      rows.push([
        "Admin level",
        "ADM" + (p.admin_level != null ? p.admin_level : "?"),
      ]);
      if (p.iso_code) rows.push(["Country", p.iso_code]);
      if (p.area_km2 != null)
        rows.push(["Area", Math.round(p.area_km2).toLocaleString() + " km²"]);
      if (p.population != null)
        rows.push(["Population", Math.round(p.population).toLocaleString()]);
      const isSel = selected.has(p.boundary_id);
      const body = [
        `<div style="font-weight:700;margin-bottom:.25rem">${esc(
          p.name || "Boundary",
        )}</div>`,
        `<div style="font-size:.62rem;color:#6b7280">via ${esc(
          sourceLabels[p.source] || p.source || "",
        )}</div>`,
        '<table style="margin-top:.35rem;font-size:.66rem;color:#374151">' +
          rows
            .map(
              ([k, v]) =>
                `<tr><td style="color:#9ca3af;padding-right:.5rem">${esc(
                  k,
                )}</td><td>${esc(v)}</td></tr>`,
            )
            .join("") +
          "</table>",
      ];
      if (isAreaPhase()) {
        body.push(
          `<button type="button" class="mp-ab-act button button-sm ${
            isSel ? "outline-style" : "primary-dark"
          } text-xs mt-2 w-full">` +
            `${isSel ? "Remove from area" : "Add to area"}</button>`,
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
      const node = document.createElement("div");
      node.innerHTML = body.join("");
      const act = node.querySelector(".mp-ab-act");
      if (act) act.addEventListener("click", () => toggleSelect(featToDesc(f)));
      const par = node.querySelector(".mp-ab-parent");
      if (par && point) {
        par.addEventListener("click", () => {
          const pf = parentOf(point, p.admin_level || 0);
          if (pf) pinInspect(pf, point);
        });
      }
      return node;
    }

    function showHover(f, point) {
      const filt = ["==", ["get", "boundary_id"], f.properties.boundary_id];
      if (map.getLayer(HOVER)) map.setFilter(HOVER, filt);
      if (map.getLayer(FILL_HOVER)) map.setFilter(FILL_HOVER, filt);
      panel.setInspect(inspectHTML(f, point), false);
    }
    function clearHover() {
      const none = ["==", ["get", "boundary_id"], "__none__"];
      if (map.getLayer(HOVER)) map.setFilter(HOVER, none);
      if (map.getLayer(FILL_HOVER)) map.setFilter(FILL_HOVER, none);
      if (pinned) panel.setInspect(pinned, false);
      else panel.clearInspect();
    }
    function pinInspect(f, point) {
      const node = inspectHTML(f, point);
      pinned = node.cloneNode(true);
      // re-bind handlers on the pinned clone
      const act = pinned.querySelector(".mp-ab-act");
      if (act) act.addEventListener("click", () => toggleSelect(featToDesc(f)));
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
      if (typeof ref === "string") {
        try {
          ref = JSON.parse(ref);
        } catch (e) {
          ref = {};
        }
      }
      return {
        key: p.boundary_id,
        name: p.name,
        level: p.admin_level,
        parent_name: p.parent_name || "",
        source: p.source,
        country: p.iso_code,
        ref: ref,
        area_km2: p.area_km2,
        population: p.population != null ? p.population : null,
      };
    }
    function rowToDesc(a) {
      const ref = a.ref || {};
      const key = ref.boundary_id || `${a.source}:${a.region || ""}:${a.name}`;
      return {
        key,
        name: a.name,
        level: a.level,
        parent_name: a.parent_name || ref.parent_name || "",
        source: a.source,
        country: a.country,
        ref,
        area_km2: a.area_km2,
        population: a.population != null ? a.population : null,
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
      setStatus("Fetching geometry…");
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
        if (!resp.ok || d.status !== "ok" || !d.geometry) {
          setStatus(d.detail || "Geometry lookup failed");
          return;
        }
        // New picks default to the intervention arm; the per-row pill changes it.
        selected.set(id, { desc, geometry: d.geometry, arm: "intervention" });
        onAreaAdd(id, d.geometry, desc, "intervention");
        syncSelectedSource();
        renderSummary();
        setStatus("");
      } catch (e) {
        setStatus("Failed: " + e);
      }
    }
    function syncSelectedSource() {
      ensureLayers();
      const feats = [];
      selected.forEach((v) =>
        feats.push({ type: "Feature", geometry: v.geometry, properties: {} }),
      );
      map
        .getSource(SEL_SRC)
        .setData({ type: "FeatureCollection", features: feats });
    }

    // ---- search ----
    // Country-wide by name across all levels via the resolver-backed areas endpoint
    // (reuses urls.areas), scoped to the active source + the resolved country. Falls
    // back to a client-side filter over the in-view features when we don't yet know
    // the country (e.g. before the first viewport load) or the endpoint is absent.
    let searchCtrl = null;
    function renderResults(items) {
      resultsEl.innerHTML = "";
      items.slice(0, 40).forEach(({ desc, label }) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className =
          "block w-full text-left truncate text-purple-700 text-xs px-1 py-0.5 hover:bg-purple-50";
        b.textContent = label;
        b.addEventListener("click", () => {
          if (isAreaPhase()) toggleSelect(desc);
          else setStatus("Switch to the area phase to add boundaries.");
        });
        resultsEl.appendChild(b);
      });
    }
    function clientFilter(q) {
      return features
        .filter((f) => (f.properties.name || "").toLowerCase().includes(q))
        .map((f) => {
          const p = f.properties;
          return {
            desc: featToDesc(f),
            label:
              `${p.name} · ADM${p.admin_level}` +
              (p.area_km2 ? ` · ${Math.round(p.area_km2)} km²` : ""),
          };
        });
    }
    // Place search (Mapbox geocoding) used on a cold map with no country yet:
    // type a place name → results → click one → fly the map there so boundaries
    // load. Independent of the admin-area resolver (which needs a country), so it
    // works from a blank new-plan page.
    let geocodeCtrl = null;
    async function geocodeAndFly(q) {
      const token = (window.mapboxgl && mapboxgl.accessToken) || "";
      if (!token) {
        renderResults(clientFilter(q.toLowerCase()));
        return;
      }
      if (geocodeCtrl) geocodeCtrl.abort();
      geocodeCtrl = new AbortController();
      setStatus("Searching places…");
      try {
        const url =
          "https://api.mapbox.com/geocoding/v5/mapbox.places/" +
          encodeURIComponent(q) +
          ".json?limit=6&types=region,district,place,locality&access_token=" +
          encodeURIComponent(token);
        const r = await fetch(url, { signal: geocodeCtrl.signal });
        const d = await r.json();
        const feats = (d && d.features) || [];
        resultsEl.innerHTML = "";
        if (!feats.length) {
          setStatus("No places found");
          return;
        }
        feats.forEach((f) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className =
            "block w-full text-left truncate text-purple-700 text-xs px-1 py-0.5 hover:bg-purple-50";
          b.textContent = f.place_name || f.text;
          b.addEventListener("click", () => {
            // Seed the country from the geocode result so the boundary layer can
            // load on arrival — the global Overture source needs an iso, and it's
            // otherwise only inferred from boundaries that haven't loaded yet. The
            // viewport endpoint normalizes alpha-2 (Mapbox) → alpha-3 (resolver).
            const ctry = (f.context || []).find((c) =>
              (c.id || "").startsWith("country"),
            );
            const code =
              (ctry && ctry.short_code) ||
              (f.properties && f.properties.short_code) ||
              "";
            if (code && !detectedIso) detectedIso = code.toUpperCase();
            map.flyTo({ center: f.center, zoom: 9 });
            searchEl.value = "";
            resultsEl.innerHTML = "";
            setStatus("Moved — click a boundary on the map to add it.");
          });
          resultsEl.appendChild(b);
        });
        setStatus(feats.length + " place(s) — click to go there");
      } catch (e) {
        if (e && e.name === "AbortError") return;
        renderResults(clientFilter(q.toLowerCase()));
      }
    }

    async function runSearch() {
      const q = (searchEl.value || "").trim();
      resultsEl.innerHTML = "";
      if (!q) {
        setStatus("");
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
      setStatus("Searching…");
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
          if (d && d.status === "ok")
            (d.areas || []).forEach((a) =>
              items.push({
                desc: rowToDesc(a),
                label:
                  `${a.name} · ${a.level_label || "ADM" + a.level}` +
                  (a.area_km2 ? ` · ${Math.round(a.area_km2)} km²` : ""),
              }),
            );
        });
        renderResults(items);
        setStatus(items.length ? `${items.length} match(es)` : "No matches");
      } catch (e) {
        if (e && e.name === "AbortError") return;
        renderResults(clientFilter(q.toLowerCase())); // network error → best-effort
      }
    }
    searchEl.addEventListener("input", M.debounce(runSearch, 250));

    sourceSel.addEventListener("change", () => {
      source = sourceSel.value || null;
      refresh();
    });

    // ---- map interaction wiring (bound once, guarded by layer.on) ----
    let wired = false;
    function wireMap() {
      if (wired) return;
      wired = true;
      map.on("mousemove", FILL, (e) => {
        if (!layer.on || !e.features.length) return;
        map.getCanvas().style.cursor = "pointer";
        const f = smallestAt(e.point) || e.features[0];
        showHover(f, e.point);
      });
      map.on("mouseleave", FILL, () => {
        if (!layer.on) return;
        map.getCanvas().style.cursor = "";
        clearHover();
      });
      map.on("click", FILL, (e) => {
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
      map.on("moveend", () => {
        if (layer.on) refreshDebounced();
      });
    }

    const layer = panel.registerLayer({
      id: "admin",
      label: "Boundaries",
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

    return {
      layer,
      refresh,
      // Re-render the selected-boundary list (e.g. when the host toggles sampling
      // mode on/off, so the per-boundary arm pills appear/disappear).
      renderSelected: renderSummary,
      enable() {
        layer.setEnabled(true);
      },
      teardown,
      selectedCount: () => selected.size,
    };
  }

  global.MicroplansAdminBoundaries = { register };
})(window);
