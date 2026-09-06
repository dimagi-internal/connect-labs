/* The choropleth: paint, fit, hover, highlight. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};
  var T = window.Targeting;
  var util = T.util;
  var scale = T.scale;
  var state = T.state;

  var AFRICA_BOUNDS = [
    [-18.5, -35.5],
    [52.0, 38.0],
  ];

  var map = null;
  var geojson = null;
  var selected = new Set();

  function meta() {
    var S = state.get();
    return S.indicatorMeta[S.indicator] || {};
  }

  function legend() {
    var el = document.getElementById('tg-legend');
    if (!el) return;
    el.innerHTML = '';
    scale.legendItems(meta()).forEach(function (item) {
      var box = document.createElement('span');
      box.className = 'inline-flex items-center gap-1';
      var sw = document.createElement('span');
      sw.className = 'inline-block w-3 h-3 rounded-sm';
      sw.style.background = item.color;
      var lbl = document.createElement('span');
      lbl.textContent = item.last ? item.value + '+' : item.value;
      box.appendChild(sw);
      box.appendChild(lbl);
      el.appendChild(box);
    });
    var title = document.getElementById('tg-legend-title');
    var m = meta();
    if (title)
      title.textContent = (m.label || '') + (m.unit ? ' (' + m.unit + ')' : '');
  }

  function init() {
    if (!window.MAPBOX_TOKEN) {
      document.getElementById('tg-map').innerHTML =
        '<div class="flex items-center justify-center h-full text-stone-500 text-sm">' +
        'Map unavailable — MAPBOX_TOKEN is not configured. The table and download still work.</div>';
      return Promise.resolve();
    }
    mapboxgl.accessToken = window.MAPBOX_TOKEN;
    map = new mapboxgl.Map({
      container: 'tg-map',
      style: 'mapbox://styles/mapbox/light-v11',
      // Mapbox v3 defaults to a globe at low zoom, which renders Africa as a
      // patch on a sphere alongside South America. This is a thematic map of
      // one continent, so a flat projection is the honest frame.
      projection: 'mercator',
      bounds: AFRICA_BOUNDS,
      fitBoundsOptions: { padding: 20 },
    });
    map.addControl(new mapboxgl.NavigationControl(), 'top-right');
    return new Promise(function (resolve) {
      map.on('load', resolve);
    });
  }

  function clear() {
    geojson = null;
    if (map && map.getSource('areas')) {
      map.removeLayer('areas-dim');
      map.removeLayer('areas-line');
      map.removeLayer('areas-fill');
      map.removeSource('areas');
      selected = new Set();
    }
  }

  function paint(data) {
    geojson = data;
    legend();
    if (!map || !geojson) return;

    map.addSource('areas', { type: 'geojson', data: geojson, promoteId: 'pk' });
    map.addLayer({
      id: 'areas-fill',
      type: 'fill',
      source: 'areas',
      paint: {
        'fill-color': scale.colorExpression(state.get().indicator, meta()),
        'fill-opacity': 0.85,
      },
    });
    map.addLayer({
      id: 'areas-line',
      type: 'line',
      source: 'areas',
      paint: {
        'line-color': [
          'case',
          ['boolean', ['feature-state', 'above'], false],
          '#0b3d39',
          '#ffffff',
        ],
        'line-width': [
          'case',
          ['boolean', ['feature-state', 'above'], false],
          2,
          0.4,
        ],
      },
    });
    // Knock back unselected areas just enough to make the selection read, but
    // not so far that the ramp the legend advertises becomes invisible. At
    // 0.55 the whole continent washed out to near-white.
    map.addLayer({
      id: 'areas-dim',
      type: 'fill',
      source: 'areas',
      paint: {
        'fill-color': '#f5f5f4',
        'fill-opacity': [
          'case',
          ['boolean', ['feature-state', 'above'], false],
          0,
          0.3,
        ],
      },
    });
    wireTooltip();
    fitToData();
  }

  /* The frame follows the question. Leaving Liberia as a speck on a continental
     map while the table lists its fifteen counties is the map disagreeing with
     everything beside it; when the scope is the continent the continent bounds
     stand, because fitting to whichever countries happened to have data made
     the frame lurch on every method change. */
  function fitToData() {
    if (!map) return;
    if (!state.get().iso) {
      map.fitBounds(AFRICA_BOUNDS, { padding: 20, duration: 400 });
      return;
    }
    var b = null;
    (geojson.features || []).forEach(function (f) {
      eachPosition(f.geometry, function (lng, lat) {
        if (!b) {
          b = [lng, lat, lng, lat];
          return;
        }
        if (lng < b[0]) b[0] = lng;
        if (lat < b[1]) b[1] = lat;
        if (lng > b[2]) b[2] = lng;
        if (lat > b[3]) b[3] = lat;
      });
    });
    if (b) {
      map.fitBounds(
        [
          [b[0], b[1]],
          [b[2], b[3]],
        ],
        { padding: 30, duration: 400 },
      );
    }
  }

  function eachPosition(geom, fn) {
    if (!geom) return;
    var walk = function (c) {
      if (typeof c[0] === 'number') {
        fn(c[0], c[1]);
        return;
      }
      c.forEach(walk);
    };
    if (geom.type === 'GeometryCollection') {
      (geom.geometries || []).forEach(function (g) {
        eachPosition(g, fn);
      });
      return;
    }
    walk(geom.coordinates || []);
  }

  var popup = null;

  function wireTooltip() {
    popup =
      popup || new mapboxgl.Popup({ closeButton: false, closeOnClick: false });
    map.on('mousemove', 'areas-fill', function (e) {
      var S = state.get();
      var m = meta();
      var p = e.features[0].properties;
      map.getCanvas().style.cursor = 'pointer';
      var rate = p[S.indicator];
      var inherited = p.inherited === true || p.inherited === 'true';

      // The label and the unit come from the measure. They used to be the
      // literal strings "U5MR:" and "per 1,000" wrapped around whatever value
      // the current indicator held — so hovering a county while looking at ORS
      // coverage reported "U5MR: 52.3 per 1,000" for a figure that was a
      // percentage of treated children. The number was right and everything
      // around it was wrong.
      var rows =
        '<div>' +
        util.esc(m.label || S.indicator) +
        ': <b>' +
        (rate === null || rate === 'null' ? 'no data' : rate) +
        '</b>' +
        // "1 % of children" reads as a typo; "1% of children" does not.
        (m.unit
          ? (m.unit.charAt(0) === '%' ? '' : ' ') + util.esc(m.unit)
          : '') +
        '</div>';

      // Counts here are as measured. The tiles above may be carried to a
      // delivery year, and showing the two side by side without saying which
      // is which invites the reader to treat one as a correction of the other.
      var asOf = S.year
        ? ' <span style="color:#a8a29e">(as measured)</span>'
        : '';
      rows +=
        '<div>Births/yr: <b>' +
        util.fmtFull(p.births === 'null' ? null : p.births) +
        '</b>' +
        asOf +
        '</div>' +
        '<div>Under-5: <b>' +
        util.fmtFull(p.pop_u5 === 'null' ? null : p.pop_u5) +
        '</b>' +
        asOf +
        '</div>';

      popup
        .setLngLat(e.lngLat)
        .setHTML(
          '<div style="font:13px/1.4 system-ui;min-width:180px">' +
            '<div style="font-weight:600">' +
            util.esc(p.name) +
            '</div>' +
            '<div style="color:#57534e">' +
            util.esc(p.country) +
            '</div>' +
            '<hr style="margin:6px 0;border:0;border-top:1px solid #e7e5e4">' +
            rows +
            '<div style="color:#78716c;margin-top:4px;font-size:11px">' +
            util.esc(p.source || 'no source') +
            (inherited ? ' · figure applied from a coarser level' : '') +
            '</div>' +
            '</div>',
        )
        .addTo(map);
    });
    map.on('mouseleave', 'areas-fill', function () {
      map.getCanvas().style.cursor = '';
      popup.remove();
    });
  }

  function applySelection(pks) {
    if (!map || !map.getSource('areas')) return;
    selected.forEach(function (pk) {
      map.setFeatureState({ source: 'areas', id: pk }, { above: false });
    });
    selected = new Set(pks || []);
    selected.forEach(function (pk) {
      map.setFeatureState({ source: 'areas', id: pk }, { above: true });
    });
  }

  window.Targeting.map = {
    init: init,
    clear: clear,
    paint: paint,
    legend: legend,
    applySelection: applySelection,
    isReady: function () {
      return !!map;
    },
  };
})();
