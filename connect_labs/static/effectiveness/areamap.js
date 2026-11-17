/* A map of places painted by a page: outlines in, a colour per place in, hover and click out.
 *
 * It knows nothing about models or scenarios. A page gives it the outlines (`load`: countries, or states and provinces,
 * each carrying a `key`) and a fill and an optional emphasis per place each time its numbers change (`paint`); it
 * reports a hover as a tooltip the page wrote and a click as a place key. `ramp` is the pure part (breaks and colours for
 * a set of numbers), kept apart so a test can run it without a map. */
(function () {
  'use strict';
  var U = window.EffUI;
  var h = U.h;

  var AFRICA_BOUNDS = [
    [-18.5, -35.5],
    [52.0, 38.0],
  ];
  var SEQUENTIAL = ['#e6f4f1', '#a8d8cf', '#5fb8a9', '#2a8f82', '#0b5d57'];
  var NONE = '#e5e7eb';

  // Class boundaries so each colour holds about the same number of places: a few very large values must not leave
  // every other place the same pale shade. Returns the lower edge of every class after the first.
  function breaks(values, classes) {
    var xs = values
      .filter(function (v) {
        return v != null && isFinite(v);
      })
      .sort(function (a, b) {
        return a - b;
      });
    var out = [];
    for (var i = 1; i < classes; i++) {
      var v = xs[Math.floor((i * xs.length) / classes)];
      if (v != null && (!out.length || v > out[out.length - 1])) out.push(v);
    }
    return out;
  }

  function colorFor(v, edges, colors) {
    if (v == null || !isFinite(v)) return null;
    var i = 0;
    while (i < edges.length && v >= edges[i]) i++;
    return colors[Math.min(i, colors.length - 1)];
  }

  // The colours for these numbers, and the legend that explains them: [{color, from, to}] low to high. When every number
  // is the same there is nothing to rank: one class, in the middle colour, rather than a ramp of the same value.
  function ramp(values, colors) {
    colors = colors || SEQUENTIAL;
    var xs = values.filter(function (v) {
      return v != null && isFinite(v);
    });
    if (!xs.length)
      return {
        color: function () {
          return null;
        },
        classes: [],
      };
    var lo = Math.min.apply(null, xs),
      hi = Math.max.apply(null, xs);
    if (lo === hi) {
      var only = colors[Math.floor(colors.length / 2)];
      return {
        color: function (v) {
          return v == null || !isFinite(v) ? null : only;
        },
        classes: [{ color: only, from: lo, to: hi }],
      };
    }
    var edges = breaks(values, colors.length);
    var classes = [];
    for (var i = 0; i <= edges.length; i++)
      classes.push({
        color: colors[Math.min(i, colors.length - 1)],
        from: i === 0 ? lo : edges[i - 1],
        to: i === edges.length ? hi : edges[i],
      });
    return {
      color: function (v) {
        return colorFor(v, edges, colors);
      },
      classes: classes,
    };
  }

  function legend(el, r, fmt, extra) {
    var kids = r.classes.map(function (c) {
      return h('span', { class: 'lg' }, [
        h('i', { css: { background: c.color } }),
        fmt(c.from) + (c.to !== c.from ? ' – ' + fmt(c.to) : ''),
      ]);
    });
    kids.push(
      h('span', { class: 'lg' }, [
        h('i', { css: { background: NONE } }),
        'no figure',
      ]),
    );
    (extra || []).forEach(function (e) {
      kids.push(e);
    });
    U.put(el, kids);
  }

  function create(el, opts) {
    var token = window.MAPBOX_TOKEN;
    if (!window.mapboxgl || !token) {
      U.put(el, [
        h('p', {
          class: 'muted',
          css: { padding: '16px' },
          text: 'The map needs a map token, which this site has not been given. The table beside it has the same figures.',
        }),
      ]);
      return { available: false, load: noop, paint: noop, select: noop };
    }
    function noop() {}

    mapboxgl.accessToken = token;
    var map = new mapboxgl.Map({
      container: el,
      style: 'mapbox://styles/mapbox/light-v11',
      // Mapbox v3 draws a globe at low zoom: a thematic map of one continent wants the flat one.
      projection: 'mercator',
      bounds: AFRICA_BOUNDS,
      fitBoundsOptions: { padding: 12 },
      // On a phone one finger scrolls the page and two move the map.
      cooperativeGestures: window.matchMedia('(max-width: 700px)').matches,
    });
    map.addControl(
      new mapboxgl.NavigationControl({ showCompass: false }),
      'top-right',
    );

    var ready = false,
      shapes = null,
      drawn = null,
      painted = null,
      chosen = null;
    var popup = new mapboxgl.Popup({ closeButton: false, closeOnClick: false });

    function addLayers() {
      map.addSource('places', {
        type: 'geojson',
        data: shapes,
        promoteId: 'key',
      });
      map.addLayer({
        id: 'places-fill',
        type: 'fill',
        source: 'places',
        paint: {
          'fill-color': [
            'to-color',
            ['coalesce', ['feature-state', 'fill'], NONE],
          ],
          'fill-opacity': 0.9,
        },
      });
      map.addLayer({
        id: 'places-line',
        type: 'line',
        source: 'places',
        paint: {
          'line-color': [
            'case',
            ['boolean', ['feature-state', 'sel'], false],
            '#111827',
            ['boolean', ['feature-state', 'edge'], false],
            '#0b3d39',
            '#ffffff',
          ],
          'line-width': [
            'case',
            ['boolean', ['feature-state', 'sel'], false],
            3,
            ['boolean', ['feature-state', 'edge'], false],
            2,
            0.4,
          ],
        },
      });
      map.on('mousemove', 'places-fill', function (e) {
        var f = e.features && e.features[0];
        if (!f) return;
        map.getCanvas().style.cursor = 'pointer';
        var tip = opts.tooltip
          ? opts.tooltip(
              f.properties.key,
              f.properties.name,
              f.properties.country,
            )
          : null;
        if (tip == null) return popup.remove();
        popup
          .setLngLat(e.lngLat)
          .setDOMContent(
            typeof tip === 'string' ? document.createTextNode(tip) : tip,
          )
          .addTo(map);
      });
      map.on('mouseleave', 'places-fill', function () {
        map.getCanvas().style.cursor = '';
        popup.remove();
      });
      map.on('click', 'places-fill', function (e) {
        var f = e.features && e.features[0];
        if (f && opts.onSelect) opts.onSelect(f.properties.key);
      });
    }

    function apply() {
      if (!ready || !shapes) return;
      var src = map.getSource('places');
      if (!src) addLayers();
      else if (drawn !== shapes) src.setData(shapes);
      drawn = shapes;
      map.removeFeatureState({ source: 'places' });
      Object.keys(painted || {}).forEach(function (key) {
        map.setFeatureState({ source: 'places', id: key }, painted[key]);
      });
      if (chosen)
        map.setFeatureState(
          { source: 'places', id: chosen },
          Object.assign({}, painted && painted[chosen], { sel: true }),
        );
    }

    map.on('load', function () {
      ready = true;
      apply();
    });

    return {
      available: true,
      // The outlines to draw (countries, or states and provinces); a different set replaces the one on the map.
      load: function (geojson) {
        shapes = geojson;
        painted = null;
        chosen = null;
        apply();
      },
      // `{key: {fill: '#hex', edge: true}}`: a place with no entry is drawn as having no figure.
      paint: function (byKey) {
        painted = byKey;
        apply();
      },
      select: function (key) {
        chosen = key;
        apply();
      },
    };
  }

  window.EffMap = {
    create: create,
    ramp: ramp,
    legend: legend,
    NONE: NONE,
    SEQUENTIAL: SEQUENTIAL,
  };
})();
