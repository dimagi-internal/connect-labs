/*
 * The area picker for solicitations: name where work happens, as whole countries
 * or admin areas at any level beneath them, on one map. Used by
 *   - a call's owner, naming the areas the work must be inside,
 *   - a respondent, naming where they would do the work, and
 *   - read-only maps of either (no urls).
 *
 * The person chooses the LEVEL they pick at (Country, Level 1, Level 2...: the
 * same neutral words everywhere, since every country names its tiers its own
 * way, and as many levels as the country in view has), and that one switch
 * decides what the map outlines,
 * what a click selects and what the search finds. Clicking an area picks it;
 * clicking it again drops it. Any number of areas, at mixed levels, may be
 * picked; the server drops one already inside another.
 *
 *   - Countries (level 0) are drawn from Mapbox's country tiles, so the whole
 *     world is clickable without fetching an outline. The server resolves a
 *     picked country's outline itself (Overture, by its code).
 *   - Levels 1+ reuse the microplans boundary layer wholesale
 *     (MicroplansAdminBoundaries), always on, asked for one level at a time;
 *     its search box mounts in this picker's toolbar. Its layers card is not
 *     shown: the boundaries are simply there.
 *
 * The hidden input carries descriptors only. The server resolves each area's
 * geometry and is the judge of "inside the limits" (solicitations/
 * service_areas.py); the check here is a courtesy.
 *
 *   SolServiceAreaPicker.create({
 *     wrap, mapEl, listEl, toolsEl, input,  // elements (wrap is position:relative)
 *     token, csrf,
 *     urls: { viewport, areas, geometry, levels },  // omit for a read-only map
 *     initial: [area],              // stored areas (with geometry)
 *     limits: [area],               // the call's limits, drawn dashed
 *     minLevel: 0,                  // coarsest level allowed (0 = whole countries)
 *     level: 0,                     // the level the map starts on
 *     countries: [{alpha2, alpha3, name}],
 *     levelLabels: { default: {0: 'Country', 1: '...'}, NGA: {1: 'State', ...} },
 *     emptyText: 'No areas picked yet.',
 *     onChange: (count) => {},
 *   })
 *
 *   SolServiceAreaPicker.insideAny(geometry, limits)  // true | false | null (can't tell)
 */
(function (global) {
  'use strict';

  const PICK = '#7c3aed';
  const LIMIT = '#0f766e';

  // ---- geometry helpers (pure; unit-tested) --------------------------------
  function rings(geometry) {
    if (!geometry) return [];
    if (geometry.type === 'Polygon') return [geometry.coordinates];
    if (geometry.type === 'MultiPolygon') return geometry.coordinates;
    return [];
  }
  function inRing(pt, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i][0],
        yi = ring[i][1],
        xj = ring[j][0],
        yj = ring[j][1];
      if (
        yi > pt[1] !== yj > pt[1] &&
        pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi
      )
        inside = !inside;
    }
    return inside;
  }
  function inGeometry(pt, geometry) {
    return rings(geometry).some(
      (poly) =>
        poly.length &&
        inRing(pt, poly[0]) &&
        !poly.slice(1).some((hole) => inRing(pt, hole)),
    );
  }
  // Area-weighted centroid of the largest polygon's outer ring.
  function centroid(geometry) {
    let best = null,
      bestArea = 0;
    rings(geometry).forEach((poly) => {
      const ring = poly[0] || [];
      let a = 0,
        cx = 0,
        cy = 0;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const f = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
        a += f;
        cx += (ring[j][0] + ring[i][0]) * f;
        cy += (ring[j][1] + ring[i][1]) * f;
      }
      if (Math.abs(a) > bestArea) {
        bestArea = Math.abs(a);
        best = [cx / (3 * a), cy / (3 * a)];
      }
    });
    return best;
  }
  // Whether an area lies inside any limit: tested at its centroid, when the
  // centroid is inside the area itself. null when that can't be decided here
  // (an odd shape): the server decides.
  function insideAny(geometry, limits) {
    if (!limits || !limits.length) return true;
    const c = centroid(geometry);
    if (!c || !inGeometry(c, geometry)) return null;
    return limits.some((l) => inGeometry(c, l.geometry));
  }

  function levelLabel(labels, level, country) {
    const byCountry = (labels && labels[country]) || {};
    const fallback = (labels && labels.default) || {};
    return (
      byCountry[level] ||
      fallback[level] ||
      (Number(level) === 0 ? 'Country' : 'Level ' + level)
    );
  }
  function featureCollection(items) {
    return {
      type: 'FeatureCollection',
      features: (items || [])
        .filter((a) => a && a.geometry)
        .map((a) => ({
          type: 'Feature',
          geometry: a.geometry,
          properties: {},
        })),
    };
  }
  // A whole country as an area descriptor. Its identity is its code, so a pick
  // made on the map and the stored area compare equal (service_areas.area_key).
  function countryDesc(alpha3, alpha2, name) {
    const key = 'country:' + alpha3;
    return {
      key,
      name: name || alpha3,
      level: 0,
      source: 'overture',
      country: alpha3,
      region: '',
      parent_name: '',
      ref: { boundary_id: key, alpha2: alpha2 || '', subtype: 'country' },
    };
  }
  // [west, south, east, north] of any GeoJSON coordinates (nested arrays).
  function bounds(coords, acc) {
    acc = acc || [180, 90, -180, -90];
    if (!coords || !coords.length) return acc;
    if (typeof coords[0] === 'number') {
      acc[0] = Math.min(acc[0], coords[0]);
      acc[1] = Math.min(acc[1], coords[1]);
      acc[2] = Math.max(acc[2], coords[0]);
      acc[3] = Math.max(acc[3], coords[1]);
      return acc;
    }
    coords.forEach((c) => bounds(c, acc));
    return acc;
  }
  // Countries whose name matches a typed query, best (prefix) matches first.
  function matchCountries(countries, q, limit) {
    q = (q || '').trim().toLowerCase();
    if (!q) return [];
    const starts = [],
      contains = [];
    (countries || []).forEach((c) => {
      const n = (c.name || '').toLowerCase();
      if (n.startsWith(q) || (c.alpha3 || '').toLowerCase() === q)
        starts.push(c);
      else if (n.includes(q)) contains.push(c);
    });
    return starts.concat(contains).slice(0, limit || 8);
  }

  // Mapbox's own country polygons: one per country in the "US" worldview.
  const COUNTRY_TILES = 'mapbox://mapbox.country-boundaries-v1';
  const COUNTRY_LAYER = 'country_boundaries';
  const WORLDVIEW = [
    'any',
    ['==', 'all', ['get', 'worldview']],
    ['in', 'US', ['get', 'worldview']],
  ];
  const ISO3 = ['get', 'iso_3166_1_alpha_3'];

  // ---- the picker -----------------------------------------------------------
  function create(opts) {
    const urls = opts.urls || null;
    const readOnly = !urls;
    const limits = opts.limits || [];
    const labels = opts.levelLabels || {};
    const listEl = opts.listEl || null;
    const toolsEl = opts.toolsEl || null;
    const input = opts.input || null;
    const onChange = opts.onChange || function () {};
    const countries = opts.countries || [];
    // The coarsest level the person may pick at (0 = a whole country), and the
    // finer levels of the country in view: countries differ in depth, so they
    // are asked of the server per country (urls.levels), [1, 2, 3] until known.
    const minLevel = Number(opts.minLevel) || 0;
    let subLevels = [1, 2, 3];
    const levelsByCountry = new Map();
    function levels() {
      return (minLevel === 0 ? [0] : []).concat(
        subLevels.filter((l) => l >= Math.max(1, minLevel)),
      );
    }
    let level =
      levels().indexOf(Number(opts.level)) !== -1
        ? Number(opts.level)
        : levels()[0];
    const M = global.Microplans;
    const esc =
      (M && M.esc) ||
      ((s) =>
        String(s == null ? '' : s).replace(
          /[&<>"']/g,
          (c) => '&#' + c.charCodeAt(0) + ';',
        ));

    // key -> { desc, geometry }
    const selected = new Map();
    (opts.initial || []).forEach((a) => {
      if (a && a.key) selected.set(a.key, { desc: a, geometry: a.geometry });
    });
    const pickedCountries = () =>
      Array.from(selected.values())
        .map((v) => v.desc || {})
        .filter((d) => Number(d.level) === 0)
        .map((d) => d.country);

    let map = null;
    let adminLayer = null;
    let hoverText = '';

    // The country a sub-national level searches and draws: the call's, else the
    // one under the middle of the map, else the last country picked.
    function centreCountry() {
      if (!map || !map.getLayer('sap-country-fill')) return null;
      try {
        const hit = map.queryRenderedFeatures(map.project(map.getCenter()), {
          layers: ['sap-country-fill'],
        })[0];
        return (hit && hit.properties.iso_3166_1_alpha_3) || null;
      } catch (e) {
        return null;
      }
    }
    function country() {
      if (limits[0] && limits[0].country) return limits[0].country;
      const c = centreCountry();
      if (c) return c;
      const picked = pickedCountries();
      if (picked.length) return picked[picked.length - 1];
      const first = (selected.values().next().value || {}).desc;
      return (first && first.country) || null;
    }
    // "Country", "Level 1", "Level 2"...: every country has its own words for
    // its tiers, so the map uses the same neutral ones everywhere.
    function label(lvl) {
      return levelLabel(labels, lvl, null);
    }
    // Ask which levels the country in view has, once per country.
    let levelsFor = null;
    async function refreshLevels() {
      const iso = country();
      if (!iso || iso === levelsFor || !urls || !urls.levels) return;
      levelsFor = iso;
      let found = levelsByCountry.get(iso);
      if (!found) {
        try {
          const r = await fetch(
            urls.levels + '?iso=' + encodeURIComponent(iso),
            { credentials: 'same-origin' },
          );
          const d = await r.json();
          found = d && d.status === 'ok' ? d.levels || [] : null;
        } catch (e) {
          found = null;
        }
        if (!found) return; // keep what we have; the next move asks again
        levelsByCountry.set(iso, found);
      }
      if (iso !== country()) return; // the map moved on meanwhile
      subLevels = found.length ? found : [1, 2, 3];
      const now = levels();
      if (now.indexOf(level) === -1) {
        // This country stops above the level in use: drop to its deepest.
        const coarser = now.filter((l) => l < level);
        setLevel(coarser.length ? coarser[coarser.length - 1] : now[0]);
      }
      renderLevels();
    }

    function writeInput() {
      if (!input) return;
      const out = [];
      selected.forEach((v) => {
        const d = v.desc || {};
        out.push({
          name: d.name,
          level: d.level,
          source: d.source,
          country: d.country,
          region: d.region || '',
          parent_name: d.parent_name || '',
          ref: d.ref || {},
        });
      });
      input.value = JSON.stringify(out);
    }

    function renderList(message) {
      if (!listEl) return;
      listEl.innerHTML = '';
      if (message) {
        const warn = document.createElement('p');
        warn.className = 'text-sm text-red-600 mb-2';
        warn.setAttribute('role', 'alert');
        warn.textContent = message;
        listEl.appendChild(warn);
      }
      if (!selected.size) {
        if (readOnly) return; // a map of the call's own areas: nothing was meant to be picked
        const p = document.createElement('p');
        p.className = 'text-sm text-gray-500';
        p.textContent = opts.emptyText || 'No areas picked yet.';
        listEl.appendChild(p);
        return;
      }
      const ul = document.createElement('ul');
      ul.className = 'flex flex-wrap gap-2';
      selected.forEach((v, id) => {
        const d = v.desc || {};
        const li = document.createElement('li');
        li.className =
          'inline-flex items-center gap-1.5 rounded-full border border-purple-200 bg-purple-50 px-3 py-1 text-sm';
        li.innerHTML =
          '<span class="font-medium text-gray-900">' +
          esc(d.name) +
          '</span><span class="text-xs text-purple-700">' +
          esc(d.level_label || levelLabel(labels, d.level, d.country)) +
          '</span>' +
          (d.parent_name
            ? '<span class="text-xs text-gray-500">' +
              esc(d.parent_name) +
              '</span>'
            : '');
        if (!readOnly) {
          const x = document.createElement('button');
          x.type = 'button';
          x.className = 'ml-1 text-gray-400 hover:text-red-600 leading-none';
          x.setAttribute('aria-label', 'Remove ' + (d.name || 'area'));
          x.textContent = '×';
          x.addEventListener('click', () => remove(id));
          li.appendChild(x);
        }
        ul.appendChild(li);
      });
      listEl.appendChild(ul);
    }

    function drawPicked() {
      if (!map) return;
      if (map.getSource('sap-picked')) {
        // Countries are filled from the country tiles below; this draws the rest.
        const items = [];
        selected.forEach((v) => {
          if (Number((v.desc || {}).level) !== 0)
            items.push({ geometry: v.geometry });
        });
        map.getSource('sap-picked').setData(featureCollection(items));
      }
      if (map.getLayer('sap-country-picked'))
        map.setFilter('sap-country-picked', [
          'all',
          WORLDVIEW,
          ['in', ISO3, ['literal', pickedCountries()]],
        ]);
    }
    function remove(id) {
      if (!selected.has(id)) return;
      const lvl = Number((selected.get(id).desc || {}).level);
      if (lvl !== 0 && adminLayer && adminLayer.removeArea)
        adminLayer.removeArea(id);
      selected.delete(id);
      changed();
    }
    function changed(message) {
      writeInput();
      renderList(message);
      drawPicked();
      renderStatus();
      onChange(selected.size);
    }

    // ---- toolbar: the level switch and the search -------------------------
    let levelBar = null,
      countrySearch = null,
      countryResults = null,
      countryBox = null,
      navHost = null,
      statusEl = null;
    function renderLevels() {
      if (!levelBar) return;
      const all = levels();
      levelBar.parentNode.hidden = all.length < 2;
      levelBar.innerHTML = '';
      all.forEach((lvl) => {
        const on = lvl === level;
        const b = document.createElement('button');
        b.type = 'button';
        b.dataset.level = String(lvl);
        b.setAttribute('role', 'radio');
        b.setAttribute('aria-checked', on ? 'true' : 'false');
        b.textContent = label(lvl);
        b.className =
          'px-3 py-1.5 text-sm font-medium transition-colors ' +
          (on
            ? 'bg-brand-deep-purple text-white'
            : 'bg-white text-gray-700 hover:bg-gray-50');
        b.addEventListener('click', () => setLevel(lvl));
        levelBar.appendChild(b);
      });
    }
    function renderStatus() {
      if (!statusEl) return;
      const n = selected.size;
      const what = level === 0 ? 'a country' : 'an area';
      statusEl.textContent =
        hoverText ||
        (n ? n + ' picked. ' : '') +
          'Click ' +
          what +
          ' on the map to pick it, and again to drop it.';
    }
    function buildToolbar() {
      if (readOnly || !toolsEl) return;
      toolsEl.innerHTML = '';
      const row = document.createElement('div');
      row.className = 'flex flex-wrap items-start gap-x-4 gap-y-2';
      {
        const group = document.createElement('div');
        group.className = 'flex items-center gap-2';
        group.innerHTML =
          '<span class="text-sm font-medium text-gray-700">Pick by</span>';
        levelBar = document.createElement('div');
        levelBar.setAttribute('role', 'radiogroup');
        levelBar.setAttribute('aria-label', 'Pick areas by');
        levelBar.setAttribute('data-testid', 'sap-levels');
        levelBar.className =
          'inline-flex flex-wrap rounded-lg border border-gray-300 overflow-hidden divide-x divide-gray-300';
        group.appendChild(levelBar);
        row.appendChild(group);
      }
      toolsEl.appendChild(row);
      // The search lives in the map's own panel, like every other labs map:
      // a country search while picking countries, the boundary layer's source
      // and area search below that. Built here, mounted once the map is up.
      countryBox = document.createElement('div');
      countryBox.className = 'mb-1';
      if (minLevel === 0) {
        countrySearch = document.createElement('input');
        countrySearch.type = 'search';
        countrySearch.className = 'base-input text-xs w-full';
        countrySearch.placeholder = 'Search for a country…';
        countrySearch.setAttribute('data-testid', 'sap-country-search');
        countrySearch.setAttribute('aria-label', 'Search for a country');
        countryResults = document.createElement('div');
        countryResults.className =
          'mt-1 max-h-40 overflow-y-auto bg-white border border-gray-200 rounded';
        countryResults.hidden = true;
        countrySearch.addEventListener('input', renderCountryResults);
        countrySearch.addEventListener('keydown', (e) => {
          if (e.key !== 'Enter') return;
          e.preventDefault(); // never submit the form from the search box
          const first = matchCountries(countries, countrySearch.value, 1)[0];
          if (first) pickFromSearch(first);
        });
        countryBox.appendChild(countrySearch);
        countryBox.appendChild(countryResults);
      }
      // The boundary layer's own source + search + results land here.
      navHost = document.createElement('div');
      navHost.className = 'sap-nav';
      statusEl = document.createElement('p');
      statusEl.className = 'text-xs text-gray-500 mt-2';
      statusEl.setAttribute('aria-live', 'polite');
      toolsEl.appendChild(statusEl);
      renderLevels();
      renderStatus();
    }
    function renderCountryResults() {
      const hits = matchCountries(countries, countrySearch.value, 8);
      countryResults.innerHTML = '';
      countryResults.hidden = !hits.length;
      const picked = pickedCountries();
      hits.forEach((c) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className =
          'flex w-full items-center justify-between gap-2 px-2 py-1 text-left text-xs hover:bg-purple-50';
        b.innerHTML =
          '<span>' +
          esc(c.name) +
          '</span>' +
          (picked.indexOf(c.alpha3) !== -1
            ? '<span class="text-xs text-purple-700">picked · click to drop</span>'
            : '');
        b.addEventListener('click', () => pickFromSearch(c));
        countryResults.appendChild(b);
      });
    }
    function pickFromSearch(c) {
      toggleCountry(countryDesc(c.alpha3, c.alpha2, c.name));
      countrySearch.value = '';
      countryResults.hidden = true;
      flyToCountry(c);
    }
    function showSearchFor() {
      if (countryBox) countryBox.hidden = level !== 0 || !countrySearch;
      if (countryResults && level !== 0) countryResults.hidden = true;
      // Picking countries uses the country tiles, not the boundary layer, so
      // its row (switch, source, search) steps aside until a level is chosen.
      if (adminLayer && adminLayer.layer) {
        // .mp-lyr sets display:flex, which would beat the hidden attribute.
        adminLayer.layer.row.style.display = level === 0 ? 'none' : '';
      }
      if (navHost) {
        const q = navHost.querySelector('.mp-ab-search');
        if (q) q.placeholder = 'Search ' + label(level) + ' areas by name…';
      }
    }

    // ---- picking ------------------------------------------------------------
    function toggleCountry(desc) {
      if (selected.has(desc.key)) {
        selected.delete(desc.key);
      } else {
        selected.set(desc.key, { desc, geometry: null });
      }
      changed();
    }
    function setLevel(lvl) {
      if (lvl === level || levels().indexOf(lvl) === -1) return;
      const wasCountry = level === 0;
      level = lvl;
      renderLevels();
      showSearchFor();
      hoverText = '';
      renderStatus();
      if (!map) return;
      paintCountries();
      if (!adminLayer) return;
      if (level === 0) {
        adminLayer.disable();
        return;
      }
      // From the world view into a country: go to the country just picked (else
      // the one in view) first, so there are boundaries to pick from.
      if (wasCountry && map.getZoom() < 4)
        zoomToCountry(pickedCountries().slice(-1)[0] || country());
      if (adminLayer.isOn()) adminLayer.refresh();
      else adminLayer.enable();
    }

    writeInput();
    renderList();
    buildToolbar();
    if (!readOnly) refreshLevels(); // a call's limits name the country up front

    if (!opts.mapEl || !opts.token || !global.mapboxgl)
      return { count: () => selected.size };
    mapboxgl.accessToken = opts.token;
    try {
      map = new mapboxgl.Map({
        container: opts.mapEl,
        style: 'mapbox://styles/mapbox/light-v11',
        center: [15, 5],
        zoom: 2,
      });
    } catch (e) {
      return { count: () => selected.size }; // no WebGL: the list still works
    }
    map.addControl(
      new mapboxgl.NavigationControl({ showCompass: false }),
      'bottom-left',
    );
    // The map can be dragged taller or shorter: keep the canvas filling it.
    if (global.ResizeObserver && opts.wrap)
      new ResizeObserver(() => map.resize()).observe(opts.wrap);

    function zoomToCountry(a3) {
      if (!a3) return;
      let box = null;
      try {
        map
          .querySourceFeatures('sap-countries', {
            sourceLayer: COUNTRY_LAYER,
            filter: ['==', ISO3, a3],
          })
          .forEach((f) => {
            box = bounds(f.geometry && f.geometry.coordinates, box);
          });
      } catch (e) {
        box = null;
      }
      if (box && box[0] <= box[2]) {
        map.fitBounds(
          [
            [box[0], box[1]],
            [box[2], box[3]],
          ],
          { padding: 32, duration: 600 },
        );
        return;
      }
      const c = countries.find((x) => x.alpha3 === a3);
      if (c) flyToCountry(c);
    }
    async function flyToCountry(c) {
      try {
        const r = await fetch(
          'https://api.mapbox.com/geocoding/v5/mapbox.places/' +
            encodeURIComponent(c.name) +
            '.json?types=country&limit=1&country=' +
            encodeURIComponent((c.alpha2 || '').toLowerCase()) +
            '&access_token=' +
            encodeURIComponent(opts.token),
        );
        const f = ((await r.json()).features || [])[0];
        if (f && f.bbox)
          map.fitBounds(
            [
              [f.bbox[0], f.bbox[1]],
              [f.bbox[2], f.bbox[3]],
            ],
            { padding: 24, duration: 600 },
          );
      } catch (e) {
        /* the person pans there */
      }
    }

    function fit() {
      const items = limits.length
        ? limits
        : Array.from(selected.values())
            .filter((v) => v.geometry)
            .map((v) => ({ geometry: v.geometry }));
      if (!items.length || !M || !M.fitTo) return;
      try {
        M.fitTo(map, featureCollection(items), {
          padding: 32,
          maxZoom: 9,
          duration: 0,
        });
      } catch (e) {
        /* unusable geometry: framing is a nicety */
      }
    }

    // Countries are outlined at every level, and clickable at level 0.
    function paintCountries() {
      if (!map.getLayer('sap-country-line')) return;
      const picking = level === 0 && !readOnly;
      map.setPaintProperty(
        'sap-country-line',
        'line-color',
        picking ? PICK : '#6b7280',
      );
      map.setPaintProperty(
        'sap-country-line',
        'line-width',
        picking ? 1.2 : 0.8,
      );
      // Below the country level, a picked country is a backdrop for the areas in it.
      map.setPaintProperty(
        'sap-country-picked',
        'fill-opacity',
        level === 0 ? 0.25 : 0.1,
      );
      if (!picking)
        map.setFilter('sap-country-hover', ['==', ISO3, '__none__']);
    }
    function addCountries() {
      map.addSource('sap-countries', { type: 'vector', url: COUNTRY_TILES });
      map.addLayer({
        id: 'sap-country-fill',
        type: 'fill',
        source: 'sap-countries',
        'source-layer': COUNTRY_LAYER,
        filter: WORLDVIEW,
        paint: { 'fill-color': PICK, 'fill-opacity': 0 },
      });
      map.addLayer({
        id: 'sap-country-hover',
        type: 'fill',
        source: 'sap-countries',
        'source-layer': COUNTRY_LAYER,
        filter: ['==', ISO3, '__none__'],
        paint: { 'fill-color': PICK, 'fill-opacity': 0.12 },
      });
      map.addLayer({
        id: 'sap-country-picked',
        type: 'fill',
        source: 'sap-countries',
        'source-layer': COUNTRY_LAYER,
        filter: ['==', ISO3, '__none__'],
        paint: { 'fill-color': PICK, 'fill-opacity': 0.25 },
      });
      map.addLayer({
        id: 'sap-country-line',
        type: 'line',
        source: 'sap-countries',
        'source-layer': COUNTRY_LAYER,
        filter: WORLDVIEW,
        paint: { 'line-color': '#6b7280', 'line-width': 0.8 },
      });
      paintCountries();
      if (readOnly) return;
      map.on('mousemove', 'sap-country-fill', (e) => {
        if (level !== 0 || !e.features.length) return;
        const p = e.features[0].properties;
        map.getCanvas().style.cursor = 'pointer';
        map.setFilter('sap-country-hover', ['==', ISO3, p.iso_3166_1_alpha_3]);
        const on = selected.has('country:' + p.iso_3166_1_alpha_3);
        hoverText =
          (p.name_en || p.name) +
          (on ? ': picked. Click to drop it.' : ': click to pick it.');
        renderStatus();
      });
      map.on('mouseleave', 'sap-country-fill', () => {
        if (level !== 0) return;
        map.getCanvas().style.cursor = '';
        map.setFilter('sap-country-hover', ['==', ISO3, '__none__']);
        hoverText = '';
        renderStatus();
      });
      map.on('click', 'sap-country-fill', (e) => {
        if (level !== 0 || !e.features.length) return;
        const p = e.features[0].properties;
        toggleCountry(
          countryDesc(p.iso_3166_1_alpha_3, p.iso_3166_1, p.name_en || p.name),
        );
        hoverText = '';
        renderStatus();
      });
      // The levels on offer follow the country in view (some have two, some four).
      map.on('moveend', refreshLevels);
      map.once('idle', refreshLevels);
    }

    map.on('load', () => {
      addCountries();
      map.addSource('sap-limits', {
        type: 'geojson',
        data: featureCollection(limits),
      });
      map.addLayer({
        id: 'sap-limits-fill',
        type: 'fill',
        source: 'sap-limits',
        paint: { 'fill-color': LIMIT, 'fill-opacity': 0.06 },
      });
      map.addLayer({
        id: 'sap-limits-line',
        type: 'line',
        source: 'sap-limits',
        paint: {
          'line-color': LIMIT,
          'line-width': 2.2,
          'line-dasharray': [2, 1.5],
        },
      });
      map.addSource('sap-picked', {
        type: 'geojson',
        data: featureCollection([]),
      });
      map.addLayer({
        id: 'sap-picked-fill',
        type: 'fill',
        source: 'sap-picked',
        paint: { 'fill-color': PICK, 'fill-opacity': 0.25 },
      });
      map.addLayer({
        id: 'sap-picked-line',
        type: 'line',
        source: 'sap-picked',
        paint: { 'line-color': PICK, 'line-width': 1.6 },
      });
      drawPicked();
      fit();

      if (
        readOnly ||
        !global.MicroplansMapPanel ||
        !global.MicroplansAdminBoundaries
      )
        return;
      // The boundary layer adds its outlines later, above ours: keep the call's
      // dashed limits on top once they arrive, or they hide under them.
      let limitsOnTop = !limits.length;
      map.on('sourcedata', (e) => {
        if (
          limitsOnTop ||
          e.sourceId !== 'mp-admin' ||
          !map.getLayer('mp-admin-line')
        )
          return;
        map.moveLayer('sap-limits-line');
        limitsOnTop = true;
      });
      // The map's panel, as on the other labs maps: the Boundaries layer with
      // its source and area search, and above it the country search.
      const panel = MicroplansMapPanel.create({
        map,
        mount: opts.wrap,
        tabs: ['layers'],
      });
      const layersSec = panel.el.querySelector('[data-sec="layers"]');
      if (countryBox && layersSec)
        layersSec.insertBefore(countryBox, layersSec.firstChild);
      adminLayer = MicroplansAdminBoundaries.register({
        panel,
        map,
        csrf: opts.csrf,
        navHost,
        urls: {
          viewport: urls.viewport,
          geometry: urls.geometry,
          areas: urls.areas,
        },
        getCountryIso: country,
        getLevel: () => (level > 0 ? level : null),
        isAreaPhase: () => level > 0,
        onHover: (desc) => {
          hoverText = desc
            ? desc.name +
              ' (' +
              levelLabel(labels, desc.level, desc.country) +
              ')' +
              (selected.has(desc.key)
                ? ': picked. Click to drop it.'
                : ': click to pick it.')
            : '';
          renderStatus();
        },
        onAreaAdd: (id, geometry, desc) => {
          if (insideAny(geometry, limits) === false) {
            // Not inside the call's areas: undo the layer's pick and say why.
            setTimeout(() => adminLayer && adminLayer.removeArea(id), 0);
            changed(
              (desc.name || 'That area') +
                ' is outside the areas this call covers. Pick an area inside the dashed outline.',
            );
            return;
          }
          selected.set(id, { desc, geometry });
          changed();
        },
        onAreaRemove: (id) => {
          if (!selected.has(id)) return;
          selected.delete(id);
          changed();
        },
      });
      if (adminLayer.restore) {
        adminLayer.restore(
          Array.from(selected.entries())
            .filter(([, v]) => Number((v.desc || {}).level) !== 0)
            .map(([id, v]) => ({
              ...v.desc,
              boundary_id: id,
              geometry: v.geometry,
            })),
        );
      }
      if (adminLayer.layer && navHost) adminLayer.layer.setBody(navHost);
      showSearchFor();
      if (level > 0) adminLayer.enable();
    });

    return {
      count: () => selected.size,
      map: () => map,
      level: () => level,
      setLevel,
    };
  }

  // Start every map the page declares (_service_area_picker.html renders a
  // <script id="sap-config-<id>"> per map).
  function boot() {
    if (typeof document === 'undefined') return;
    document.querySelectorAll('script[id^="sap-config-"]').forEach((el) => {
      if (el.dataset.started) return;
      el.dataset.started = '1';
      let cfg;
      try {
        cfg = JSON.parse(el.textContent || '{}');
      } catch (e) {
        return;
      }
      const byId = (suffix) =>
        document.getElementById('sap-' + cfg.id + '-' + suffix);
      create({
        wrap: byId('wrap'),
        mapEl: byId('map'),
        listEl: byId('list'),
        toolsEl: byId('tools'),
        input: cfg.input_id ? document.getElementById(cfg.input_id) : null,
        token: cfg.token,
        csrf: cfg.csrf,
        urls: cfg.urls || null,
        initial: cfg.initial,
        limits: cfg.limits,
        country: cfg.country,
        levelLabels: cfg.level_labels,
        emptyText: cfg.empty_text,
        countries: cfg.countries,
        minLevel: cfg.min_level,
        level: cfg.level,
      });
    });
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading')
      document.addEventListener('DOMContentLoaded', boot);
    else boot();
  }

  global.SolServiceAreaPicker = {
    create,
    boot,
    insideAny,
    centroid,
    inGeometry,
    countryDesc,
    matchCountries,
    bounds,
  };
})(typeof window !== 'undefined' ? window : globalThis);
