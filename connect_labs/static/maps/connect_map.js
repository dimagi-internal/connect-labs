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
 *
 * When the browser will not start WebGL (after a GPU reset, too many live
 * contexts, a blocklisted driver), Mapbox throws "Failed to initialize WebGL"
 * from its constructor. createMap does not let that escape: it draws a small
 * "map unavailable" panel in `el` and returns an inert stand-in map whose every
 * method is a no-op and whose 'load' never fires, so the code above runs to the
 * end and the rest of the page draws. `ConnectMap.isAvailable(map)` tells the
 * two apart when a caller needs to.
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

  var UNAVAILABLE_TEXT =
    "Map unavailable \u2014 this browser couldn't start WebGL. " +
    'Reload the page to try again.';

  // Mapbox v3 throws `new Error('Failed to initialize WebGL.')` when the
  // browser refuses it a WebGL2 context. Anything else the constructor throws
  // (a bad container, a bad option) is a bug in the caller and still throws.
  function _isWebGLFailure(err) {
    return /webgl/i.test(String((err && err.message) || err));
  }

  // An inert stand-in for a mapboxgl.Map. Every property is a callable that
  // returns the stand-in again, so chained calls (`map.on(...).on(...)`),
  // getters (`map.getSource(id).setData(...)`, `map.getCanvas().style.cursor
  // = ...`) and the ConnectMap / PlanLayers drawers all no-op instead of
  // throwing. Listeners are dropped, so 'load' never fires.
  function _unavailableMap(el, err) {
    var stub;
    var target = function () {};
    var own = {
      __connectMapUnavailable: true,
      error: err || null,
      // Not a thenable: `await map` / Promise.resolve(map) must not hang.
      then: undefined,
      toJSON: undefined,
      loaded: function () {
        return false;
      },
      isStyleLoaded: function () {
        return false;
      },
      getContainer: function () {
        return el;
      },
      getStyle: function () {
        return { layers: [], sources: {} };
      },
    };
    own[Symbol.toPrimitive] = function (hint) {
      return hint === 'number' ? NaN : '[ConnectMap unavailable]';
    };
    own[Symbol.iterator] = function () {
      return [][Symbol.iterator]();
    };
    stub = new Proxy(target, {
      get: function (_t, prop) {
        if (Object.prototype.hasOwnProperty.call(own, prop)) return own[prop];
        return stub;
      },
      set: function () {
        return true;
      },
      has: function () {
        return true;
      },
      apply: function () {
        return stub;
      },
      construct: function () {
        return stub;
      },
    });
    return stub;
  }

  function isAvailable(map) {
    return !!map && !map.__connectMapUnavailable;
  }

  // The panel that stands where the map would have been. Inline styles so it
  // reads the same with or without Tailwind on the page.
  function _renderUnavailable(el) {
    if (!el || !el.ownerDocument) return;
    var doc = el.ownerDocument;
    var panel = doc.createElement('div');
    panel.className = 'connect-map-unavailable';
    panel.setAttribute('role', 'status');
    panel.style.cssText =
      'display:flex;align-items:center;justify-content:center;' +
      'box-sizing:border-box;width:100%;height:100%;min-height:120px;' +
      'padding:16px;background:#f8fafc;border:1px dashed #cbd5e1;' +
      'border-radius:6px;color:#475569;font-size:13px;line-height:1.4;' +
      'text-align:center;';
    panel.textContent = UNAVAILABLE_TEXT;
    el.appendChild(panel);
  }

  function createMap(el, opts) {
    opts = opts || {};
    ready();
    // Mapbox takes an element id as well as an element; resolve it here so a
    // failed start can still clean up and draw the panel in the right place.
    if (typeof el === 'string' && global.document) {
      el = global.document.getElementById(el) || el;
    }
    // What the container held before Mapbox touched it, so a failed start can
    // take back exactly what Mapbox added (its canvas container, its classes).
    var before =
      el && el.childNodes ? Array.prototype.slice.call(el.childNodes) : [];
    try {
      return new global.mapboxgl.Map({
        container: el,
        style: opts.style || DARK,
        center: opts.center || [0, 0],
        zoom: opts.zoom != null ? opts.zoom : 9,
        attributionControl: true,
        interactive: opts.interactive !== false,
        // 'globe' draws the world as a sphere with atmosphere. Opt-in, so every
        // existing caller keeps the flat mercator it was written against.
        projection: opts.projection || undefined,
      });
    } catch (err) {
      if (!_isWebGLFailure(err)) throw err;
      // One map that cannot start must not take the whole page down with it:
      // say so in its own spot and hand back a map that does nothing.
      if (global.console) {
        console.warn('[ConnectMap] map unavailable, WebGL did not start', err);
      }
      if (el && el.childNodes) {
        Array.prototype.slice.call(el.childNodes).forEach(function (node) {
          if (before.indexOf(node) === -1) el.removeChild(node);
        });
        if (el.classList) el.classList.remove('mapboxgl-map');
      }
      _renderUnavailable(el);
      return _unavailableMap(el, err);
    }
  }

  /* The dark basemap ships place labels at reading brightness, which makes the
   * geography compete with the data drawn on top of it. Dim what anchors the
   * eye (country and settlement names) and drop what is only noise at these
   * zooms (POIs, roads, transit).
   *
   * Guarded on source === 'composite' so only Mapbox's own layers are touched,
   * never ones the page adds afterwards. Call inside a 'load' handler.
   */
  function calmBasemap(map, opts) {
    if (!isAvailable(map)) return;
    opts = opts || {};
    var text = opts.text == null ? 0.4 : opts.text;
    // Icons carry as much visual weight as the labels beside them, so dimming
    // one without the other leaves the basemap half-calmed. The wall display
    // already did both; this is that behaviour, shared.
    var icon = opts.icon == null ? 0.3 : opts.icon;
    try {
      var layers = map.getStyle().layers || [];
      for (var i = 0; i < layers.length; i++) {
        var layer = layers[i];
        if (layer.type !== 'symbol' || layer.source !== 'composite') continue;
        if (/poi|road|transit|airport/.test(layer.id)) {
          map.setLayoutProperty(layer.id, 'visibility', 'none');
          continue;
        }
        map.setPaintProperty(layer.id, 'text-opacity', text);
        map.setPaintProperty(layer.id, 'icon-opacity', icon);
      }
    } catch (err) {
      // A style without these layers is not worth breaking the map over, but
      // it is worth saying: silently skipping leaves a basemap that competes
      // with the data and no clue why.
      if (global.console) console.warn('[pulse] basemap calming skipped', err);
    }
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
    if (!isAvailable(map)) return;
    try {
      map.fitBounds(bounds(geojson), {
        padding: padding == null ? 32 : padding,
        duration: 0,
      });
    } catch (e) {}
  }

  // --- sources/layers ---
  function _setSource(map, id, geojson) {
    if (!isAvailable(map)) return;
    var s = map.getSource(id);
    if (s) s.setData(geojson);
    else map.addSource(id, { type: 'geojson', data: geojson });
  }

  function remove(map, ids) {
    if (!isAvailable(map)) return;
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
    if (!isAvailable(map)) return;
    opts = opts || {};
    var active = opts.activeWard || null;
    var ACTIVE = opts.activeColor || '#34d399';
    var MUTED = opts.mutedColor || '#94a3b8';
    // Fill + label colours are themeable so the same module serves a dark or a
    // light basemap. Defaults are the original dark theme (backward-compatible);
    // a light-basemap caller passes activeFill/mutedFill/labelColor/labelHalo.
    var ACTIVE_FILL = opts.activeFill || '#16351f';
    var MUTED_FILL = opts.mutedFill || '#1a2236';
    var LABEL = opts.labelColor || '#e2e8f0';
    var LABEL_HALO = opts.labelHalo || '#0b1020';
    _setSource(map, id, geojson);
    map.addLayer({
      id: id + '-fill',
      type: 'fill',
      source: id,
      paint: {
        'fill-color': active
          ? ['case', ['==', ['get', 'ward'], active], ACTIVE_FILL, MUTED_FILL]
          : MUTED_FILL,
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
          'text-color': LABEL,
          'text-halo-color': LABEL_HALO,
          'text-halo-width': 1.4,
        },
      });
    }
  }

  // Dense point layer (e.g. service-delivery visits) — small + translucent so
  // it reads as ward-fill saturation, not confetti.
  function points(map, id, geojson, opts) {
    if (!isAvailable(map)) return;
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
    if (!isAvailable(map)) return;
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
        'circle-stroke-color': opts.strokeColor || '#0b1020',
        'circle-stroke-width':
          opts.strokeWidth == null ? 0.6 : opts.strokeWidth,
        'circle-opacity': opts.opacity == null ? 0.95 : opts.opacity,
      },
    });
  }

  global.ConnectMap = {
    calmBasemap: calmBasemap,
    ready: ready,
    createMap: createMap,
    isAvailable: isAvailable,
    UNAVAILABLE_TEXT: UNAVAILABLE_TEXT,
    bounds: bounds,
    fit: fit,
    boundary: boundary,
    points: points,
    pins: pins,
    remove: remove,
    setSource: _setSource,
  };
})(window);
