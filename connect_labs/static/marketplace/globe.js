/* The marketplace globe.
 *
 * The same stack as the Pulse wall display and the partner network -- Mapbox GL
 * through the shared ConnectMap helper -- so this reads as part of the product
 * rather than a second map implementation that happens to plot dots.
 *
 * What it adds over the pulse network map is that it draws the CURRENT FILTER.
 * The points come from an endpoint carrying the same query string as the page,
 * so "on the bench, in Nigeria, applied to CHC" is a shape on the globe rather
 * than a number in a table.
 *
 * Precision survives, and that is the reason the payload carries it: an
 * organisation located to a town is a filled point, one known only to its
 * country is a hollow ring, because the middle of that country is exactly what
 * we do not know. A map that flattens the two draws a rooftop from the word
 * "Nigeria".
 */
(function () {
  'use strict';

  var DELIVERING = '#feaf31'; // brand-marigold
  var BENCH = '#a9b3e8';

  function render(container, points) {
    if (!window.ConnectMap || !window.mapboxgl || !window.MAPBOX_TOKEN) {
      // No token or no library: say so rather than leaving a dark rectangle
      // that reads as a broken map.
      container.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:center;height:100%;' +
        'color:#8ea1ff;font-size:13px;">The map needs a Mapbox token to draw.</div>';
      return;
    }

    var map = window.ConnectMap.createMap(container, {
      center: [22, 4],
      zoom: 1.6,
      projection: 'globe',
      interactive: true,
    });
    map.addControl(
      new window.mapboxgl.NavigationControl({ showCompass: false }),
      'top-right',
    );
    map.scrollZoom.disable(); // a wheel over the page should scroll the page

    // Mapbox reports style, source and expression failures through this event
    // rather than by throwing, so without it a broken layer is a blank map and
    // no explanation.
    map.on('error', function (e) {
      console.error(
        '[marketplace:globe] map error:',
        (e && e.error && e.error.message) || e,
      );
    });

    map.on('load', function () {
      window.ConnectMap.calmBasemap(map, { text: 0.45 });
      map.setFog({
        color: '#100a3d',
        'high-color': '#16006d',
        'horizon-blend': 0.06,
        'space-color': '#08042a',
        'star-intensity': 0.08,
      });

      map.addSource('orgs', { type: 'geojson', data: collection(points) });

      map.addLayer({
        id: 'orgs-glow',
        type: 'circle',
        source: 'orgs',
        filter: ['get', 'delivering'],
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 7, 6, 20],
          'circle-color': DELIVERING,
          'circle-opacity': 0.14,
          'circle-blur': 0.9,
        },
      });

      map.addLayer({
        id: 'orgs',
        type: 'circle',
        source: 'orgs',
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 3.4, 6, 8],
          // A country-only point is hollow: the fill is transparent and only
          // the ring is drawn, so it cannot be read as a located organisation.
          'circle-color': [
            'case',
            ['==', ['get', 'precision'], 'country'],
            'rgba(0,0,0,0)',
            ['case', ['get', 'delivering'], DELIVERING, BENCH],
          ],
          'circle-stroke-width': 1.4,
          'circle-stroke-color': [
            'case',
            ['get', 'delivering'],
            DELIVERING,
            BENCH,
          ],
          'circle-opacity': 0.95,
        },
      });

      var popup = new window.mapboxgl.Popup({
        closeButton: false,
        offset: 10,
        className: 'mk-globe-popup',
      });

      map.on('mouseenter', 'orgs', function (e) {
        map.getCanvas().style.cursor = 'pointer';
        var p = e.features[0].properties;
        popup
          .setLngLat(e.features[0].geometry.coordinates.slice())
          .setHTML(
            '<div style="font-family:Work Sans,system-ui,sans-serif;padding:2px 1px;">' +
              '<div style="font-weight:600;font-size:12.5px;color:#16006d;">' +
              escapeHtml(p.name) +
              '</div>' +
              '<div style="font-size:11.5px;color:#6b7280;margin-top:2px;">' +
              escapeHtml(p.place || placeFallback(p.precision)) +
              '</div>' +
              '</div>',
          )
          .addTo(map);
      });
      map.on('mouseleave', 'orgs', function () {
        map.getCanvas().style.cursor = '';
        popup.remove();
      });
    });
  }

  function placeFallback(precision) {
    return precision === 'country' ? 'country only' : 'location unknown';
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[c];
    });
  }

  function collection(points) {
    return {
      type: 'FeatureCollection',
      features: points.map(function (p) {
        return {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
          properties: {
            name: p.name,
            place: p.place,
            precision: p.precision,
            delivering: !!p.delivering,
            slug: p.slug,
          },
        };
      }),
    };
  }

  function start() {
    var container = document.getElementById('mk-globe');
    if (!container) return;
    var url = container.getAttribute('data-points-url');
    if (!url) return;

    fetch(url, { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('points ' + r.status);
        return r.json();
      })
      .then(function (data) {
        render(container, (data && data.points) || []);
      })
      .catch(function (err) {
        console.error('[marketplace:globe]', err);
        container.innerHTML =
          '<div style="display:flex;align-items:center;justify-content:center;height:100%;' +
          'color:#8ea1ff;font-size:13px;">Could not load the map.</div>';
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
