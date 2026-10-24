/*
 * Service-area picker for solicitations: pick admin areas at ANY level (a whole
 * state, an LGA, a few wards) on a map, for
 *   - a call's owner, naming the areas the work must be inside, and
 *   - a respondent, naming where they would do the work.
 *
 * It reuses the microplans boundary layer wholesale (as boundary_bulk_picker.js
 * does): MicroplansAdminBoundaries draws every level, picks the smallest area
 * under a click, offers "Select parent" to climb a level, and searches every
 * level by name. This file owns only the selected list, the hidden input the
 * form posts, and (for a respondent on a limited call) the limits drawn on the
 * map, with picks outside them refused.
 *
 * The hidden input carries descriptors only. The server resolves each area's
 * geometry itself and is the judge of "inside the limits"
 * (solicitations/service_areas.py); the check here is a courtesy.
 *
 *   SolServiceAreaPicker.create({
 *     wrap, mapEl, listEl, input,   // elements (wrap is position:relative)
 *     token, csrf,
 *     urls: { viewport, areas, geometry },  // omit for a read-only map
 *     initial: [area],              // stored areas (with geometry)
 *     limits: [area],               // the call's limits, drawn dashed
 *     country: 'NGA' | null,
 *     levelLabels: { default: {1: '...'}, NGA: {1: 'State', ...} },
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
    return byCountry[level] || fallback[level] || 'ADM' + level;
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

  // ---- the picker -----------------------------------------------------------
  function create(opts) {
    const urls = opts.urls || null;
    const readOnly = !urls;
    const limits = opts.limits || [];
    const labels = opts.levelLabels || {};
    const listEl = opts.listEl || null;
    const input = opts.input || null;
    const onChange = opts.onChange || function () {};
    const M = global.Microplans;
    const esc =
      (M && M.esc) ||
      ((s) =>
        String(s == null ? '' : s).replace(
          /[&<>"']/g,
          (c) => '&#' + c.charCodeAt(0) + ';',
        ));

    // id -> { desc, geometry }
    const selected = new Map();
    (opts.initial || []).forEach((a) => {
      if (a && a.key) selected.set(a.key, { desc: a, geometry: a.geometry });
    });

    // The country searched and drawn. A limited call's is its limits'; otherwise
    // the person chooses it (the boundary layer would otherwise guess it from
    // whatever is in view, and search the wrong country).
    let chosen = opts.country || null;
    function country() {
      if (chosen) return chosen;
      const first = limits[0] || (selected.values().next().value || {}).desc;
      return (first && first.country) || null;
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

    let adminLayer = null;
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

    let map = null;
    function drawPicked() {
      if (!map || !map.getSource('sap-picked')) return;
      const items = [];
      selected.forEach((v) => items.push({ geometry: v.geometry }));
      map.getSource('sap-picked').setData(featureCollection(items));
    }
    function remove(id) {
      if (!selected.has(id)) return;
      if (adminLayer && adminLayer.removeArea) adminLayer.removeArea(id);
      selected.delete(id);
      changed();
    }
    function changed(message) {
      writeInput();
      renderList(message);
      drawPicked();
      onChange(selected.size);
    }

    writeInput();
    renderList();

    if (!opts.mapEl || !opts.token || !global.mapboxgl)
      return { count: () => selected.size };
    if (!readOnly && !limits.length && (opts.countries || []).length)
      countryPicker();
    mapboxgl.accessToken = opts.token;
    try {
      map = new mapboxgl.Map({
        container: opts.mapEl,
        style: 'mapbox://styles/mapbox/light-v11',
        center: [8.6753, 9.082],
        zoom: 3,
      });
    } catch (e) {
      return { count: () => selected.size }; // no WebGL: the list still works
    }
    map.addControl(
      new mapboxgl.NavigationControl({ showCompass: false }),
      'bottom-left',
    );

    // A "Country" select above the map, for a call with no limits.
    function countryPicker() {
      const label = document.createElement('label');
      label.className = 'flex items-center gap-2 text-sm text-gray-700 mb-2';
      label.innerHTML = '<span class="font-medium">Country</span>';
      const select = document.createElement('select');
      select.className = 'base-dropdown max-w-xs';
      select.setAttribute('data-testid', 'sap-country');
      const current = country();
      select.innerHTML =
        '<option value="">Choose a country…</option>' +
        opts.countries
          .map(
            (c) =>
              '<option value="' +
              esc(c.alpha3) +
              '"' +
              (c.alpha3 === current ? ' selected' : '') +
              '>' +
              esc(c.name) +
              '</option>',
          )
          .join('');
      select.addEventListener('change', () => {
        chosen = select.value || null;
        const picked = opts.countries.find((c) => c.alpha3 === chosen);
        if (picked) flyToCountry(picked);
        if (adminLayer && adminLayer.refresh) adminLayer.refresh();
      });
      label.appendChild(select);
      opts.wrap.parentNode.insertBefore(label, opts.wrap);
      return select;
    }
    async function flyToCountry(c) {
      try {
        const r = await fetch(
          'https://api.mapbox.com/geocoding/v5/mapbox.places/' +
            encodeURIComponent(c.name) +
            '.json?types=country&limit=1&country=' +
            encodeURIComponent(c.alpha2.toLowerCase()) +
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
        /* the search still works; the person pans there */
      }
    }

    function fit() {
      const items = limits.length
        ? limits
        : Array.from(selected.values()).map((v) => ({ geometry: v.geometry }));
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

    map.on('load', () => {
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
      const panel = MicroplansMapPanel.create({ map, mount: opts.wrap });
      const rail = document.createElement('div');
      rail.style.display = 'none';
      opts.wrap.appendChild(rail);
      adminLayer = MicroplansAdminBoundaries.register({
        panel,
        map,
        csrf: opts.csrf,
        controlsHost: rail,
        urls: {
          viewport: urls.viewport,
          geometry: urls.geometry,
          areas: urls.areas,
        },
        getCountryIso: country,
        isAreaPhase: () => true,
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
          Array.from(selected.entries()).map(([id, v]) => ({
            ...v.desc,
            boundary_id: id,
            geometry: v.geometry,
          })),
        );
      }
      adminLayer.enable();
    });

    return { count: () => selected.size, map: () => map };
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
  };
})(typeof window !== 'undefined' ? window : globalThis);
