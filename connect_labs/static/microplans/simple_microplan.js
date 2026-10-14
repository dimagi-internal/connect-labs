/* ============================================================
   Simple Microplan creator
   1. Boundary  2. Work areas (auto-grid / draw / smart draw)
   3. Grouping (auto / manual)  4. Export CSV
   The grid engine, building fetch, slugs, singleton columns and CSV writer are
   the standalone generator's (a browser port of generate_grid_v2.py), kept as
   they were so the exported CSV does not change shape.
   ============================================================ */
(function () {
  'use strict';

  const STATE = JSON.parse(document.getElementById('mp-state').textContent);
  const EDITABLE = !!STATE.editable;
  const $ = (id) => document.getElementById(id);

  const S3_BASE =
    'https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop';
  const GOB_PREFIX = 'google-research-open-buildings/geoparquet-by-country';
  const HYPARQUET_URL = 'https://cdn.jsdelivr.net/npm/hyparquet@1.26.2/+esm';
  const SINGLETON_THRESHOLD_M = 300;
  const MAX_CELLS = 400000;
  const BLDG_LAYER_CAP = 60000;
  const WGS84 = '+proj=longlat +datum=WGS84 +no_defs';
  const CSV_HEADERS = [
    'Area Slug',
    'Ward',
    'Centroid',
    'Boundary',
    'Building Count',
    'Expected Visit Count',
    'LGA',
    'State',
    'Target Population',
  ];

  const ISO3_TO_2 = {
    IND: 'IN',
    NGA: 'NG',
    KEN: 'KE',
    GHA: 'GH',
    TZA: 'TZ',
    UGA: 'UG',
    ETH: 'ET',
    BGD: 'BD',
    PAK: 'PK',
    LKA: 'LK',
    IDN: 'ID',
    PHL: 'PH',
    ZAF: 'ZA',
    RWA: 'RW',
    MOZ: 'MZ',
  };

  // OCHA COD-AB column candidates (same lists as the Python script)
  const WARD_NAME_COLS = [
    'ADM3_EN',
    'adm3_name',
    'adm3_ref_name',
    'ADM3_REF',
    'admin3Name',
    'wardname',
    'WardName',
    'WARD_NAME',
    'ward',
    'name',
  ];
  const LGA_NAME_COLS = [
    'ADM2_EN',
    'adm2_name',
    'ADM2_REF',
    'admin2Name',
    'lganame',
    'LGAName',
    'LGA_NAME',
    'lga',
  ];
  const STATE_NAME_COLS = [
    'ADM1_EN',
    'adm1_name',
    'ADM1_REF',
    'admin1Name',
    'statename',
    'StateName',
    'STATE_NAME',
    'state',
  ];

  if (typeof L === 'undefined') {
    $('map').innerHTML =
      '<div style="padding:24px;font:14px system-ui;color:#475569">Could not load the map ' +
      'library (Leaflet). This page needs an internet connection — reconnect and reload.</div>';
    return;
  }

  /* ============================================================
     Plan state
     ============================================================ */
  const saved = STATE.data || {};
  let boundary = Array.isArray(saved.boundary) ? saved.boundary : []; // [{polys, ward, lga, state, optionalBuildings}]
  let rows = Array.isArray(saved.rows) ? saved.rows : []; // the CSV rows, in export order
  let settings = saved.settings || {};
  let admin = settings.admin || null; // reverse-geocoded {ward,lga,state,iso2}
  let dirty = false;

  /* ============================================================
     Small helpers
     ============================================================ */
  function escapeHtml(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function csrfToken() {
    const el = document.querySelector('input[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  }
  function fmtDate(iso) {
    try {
      return new Date(iso).toLocaleString(undefined, {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch (e) {
      return iso;
    }
  }
  const slugOf = (r) => r['Area Slug'];
  const groupOf = (r) => r['Work Area Group Name'] || 'Ungrouped';

  // Centroid is stored "lng lat" -> [lon, lat]
  function parseCentroidLL(str) {
    if (!str) return null;
    const p = String(str).trim().split(/\s+/).map(Number);
    if (p.length < 2 || p.some(isNaN)) return null;
    return [p[0], p[1]];
  }
  // WKT POLYGON "((lng lat, ...))" -> [[lon,lat],...]
  function parseWktRing(wkt) {
    if (!wkt) return null;
    const m = String(wkt).match(/\(\(([^)]*)\)\)/);
    if (!m) return null;
    const ring = m[1]
      .split(',')
      .map((pair) => pair.trim().split(/\s+/).map(Number))
      .filter((c) => c.length >= 2 && !c.slice(0, 2).some(isNaN))
      .map((c) => [c[0], c[1]]);
    return ring.length >= 3 ? ring : null;
  }
  function ringToWkt(ring) {
    return (
      'POLYGON((' +
      ring.map((p) => p[0].toFixed(8) + ' ' + p[1].toFixed(8)).join(', ') +
      '))'
    );
  }

  /* ---------- log panel ---------- */
  function log(msg, cls) {
    const box = $('mp-log');
    box.style.display = 'block';
    const line = document.createElement('div');
    if (cls) line.className = cls;
    line.textContent = msg;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }
  function clearLog() {
    const b = $('mp-log');
    b.innerHTML = '';
    b.style.display = 'none';
  }

  /* ---------- progress bar ---------- */
  let progBase = 0,
    progSpan = 1,
    progHideTimer = null;
  function setProgress(frac, label) {
    clearTimeout(progHideTimer);
    $('mp-progress').style.display = 'block';
    const pct = Math.max(
      0,
      Math.min(100, Math.round((progBase + frac * progSpan) * 100)),
    );
    $('mp-bar-fill').style.width = pct + '%';
    $('mp-bar-label').textContent = (label ? label + ' — ' : '') + pct + '%';
  }
  function hideProgress(delayMs) {
    clearTimeout(progHideTimer);
    progHideTimer = setTimeout(() => {
      $('mp-progress').style.display = 'none';
      $('mp-bar-fill').style.width = '0%';
    }, delayMs || 0);
  }

  /* ============================================================
     Map
     ============================================================ */
  const map = L.map('map', { zoomControl: false }).setView([9.6, 8.1], 6);
  L.control.zoom({ position: 'topleft' }).addTo(map);
  // OSM, as the other labs Leaflet maps use (CARTO's free tiles now ask for a key).
  // OSM refuses tile requests with no Referer, and labs' Referrer-Policy is
  // same-origin, so the tiles send this site's origin explicitly.
  const streets = L.tileLayer(
    'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    {
      attribution: '&copy; OpenStreetMap contributors',
      maxNativeZoom: 19,
      maxZoom: 20,
      referrerPolicy: 'strict-origin-when-cross-origin',
    },
  ).addTo(map);
  const satellite = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {
      maxNativeZoom: 19,
      maxZoom: 20,
      attribution:
        'Imagery &copy; Esri &mdash; Esri, Maxar, Earthstar Geographics',
    },
  );
  L.control
    .layers({ Streets: streets, Satellite: satellite }, null, {
      position: 'topright',
      collapsed: false,
    })
    .addTo(map);

  const FitControl = L.Control.extend({
    options: { position: 'topleft' },
    onAdd: function () {
      const c = L.DomUtil.create('div', 'leaflet-bar fit-control');
      const a = L.DomUtil.create('a', '', c);
      a.href = '#';
      a.title = 'Fit map to the microplan';
      a.innerHTML = '&#x21F2;';
      L.DomEvent.on(a, 'click', (e) => {
        L.DomEvent.preventDefault(e);
        fitToData();
      });
      return c;
    },
  });
  map.addControl(new FitControl());

  const waCountCtl = L.control({ position: 'bottomleft' });
  waCountCtl.onAdd = function () {
    const d = L.DomUtil.create('div', 'wa-total-box');
    d.id = 'wa-total-box';
    d.style.display = 'none';
    return d;
  };
  waCountCtl.addTo(map);

  const boundaryLayer = L.layerGroup().addTo(map);
  const waLayer = L.layerGroup().addTo(map);
  const bldgLayer = L.layerGroup();
  const drawLayer = L.layerGroup().addTo(map);
  const baseLayer = L.layerGroup().addTo(map);
  const snapMarker = L.circleMarker([0, 0], {
    radius: 6,
    color: '#fff',
    weight: 2,
    fillColor: '#f97316',
    fillOpacity: 1,
    interactive: false,
  });

  /* ============================================================
     Work areas on the map + selection
     ============================================================ */
  const groupColors = {};
  function colorForGroup(group) {
    if (groupColors[group]) return groupColors[group];
    const i = Object.keys(groupColors).length;
    const hue = (i * 137.508) % 360; // golden-angle spacing -> distinct hues
    groupColors[group] = `hsl(${hue.toFixed(1)}, 68%, 52%)`;
    return groupColors[group];
  }

  const entries = new Map(); // slug -> {row, layer}
  const selected = new Set(); // slugs
  let selectMode = false;

  function waStyle(row) {
    const c = colorForGroup(groupOf(row));
    return selected.has(slugOf(row))
      ? { color: '#0f172a', weight: 3, fillColor: c, fillOpacity: 0.85 }
      : { color: c, weight: 1, fillColor: c, fillOpacity: 0.55 };
  }
  function tooltipHtml(row) {
    return (
      '<div class="popup"><div class="popup-title">' +
      escapeHtml(slugOf(row)) +
      '</div>' +
      '<div class="popup-row"><span>Building Count</span><b>' +
      escapeHtml(row['Building Count']) +
      '</b></div>' +
      '<div class="popup-row"><span>Expected Visit Count</span><b>' +
      escapeHtml(row['Expected Visit Count']) +
      '</b></div>' +
      '<div class="popup-row"><span>Work Area Group</span><b>' +
      escapeHtml(groupOf(row)) +
      '</b></div></div>'
    );
  }

  function addEntry(row) {
    const ring = parseWktRing(row['Boundary']);
    const c = parseCentroidLL(row['Centroid']);
    let layer;
    if (ring)
      layer = L.polygon(
        ring.map((p) => [p[1], p[0]]),
        waStyle(row),
      );
    else if (c)
      layer = L.circleMarker(
        [c[1], c[0]],
        Object.assign({ radius: 5 }, waStyle(row)),
      );
    else return;
    layer.bindTooltip(() => tooltipHtml(row), { sticky: true, opacity: 0.95 });
    layer.on('click', (e) => {
      if (draw.active || picking) return; // the map's own click handler places the point
      L.DomEvent.stopPropagation(e);
      onWorkAreaClick(slugOf(row), e.originalEvent);
    });
    entries.set(slugOf(row), { row, layer, ring });
    waLayer.addLayer(layer);
  }

  function renderWorkAreas() {
    waLayer.clearLayers();
    entries.clear();
    rows.forEach(addEntry);
    for (const s of [...selected]) if (!entries.has(s)) selected.delete(s);
    refreshGroups();
    renderSelection();
    updateCounts();
  }

  function restyle(slug) {
    const e = entries.get(slug);
    if (e) e.layer.setStyle(waStyle(e.row));
  }

  function updateCounts() {
    $('mp-wa-count').textContent = rows.length;
    const el = $('wa-total-box');
    el.style.display = rows.length ? 'block' : 'none';
    el.innerHTML =
      '<b>' + rows.length + '</b> work area' + (rows.length === 1 ? '' : 's');
    // Connect's work-area import rejects a row with a blank Ward, LGA or State
    const missing = $('mp-admin-missing');
    if (missing && window.SimpleMicroplanAdmin) {
      const n = window.SimpleMicroplanAdmin.countMissingAdmin(rows);
      missing.hidden = !n;
      missing.textContent =
        n +
        (n === 1 ? ' work area has' : ' work areas have') +
        ' no Ward/LGA/State — Connect will reject them.';
    }
  }

  function onWorkAreaClick(slug, oe) {
    const toggle =
      selectMode || (oe && (oe.ctrlKey || oe.metaKey || oe.shiftKey));
    if (toggle) {
      if (selected.has(slug)) selected.delete(slug);
      else selected.add(slug);
      restyle(slug);
    } else {
      const only = selected.size === 1 && selected.has(slug);
      const prev = [...selected];
      selected.clear();
      prev.forEach(restyle);
      if (!only) {
        selected.add(slug);
        restyle(slug);
      }
    }
    renderSelection();
  }

  function setSelection(slugs) {
    const prev = [...selected];
    selected.clear();
    prev.forEach(restyle);
    slugs.forEach((s) => {
      selected.add(s);
      restyle(s);
    });
    renderSelection();
  }

  function groupNames() {
    return [...new Set(rows.map(groupOf))].sort();
  }

  function renderSelection(note) {
    const el = $('mp-selection');
    if (!selected.size) {
      el.innerHTML =
        (note ? '<p class="group-done">' + escapeHtml(note) + '</p>' : '') +
        '<p class="muted">No work areas selected.</p>';
      return;
    }
    const list = [...selected]
      .map((s) => entries.get(s))
      .filter(Boolean)
      .map((e) => e.row);
    const totalB = list.reduce(
      (s, r) => s + (Number(r['Building Count']) || 0),
      0,
    );
    let html =
      '<p class="sel-count"><b>' +
      list.length +
      '</b> selected · ' +
      totalB +
      ' buildings <button class="link-btn" id="mp-sel-clear" style="float:right">Clear</button></p>';
    if (list.length === 1) {
      const r = list[0];
      html +=
        '<div class="popup" style="margin-top:8px">' +
        [
          ['Slug', slugOf(r)],
          ['Group', groupOf(r)],
          ['Building Count', r['Building Count']],
          ['Expected Visit Count', r['Expected Visit Count']],
          ['Ward', r['Ward']],
          ['LGA', r['LGA']],
          ['State', r['State']],
        ]
          .map(
            ([k, v]) =>
              '<div class="popup-row"><span>' +
              k +
              '</span><b>' +
              escapeHtml(v) +
              '</b></div>',
          )
          .join('') +
        '</div>';
    } else {
      const shown = list.slice(0, 60);
      html +=
        '<div class="chips">' +
        shown
          .map(
            (r) =>
              '<span class="chip"><span class="swatch" style="background:' +
              colorForGroup(groupOf(r)) +
              '"></span>' +
              escapeHtml(slugOf(r)) +
              '<button data-slug="' +
              escapeHtml(slugOf(r)) +
              '" title="Remove from selection">×</button></span>',
          )
          .join('') +
        (list.length > shown.length
          ? '<span class="chip">+' +
            (list.length - shown.length) +
            ' more</span>'
          : '') +
        '</div>';
    }
    if (EDITABLE) {
      html +=
        '<div class="group-edit">' +
        '<label for="mp-group-name">Group name for the selected work area' +
        (list.length === 1 ? '' : 's') +
        '</label>' +
        '<div class="inline-row">' +
        '<input class="control" id="mp-group-name" list="mp-group-list" autocomplete="off" placeholder="Type or pick a group">' +
        '<button class="btn-secondary inline" id="mp-group-apply">Assign</button></div>' +
        '<datalist id="mp-group-list">' +
        groupNames()
          .map((g) => '<option value="' + escapeHtml(g) + '">')
          .join('') +
        '</datalist>' +
        '<div class="msg" id="mp-group-msg"></div>' +
        '<button class="btn-secondary danger" id="mp-sel-delete">Delete selected work area' +
        (list.length === 1 ? '' : 's') +
        '</button>' +
        '</div>';
    }
    el.innerHTML = html;
    el.querySelector('#mp-sel-clear').onclick = () => setSelection([]);
    el.querySelectorAll('.chip button[data-slug]').forEach((b) => {
      b.onclick = () => {
        selected.delete(b.dataset.slug);
        restyle(b.dataset.slug);
        renderSelection();
      };
    });
    if (EDITABLE) {
      const inp = el.querySelector('#mp-group-name');
      if (list.length === 1) inp.value = groupOf(list[0]);
      const apply = () => {
        const name = inp.value.trim();
        if (!name) {
          el.querySelector('#mp-group-msg').textContent =
            'Type a group name first.';
          return;
        }
        list.forEach((r) => {
          r['Work Area Group Name'] = name;
          restyle(slugOf(r));
        });
        refreshGroups();
        markDirty();
        // done with these: deselect so the next group starts from a clean slate
        const prev = [...selected];
        selected.clear();
        prev.forEach(restyle);
        renderSelection(
          'Moved ' +
            list.length +
            ' work area' +
            (list.length === 1 ? '' : 's') +
            ' to “' +
            name +
            '”.',
        );
      };
      el.querySelector('#mp-group-apply').onclick = apply;
      inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') apply();
      });
      el.querySelector('#mp-sel-delete').onclick = () => {
        if (
          !confirm(
            'Delete ' +
              list.length +
              ' work area' +
              (list.length === 1 ? '' : 's') +
              '?',
          )
        )
          return;
        const gone = new Set(list.map(slugOf));
        rows = rows.filter((r) => !gone.has(slugOf(r)));
        selected.clear();
        renderWorkAreas();
        markDirty();
      };
    }
  }

  function refreshGroups() {
    const counts = new Map();
    const bldgs = new Map();
    rows.forEach((r) => {
      counts.set(groupOf(r), (counts.get(groupOf(r)) || 0) + 1);
      bldgs.set(
        groupOf(r),
        (bldgs.get(groupOf(r)) || 0) + (Number(r['Building Count']) || 0),
      );
    });
    // building-grouped WAGs outside the range they were made for
    const checked = new Set((groupCheck && groupCheck.names) || []);
    const missed = (g) =>
      checked.has(g) &&
      (bldgs.get(g) < groupCheck.low || bldgs.get(g) > groupCheck.high);
    const box = $('mp-group-legend');
    const names = [...counts.keys()].sort();
    box.innerHTML = names.length
      ? names
          .map(
            (g) =>
              '<div class="grp" data-g="' +
              escapeHtml(g) +
              '" title="Select this group">' +
              '<span class="swatch" style="background:' +
              colorForGroup(g) +
              '"></span>' +
              '<span class="g-name">' +
              escapeHtml(g) +
              (missed(g)
                ? ' <span class="g-flag" title="Outside the building range">⚠</span>'
                : '') +
              '</span><span class="g-b">' +
              bldgs.get(g) +
              ' bldg</span><span class="g-n">' +
              counts.get(g) +
              ' WA</span></div>',
          )
          .join('')
      : '';
    box.querySelectorAll('.grp').forEach((node) => {
      node.onclick = () => {
        const g = node.dataset.g;
        const slugs = rows.filter((r) => groupOf(r) === g).map(slugOf);
        setSelection(slugs);
        zoomTo(slugs);
      };
    });
  }

  function zoomTo(slugs) {
    let b = null;
    slugs.forEach((s) => {
      const e = entries.get(s);
      if (!e) return;
      const lb = e.layer.getBounds
        ? e.layer.getBounds()
        : L.latLngBounds([e.layer.getLatLng()]);
      b = b
        ? b.extend(lb)
        : L.latLngBounds(lb.getSouthWest(), lb.getNorthEast());
    });
    if (b) map.fitBounds(b.pad(0.3), { maxZoom: 18 });
  }

  function fitToData() {
    let b = null;
    const ext = (lb) => {
      b = b
        ? b.extend(lb)
        : L.latLngBounds(lb.getSouthWest(), lb.getNorthEast());
    };
    boundaryLayer.eachLayer((l) => {
      if (l.getBounds) ext(l.getBounds());
    });
    entries.forEach((e) =>
      ext(
        e.layer.getBounds
          ? e.layer.getBounds()
          : L.latLngBounds([e.layer.getLatLng()]),
      ),
    );
    if (b) map.fitBounds(b.pad(0.1));
  }

  /* ============================================================
     Find a place (OSM Nominatim; searches on submit only, per its usage policy)
     ============================================================ */
  const placeLayer = L.layerGroup().addTo(map);

  function goToPlace(lat, lon, bbox, label) {
    placeLayer.clearLayers();
    placeLayer.addLayer(
      L.circleMarker([lat, lon], {
        radius: 7,
        color: '#fff',
        weight: 2,
        fillColor: '#dc2626',
        fillOpacity: 1,
        interactive: false,
      }).bindTooltip(label, { permanent: false, direction: 'top' }),
    );
    if (bbox) map.fitBounds(bbox, { maxZoom: 17 });
    else map.setView([lat, lon], Math.max(map.getZoom(), 16));
    $('mp-place-stat').textContent = label;
  }

  async function searchPlace(q) {
    const box = $('mp-place-results');
    box.style.display = 'none';
    const ll = parseLatLon(q);
    if (ll && /^\s*-?\d/.test(q)) {
      goToPlace(ll[1], ll[0], null, ll[1].toFixed(6) + ', ' + ll[0].toFixed(6));
      return;
    }
    $('mp-place-stat').textContent = 'Searching…';
    let hits = [];
    try {
      const r = await fetch(
        'https://nominatim.openstreetmap.org/search?format=json&limit=8&q=' +
          encodeURIComponent(q),
      );
      if (!r.ok) throw new Error('HTTP ' + r.status);
      hits = await r.json();
    } catch (e) {
      $('mp-place-stat').textContent =
        'Search failed (' + e.message + '). Try again in a moment.';
      return;
    }
    if (!hits.length) {
      $('mp-place-stat').textContent = 'No places found for “' + q + '”.';
      return;
    }
    const pick = (h) => {
      const b = (h.boundingbox || []).map(Number); // [south, north, west, east]
      const bbox =
        b.length === 4 && !b.some(isNaN)
          ? [
              [b[0], b[2]],
              [b[1], b[3]],
            ]
          : null;
      goToPlace(Number(h.lat), Number(h.lon), bbox, h.display_name);
      box.style.display = 'none';
    };
    if (hits.length === 1) {
      pick(hits[0]);
      return;
    }
    $('mp-place-stat').textContent = hits.length + ' matches — pick one.';
    box.innerHTML = hits
      .map(
        (h, i) =>
          '<div class="result" data-i="' +
          i +
          '"><span class="r-label" title="' +
          escapeHtml(h.display_name) +
          '">' +
          escapeHtml(h.display_name) +
          '</span>' +
          '<span class="r-type">' +
          escapeHtml(h.addresstype || h.type || '') +
          '</span></div>',
      )
      .join('');
    box.style.display = 'block';
    box.querySelectorAll('.result').forEach((node) => {
      node.onclick = () => pick(hits[+node.dataset.i]);
    });
  }

  $('mp-place-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const q = $('mp-place').value.trim();
    if (q) searchPlace(q);
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#mp-place-form'))
      $('mp-place-results').style.display = 'none';
  });

  /* ============================================================
     Boundary
     ============================================================ */
  function geomToMultiPoly(geom) {
    if (!geom) return [];
    if (geom.type === 'Polygon') return [geom.coordinates];
    if (geom.type === 'MultiPolygon') return geom.coordinates;
    if (geom.type === 'GeometryCollection')
      return (geom.geometries || []).flatMap(geomToMultiPoly);
    return [];
  }

  function ringAreaHa(ring) {
    // approx planar area of a lon/lat ring, hectares
    if (ring.length < 4) return 0;
    const lat0 = (ring[0][1] * Math.PI) / 180;
    const mx = 111320 * Math.cos(lat0),
      my = 110574;
    let a = 0;
    for (let i = 1; i < ring.length; i++) {
      a +=
        ring[i - 1][0] * mx * (ring[i][1] * my) -
        ring[i][0] * mx * (ring[i - 1][1] * my);
    }
    return Math.abs(a / 2) / 10000;
  }

  function renderBoundary() {
    boundaryLayer.clearLayers();
    let ha = 0;
    boundary.forEach((part) => {
      boundaryLayer.addLayer(
        L.geoJSON(
          { type: 'MultiPolygon', coordinates: part.polys },
          {
            style: {
              color: '#0f172a',
              weight: 2.5,
              dashArray: '7 5',
              fill: false,
            },
            interactive: false,
          },
        ),
      );
      part.polys.forEach((poly) =>
        poly.forEach((ring, i) => {
          ha += (i === 0 ? 1 : -1) * ringAreaHa(ring);
        }),
      );
    });
    const wards = boundary.map((p) => p.ward).filter(Boolean);
    $('mp-boundary-stat').innerHTML = boundary.length
      ? '<b>Boundary set</b> — ' +
        boundary.length +
        ' part' +
        (boundary.length === 1 ? '' : 's') +
        ', ~' +
        ha.toFixed(1) +
        ' ha' +
        (wards.length ? ' · ' + escapeHtml(wards.join(', ')) : '')
      : 'No boundary yet.';
  }

  function setBoundary(parts) {
    boundary = parts;
    renderBoundary();
    fitToData();
    markDirty();
  }

  $('mp-source').addEventListener('change', () => {
    const v = $('mp-source').value;
    ['draw', 'file', 'bbox', 'wards', 'basegrid'].forEach((k) => {
      $('mp-src-' + k).style.display = k === v ? '' : 'none';
    });
    $('mp-boundary-apply').style.display = v === 'draw' ? 'none' : '';
  });

  /* ---- uploaded boundary GeoJSON ---- */
  let uploadedBoundary = null;
  $('mp-boundary-file').addEventListener('change', (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      try {
        const gj = JSON.parse(rd.result);
        let polys = [];
        if (gj.type === 'FeatureCollection')
          polys = gj.features.flatMap((ft) => geomToMultiPoly(ft.geometry));
        else if (gj.type === 'Feature') polys = geomToMultiPoly(gj.geometry);
        else polys = geomToMultiPoly(gj);
        if (!polys.length) throw new Error('no polygon geometry found');
        // union so overlapping features don't cancel out in even-odd tests
        uploadedBoundary =
          polys.length > 1
            ? polygonClipping.union(...polys.map((p) => [p]))
            : [polys[0]];
        $('mp-file-stat').innerHTML =
          '<b>' +
          escapeHtml(f.name) +
          '</b> loaded — ' +
          polys.length +
          ' polygon part(s). Click “Use as boundary”.';
      } catch (err) {
        uploadedBoundary = null;
        $('mp-file-stat').textContent =
          'Could not read boundary: ' + err.message;
      }
    };
    rd.readAsText(f);
  });

  /* ---- wards file + search + chips + batch CSV ---- */
  let wardsList = null; // [{name, lga, state, feature}]
  let wardSel = [];

  function findCol(keys, candidates) {
    const lower = {};
    keys.forEach((k) => {
      lower[k.toLowerCase()] = k;
    });
    for (const c of candidates)
      if (lower[c.toLowerCase()]) return lower[c.toLowerCase()];
    return null;
  }

  $('mp-wards-file').addEventListener('change', (e) => {
    const f = e.target.files[0];
    if (!f) return;
    $('mp-wards-stat').textContent =
      'Reading ' + f.name + '… (large files can take a moment)';
    const rd = new FileReader();
    rd.onload = () => {
      try {
        const gj = JSON.parse(rd.result);
        const feats = gj.type === 'FeatureCollection' ? gj.features : [gj];
        const keys = Object.keys((feats[0] || {}).properties || {});
        const wc = findCol(keys, WARD_NAME_COLS);
        const lc = findCol(keys, LGA_NAME_COLS);
        const sc = findCol(keys, STATE_NAME_COLS);
        if (!wc)
          throw new Error('no ward-name column among: ' + keys.join(', '));
        wardsList = feats
          .filter((ft) => ft.geometry)
          .map((ft) => ({
            name: String(ft.properties[wc] ?? '').trim(),
            lga: lc ? String(ft.properties[lc] ?? '').trim() : '',
            state: sc ? String(ft.properties[sc] ?? '').trim() : '',
            feature: ft,
          }))
          .filter((w) => w.name);
        $('mp-wards-stat').innerHTML =
          '<b>' +
          wardsList.length +
          '</b> wards | columns → ward:' +
          escapeHtml(wc) +
          (lc ? ' lga:' + escapeHtml(lc) : '') +
          (sc ? ' state:' + escapeHtml(sc) : '');
        $('mp-ward-search').disabled = false;
        $('mp-batch-file').disabled = false;
      } catch (err) {
        wardsList = null;
        $('mp-wards-stat').textContent =
          'Could not read wards file: ' + err.message;
        $('mp-ward-search').disabled = true;
        $('mp-batch-file').disabled = true;
      }
    };
    rd.readAsText(f);
  });

  function renderWardChips() {
    const box = $('mp-ward-chips');
    box.innerHTML = '';
    wardSel.forEach((w, i) => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.innerHTML =
        escapeHtml(w.name) + (w.lga ? ' · ' + escapeHtml(w.lga) : '');
      const x = document.createElement('button');
      x.textContent = '×';
      x.title = 'Remove';
      x.onclick = () => {
        wardSel.splice(i, 1);
        renderWardChips();
      };
      chip.appendChild(x);
      box.appendChild(chip);
    });
  }

  $('mp-ward-search').addEventListener('input', () => {
    const q = $('mp-ward-search').value.trim().toLowerCase();
    const box = $('mp-ward-results');
    if (!q || !wardsList) {
      box.style.display = 'none';
      box.innerHTML = '';
      return;
    }
    const hits = wardsList
      .filter((w) => w.name.toLowerCase().includes(q))
      .slice(0, 12);
    if (!hits.length) {
      box.innerHTML = '<div class="result-empty">No matches</div>';
      box.style.display = 'block';
      return;
    }
    box.innerHTML = hits
      .map(
        (w, i) =>
          '<div class="result" data-i="' +
          i +
          '"><span class="r-label">' +
          escapeHtml(w.name) +
          '</span>' +
          '<span class="r-type">' +
          escapeHtml([w.lga, w.state].filter(Boolean).join(', ') || 'ward') +
          '</span></div>',
      )
      .join('');
    box.style.display = 'block';
    box.querySelectorAll('.result').forEach((node) => {
      node.onclick = () => {
        const w = hits[+node.dataset.i];
        if (!wardSel.includes(w)) {
          wardSel.push(w);
          renderWardChips();
        }
        box.style.display = 'none';
        $('mp-ward-search').value = '';
      };
    });
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#mp-src-wards'))
      $('mp-ward-results').style.display = 'none';
  });

  $('mp-batch-file').addEventListener('change', (e) => {
    const f = e.target.files[0];
    if (!f || !wardsList) return;
    Papa.parse(f, {
      header: true,
      skipEmptyLines: true,
      transformHeader: (h) => h.trim(),
      complete: (res) => {
        const fields = res.meta.fields || [];
        const wc = findCol(fields, [
          'ward',
          'WARD',
          'adm3_en',
          'adm3',
          'ward_name',
          'wardname',
        ]);
        const lc = findCol(fields, [
          'lga',
          'LGA',
          'adm2_en',
          'adm2',
          'lga_name',
        ]);
        const sc = findCol(fields, [
          'state',
          'STATE',
          'adm1_en',
          'adm1',
          'state_name',
        ]);
        if (!wc) {
          log(
            'Batch CSV needs a ward column. Found: ' + fields.join(', '),
            'err',
          );
          return;
        }
        let ok = 0;
        const failed = [];
        res.data.forEach((row) => {
          const wname = String(row[wc] || '').trim();
          if (!wname) return;
          const lhint = lc
            ? String(row[lc] || '')
                .trim()
                .toUpperCase()
            : '';
          const shint = sc
            ? String(row[sc] || '')
                .trim()
                .toUpperCase()
            : '';
          let m = wardsList.filter(
            (w) => w.name.toUpperCase() === wname.toUpperCase(),
          );
          if (shint) m = m.filter((w) => w.state.toUpperCase() === shint);
          if (lhint) m = m.filter((w) => w.lga.toUpperCase() === lhint);
          if (m.length === 1) {
            if (!wardSel.includes(m[0])) {
              wardSel.push(m[0]);
              ok++;
            }
          } else {
            failed.push(
              wname +
                (m.length
                  ? ' (ambiguous: ' + m.length + ' matches)'
                  : ' (not found)'),
            );
          }
        });
        renderWardChips();
        log(
          '[batch] ' +
            ok +
            ' wards added from ' +
            f.name +
            (failed.length ? ' | skipped: ' + failed.join('; ') : ''),
          failed.length ? 'warn' : 'ok',
        );
      },
    });
  });

  /* ---- base points (grid anchor + base-point-grid boundary) ---- */
  function parseLatLon(text) {
    const p = String(text || '')
      .trim()
      .split(/[\s,]+/)
      .filter(Boolean)
      .map(Number);
    if (p.length < 2 || p.slice(0, 2).some(isNaN)) return null;
    const lat = p[0],
      lon = p[1];
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
    return [lon, lat];
  }
  const baseIcon = L.divIcon({
    className: 'base-pin',
    html:
      '<svg viewBox="0 0 24 24" width="26" height="26" fill="#16a34a" stroke="#fff" stroke-width="1.2">' +
      '<path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z"/>' +
      '<circle cx="12" cy="9" r="2.6" fill="#fff" stroke="none"/></svg>',
    iconSize: [26, 26],
    iconAnchor: [13, 25],
  });
  function updateBaseMarkers() {
    baseLayer.clearLayers();
    [
      ['mp-base', 'Grid base point'],
      ['mp-bg-base', 'Base point grid centre'],
    ].forEach(([id, label]) => {
      const pt = parseLatLon($(id).value);
      if (!pt) return;
      const mk = L.marker([pt[1], pt[0]], {
        icon: baseIcon,
        draggable: EDITABLE,
        zIndexOffset: 2000,
      });
      mk.bindTooltip(label, { direction: 'top', offset: [0, -22] });
      mk.on('dragend', () => {
        const ll = mk.getLatLng();
        $(id).value = ll.lat.toFixed(6) + ', ' + ll.lng.toFixed(6);
        markDirty();
      });
      baseLayer.addLayer(mk);
    });
  }
  let picking = null; // input id being picked
  function setPicking(id) {
    picking = id;
    $('mp-base-pick').classList.toggle('active', id === 'mp-base');
    $('mp-base-pick').textContent = id === 'mp-base' ? 'Click map…' : 'Pick';
    $('mp-bg-pick').classList.toggle('active', id === 'mp-bg-base');
    $('mp-bg-pick').textContent = id === 'mp-bg-base' ? 'Click map…' : 'Pick';
    map.getContainer().classList.toggle('mp-drawing', !!id || draw.active);
  }
  $('mp-base-pick').addEventListener('click', () => {
    stopDraw();
    setPicking(picking === 'mp-base' ? null : 'mp-base');
  });
  $('mp-bg-pick').addEventListener('click', () => {
    stopDraw();
    setPicking(picking === 'mp-bg-base' ? null : 'mp-bg-base');
  });
  ['mp-base', 'mp-bg-base'].forEach((id) =>
    $(id).addEventListener('input', updateBaseMarkers),
  );

  /* ---- resolve the chosen source into boundary parts ---- */
  $('mp-boundary-apply').addEventListener('click', () => {
    const src = $('mp-source').value;
    clearLog();
    if (src === 'file') {
      if (!uploadedBoundary) {
        log('Upload a boundary GeoJSON first.', 'err');
        return;
      }
      setBoundary([{ polys: uploadedBoundary, ward: '', lga: '', state: '' }]);
    } else if (src === 'bbox') {
      const p = $('mp-bbox')
        .value.trim()
        .split(/[\s,]+/)
        .filter(Boolean)
        .map(Number);
      if (p.length !== 4 || p.some(isNaN) || p[0] >= p[2] || p[1] >= p[3]) {
        log('Bounding box must be: minLon, minLat, maxLon, maxLat', 'err');
        return;
      }
      const ring = [
        [p[0], p[1]],
        [p[2], p[1]],
        [p[2], p[3]],
        [p[0], p[3]],
        [p[0], p[1]],
      ];
      setBoundary([{ polys: [[ring]], ward: '', lga: '', state: '' }]);
    } else if (src === 'wards') {
      if (!wardSel.length) {
        log('Load the admin GeoJSON and pick at least one ward.', 'err');
        return;
      }
      setBoundary(
        wardSel.map((w) => ({
          polys: geomToMultiPoly(w.feature.geometry),
          ward: w.name,
          lga: w.lga,
          state: w.state,
        })),
      );
    } else if (src === 'basegrid') {
      const base = parseLatLon($('mp-bg-base').value);
      if (!base) {
        log(
          'Base point grid needs a base point — type a "lat, long" or use Pick.',
          'err',
        );
        return;
      }
      const nRows = Math.round(parseFloat($('mp-rows').value));
      const nCols = Math.round(parseFloat($('mp-cols').value));
      if (!(nRows >= 1) || !(nCols >= 1)) {
        log('Rows and columns must be at least 1.', 'err');
        return;
      }
      if (nRows * nCols > MAX_CELLS) {
        log(
          'rows × columns = ' +
            (nRows * nCols).toLocaleString() +
            ' cells exceeds the limit of ' +
            MAX_CELLS.toLocaleString() +
            '.',
          'err',
        );
        return;
      }
      const cs = Math.max(parseFloat($('mp-cell').value) || 50, 5);
      // rectangle centred on the base point, built in UTM so it is lattice-exact
      const conv = proj4(WGS84, autoUtmDef(base[0], base[1]).def);
      const [bx, by] = conv.forward([base[0], base[1]]);
      // inset a hair on every side: projection round-trips can't add a row/column,
      // and no rectangle edge coincides exactly with a grid lattice line
      const eps = 1e-6;
      const x0 = bx - Math.floor(nCols / 2) * cs + eps,
        y0 = by - Math.floor(nRows / 2) * cs + eps;
      const x1 = x0 + nCols * cs - 2 * eps,
        y1 = y0 + nRows * cs - 2 * eps;
      const ring = [
        [x0, y0],
        [x1, y0],
        [x1, y1],
        [x0, y1],
        [x0, y0],
      ].map((pt) => conv.inverse(pt));
      // the grid in step 2 anchors to the same point
      $('mp-base').value = $('mp-bg-base').value;
      updateBaseMarkers();
      log(
        '[basegrid] ' +
          nRows +
          ' × ' +
          nCols +
          ' cells of ' +
          cs +
          'm centred on the base point',
      );
      setBoundary([
        {
          polys: [[ring]],
          ward: '',
          lga: '',
          state: '',
          optionalBuildings: true,
        },
      ]);
    }
  });
  $('mp-boundary-clear').addEventListener('click', () => {
    if (!boundary.length) return;
    if (!confirm('Clear the boundary? Work areas stay where they are.')) return;
    setBoundary([]);
  });
  $('mp-bfinish-btn').addEventListener('click', () => finishClicked());
  $('mp-wafinish-btn').addEventListener('click', () => finishClicked());
  $('mp-bdraw-btn').addEventListener('click', () =>
    draw.active ? stopDraw() : startDraw('boundary'),
  );

  /* ============================================================
     Drawing (boundary, hand-drawn and smart work areas)
     ============================================================ */
  const draw = { active: false, purpose: null, pts: [], snaps: [], poly: null };

  function startDraw(purpose) {
    stopDraw();
    setPicking(null);
    draw.active = true;
    draw.purpose = purpose;
    resetDrawShape();
    map.getContainer().classList.add('mp-drawing');
    map.doubleClickZoom.disable();
    if (purpose === 'smart') buildSnapCtx();
    syncDrawButtons();
  }

  function resetDrawShape() {
    drawLayer.clearLayers();
    draw.pts = [];
    draw.snaps = [];
    draw.poly = null;
  }

  function stopDraw() {
    if (!draw.active) return;
    draw.active = false;
    draw.purpose = null;
    resetDrawShape();
    if (map.hasLayer(snapMarker)) map.removeLayer(snapMarker);
    map.getContainer().classList.toggle('mp-drawing', !!picking);
    map.doubleClickZoom.enable();
    syncDrawButtons();
  }

  function syncDrawButtons() {
    const b = draw.active && draw.purpose === 'boundary';
    $('mp-bdraw-btn').textContent = b ? 'Stop drawing' : 'Start drawing';
    $('mp-bdraw-btn').classList.toggle('active', b);
    const w = draw.active && draw.purpose !== 'boundary';
    $('mp-wadraw-btn').textContent = w
      ? 'Stop drawing'
      : waMode === 'smart'
        ? 'Smart-draw work areas'
        : 'Draw work areas';
    $('mp-wadraw-btn').classList.toggle('active', w);
    $('mp-bfinish-btn').style.display = b ? '' : 'none';
    $('mp-wafinish-btn').style.display = w ? '' : 'none';
  }

  // "Finish" does what a double-click does
  function finishClicked() {
    if (!draw.active) return;
    if (draw.pts.length < 3) {
      log('Place at least 3 corners on the map before finishing.', 'warn');
      return;
    }
    finishShape();
  }

  function addDrawPoint(latlng) {
    let ll = latlng,
      snap = null;
    if (draw.purpose === 'smart') {
      const s = snapLatLng(latlng);
      if (s) {
        ll = s.latlng;
        snap = s;
      }
    }
    if (
      draw.pts.length &&
      map.distance(draw.pts[draw.pts.length - 1], ll) < 0.5
    )
      return;
    draw.pts.push(ll);
    draw.snaps.push(snap);
    const mk = L.circleMarker(ll, {
      radius: 4,
      color: '#fff',
      weight: 2,
      fillColor: snap ? '#f97316' : '#16a34a',
      fillOpacity: 1,
      interactive: false,
    });
    if (draw.pts.length === 1) {
      mk.bindTooltip('Start', {
        permanent: true,
        direction: 'top',
        className: 'mp-draw-tip',
        offset: [0, -4],
      });
    }
    drawLayer.addLayer(mk);
    if (!draw.poly) {
      draw.poly = L.polygon(draw.pts, {
        color: '#16a34a',
        weight: 2,
        dashArray: '6 5',
        fillColor: '#16a34a',
        fillOpacity: 0.08,
        interactive: false,
      });
      drawLayer.addLayer(draw.poly);
    } else {
      draw.poly.setLatLngs(draw.pts);
    }
  }

  async function finishShape() {
    if (draw.pts.length < 3) {
      resetDrawShape();
      return;
    }
    const pts = draw.pts.slice(),
      snaps = draw.snaps.slice(),
      purpose = draw.purpose;
    resetDrawShape();
    if (purpose === 'boundary') {
      const ring = pts.map((p) => [p.lng, p.lat]);
      ring.push([...ring[0]]);
      stopDraw();
      setBoundary([{ polys: [[ring]], ward: '', lga: '', state: '' }]);
      return;
    }
    // work areas: keep drawing the next one until the person stops
    try {
      if (purpose === 'smart') await addSmartWorkArea(pts, snaps);
      else await addHandWorkArea(pts);
    } catch (e) {
      console.error(e);
      log('ERROR: ' + (e && e.message ? e.message : e), 'err');
    }
    if (draw.active && draw.purpose === 'smart') buildSnapCtx();
  }

  map.on('click', (e) => {
    if (picking) {
      $(picking).value =
        e.latlng.lat.toFixed(6) + ', ' + e.latlng.lng.toFixed(6);
      updateBaseMarkers();
      setPicking(null);
      markDirty();
      return;
    }
    if (draw.active) addDrawPoint(e.latlng);
  });
  map.on('dblclick', () => {
    if (draw.active) finishShape();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (draw.active) {
        if (draw.pts.length >= 3) finishShape().then(stopDraw);
        else stopDraw();
      } else if (picking) setPicking(null);
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      save();
    }
  });

  /* ---------- smart snapping ---------- */
  // Targets are the boundary rings and the work-area rings near the view. Each is
  // kept in lon/lat (what goes into the CSV) and in metres (for distances).
  let snapCtx = null;
  function buildSnapCtx() {
    const c = map.getCenter();
    const conv = proj4(WGS84, autoUtmDef(c.lng, c.lat).def);
    const view = map.getBounds().pad(1);
    const near = (ring) => ring.some((p) => view.contains([p[1], p[0]]));
    const lls = [];
    boundary.forEach((part) =>
      part.polys.forEach((poly) => poly.forEach((r) => lls.push(r))),
    );
    entries.forEach((e) => {
      if (e.ring) lls.push(e.ring);
    });
    const rings = [];
    for (const r0 of lls) {
      if (!near(r0)) continue;
      const ll = r0.slice();
      const f = ll[0],
        l = ll[ll.length - 1];
      if (f[0] !== l[0] || f[1] !== l[1]) ll.push([f[0], f[1]]);
      const xy = ll.map((p) => conv.forward([p[0], p[1]]));
      const cum = [0];
      for (let i = 1; i < xy.length; i++)
        cum.push(
          cum[i - 1] +
            Math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]),
        );
      rings.push({ ll, xy, cum, L: cum[cum.length - 1] });
    }
    snapCtx = { conv, rings };
  }
  map.on('moveend', () => {
    if (draw.active && draw.purpose === 'smart') buildSnapCtx();
  });

  function snapTolerance() {
    return Math.max(parseFloat($('mp-snap').value) || 20, 1);
  }

  function snapLatLng(latlng) {
    if (!snapCtx) return null;
    const tol = snapTolerance();
    const [px, py] = snapCtx.conv.forward([latlng.lng, latlng.lat]);
    let bestV = null,
      bestS = null;
    for (const r of snapCtx.rings) {
      for (let i = 0; i + 1 < r.xy.length; i++) {
        const [ax, ay] = r.xy[i],
          [bx, by] = r.xy[i + 1];
        const dv = Math.hypot(px - ax, py - ay);
        if (dv <= tol && (!bestV || dv < bestV.d))
          bestV = { d: dv, ring: r, i, t: 0 };
        const dx = bx - ax,
          dy = by - ay,
          len2 = dx * dx + dy * dy;
        if (!len2) continue;
        const t = Math.max(
          0,
          Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2),
        );
        const ds = Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
        if (ds <= tol && (!bestS || ds < bestS.d))
          bestS = { d: ds, ring: r, i, t };
      }
    }
    const best = bestV || bestS; // corners win: shared corners stay exactly shared
    if (!best) return null;
    const r = best.ring;
    let lon, lat;
    if (best.t === 0) {
      [lon, lat] = r.ll[best.i];
    } else {
      const [ax, ay] = r.xy[best.i],
        [bx, by] = r.xy[best.i + 1];
      [lon, lat] = snapCtx.conv.inverse([
        ax + best.t * (bx - ax),
        ay + best.t * (by - ay),
      ]);
    }
    return {
      latlng: L.latLng(lat, lon),
      ring: r,
      pos: r.cum[best.i] + best.t * (r.cum[best.i + 1] - r.cum[best.i]),
    };
  }

  let snapRaf = null;
  map.on('mousemove', (e) => {
    if (!(draw.active && draw.purpose === 'smart')) return;
    if (snapRaf) return;
    snapRaf = requestAnimationFrame(() => {
      snapRaf = null;
      const s = snapLatLng(e.latlng);
      if (s) {
        snapMarker.setLatLng(s.latlng);
        if (!map.hasLayer(snapMarker)) snapMarker.addTo(map);
      } else if (map.hasLayer(snapMarker)) map.removeLayer(snapMarker);
    });
  });

  // Corners of the shortest way along `ring` from position a to b (exclusive),
  // or [] when following the ring would be a long detour.
  function ringPath(ring, a, b, straight, tol) {
    const L = ring.L;
    if (!L) return [];
    const d1 = (((b - a) % L) + L) % L,
      d2 = L - d1;
    const fwd = d1 <= d2,
      d = fwd ? d1 : d2;
    if (d < 1e-6 || d > 3 * straight + 2 * tol) return [];
    const items = [];
    for (let k = 0; k < ring.ll.length - 1; k++) {
      const off = fwd
        ? (((ring.cum[k] - a) % L) + L) % L
        : (((a - ring.cum[k]) % L) + L) % L;
      if (off > 1e-6 && off < d - 1e-6) items.push([off, ring.ll[k]]);
    }
    items.sort((x, y) => x[0] - y[0]);
    return items.map((x) => x[1]);
  }

  /* ---------- turning a drawn shape into CSV rows ---------- */
  function projectedArea(poly) {
    return poly.reduce(
      (s, ring, i) => s + (i === 0 ? 1 : -1) * ringCentroid(ring).area,
      0,
    );
  }

  async function addHandWorkArea(pts) {
    const ring = pts.map((p) => [p.lng, p.lat]);
    ring.push([...ring[0]]);
    await addDrawnRows([ring]);
  }

  async function addSmartWorkArea(pts, snaps) {
    const tol = snapTolerance();
    // 1. edges between two corners snapped to the same line follow that line
    const traced = [];
    for (let i = 0; i < pts.length; i++) {
      const j = (i + 1) % pts.length;
      traced.push([pts[i].lng, pts[i].lat]);
      const sa = snaps[i],
        sb = snaps[j];
      if (sa && sb && sa.ring === sb.ring) {
        traced.push(
          ...ringPath(
            sa.ring,
            sa.pos,
            sb.pos,
            map.distance(pts[i], pts[j]),
            tol,
          ),
        );
      }
    }
    traced.push([...traced[0]]);

    // 2. clip to the boundary and cut out existing work areas, in metres
    const c = traced[0];
    const conv = proj4(WGS84, autoUtmDef(c[0], c[1]).def);
    const fwd = (ring) => ring.map((p) => conv.forward([p[0], p[1]]));
    let pieces = [[fwd(traced)]];
    try {
      if (boundary.length) {
        const bProj = boundary.flatMap((part) =>
          part.polys.map((poly) => poly.map(fwd)),
        );
        pieces = polygonClipping.intersection(pieces, bProj);
      }
      const [minX, minY, maxX, maxY] = boundsOfMultiPoly([[traced]]);
      const neighbours = [];
      entries.forEach((e) => {
        if (!e.ring) return;
        const [a, b, cc, d] = boundsOfMultiPoly([[e.ring]]);
        if (cc < minX || a > maxX || d < minY || b > maxY) return;
        neighbours.push([fwd(e.ring)]);
      });
      if (neighbours.length && pieces.length)
        pieces = polygonClipping.difference(pieces, ...neighbours);
    } catch (e) {
      log(
        '[smart] Could not clip this shape (' +
          (e.message || e) +
          ') — kept it as drawn.',
        'warn',
      );
      pieces = [[fwd(traced)]];
    }
    // 3. drop slivers left by floating-point edges
    const areas = pieces.map(projectedArea);
    const biggest = Math.max(0, ...areas);
    const keep = pieces.filter(
      (p, i) => areas[i] >= Math.max(25, biggest * 0.02),
    );
    if (!keep.length) {
      log(
        '[smart] Nothing left after clipping — the shape is outside the boundary or already covered.',
        'warn',
      );
      return;
    }
    if (keep.length < pieces.length)
      log('[smart] Dropped ' + (pieces.length - keep.length) + ' sliver(s).');
    await addDrawnRows(
      keep.map((p) => p[0].map((pt) => conv.inverse(pt))),
      keep.map((p) => [p]),
      conv,
    );
  }

  // rings: lon/lat exterior rings. projPolys/conv (optional) give the projected
  // shapes so the centroid matches the grid's area centroid.
  async function addDrawnRows(rings, projPolys, conv) {
    const autofill = $('mp-autofill').checked;
    const fill = Math.max(0, Math.round(parseFloat($('mp-fill').value) || 0));
    const usedSlugs = new Set(rows.map(slugOf));
    const newRows = [];
    for (let k = 0; k < rings.length; k++) {
      const ring = rings[k];
      let cLon, cLat;
      if (projPolys) {
        [cLon, cLat] = conv.inverse(multiPolyCentroid(projPolys[k]));
      } else {
        const cv = proj4(WGS84, autoUtmDef(ring[0][0], ring[0][1]).def);
        [cLon, cLat] = cv.inverse(
          multiPolyCentroid([[ring.map((p) => cv.forward([p[0], p[1]]))]]),
        );
      }
      const adm = await adminFor(cLon, cLat);
      let bc = fill,
        evc = fill;
      if (autofill) {
        const n = await countBuildingsIn(ring, adm.iso);
        if (n === null) {
          log(
            '[autofill] No building data for this area — used the minimum fill (' +
              fill +
              ').',
            'warn',
          );
        } else {
          bc = n;
          evc = n;
        }
      }
      const prefix =
        $('mp-prefix').value.trim() ||
        (adm.ward.slice(0, 2) || 'WA').toUpperCase();
      newRows.push({
        'Area Slug': genSlug(prefix, usedSlugs),
        Ward: adm.ward,
        Centroid: cLon.toFixed(8) + ' ' + cLat.toFixed(8),
        Boundary: ringToWkt(ring),
        'Building Count': bc,
        'Expected Visit Count': evc,
        LGA: adm.lga,
        State: adm.state,
        'Target Population': 0,
        'Work Area Group Name': adm.ward || 'Microplan',
      });
    }
    await fillAdminFromLabsWards(newRows);
    if (!rows.length && !$('mp-csv-name').value.trim()) {
      const a = newRows[0];
      $('mp-csv-name').value = autoCsvName(
        a['Ward'],
        a['LGA'],
        a['State'],
        isoValue() || (admin && admin.iso2) || '',
      );
    }
    rows = rows.concat(newRows);
    newRows.forEach(addEntry);
    refreshGroups();
    updateCounts();
    markDirty();
    log(
      '[draw] Added ' +
        newRows
          .map((r) => r['Area Slug'] + ' (' + r['Building Count'] + ' bldg)')
          .join(', '),
      'ok',
    );
  }

  function isoValue() {
    let iso = $('mp-iso').value.trim().toUpperCase();
    if (iso.length === 3) iso = ISO3_TO_2[iso] || iso;
    return iso.slice(0, 2);
  }

  // Ward/LGA/State for a drawn work area: from the boundary part it falls in, else
  // one reverse-geocode per plan (cached in the plan's settings).
  async function adminFor(lon, lat) {
    const part = boundary.find((p) => p.ward && pipMulti(lon, lat, p.polys));
    if (!admin) {
      log('[geocode] Looking up admin fields / country for this area…');
      admin = await reverseGeocode(lon, lat);
      log(
        '[geocode] Ward: ' +
          admin.ward +
          ' | LGA: ' +
          admin.lga +
          ' | State: ' +
          admin.state +
          ' | ISO: ' +
          admin.iso2,
      );
    }
    if (part)
      return {
        ward: part.ward,
        lga: part.lga,
        state: part.state,
        iso: isoValue() || admin.iso2,
      };
    return {
      ward: admin.ward,
      lga: admin.lga,
      state: admin.state,
      iso: isoValue() || admin.iso2,
    };
  }

  /* ---------- building footprints for drawn areas ---------- */
  let allBuildings = [];
  const buildingKeys = new Set();
  const coveredBoxes = []; // bboxes already fetched

  function addBuildings(list) {
    let added = 0;
    for (const b of list) {
      const k = b.lon.toFixed(7) + ',' + b.lat.toFixed(7);
      if (buildingKeys.has(k)) continue;
      buildingKeys.add(k);
      allBuildings.push(b);
      added++;
    }
    if (added) setBuildingsOverlay(allBuildings);
  }

  async function countBuildingsIn(ring, iso) {
    if (!iso) return null;
    const bb = boundsOfMultiPoly([[ring]]);
    // ~1 cm of slack: an area clipped onto the boundary edge can land a float-hair outside it
    const eps = 1e-7;
    const inside = (box) =>
      box[0] - eps <= bb[0] &&
      box[1] - eps <= bb[1] &&
      box[2] + eps >= bb[2] &&
      box[3] + eps >= bb[3];
    if (!coveredBoxes.some(inside)) {
      // fetch the whole boundary once when the area is inside it, else just around the area
      let fb = null;
      if (boundary.length) {
        const bbB = boundsOfMultiPoly(boundary.flatMap((p) => p.polys));
        if (inside(bbB)) fb = bbB;
      }
      if (!fb) {
        const padLat = 200 / 110574,
          padLon =
            200 / (111320 * Math.max(Math.cos((bb[1] * Math.PI) / 180), 0.01));
        fb = [bb[0] - padLon, bb[1] - padLat, bb[2] + padLon, bb[3] + padLat];
      }
      try {
        setProgress(0.05, 'Reading building data');
        const got = await fetchBuildings(fb, iso, (d, t) =>
          setProgress(
            0.1 + (0.85 * d) / t,
            'Reading building data (' + d + '/' + t + ' row groups)',
          ),
        );
        coveredBoxes.push(fb);
        addBuildings(got);
        log('[buildings] ' + got.length + ' building footprints loaded', 'ok');
        setProgress(1, 'Buildings loaded');
        hideProgress(1500);
      } catch (e) {
        hideProgress(0);
        log('[buildings] ' + (e && e.message ? e.message : e), 'warn');
        return null;
      }
    }
    let n = 0;
    const polys = [[ring]];
    for (const b of allBuildings) {
      if (b.lon < bb[0] || b.lon > bb[2] || b.lat < bb[1] || b.lat > bb[3])
        continue;
      if (pipMulti(b.lon, b.lat, polys)) n++;
    }
    return n;
  }

  function setBuildingsOverlay(buildings) {
    bldgLayer.clearLayers();
    if (!buildings.length) return;
    if (buildings.length > BLDG_LAYER_CAP) {
      log(
        '[map] NOTE: ' +
          buildings.length +
          ' footprints exceed the ' +
          BLDG_LAYER_CAP +
          ' preview cap — building layer omitted.',
        'warn',
      );
      return;
    }
    const fc = {
      type: 'FeatureCollection',
      features: buildings.map((b) => ({
        type: 'Feature',
        properties: {},
        geometry: b.geom,
      })),
    };
    bldgLayer.addLayer(
      L.geoJSON(fc, {
        renderer: L.canvas(),
        interactive: false,
        style: {
          color: '#1565c0',
          weight: 1,
          fillColor: '#42a5f5',
          fillOpacity: 0.5,
        },
      }),
    );
    if ($('mp-show-bldgs').checked && !map.hasLayer(bldgLayer))
      map.addLayer(bldgLayer);
    bldgLayer.eachLayer((l) => l.bringToBack && l.bringToBack());
  }
  $('mp-show-bldgs').addEventListener('change', () => {
    if ($('mp-show-bldgs').checked) {
      if (!map.hasLayer(bldgLayer)) map.addLayer(bldgLayer);
    } else if (map.hasLayer(bldgLayer)) map.removeLayer(bldgLayer);
  });

  /* ============================================================
     Work-area mode switch
     ============================================================ */
  let waMode = 'grid';
  function setWaMode(mode) {
    if (draw.active && draw.purpose !== 'boundary') stopDraw();
    waMode = mode;
    document
      .querySelectorAll('#mp-wa-mode button')
      .forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
    $('mp-mode-grid').style.display = mode === 'grid' ? '' : 'none';
    $('mp-mode-draw').style.display = mode === 'grid' ? 'none' : '';
    $('mp-snap-field').style.display = mode === 'smart' ? '' : 'none';
    $('mp-draw-help').innerHTML =
      mode === 'smart'
        ? 'Sketch roughly. A corner within the snap distance of the boundary or another work area lands on it, ' +
          'and an edge between two such corners follows that line. The result is clipped to the boundary and never ' +
          'overlaps other work areas, so drawing up to an edge fills the gap exactly. Double-click to finish each ' +
          'area; Esc to stop.'
        : 'Click to place corners, double-click to finish a work area, then keep clicking for the next one. ' +
          'Esc to stop.';
    syncDrawButtons();
  }
  document.querySelectorAll('#mp-wa-mode button').forEach((b) =>
    b.addEventListener('click', () => {
      setWaMode(b.dataset.mode);
      markDirty();
    }),
  );
  $('mp-wadraw-btn').addEventListener('click', () => {
    if (draw.active && draw.purpose !== 'boundary') {
      stopDraw();
      return;
    }
    clearLog();
    startDraw(waMode === 'smart' ? 'smart' : 'hand');
  });
  function syncAutofill() {
    const on = $('mp-autofill').checked;
    $('mp-fill-field').style.display = on ? 'none' : '';
    $('mp-autofill-note').style.display = on ? '' : 'none';
  }
  $('mp-autofill').addEventListener('change', syncAutofill);

  $('mp-clear-was').addEventListener('click', () => {
    if (!rows.length) return;
    if (
      !confirm(
        'Remove all ' +
          rows.length +
          ' work areas? This cannot be undone once saved.',
      )
    )
      return;
    rows = [];
    selected.clear();
    $('mp-csv-name').value = '';
    renderWorkAreas();
    markDirty();
  });

  /* ============================================================
     Quadkeys, S3 listing, hyparquet building fetch
     ============================================================ */
  function lonLatToTile(lon, lat, z) {
    const n = 2 ** z;
    const x = Math.floor(((lon + 180) / 360) * n);
    const latRad = (lat * Math.PI) / 180;
    const y = Math.floor(
      ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) *
        n,
    );
    const cl = (v) => Math.min(Math.max(v, 0), n - 1);
    return [cl(x), cl(y)];
  }
  function tileToQuadkey(x, y, z) {
    let qk = '';
    for (let i = z; i > 0; i--) {
      let d = 0;
      const m = 1 << (i - 1);
      if (x & m) d += 1;
      if (y & m) d += 2;
      qk += d;
    }
    return qk;
  }
  // quadkey prefixes covering a bbox: deepest zoom (<=12) with a manageable tile count
  function bboxQuadkeys(minLon, minLat, maxLon, maxLat, maxTiles = 24) {
    for (let z = 12; z >= 0; z--) {
      const [x0, y0] = lonLatToTile(minLon, maxLat, z);
      const [x1, y1] = lonLatToTile(maxLon, minLat, z);
      if ((x1 - x0 + 1) * (y1 - y0 + 1) <= maxTiles) {
        const qks = [];
        for (let x = x0; x <= x1; x++)
          for (let y = y0; y <= y1; y++) qks.push(tileToQuadkey(x, y, z));
        return qks;
      }
    }
    return [''];
  }

  const countryFiles = new Map();
  async function listCountryFiles(iso2) {
    if (countryFiles.has(iso2)) return countryFiles.get(iso2);
    const url =
      S3_BASE +
      '/?list-type=2&prefix=' +
      encodeURIComponent(GOB_PREFIX + '/country_iso=' + iso2 + '/');
    const xml = await (await fetch(url)).text();
    const keys = [...xml.matchAll(/<Key>([^<]+\.parquet)<\/Key>/g)].map(
      (m) => m[1],
    );
    const files = keys.map((k) => ({
      key: k,
      url: S3_BASE + '/' + k.replace(/=/g, '%3D'),
      qk: (k.match(/_(\d+)\.parquet$/) || [])[1] || '',
    }));
    countryFiles.set(iso2, files);
    return files;
  }

  // GeoJSON polygon/multipolygon -> area centroid [lon, lat]
  function ringCentroid(ring) {
    let a = 0,
      sx = 0,
      sy = 0;
    for (let i = 1; i < ring.length; i++) {
      const px = ring[i - 1][0],
        py = ring[i - 1][1],
        x = ring[i][0],
        y = ring[i][1];
      const cr = px * y - x * py;
      a += cr;
      sx += (px + x) * cr;
      sy += (py + y) * cr;
    }
    a /= 2;
    if (!a) return { area: 0, cx: ring[0][0], cy: ring[0][1] };
    return { area: Math.abs(a), cx: sx / (6 * a), cy: sy / (6 * a) };
  }
  function geomCentroid(g) {
    if (g.type === 'Polygon') {
      const c = ringCentroid(g.coordinates[0]);
      return [c.cx, c.cy];
    }
    if (g.type === 'MultiPolygon') {
      let A = 0,
        cx = 0,
        cy = 0;
      for (const poly of g.coordinates) {
        const c = ringCentroid(poly[0]);
        A += c.area;
        cx += c.cx * c.area;
        cy += c.cy * c.area;
      }
      if (A) return [cx / A, cy / A];
      const c = ringCentroid(g.coordinates[0][0]);
      return [c.cx, c.cy];
    }
    return null;
  }

  let hp = null;
  async function ensureHyparquet() {
    if (hp) return hp;
    log('[setup] Loading parquet reader (hyparquet)…');
    hp = await import(HYPARQUET_URL);
    return hp;
  }

  const fileCache = new Map(); // url -> {file, meta}
  const rangeCache = new Map(); // url#rowStart -> [{qk, lon, lat, geom}]

  async function poolRun(items, n, fn) {
    const out = new Array(items.length);
    let i = 0;
    async function worker() {
      while (i < items.length) {
        const idx = i++;
        out[idx] = await fn(items[idx], idx);
      }
    }
    await Promise.all(
      Array.from({ length: Math.min(n, items.length) }, worker),
    );
    return out;
  }

  function statToStr(v) {
    if (v == null) return '';
    if (typeof v === 'string') return v;
    return new TextDecoder().decode(v);
  }

  // Fetch Google Open Buildings intersecting bbox. Returns [{lon,lat,geom}]
  async function fetchBuildings(bbox, iso2, onProgress) {
    const [minLon, minLat, maxLon, maxLat] = bbox;
    const qks = bboxQuadkeys(minLon, minLat, maxLon, maxLat);
    log('[buildings] AOI quadkeys (' + qks.length + '): ' + qks.join(', '));

    log('[buildings] Listing dataset files for country ' + iso2 + '…');
    const files = await listCountryFiles(iso2);
    if (!files.length) {
      throw new Error(
        'Google Open Buildings has no coverage for country "' +
          iso2 +
          '" — check the Country ISO field (alpha-2, e.g. NG, IN, KE).',
      );
    }
    const matched = files.filter((f) =>
      qks.some((q) => q.startsWith(f.qk) || f.qk.startsWith(q)),
    );
    log(
      '[buildings] ' +
        matched.length +
        '/' +
        files.length +
        ' country files match the area',
    );

    const { parquetMetadataAsync, parquetReadObjects, asyncBufferFromUrl } =
      await ensureHyparquet();

    const tasks = [];
    let estBytes = 0;
    for (const f of matched) {
      let entry = fileCache.get(f.url);
      if (!entry) {
        const file = await asyncBufferFromUrl({ url: f.url });
        const meta = await parquetMetadataAsync(file);
        entry = { file, meta };
        fileCache.set(f.url, entry);
      }
      const { file, meta } = entry;
      let rowStart = 0;
      for (const rg of meta.row_groups) {
        const nRows = Number(rg.num_rows);
        const qcol = rg.columns.find(
          (c) => c.meta_data.path_in_schema.join('.') === 'quadkey',
        );
        const st = qcol && qcol.meta_data.statistics;
        let hit = true;
        if (st && st.min_value != null && st.max_value != null) {
          const mn = statToStr(st.min_value),
            mx = statToStr(st.max_value);
          hit = qks.some((q) => !(mx < q || mn > q + '￿'));
        }
        if (hit) {
          for (const c of rg.columns) {
            const p = c.meta_data.path_in_schema.join('.');
            if (p === 'quadkey' || p === 'geometry')
              estBytes += Number(c.meta_data.total_compressed_size);
          }
          tasks.push({ file, url: f.url, rowStart, rowEnd: rowStart + nRows });
        }
        rowStart += nRows;
      }
    }
    log(
      '[buildings] ' +
        tasks.length +
        ' row group(s) to read (~' +
        (estBytes / 1e6).toFixed(1) +
        ' MB). This may take a while…',
    );

    let done = 0;
    const t0 = Date.now();
    const chunks = await poolRun(tasks, 4, async (t) => {
      const ck = t.url + '#' + t.rowStart;
      let mapped = rangeCache.get(ck);
      if (!mapped) {
        const rs = await parquetReadObjects({
          file: t.file,
          columns: ['quadkey', 'geometry'],
          rowStart: t.rowStart,
          rowEnd: t.rowEnd,
        });
        mapped = [];
        for (const r of rs) {
          const c = geomCentroid(r.geometry);
          if (c)
            mapped.push({
              qk: r.quadkey,
              lon: c[0],
              lat: c[1],
              geom: r.geometry,
            });
        }
        rangeCache.set(ck, mapped);
      }
      done++;
      if (onProgress) onProgress(done, tasks.length);
      return mapped;
    });
    log(
      '[buildings] Row groups read in ' +
        ((Date.now() - t0) / 1000).toFixed(0) +
        's',
    );

    const out = [];
    for (const chunk of chunks) {
      for (const b of chunk) {
        if (!qks.some((q) => b.qk.startsWith(q))) continue;
        if (
          b.lon < minLon ||
          b.lon > maxLon ||
          b.lat < minLat ||
          b.lat > maxLat
        )
          continue;
        out.push(b);
      }
    }
    return out;
  }

  /* ============================================================
     CRS + geocoding (ports of auto_crs / reverse_geocode)
     ============================================================ */
  function autoUtmDef(lon, lat) {
    const zone = Math.floor((lon + 180) / 6) + 1;
    return {
      def:
        '+proj=utm +zone=' +
        zone +
        (lat < 0 ? ' +south' : '') +
        ' +datum=WGS84 +units=m +no_defs',
      name: 'EPSG:' + ((lat >= 0 ? 32600 : 32700) + zone),
    };
  }

  async function reverseGeocode(lon, lat) {
    const url =
      'https://nominatim.openstreetmap.org/reverse?lat=' +
      lat +
      '&lon=' +
      lon +
      '&format=json&zoom=14&addressdetails=1';
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const a = (await r.json()).address || {};
      const pick = (...ks) => {
        for (const k of ks) if (a[k]) return a[k];
        return '';
      };
      return {
        ward: pick(
          'suburb',
          'village',
          'town',
          'city_district',
          'neighbourhood',
          'hamlet',
        ),
        lga: pick('county', 'state_district', 'district', 'region'),
        state: pick('state'),
        iso2: (a.country_code || '').toUpperCase(),
      };
    } catch (e) {
      log(
        '[geocode] Warning: reverse geocoding failed (' +
          e.message +
          ') — admin fields left blank',
        'warn',
      );
      return { ward: '', lga: '', state: '', iso2: '' };
    }
  }

  // Blank Ward / LGA / State on new work areas (OpenStreetMap often has no ward)
  // come from Labs' own ward boundaries: the ward containing each centroid, via
  // the boundary-viewport endpoint the CHC creator's Boundaries layer uses.
  const WARDS_URL = $('app').dataset.wardsUrl || '';
  const wardsFetched = []; // [{box, features}]
  async function wardsAround(box) {
    const inside = (b) =>
      b[0] <= box[0] && b[1] <= box[1] && b[2] >= box[2] && b[3] >= box[3];
    const hit = wardsFetched.find((w) => inside(w.box));
    if (hit) return hit.features;
    const pad = 0.02; // ~2 km, so the next area drawn nearby reuses this fetch
    const q = [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad];
    const params = new URLSearchParams({
      bbox: q.map((n) => n.toFixed(5)).join(','),
      level: '3',
      source: 'labs',
      zoom: '14', // full-resolution outlines, so cells near a ward edge land right
    });
    const r = await fetch(WARDS_URL + '?' + params.toString(), {
      credentials: 'same-origin',
    });
    const data = await r.json().catch(() => null);
    if (!r.ok || !data || data.status !== 'ok')
      throw new Error((data && data.detail) || 'HTTP ' + r.status);
    const features = data.features || [];
    wardsFetched.push({ box: q, features });
    return features;
  }

  async function fillAdminFromLabsWards(newRows) {
    const A = window.SimpleMicroplanAdmin;
    if (!A || !WARDS_URL) return;
    const box = A.bboxOfRowsNeedingAdmin(newRows);
    if (!box) return;
    log(
      '[wards] ' +
        A.countMissingAdmin(newRows) +
        ' work area(s) without Ward/LGA/State — looking them up in Labs ward boundaries…',
    );
    try {
      const n = A.fillAdminFromWards(newRows, await wardsAround(box));
      const left = A.countMissingAdmin(newRows);
      log(
        '[wards] Filled ' +
          n +
          (left ? '; ' + left + ' still blank (no Labs ward there).' : '.'),
        left ? 'warn' : 'ok',
      );
    } catch (e) {
      log(
        '[wards] Ward lookup failed (' +
          (e.message || e) +
          ') — admin fields left as they were.',
        'warn',
      );
    }
  }

  /* ============================================================
     Grid engine (port of build_grid + sjoin counting)
     ============================================================ */
  function closeRing(ring) {
    const f = ring[0],
      l = ring[ring.length - 1];
    if (f[0] !== l[0] || f[1] !== l[1]) ring.push([f[0], f[1]]);
    return ring;
  }

  // boundaryProj: MultiPolygon coords in projected metres.
  // baseProj (optional): [x, y] lattice anchor — the grid origin is shifted so
  // this point falls exactly on a cell corner.
  function buildGrid(boundaryProj, cellSize, baseProj) {
    let minX = Infinity,
      minY = Infinity,
      maxX = -Infinity,
      maxY = -Infinity;
    const segs = [];
    for (const poly of boundaryProj)
      for (const ring of poly) {
        for (let i = 0; i < ring.length; i++) {
          const [x, y] = ring[i];
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
          if (i > 0) segs.push([ring[i - 1][0], ring[i - 1][1], x, y]);
        }
      }
    if (baseProj) {
      // +1e-7 tolerance: a bounds value sitting a float-hair below a lattice line
      // must not pull in a sliver row/column
      minX =
        baseProj[0] +
        Math.floor((minX - baseProj[0]) / cellSize + 1e-7) * cellSize;
      minY =
        baseProj[1] +
        Math.floor((minY - baseProj[1]) / cellSize + 1e-7) * cellSize;
    }
    const nx = Math.ceil((maxX - minX) / cellSize);
    const ny = Math.ceil((maxY - minY) / cellSize);
    if (nx * ny > MAX_CELLS) {
      throw new Error(
        'This area would create ~' +
          (nx * ny).toLocaleString() +
          ' cells at ' +
          cellSize +
          'm (limit ' +
          MAX_CELLS.toLocaleString() +
          '). Use a smaller area or a larger cell size.',
      );
    }

    // 1. mark cells the boundary passes through
    const edge = new Set();
    const clamp = (v, hi) => Math.min(Math.max(v, 0), hi);
    for (const [x1, y1, x2, y2] of segs) {
      const ix0 = clamp(
        Math.floor((Math.min(x1, x2) - minX) / cellSize),
        nx - 1,
      );
      const ix1 = clamp(
        Math.floor((Math.max(x1, x2) - minX) / cellSize),
        nx - 1,
      );
      const iy0 = clamp(
        Math.floor((Math.min(y1, y2) - minY) / cellSize),
        ny - 1,
      );
      const iy1 = clamp(
        Math.floor((Math.max(y1, y2) - minY) / cellSize),
        ny - 1,
      );
      for (let ix = ix0; ix <= ix1; ix++)
        for (let iy = iy0; iy <= iy1; iy++) edge.add(ix + ',' + iy);
    }

    const cells = new Map(); // "ix,iy" -> {ix, iy, poly (multipoly, projected), full}

    // 2. scanline: cells whose centre is inside and that aren't edge cells are full squares
    for (let iy = 0; iy < ny; iy++) {
      const cy = minY + (iy + 0.5) * cellSize;
      const xs = [];
      for (const [x1, y1, x2, y2] of segs) {
        if (y1 > cy !== y2 > cy)
          xs.push(x1 + ((cy - y1) * (x2 - x1)) / (y2 - y1));
      }
      xs.sort((a, b) => a - b);
      for (let k = 0; k + 1 < xs.length; k += 2) {
        const a = xs[k],
          b = xs[k + 1];
        let ixMin = Math.floor((a - minX) / cellSize - 0.5) + 1;
        let ixMax = Math.ceil((b - minX) / cellSize - 0.5) - 1;
        ixMin = Math.max(ixMin, 0);
        ixMax = Math.min(ixMax, nx - 1);
        for (let ix = ixMin; ix <= ixMax; ix++) {
          const key = ix + ',' + iy;
          if (edge.has(key) || cells.has(key)) continue;
          const x0 = minX + ix * cellSize,
            y0 = minY + iy * cellSize;
          cells.set(key, {
            ix,
            iy,
            full: true,
            poly: [
              [
                [
                  [x0, y0],
                  [x0 + cellSize, y0],
                  [x0 + cellSize, y0 + cellSize],
                  [x0, y0 + cellSize],
                  [x0, y0],
                ],
              ],
            ],
          });
        }
      }
    }

    // 3. boundary cells: work areas are never clipped — any cell that touches
    //    the boundary keeps its FULL square and may overflow the boundary
    for (const key of edge) {
      const [ix, iy] = key.split(',').map(Number);
      const x0 = minX + ix * cellSize,
        y0 = minY + iy * cellSize;
      const cellRing = [
        [x0, y0],
        [x0 + cellSize, y0],
        [x0 + cellSize, y0 + cellSize],
        [x0, y0 + cellSize],
        [x0, y0],
      ];
      let touches = false;
      try {
        touches =
          polygonClipping.intersection([[cellRing]], boundaryProj).length > 0;
      } catch (e) {
        touches = false;
      }
      // centre test rescues degenerate float cases where the intersection of
      // exactly-coincident edges comes back empty
      if (
        touches ||
        pipMulti(x0 + cellSize / 2, y0 + cellSize / 2, boundaryProj)
      ) {
        cells.set(key, { ix, iy, full: true, poly: [[cellRing]] });
      }
    }

    return { cells, minX, minY, nx, ny };
  }

  function pipMulti(x, y, polys) {
    // even-odd point-in-multipolygon
    let inside = false;
    for (const poly of polys)
      for (const ring of poly) {
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          const xi = ring[i][0],
            yi = ring[i][1],
            xj = ring[j][0],
            yj = ring[j][1];
          if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi)
            inside = !inside;
        }
      }
    return inside;
  }

  function multiPolyCentroid(polys) {
    // area centroid, holes subtracted
    let A = 0,
      cx = 0,
      cy = 0;
    for (const poly of polys) {
      poly.forEach((ring, r) => {
        const c = ringCentroid(ring);
        const w = r === 0 ? c.area : -c.area;
        A += w;
        cx += c.cx * w;
        cy += c.cy * w;
      });
    }
    if (!A) {
      const c = ringCentroid(polys[0][0]);
      return [c.cx, c.cy];
    }
    return [cx / A, cy / A];
  }

  function largestExterior(polys) {
    let best = polys[0][0],
      bestA = -1;
    for (const poly of polys) {
      const a = ringCentroid(poly[0]).area;
      if (a > bestA) {
        bestA = a;
        best = poly[0];
      }
    }
    return best;
  }

  function boundsOfMultiPoly(polys) {
    let minLon = Infinity,
      minLat = Infinity,
      maxLon = -Infinity,
      maxLat = -Infinity;
    for (const poly of polys)
      for (const ring of poly)
        for (const [x, y] of ring) {
          if (x < minLon) minLon = x;
          if (x > maxLon) maxLon = x;
          if (y < minLat) minLat = y;
          if (y > maxLat) maxLat = y;
        }
    return [minLon, minLat, maxLon, maxLat];
  }

  /* ============================================================
     Slugs, camelCase names, singleton columns, CSV
     ============================================================ */
  function genSlug(prefix, used) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
    prefix = (prefix.toUpperCase().slice(0, 2) + 'XX').slice(0, 2);
    for (let i = 0; i < 200000; i++) {
      const slug =
        prefix +
        String(Math.floor(Math.random() * 1000)).padStart(3, '0') +
        chars[Math.floor(Math.random() * 26)] +
        chars[Math.floor(Math.random() * 26)];
      if (!used.has(slug)) {
        used.add(slug);
        return slug;
      }
    }
    throw new Error('Cannot generate unique slug.');
  }

  function toCamel(name) {
    const tokens = String(name || '')
      .trim()
      .split(/[^0-9A-Za-z]+/)
      .filter(Boolean);
    if (!tokens.length) return '';
    return (
      tokens[0].toLowerCase() +
      tokens
        .slice(1)
        .map((t) => t[0].toUpperCase() + t.slice(1))
        .join('')
    );
  }

  function autoCsvName(ward, lga, state, iso) {
    const parts = [ward, lga, state].map(toCamel).filter(Boolean);
    if (iso) parts.push(iso.toUpperCase());
    return (parts.length ? 'grid_' + parts.join('_') : 'grid') + '.csv';
  }

  function haversineM(lon1, lat1, lon2, lat2) {
    const R = 6371000,
      r = Math.PI / 180;
    const dphi = (lat2 - lat1) * r,
      dlam = (lon2 - lon1) * r;
    const a =
      Math.sin(dphi / 2) ** 2 +
      Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dlam / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  function addSingletonColumns(rs) {
    const parsed = rs.map((r) => {
      const p = String(r['Centroid']).split(/\s+/).map(Number);
      return p.length >= 2 && !p.slice(0, 2).some(isNaN)
        ? [p[0], p[1]]
        : [null, null];
    });
    const nonSingle = [];
    rs.forEach((r, i) => {
      if (r['Building Count'] !== 1 && parsed[i][0] !== null) {
        nonSingle.push([parsed[i][0], parsed[i][1], r['Area Slug']]);
      }
    });
    rs.forEach((r, i) => {
      if (r['Building Count'] !== 1) {
        r['keepSingleton'] = 'N/A';
        r['distanceSingleton'] = '';
        r['nameDistanceSingleton'] = '';
        return;
      }
      const [lon, lat] = parsed[i];
      let bestD = Infinity,
        bestSlug = '';
      if (lon !== null) {
        for (const [nlon, nlat, nslug] of nonSingle) {
          const d = haversineM(lon, lat, nlon, nlat);
          if (d < bestD) {
            bestD = d;
            bestSlug = nslug;
          }
        }
      }
      r['keepSingleton'] = bestD <= SINGLETON_THRESHOLD_M ? 'Yes' : 'No';
      r['distanceSingleton'] =
        bestD === Infinity ? '' : Math.round(bestD * 100) / 100;
      r['nameDistanceSingleton'] = bestSlug;
    });
  }

  function rowsToCsv(rs) {
    const fieldnames = [...CSV_HEADERS];
    const seen = new Set(fieldnames);
    for (const r of rs) {
      for (const k of Object.keys(r))
        if (!seen.has(k)) {
          seen.add(k);
          fieldnames.push(k);
        }
    }
    const esc = (v) => {
      v = String(v ?? '');
      return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
    };
    return (
      fieldnames.join(',') +
      '\n' +
      rs.map((r) => fieldnames.map((f) => esc(r[f])).join(',')).join('\n') +
      '\n'
    );
  }

  /* ============================================================
     Auto-grid pipeline (port of run())
     ============================================================ */
  async function runJob(job, params, usedSlugs, sharedIso) {
    const bbox = boundsOfMultiPoly(job.polys);
    const centerLon = (bbox[0] + bbox[2]) / 2,
      centerLat = (bbox[1] + bbox[3]) / 2;

    setProgress(0.03, 'Resolving admin fields');
    let { ward, lga, state } = job;
    let iso = params.iso || sharedIso.value;
    if (!(ward && lga && state) || !iso) {
      log(
        '[geocode] Looking up admin fields / country for the work-area centroid…',
      );
      const g = await reverseGeocode(centerLon, centerLat);
      ward = ward || g.ward;
      lga = lga || g.lga;
      state = state || g.state;
      iso = iso || g.iso2;
      log(
        '[geocode] Ward: ' +
          ward +
          ' | LGA: ' +
          lga +
          ' | State: ' +
          state +
          ' | ISO: ' +
          iso,
      );
    }
    if (!iso && !job.optionalBuildings) {
      throw new Error(
        'Could not determine the country — fill in the Country ISO field (alpha-2, e.g. NG).',
      );
    }
    if (iso) {
      if (iso.length === 3) iso = ISO3_TO_2[iso.toUpperCase()] || iso;
      iso = iso.toUpperCase().slice(0, 2);
      sharedIso.value = iso;
    }

    // boundary cells keep their full square and can overflow the boundary by up
    // to one cell — widen the building fetch box to cover that overflow
    const padLat = params.cellSize / 110574;
    const padLon =
      params.cellSize /
      (111320 * Math.max(Math.cos((centerLat * Math.PI) / 180), 0.01));
    const fetchBbox = [
      bbox[0] - padLon,
      bbox[1] - padLat,
      bbox[2] + padLon,
      bbox[3] + padLat,
    ];

    let buildings = [];
    if (iso) {
      setProgress(0.08, 'Locating building data');
      try {
        buildings = await fetchBuildings(fetchBbox, iso, (d, t) => {
          setProgress(
            0.1 + (0.68 * d) / t,
            'Reading building data (' + d + '/' + t + ' row groups)',
          );
          if (d === t || d % 4 === 0)
            log('[buildings] … ' + d + '/' + t + ' row groups');
        });
        coveredBoxes.push(fetchBbox);
        log(
          '[buildings] ' +
            buildings.length +
            ' building footprints in the area bbox',
          'ok',
        );
      } catch (e) {
        if (!job.optionalBuildings) throw e;
        log('[buildings] ' + (e && e.message ? e.message : e), 'warn');
        log(
          '[buildings] Continuing without building data — counts will be 0.',
          'warn',
        );
      }
    } else {
      log(
        '[buildings] Country unknown — skipping building download; counts will be 0.',
        'warn',
      );
    }
    if (iso && !buildings.length) {
      log(
        '[buildings] 0 results — the boundary may be outside Google Open Buildings coverage.',
        'warn',
      );
    }

    const utm = autoUtmDef(centerLon, centerLat);
    log(
      '[grid] CRS: ' + utm.name + ' | building ' + params.cellSize + 'm grid…',
    );
    const conv = proj4(WGS84, utm.def);

    const boundaryProj = job.polys.map((poly) =>
      poly.map((ring) => ring.map((pt) => conv.forward([pt[0], pt[1]]))),
    );
    boundaryProj.forEach((poly) => poly.forEach(closeRing));
    let baseProj = null;
    if (params.base) {
      baseProj = conv.forward([params.base[0], params.base[1]]);
      log(
        '[grid] Base point ' +
          params.base[1].toFixed(6) +
          ', ' +
          params.base[0].toFixed(6) +
          ' — grid corners aligned to it',
      );
    }
    setProgress(0.82, 'Building grid');
    const grid = buildGrid(boundaryProj, params.cellSize, baseProj);
    log('[grid] ' + grid.cells.size + ' cells');

    setProgress(0.9, 'Counting buildings per cell');
    // count building centroids per (clipped) cell
    for (const b of buildings) {
      const [bx, by] = conv.forward([b.lon, b.lat]);
      const ix = Math.floor((bx - grid.minX) / params.cellSize);
      const iy = Math.floor((by - grid.minY) / params.cellSize);
      const cell = grid.cells.get(ix + ',' + iy);
      if (!cell) continue;
      if (cell.full || pipMulti(bx, by, cell.poly))
        cell.count = (cell.count || 0) + 1;
    }

    // assemble rows (ix-major then iy, mirroring the Python cell order)
    const sorted = [...grid.cells.values()].sort(
      (a, b) => a.ix - b.ix || a.iy - b.iy,
    );
    const prefix = params.prefix || (ward.slice(0, 2) || 'WA').toUpperCase();
    const group = ward || 'Microplan';
    // without building data every cell would be "empty" — keep them all instead
    const dropEmpty = params.dropEmpty && buildings.length > 0;
    if (params.dropEmpty && !buildings.length) {
      log(
        '[grid] No building data — ignoring "drop empty cells" for this run.',
        'warn',
      );
    }
    const out = [];
    let dropped = 0;
    for (const cell of sorted) {
      const bc = cell.count || 0;
      if (dropEmpty && bc === 0) {
        dropped++;
        continue;
      }
      const [ccx, ccy] = multiPolyCentroid(cell.poly);
      const [clon, clat] = conv.inverse([ccx, ccy]);
      const ring = largestExterior(cell.poly).map((pt) =>
        conv.inverse([pt[0], pt[1]]),
      );
      out.push({
        'Area Slug': genSlug(prefix, usedSlugs),
        Ward: ward,
        Centroid: clon.toFixed(8) + ' ' + clat.toFixed(8),
        Boundary: ringToWkt(ring),
        'Building Count': bc,
        'Expected Visit Count': Math.round(params.ratio * bc),
        LGA: lga,
        State: state,
        'Target Population': 0,
        'Work Area Group Name': group,
      });
    }
    if (dropEmpty)
      log(
        '[grid] Dropped ' +
          dropped +
          ' empty cells (' +
          out.length +
          ' remaining)',
      );

    if (params.singleton) {
      addSingletonColumns(out);
      const yes = out.filter((r) => r.keepSingleton === 'Yes').length;
      const no = out.filter((r) => r.keepSingleton === 'No').length;
      log(
        '[singleton] flagged singletons → Yes:' +
          yes +
          ' No:' +
          no +
          ' (threshold ' +
          SINGLETON_THRESHOLD_M +
          'm)',
      );
    }

    const totalB = out.reduce((s, r) => s + r['Building Count'], 0);
    log(
      '[done] ' +
        (ward || 'area') +
        ': ' +
        out.length +
        ' cells, ' +
        totalB +
        ' buildings',
      'ok',
    );
    setProgress(1, (ward || 'area') + ' done');
    return { rows: out, buildings, ward, lga, state, iso };
  }

  async function generateGrid() {
    clearLog();
    if (
      typeof proj4 === 'undefined' ||
      typeof polygonClipping === 'undefined'
    ) {
      log(
        'Map libraries (proj4 / polygon-clipping) failed to load — check your internet connection and reload.',
        'err',
      );
      return;
    }
    if (!boundary.length) {
      log('Set a boundary in step 1 first.', 'err');
      return;
    }
    const base = parseLatLon($('mp-base').value);
    if ($('mp-base').value.trim() && !base) {
      log(
        'Base point must be "lat, long" (lat ±90, long ±180) — fix or clear the field.',
        'err',
      );
      return;
    }
    const params = {
      cellSize: Math.max(parseFloat($('mp-cell').value) || 50, 5),
      ratio: Math.max(parseFloat($('mp-ratio').value) || 1.0, 0),
      prefix: $('mp-prefix').value.trim(),
      iso: $('mp-iso').value.trim().toUpperCase(),
      dropEmpty: $('mp-drop-empty').checked,
      singleton: $('mp-singleton').checked,
      base,
    };
    const jobs = boundary;

    const btn = $('mp-run');
    btn.disabled = true;
    btn.textContent = 'Generating…';
    const t0 = Date.now();
    progBase = 0;
    progSpan = 1;
    setProgress(0.01, 'Starting');
    try {
      // seed with slugs already on the map so appended grids can't collide
      const usedSlugs = new Set(rows.map(slugOf));
      const sharedIso = { value: '' };
      const allRows = [],
        allBldgs = [];
      let last = null;
      for (let i = 0; i < jobs.length; i++) {
        progBase = i / jobs.length;
        progSpan = 1 / jobs.length;
        if (jobs.length > 1)
          log(
            '── [' +
              (i + 1) +
              '/' +
              jobs.length +
              '] ' +
              (jobs[i].ward || 'area') +
              ' ──',
          );
        last = await runJob(jobs[i], params, usedSlugs, sharedIso);
        allRows.push(...last.rows);
        allBldgs.push(...last.buildings);
      }
      progBase = 0;
      progSpan = 1;
      if (!allRows.length)
        throw new Error('No cells were produced — nothing to output.');
      await fillAdminFromLabsWards(allRows);

      const appending = rows.length > 0;
      const first = allRows[0]; // one job: its admin fields, gaps filled above
      $('mp-csv-name').value = appending
        ? 'microplan_combined.csv'
        : jobs.length > 1
          ? 'grids_multiwards_' + (sharedIso.value || 'XX') + '.csv'
          : autoCsvName(first.Ward, first.LGA, first.State, sharedIso.value);

      addBuildings(allBldgs);
      rows = rows.concat(allRows);
      renderWorkAreas();
      if (appending)
        log(
          '[map] Added ' +
            allRows.length +
            ' new cells to the ' +
            (rows.length - allRows.length) +
            ' already on the map.',
          'ok',
        );
      fitToData();
      markDirty();

      const totalB = allRows.reduce((s, r) => s + r['Building Count'], 0);
      log('══ SUMMARY ══');
      log(
        'Cells: ' +
          allRows.length +
          ' | Buildings: ' +
          totalB +
          ' | Max/cell: ' +
          Math.max(0, ...allRows.map((r) => r['Building Count'])),
      );
      log(
        'Loaded into the map. Total time ' +
          ((Date.now() - t0) / 1000).toFixed(0) +
          's. Remember to Save.',
        'ok',
      );
      setProgress(1, 'Complete');
      hideProgress(2500);
    } catch (e) {
      console.error(e);
      log('ERROR: ' + (e && e.message ? e.message : e), 'err');
      hideProgress(0);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Generate grid';
    }
  }
  $('mp-run').addEventListener('click', generateGrid);

  /* ============================================================
     Auto-group work areas into WAGs
     ============================================================ */
  const Grouping = window.SimpleMicroplanGrouping;
  let groupBy = 'count'; // 'count' | 'buildings'
  // Building-grouped WAGs and the range they were made for, so the legend can flag misses.
  let groupCheck = settings.groupCheck || null; // {low, high, names: [...]}
  const undoStack = []; // previous {slug: group} maps
  const tick = () => new Promise((r) => setTimeout(r, 0));

  const slugPrefix = (slug) =>
    ((String(slug).match(/^[A-Za-z]+/) || ['WA'])[0] + 'XX')
      .slice(0, 2)
      .toUpperCase();

  // <prefix>-G-001, -G-002 … per slug prefix. When only some work areas are
  // regrouped, numbering carries on after the groups that stay.
  function makeNamer(keepRows) {
    const next = new Map();
    for (const r of keepRows) {
      const m = String(groupOf(r)).match(/^([A-Z]{2})-G-(\d+)$/);
      if (m) next.set(m[1], Math.max(next.get(m[1]) || 0, Number(m[2])));
    }
    return (prefix) => {
      const n = (next.get(prefix) || 0) + 1;
      next.set(prefix, n);
      return prefix + '-G-' + String(n).padStart(3, '0');
    };
  }

  // By work-area count. Greedy spatial grouping: sweep from a corner, each seed
  // grabs its (size-1) nearest ungrouped neighbours — on a regular grid with
  // size 4 this yields clean 2×2 blocks.
  function groupByCount(list, groupSize, corner) {
    const items = list
      .map((r) => ({ r, c: parseCentroidLL(r['Centroid']) }))
      .filter((x) => x.c);
    if (!items.length) return [];
    const latMean = items.reduce((s, x) => s + x.c[1], 0) / items.length;
    const mx = 111320 * Math.cos((latMean * Math.PI) / 180),
      my = 110574;
    const byPrefix = new Map();
    for (const x of items) {
      const prefix = slugPrefix(slugOf(x.r));
      if (!byPrefix.has(prefix)) byPrefix.set(prefix, []);
      byPrefix.get(prefix).push({ r: x.r, x: x.c[0] * mx, y: x.c[1] * my });
    }
    const out = [];
    for (const members0 of byPrefix.values()) {
      const remaining = members0
        .slice()
        .sort(Grouping.CORNERS[corner] || Grouping.CORNERS['top-left']);
      while (remaining.length) {
        const seed = remaining.shift();
        const members = [seed];
        for (let k = 1; k < groupSize && remaining.length; k++) {
          let bestI = 0,
            bestD = Infinity;
          for (let i = 0; i < remaining.length; i++) {
            const d =
              (remaining[i].x - seed.x) ** 2 + (remaining[i].y - seed.y) ** 2;
            if (d < bestD) {
              bestD = d;
              bestI = i;
            }
          }
          members.push(remaining.splice(bestI, 1)[0]);
        }
        out.push(members.map((m) => slugOf(m.r)));
      }
    }
    return out;
  }

  // Work areas in metres for the grouping module: {id, ring, weight, x, y}.
  function projectItems(list) {
    const withRing = list
      .map((r) => ({ r, ring: parseWktRing(r['Boundary']) }))
      .filter((x) => x.ring);
    if (!withRing.length) return [];
    const c0 = withRing[0].ring[0];
    const conv = proj4(WGS84, autoUtmDef(c0[0], c0[1]).def);
    return withRing.map(({ r, ring }) => {
      const xy = ring.map((p) => conv.forward([p[0], p[1]]));
      const f = xy[0],
        l = xy[xy.length - 1];
      if (f[0] !== l[0] || f[1] !== l[1]) xy.push([f[0], f[1]]);
      const [x, y] = multiPolyCentroid([[xy]]);
      return {
        id: slugOf(r),
        ring: xy,
        weight: Number(r['Building Count']) || 0,
        x,
        y,
      };
    });
  }

  function loneRadius() {
    return Math.max(0, parseFloat($('mp-group-lone').value) || 0);
  }

  // Name each member list <prefix>-G-### and write it onto the rows.
  function nameGroups(list, groups, nameFor) {
    const bySlug = new Map(list.map((r) => [slugOf(r), r]));
    return groups.map((ids) => {
      const name = nameFor(slugPrefix(ids[0]));
      ids.forEach((id) => {
        bySlug.get(id)['Work Area Group Name'] = name;
      });
      return name;
    });
  }

  // By building count: contiguous WAGs whose building totals land in the range.
  async function groupByBuildingCount(
    list,
    target,
    tol,
    corner,
    nameFor,
    onProgress,
  ) {
    const items = projectItems(list);
    if (!items.length) return null;
    const adj = await Grouping.buildAdjacency(items, {
      tick,
      onProgress: (f) =>
        onProgress(0.05 + 0.45 * f, 'Finding neighbouring work areas'),
    });
    const res = await Grouping.groupByBuildings(items, adj, {
      target,
      tolerance: tol,
      corner,
      tick,
      onProgress: (f) => onProgress(0.5 + 0.4 * f, 'Building groups'),
    });
    onProgress(0.92, 'Placing lone work areas');
    const moves = Grouping.absorbSingletons(items, res.groups, {
      radius: loneRadius(),
      low: res.low,
      high: res.high,
    });
    // totals and flags after lone areas moved
    const weight = new Map(items.map((it) => [it.id, it.weight]));
    res.sums = res.groups.map((ids) =>
      ids.reduce((a, id) => a + weight.get(id), 0),
    );
    res.flags = res.sums.map((sum, i) =>
      sum === 0
        ? 'empty'
        : sum < res.low
          ? 'under'
          : sum > res.high
            ? 'over'
            : 'ok',
    );
    const names = nameGroups(list, res.groups, nameFor);
    // work areas with no boundary can't be placed by adjacency: own group each
    list.forEach((r) => {
      if (!parseWktRing(r['Boundary']))
        r['Work Area Group Name'] = nameFor(slugPrefix(slugOf(r)));
    });
    return Object.assign(res, { names, moves });
  }

  function setGroupBy(by) {
    groupBy = by;
    document
      .querySelectorAll('#mp-group-by button')
      .forEach((b) => b.classList.toggle('active', b.dataset.by === by));
    $('mp-gb-count').style.display = by === 'count' ? '' : 'none';
    $('mp-gb-buildings').style.display = by === 'buildings' ? '' : 'none';
    syncGroupRange();
  }
  function groupTarget() {
    return Math.max(1, Math.round(parseFloat($('mp-group-bldg').value) || 100));
  }
  function groupTol() {
    return (
      Math.min(Math.max(parseFloat($('mp-group-tol').value) || 0, 0), 100) / 100
    );
  }
  function syncGroupRange() {
    const t = groupTarget(),
      tol = groupTol();
    $('mp-group-range').innerHTML =
      'Each WAG: <b>' +
      Math.ceil(t * (1 - tol)) +
      '–' +
      Math.floor(t * (1 + tol)) +
      '</b> buildings';
  }
  document.querySelectorAll('#mp-group-by button').forEach((b) =>
    b.addEventListener('click', () => {
      setGroupBy(b.dataset.by);
      markDirty();
    }),
  );
  ['mp-group-bldg', 'mp-group-tol'].forEach((id) =>
    $(id).addEventListener('input', syncGroupRange),
  );

  function setGroupProgress(frac, label) {
    $('mp-gprogress').style.display = '';
    $('mp-gbar-fill').style.width = Math.round(frac * 100) + '%';
    $('mp-gbar-text').textContent =
      label + ' — ' + Math.round(frac * 100) + '%';
  }

  function showGroupResult(html, warn) {
    $('mp-group-result').innerHTML =
      '<div class="group-result' +
      (warn ? ' warn' : '') +
      '">' +
      html +
      '</div>';
  }

  function loneNote(moves) {
    if (!moves || !moves.length) return '';
    return (
      ' <b>' +
      moves.length +
      '</b> lone work area' +
      (moves.length === 1 ? '' : 's') +
      ' joined the nearest group within ' +
      loneRadius() +
      ' m.'
    );
  }

  let grouping = false;
  async function runAutoGroup() {
    if (grouping) return;
    const scope = $('mp-group-scope').value;
    const list =
      scope === 'selected' ? rows.filter((r) => selected.has(slugOf(r))) : rows;
    if (!list.length) {
      showGroupResult(
        scope === 'selected'
          ? 'Select some work areas first (or set Group to “All work areas”).'
          : 'Nothing to group — there are no work areas yet.',
        true,
      );
      return;
    }
    const corner = $('mp-group-start').value;
    const inList = new Set(list.map(slugOf));
    const nameFor = makeNamer(rows.filter((r) => !inList.has(slugOf(r))));
    const before = {
      groups: new Map(rows.map((r) => [slugOf(r), groupOf(r)])),
      groupCheck,
    };

    grouping = true;
    const btn = $('mp-autogroup');
    btn.disabled = true;
    $('mp-group-undo').disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Grouping…';
    $('mp-group-result').innerHTML = '';
    setGroupProgress(0.02, 'Starting');
    await tick();
    try {
      if (groupBy === 'count') {
        const size = Math.max(
          1,
          Math.round(parseFloat($('mp-group-size').value) || 4),
        );
        const groups = groupByCount(list, size, corner);
        const moves = Grouping.absorbSingletons(projectItems(list), groups, {
          radius: loneRadius(),
        });
        const n = nameGroups(list, groups, nameFor).length;
        if (groupCheck) {
          // regrouped names are no longer building-checked
          const still = new Set(
            rows.filter((r) => !inList.has(slugOf(r))).map(groupOf),
          );
          groupCheck.names = groupCheck.names.filter((g) => still.has(g));
        }
        showGroupResult(
          'Made <b>' +
            n +
            '</b> WAGs of up to ' +
            size +
            ' work areas each.' +
            loneNote(moves),
        );
      } else {
        const target = groupTarget(),
          tol = groupTol();
        const res = await groupByBuildingCount(
          list,
          target,
          tol,
          corner,
          nameFor,
          setGroupProgress,
        );
        const okN = res.flags.filter((f) => f === 'ok').length;
        const under = res.flags.filter((f) => f === 'under').length;
        // above the range: one oversized work area alone, or a lone area added to a full group
        const overSingle = res.flags.filter(
          (f, i) => f === 'over' && res.groups[i].length === 1,
        ).length;
        const overJoined = res.flags.filter(
          (f, i) => f === 'over' && res.groups[i].length > 1,
        ).length;
        const empty = res.flags.filter((f) => f === 'empty').length;
        const kept = groupCheck
          ? groupCheck.names.filter((g) => !res.names.includes(g))
          : [];
        groupCheck = {
          low: res.low,
          high: res.high,
          names: kept.concat(res.names),
        };
        const misses = [];
        if (under)
          misses.push(
            '<b>' +
              under +
              '</b> below the range: they could not grow without breaking the group (an isolated patch, or hemmed in by full groups)',
          );
        if (overSingle)
          misses.push(
            '<b>' +
              overSingle +
              '</b> above the range: a single work area already has more buildings than the limit',
          );
        if (overJoined)
          misses.push(
            '<b>' +
              overJoined +
              '</b> above the range: a lone work area joined them because no nearby group had room' +
              ' (set “Lone work areas join a group within” to 0 to keep lone areas apart)',
          );
        if (empty)
          misses.push(
            '<b>' +
              empty +
              '</b> with no buildings (isolated empty work areas)',
          );
        const CORNER_LABELS = {
          'top-left': 'top-left',
          'top-right': 'top-right',
          'bottom-left': 'bottom-left',
          'bottom-right': 'bottom-right',
        };
        const cornerNote =
          corner === 'best' && CORNER_LABELS[res.corner]
            ? ' Best layout started from the ' +
              CORNER_LABELS[res.corner] +
              ' corner.'
            : '';
        showGroupResult(
          'Made <b>' +
            res.groups.length +
            (res.moves.length ? '</b> WAGs; <b>' : '</b> connected WAGs; <b>') +
            okN +
            (okN === 1 ? '</b> has ' : '</b> have ') +
            Math.ceil(res.low) +
            '–' +
            Math.floor(res.high) +
            ' buildings.' +
            cornerNote +
            loneNote(res.moves) +
            (misses.length
              ? '<ul><li>' +
                misses.join('</li><li>') +
                '</li></ul>Marked ⚠ in the list below. Try another start corner or a different target.'
              : ''),
          misses.length > 0,
        );
      }
      setGroupProgress(0.97, 'Colouring the map');
      await tick();
      undoStack.push(before);
      if (undoStack.length > 20) undoStack.shift();
      const prevSel = [...selected];
      selected.clear();
      renderWorkAreas();
      if (scope === 'selected') setSelection(prevSel); // keep them selected to regroup again
      markDirty();
      setGroupProgress(1, 'Done');
      $('mp-group-another').style.display = '';
      btn.textContent = 'Regroup';
    } catch (e) {
      console.error(e);
      showGroupResult(
        'Grouping failed: ' + escapeHtml(e && e.message ? e.message : e),
        true,
      );
      btn.textContent = 'Auto-group';
    } finally {
      grouping = false;
      btn.disabled = false;
      $('mp-group-undo').disabled = !undoStack.length;
      setTimeout(() => {
        if (!grouping) $('mp-gprogress').style.display = 'none';
      }, 900);
    }
  }

  $('mp-autogroup').addEventListener('click', runAutoGroup);
  $('mp-group-another').addEventListener('click', () => {
    const sel = $('mp-group-start');
    sel.selectedIndex = (sel.selectedIndex + 1) % sel.options.length;
    runAutoGroup();
  });
  $('mp-group-undo').addEventListener('click', () => {
    const prev = undoStack.pop();
    if (!prev) return;
    rows.forEach((r) => {
      if (prev.groups.has(slugOf(r)))
        r['Work Area Group Name'] = prev.groups.get(slugOf(r));
    });
    groupCheck = prev.groupCheck;
    renderWorkAreas();
    markDirty();
    showGroupResult('Undone — the previous groups are back.');
    $('mp-group-undo').disabled = !undoStack.length;
  });

  $('mp-select-toggle').addEventListener('click', () => {
    selectMode = !selectMode;
    $('mp-select-toggle').classList.toggle('active', selectMode);
    $('mp-select-toggle').textContent = selectMode
      ? 'Done selecting'
      : 'Select work areas';
    if (selectMode) stopDraw();
  });

  /* ============================================================
     Export
     ============================================================ */
  $('mp-download').addEventListener('click', () => {
    if (!rows.length) {
      alert('There are no work areas to export yet.');
      return;
    }
    // what you download is what is saved
    if (dirty) save();
    let name = $('mp-csv-name').value.trim() || 'work_areas.csv';
    if (!/\.csv$/i.test(name)) name += '.csv';
    const blob = new Blob([rowsToCsv(rows)], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  });

  /* ============================================================
     Saving + renaming
     ============================================================ */
  const SETTING_FIELDS = {
    source: 'mp-source',
    bbox: 'mp-bbox',
    bgBase: 'mp-bg-base',
    rows: 'mp-rows',
    cols: 'mp-cols',
    prefix: 'mp-prefix',
    iso: 'mp-iso',
    cellSize: 'mp-cell',
    ratio: 'mp-ratio',
    base: 'mp-base',
    snap: 'mp-snap',
    fill: 'mp-fill',
    groupSize: 'mp-group-size',
    groupBldg: 'mp-group-bldg',
    groupTol: 'mp-group-tol',
    groupScope: 'mp-group-scope',
    groupStart: 'mp-group-start',
    groupLone: 'mp-group-lone',
  };
  const SETTING_CHECKS = {
    dropEmpty: 'mp-drop-empty',
    singleton: 'mp-singleton',
    showBldgs: 'mp-show-bldgs',
    autofill: 'mp-autofill',
  };

  function collectSettings() {
    const s = { waMode, admin, groupBy, groupCheck };
    Object.entries(SETTING_FIELDS).forEach(([k, id]) => {
      s[k] = $(id).value;
    });
    Object.entries(SETTING_CHECKS).forEach(([k, id]) => {
      s[k] = $(id).checked;
    });
    return s;
  }
  function restoreSettings(s) {
    Object.entries(SETTING_FIELDS).forEach(([k, id]) => {
      if (s[k] != null) $(id).value = s[k];
    });
    Object.entries(SETTING_CHECKS).forEach(([k, id]) => {
      if (s[k] != null) $(id).checked = !!s[k];
    });
    $('mp-source').dispatchEvent(new Event('change'));
    setWaMode(s.waMode || 'grid');
    setGroupBy(s.groupBy || 'count');
    syncAutofill();
    updateBaseMarkers();
  }

  function setStatus(text, cls) {
    const el = $('mp-save-status');
    el.textContent = text;
    el.className = 'save-status' + (cls ? ' ' + cls : '');
  }
  // Auto-save: on unless this person switched it off (remembered in this browser)
  const AUTOSAVE_KEY = 'simpleMicroplan.autosave';
  const AUTOSAVE_DELAY_MS = 2500;
  let autosaveTimer = null;
  let changeSeq = 0; // bumped on every change, so a save knows if more came in meanwhile
  function autosaveOn() {
    return EDITABLE && $('mp-autosave').checked;
  }
  function scheduleAutosave() {
    clearTimeout(autosaveTimer);
    if (autosaveOn()) autosaveTimer = setTimeout(save, AUTOSAVE_DELAY_MS);
  }

  function markDirty() {
    if (!EDITABLE) return;
    dirty = true;
    changeSeq++;
    setStatus(
      autosaveOn() ? 'Unsaved changes — auto-saving…' : 'Unsaved changes',
      'dirty',
    );
    scheduleAutosave();
  }

  async function postJson(body) {
    const r = await fetch(STATE.api_url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken(),
      },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || 'HTTP ' + r.status);
    return data;
  }

  let saving = false;
  let saveAgain = false;
  async function save() {
    if (!EDITABLE) return;
    clearTimeout(autosaveTimer);
    if (saving) {
      saveAgain = true; // one more save once this one lands
      return;
    }
    saving = true;
    const seq = changeSeq;
    $('mp-save').disabled = true;
    setStatus('Saving…');
    try {
      await postJson({
        data: {
          boundary,
          rows,
          settings: collectSettings(),
          csv_name: $('mp-csv-name').value.trim(),
        },
      });
      dirty = changeSeq !== seq; // edits made while saving still need saving
      if (dirty) saveAgain = true;
      setStatus(
        'Saved ' +
          new Date().toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
          }),
        'ok',
      );
    } catch (e) {
      setStatus('Save failed: ' + e.message, 'err');
    } finally {
      saving = false;
      $('mp-save').disabled = false;
      if (saveAgain) {
        saveAgain = false;
        save();
      }
    }
  }
  $('mp-save').addEventListener('click', save);
  try {
    $('mp-autosave').checked = localStorage.getItem(AUTOSAVE_KEY) !== 'off';
  } catch (e) {
    /* storage blocked: keep the default (on) */
  }
  $('mp-autosave').addEventListener('change', () => {
    try {
      localStorage.setItem(
        AUTOSAVE_KEY,
        $('mp-autosave').checked ? 'on' : 'off',
      );
    } catch (e) {
      /* not remembered, still applies to this visit */
    }
    if ($('mp-autosave').checked && dirty) save();
  });
  window.addEventListener('beforeunload', (e) => {
    if (dirty && autosaveOn()) {
      // last-chance save; browsers only let small requests outlive the page (~64 KB)
      const body = JSON.stringify({
        data: {
          boundary,
          rows,
          settings: collectSettings(),
          csv_name: $('mp-csv-name').value.trim(),
        },
      });
      if (body.length < 60000) {
        fetch(STATE.api_url, {
          method: 'POST',
          credentials: 'same-origin',
          keepalive: true,
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken(),
          },
          body,
        });
      }
    }
    if (dirty) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  function showName(name) {
    $('mp-name').textContent = name;
    document.title = name + ' · Simple Microplan';
  }
  function startRename() {
    if (!EDITABLE) return;
    const inp = $('mp-name-input');
    inp.value = $('mp-name').textContent;
    $('mp-name').style.display = 'none';
    $('mp-rename-btn').style.display = 'none';
    inp.style.display = '';
    inp.focus();
    inp.select();
  }
  function endRename() {
    $('mp-name-input').style.display = 'none';
    $('mp-name').style.display = '';
    $('mp-rename-btn').style.display = '';
  }
  async function commitRename() {
    const name = $('mp-name-input').value.trim();
    endRename();
    if (!name || name === $('mp-name').textContent) return;
    const old = $('mp-name').textContent;
    showName(name);
    try {
      const res = await postJson({ name });
      showName(res.name);
    } catch (e) {
      showName(old);
      alert('Could not rename: ' + e.message);
    }
  }
  $('mp-rename-btn').addEventListener('click', startRename);
  $('mp-name').addEventListener('click', startRename);
  $('mp-name-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitRename();
    }
    if (e.key === 'Escape') {
      e.stopPropagation();
      endRename();
    }
  });
  $('mp-name-input').addEventListener('blur', () => {
    if ($('mp-name-input').style.display !== 'none') commitRename();
  });

  /* ============================================================
     Start-up
     ============================================================ */
  showName(STATE.name);
  $('mp-meta').textContent =
    'Created by ' +
    (STATE.created_by || 'unknown') +
    ' · ' +
    fmtDate(STATE.created_at);
  $('mp-csv-name').value = saved.csv_name || '';
  restoreSettings(settings);
  renderBoundary();
  renderWorkAreas();
  fitToData();

  if (!EDITABLE) {
    document.body.classList.add('readonly');
    $('mp-readonly').style.display = '';
    ['mp-save-row', 'mp-rename-btn', 'step-boundary', 'step-workareas'].forEach(
      (id) => $(id).classList.add('edit-only'),
    );
    document
      .querySelectorAll(
        '#step-grouping .field, #step-grouping .divider, #step-grouping .sub-head, #mp-select-help',
      )
      .forEach((el) => el.classList.add('edit-only'));
    $('mp-name').removeAttribute('title');
    $('mp-name').style.cursor = 'default';
  } else {
    // any settings edit is an unsaved change
    [
      ...Object.values(SETTING_FIELDS),
      ...Object.values(SETTING_CHECKS),
      'mp-csv-name',
    ].forEach((id) => {
      $(id).addEventListener('change', markDirty);
    });
    setStatus(
      STATE.updated_at ? 'Saved ' + fmtDate(STATE.updated_at) : 'Not saved yet',
      'ok',
    );
  }
})();
