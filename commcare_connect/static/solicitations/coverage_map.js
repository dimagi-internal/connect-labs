/* Coverage map for solicitations.
 *
 * Renders a plan/group's ward boundary polygons (carried in the solicitation
 * snapshot as `plans[].boundaries[] = {name, arm, geometry}`) on a Mapbox map.
 *
 * Two modes:
 *   - read-only (create form + public detail): just shows the coverage areas.
 *   - interactive (respond form): clicking a boundary toggles its WHOLE parent
 *     plan (the locked "whole plan is the unit" rule), driven by feature-state
 *     and kept in sync with the checkbox list via onToggle/setSelected.
 *
 * Exposes window.SolCoverageMap = { init(opts), setSelected(map, ids) }.
 */
(function () {
  function armColor(arm) {
    arm = (arm || '').toLowerCase();
    if (arm === 'comparison' || arm === 'control') return '#2563eb'; // blue
    if (arm === 'intervention' || arm === 'treatment') return '#059669'; // green
    return '#6366f1'; // indigo (single-arm / unlabelled)
  }

  function buildFeatures(plans) {
    var features = [];
    var fid = 1;
    (plans || []).forEach(function (plan) {
      (plan.boundaries || []).forEach(function (b) {
        if (!b || !b.geometry) return;
        features.push({
          type: 'Feature',
          id: fid++,
          properties: {
            plan_id: plan.plan_id,
            plan_name: plan.name || '',
            ward: b.name || '',
            arm: b.arm || '',
            color: armColor(b.arm),
          },
          geometry: b.geometry,
        });
      });
    });
    return features;
  }

  function extendBounds(bounds, coords) {
    // Walk nested coordinate arrays down to [lng, lat] pairs.
    if (typeof coords[0] === 'number') {
      var lng = coords[0],
        lat = coords[1];
      if (bounds[0] === null) {
        bounds[0] = [lng, lat];
        bounds[1] = [lng, lat];
      } else {
        bounds[0][0] = Math.min(bounds[0][0], lng);
        bounds[0][1] = Math.min(bounds[0][1], lat);
        bounds[1][0] = Math.max(bounds[1][0], lng);
        bounds[1][1] = Math.max(bounds[1][1], lat);
      }
      return;
    }
    coords.forEach(function (c) {
      extendBounds(bounds, c);
    });
  }

  function boundsOf(features) {
    var b = [null, null];
    features.forEach(function (f) {
      if (f.geometry && f.geometry.coordinates)
        extendBounds(b, f.geometry.coordinates);
    });
    return b[0] ? b : null;
  }

  function init(opts) {
    opts = opts || {};
    var el =
      typeof opts.container === 'string'
        ? document.getElementById(opts.container)
        : opts.container;
    if (!el) return null;
    var features = buildFeatures(opts.plans);
    if (!opts.token || !window.mapboxgl || !features.length) {
      el.style.display = 'none'; // no token / no geometry → fall back to the text list
      return null;
    }

    mapboxgl.accessToken = opts.token;
    var map = new mapboxgl.Map({
      container: el,
      style: 'mapbox://styles/mapbox/light-v11',
      attributionControl: false,
    });
    map.addControl(
      new mapboxgl.NavigationControl({ showCompass: false }),
      'top-right',
    );
    map._covFeatures = features;

    map.on('load', function () {
      map.addSource('coverage', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: features },
      });
      map.addLayer({
        id: 'coverage-fill',
        type: 'fill',
        source: 'coverage',
        paint: {
          'fill-color': ['get', 'color'],
          // Selected boundaries read much hotter than unselected so a tick is obvious.
          'fill-opacity': [
            'case',
            ['boolean', ['feature-state', 'selected'], false],
            0.65,
            0.12,
          ],
        },
      });
      map.addLayer({
        id: 'coverage-line',
        type: 'line',
        source: 'coverage',
        paint: {
          'line-color': ['get', 'color'],
          'line-width': [
            'case',
            ['boolean', ['feature-state', 'selected'], false],
            4,
            1.2,
          ],
        },
      });
      map.addLayer({
        id: 'coverage-label',
        type: 'symbol',
        source: 'coverage',
        layout: {
          'text-field': ['get', 'ward'],
          'text-size': 12,
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': '#1f2937',
          'text-halo-color': '#ffffff',
          'text-halo-width': 1.4,
        },
      });

      var bounds = boundsOf(features);
      if (bounds)
        map.fitBounds(bounds, { padding: 36, duration: 0, maxZoom: 12 });

      if (opts.interactive && typeof opts.onToggle === 'function') {
        map.on('click', 'coverage-fill', function (e) {
          if (e.features && e.features[0])
            opts.onToggle(String(e.features[0].properties.plan_id));
        });
        map.on('mouseenter', 'coverage-fill', function () {
          map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'coverage-fill', function () {
          map.getCanvas().style.cursor = '';
        });
      }
      map._covReady = true;
      if (typeof opts.onReady === 'function') opts.onReady(map);
    });

    return map;
  }

  function setSelected(map, selectedPlanIds) {
    if (!map || !map._covReady || !map._covFeatures) return;
    var sel = {};
    (selectedPlanIds || []).forEach(function (id) {
      sel[String(id)] = true;
    });
    map._covFeatures.forEach(function (f) {
      map.setFeatureState(
        { source: 'coverage', id: f.id },
        { selected: !!sel[String(f.properties.plan_id)] },
      );
    });
  }

  window.SolCoverageMap = {
    init: init,
    setSelected: setSelected,
    armColor: armColor,
  };
})();
