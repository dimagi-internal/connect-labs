/*
 * connect_map.js — shared, page-agnostic Mapbox GL map primitives.
 *
 * One small map layer ANY Connect page can consume — microplans pages and
 * workflow-template renders alike. It wraps Mapbox GL so callers don't
 * re-hand-roll map setup, source/layer plumbing, and bounds-fitting. It is
 * deliberately thin: the heavy, microplans-specific behaviours (viewport
 * fetching on pan, geocoding search, area selection) stay in the microplans
 * layer modules; this is the common substrate underneath them.
 *
 * Requires `window.mapboxgl` (load mapbox-gl before this) and, for the basemap
 * tiles, a token in `window.MAPBOX_TOKEN`. Boundary GeoJSON needs no token —
 * fetch it from the admin-boundary backend (/microplans/boundaries/viewport/).
 *
 * Usage:
 *   var map = ConnectMap.createMap(el, { center: [8.5, 9.66], zoom: 10 });
 *   map.on('load', function () {
 *     ConnectMap.boundary(map, 'wards', wardsGeoJSON, { activeWard: 'Kaura' });
 *     ConnectMap.points(map, 'sd', sdGeoJSON, { color: '#16a34a', radius: 2.4 });
 *     ConnectMap.pins(map, 'pins', pinsGeoJSON);
 *     ConnectMap.fit(map, wardsGeoJSON, 40);
 *   });
 */
(function (global) {
  'use strict';

  var DARK = 'mapbox://styles/mapbox/dark-v11';

  function ready() {
    if (!global.mapboxgl) return false;
    if (global.MAPBOX_TOKEN && !global.mapboxgl.accessToken) {
      global.mapboxgl.accessToken = global.MAPBOX_TOKEN;
    }
    return !!global.mapboxgl.accessToken;
  }

  function createMap(el, opts) {
    opts = opts || {};
    ready();
    return new global.mapboxgl.Map({
      container: el,
      style: opts.style || DARK,
      center: opts.center || [0, 0],
      zoom: opts.zoom != null ? opts.zoom : 9,
      attributionControl: true,
      interactive: opts.interactive !== false,
    });
  }

  // --- bounds ---
  function _eachCoord(geom, cb) {
    if (!geom) return;
    var c = geom.coordinates;
    if (geom.type === 'Point') cb(c);
    else if (geom.type === 'Polygon' || geom.type === 'MultiLineString')
      c.forEach(function (r) {
        r.forEach(cb);
      });
    else if (geom.type === 'MultiPolygon')
      c.forEach(function (p) {
        p.forEach(function (r) {
          r.forEach(cb);
        });
      });
    else if (geom.type === 'LineString' || geom.type === 'MultiPoint')
      c.forEach(cb);
  }

  function bounds(geojson) {
    var feats =
      geojson.type === 'FeatureCollection' ? geojson.features : [geojson];
    var b = new global.mapboxgl.LngLatBounds();
    feats.forEach(function (f) {
      _eachCoord(f.geometry || f, function (xy) {
        b.extend(xy);
      });
    });
    return b;
  }

  function fit(map, geojson, padding) {
    try {
      map.fitBounds(bounds(geojson), {
        padding: padding == null ? 32 : padding,
        duration: 0,
      });
    } catch (e) {}
  }

  // --- sources/layers ---
  function _setSource(map, id, geojson) {
    var s = map.getSource(id);
    if (s) s.setData(geojson);
    else map.addSource(id, { type: 'geojson', data: geojson });
  }

  function remove(map, ids) {
    (ids || []).forEach(function (id) {
      ['', '-fill', '-line', '-label'].forEach(function (sfx) {
        if (map.getLayer(id + sfx)) map.removeLayer(id + sfx);
      });
      if (map.getSource(id)) map.removeSource(id);
    });
  }

  // Admin-boundary polygons. `activeWard` (matched on properties.ward) is
  // highlighted; the rest read as muted context.
  function boundary(map, id, geojson, opts) {
    opts = opts || {};
    var active = opts.activeWard || null;
    var ACTIVE = opts.activeColor || '#34d399';
    var MUTED = opts.mutedColor || '#94a3b8';
    _setSource(map, id, geojson);
    map.addLayer({
      id: id + '-fill',
      type: 'fill',
      source: id,
      paint: {
        'fill-color': active
          ? ['case', ['==', ['get', 'ward'], active], '#16351f', '#1a2236']
          : '#1a2236',
        'fill-opacity': opts.fillOpacity == null ? 0.25 : opts.fillOpacity,
      },
    });
    map.addLayer({
      id: id + '-line',
      type: 'line',
      source: id,
      paint: {
        'line-color': active
          ? ['case', ['==', ['get', 'ward'], active], ACTIVE, MUTED]
          : MUTED,
        'line-width': opts.lineWidth == null ? 2.4 : opts.lineWidth,
        'line-opacity': 0.95,
      },
    });
    if (opts.label !== false) {
      map.addLayer({
        id: id + '-label',
        type: 'symbol',
        source: id,
        layout: {
          'text-field': ['get', 'ward'],
          'text-size': 12,
          'text-font': ['DIN Pro Medium', 'Arial Unicode MS Regular'],
        },
        paint: {
          'text-color': '#e2e8f0',
          'text-halo-color': '#0b1020',
          'text-halo-width': 1.4,
        },
      });
    }
  }

  // Dense point layer (e.g. service-delivery visits) — small + translucent so
  // it reads as ward-fill saturation, not confetti.
  function points(map, id, geojson, opts) {
    opts = opts || {};
    _setSource(map, id, geojson);
    map.addLayer({
      id: id,
      type: 'circle',
      source: id,
      paint: {
        'circle-radius': opts.radius == null ? 2.2 : opts.radius,
        'circle-color': opts.color || '#16a34a',
        'circle-opacity': opts.opacity == null ? 0.5 : opts.opacity,
      },
    });
  }

  // Survey pins coloured by a boolean property (default `confirmed`): purple
  // when confirmed, slate when not — distinct hues, foreground signal.
  function pins(map, id, geojson, opts) {
    opts = opts || {};
    var prop = opts.prop || 'confirmed';
    _setSource(map, id, geojson);
    map.addLayer({
      id: id,
      type: 'circle',
      source: id,
      paint: {
        'circle-radius': opts.radius == null ? 3.6 : opts.radius,
        'circle-color': [
          'case',
          ['get', prop],
          opts.confirmedColor || '#a78bfa',
          opts.absentColor || '#94a3b8',
        ],
        'circle-stroke-color': '#0b1020',
        'circle-stroke-width': 0.6,
        'circle-opacity': 0.95,
      },
    });
  }

  global.ConnectMap = {
    ready: ready,
    createMap: createMap,
    bounds: bounds,
    fit: fit,
    boundary: boundary,
    points: points,
    pins: pins,
    remove: remove,
    setSource: _setSource,
  };
})(window);
