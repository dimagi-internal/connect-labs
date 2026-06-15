(function () {
  'use strict';
  // Shared microplan map layers — the canonical paint/source definitions for a
  // plan's territories (work areas), PSU/cluster hulls, sampled household pins, and
  // building footprints. Used by BOTH the plan editor (microplans/review.js) and
  // the monitoring workflow render, so the two draw the SAME plan identically
  // instead of re-inventing layer paint.
  //
  // Self-contained (no Microplans / ConnectMap dependency) so it loads on the
  // review page and the workflow runner (workflow/run.html) alike. Each drawer is
  // idempotent: it upserts the geojson source and adds its layers only if absent.
  // Consumers own interactivity (click / hover / feature-state) — this module is
  // purely "what the plan looks like". Property names + arm colours are overridable
  // so e.g. monitoring can reuse the same paint over differently-tagged data.

  const ARM_COLOR = { intervention: '#10b981', comparison: '#3b82f6' };
  const EMPTY = { type: 'FeatureCollection', features: [] };

  function _setSource(map, id, data, sourceOpts) {
    const src = map.getSource(id);
    if (src) {
      src.setData(data || EMPTY);
    } else {
      map.addSource(
        id,
        Object.assign(
          { type: 'geojson', data: data || EMPTY },
          sourceOpts || {},
        ),
      );
    }
    return id;
  }

  // Representative point for a (Multi)Polygon — the mean of the outer ring's
  // vertices. Good enough to anchor a low-zoom dot on a small footprint/cell.
  function _centroid(geom) {
    if (!geom) return null;
    if (geom.type === 'Point') return geom.coordinates;
    let ring = null;
    if (geom.type === 'Polygon') ring = geom.coordinates && geom.coordinates[0];
    else if (geom.type === 'MultiPolygon')
      ring = geom.coordinates && geom.coordinates[0] && geom.coordinates[0][0];
    if (!ring || !ring.length) return null;
    // Drop the closing vertex (== first) so it isn't double-counted.
    const closed =
      ring.length > 1 &&
      ring[0][0] === ring[ring.length - 1][0] &&
      ring[0][1] === ring[ring.length - 1][1];
    const n = closed ? ring.length - 1 : ring.length;
    let x = 0,
      y = 0;
    for (let i = 0; i < n; i++) {
      x += ring[i][0];
      y += ring[i][1];
    }
    return n ? [x / n, y / n] : null;
  }

  // A centroid Point FeatureCollection mirroring a polygon FC's per-feature id +
  // properties, so a circle layer can show one dot per work area.
  function _centroidPoints(data) {
    const feats = ((data && data.features) || [])
      .map((f) => {
        const c = _centroid(f.geometry);
        return c
          ? {
              type: 'Feature',
              id: f.id,
              properties: f.properties || {},
              geometry: { type: 'Point', coordinates: c },
            }
          : null;
      })
      .filter(Boolean);
    return { type: 'FeatureCollection', features: feats };
  }

  // arm → colour "match" paint expression (intervention is the default; the named
  // comparison value overrides).
  function _armColor(prop, colors) {
    colors = colors || ARM_COLOR;
    return [
      'match',
      ['get', prop],
      'comparison',
      colors.comparison,
      colors.intervention,
    ];
  }

  // Work-area territories. Fill = the per-feature `fill` property (EXCLUDED → grey);
  // opacity reacts to feature-state sel/dim that the CONSUMER drives. Outline = the
  // per-feature `outline` property (the other colour dimension). promoteId lets the
  // consumer address features by their `id` for feature-state.
  function workAreas(map, opts) {
    opts = opts || {};
    const src = opts.src || 'wa';
    const fillId = opts.fillId || 'wa-fill';
    const lineId = opts.lineId || 'wa-line';
    _setSource(map, src, opts.data, { promoteId: opts.promoteId || 'id' });
    if (!map.getLayer(fillId)) {
      map.addLayer({
        id: fillId,
        type: 'fill',
        source: src,
        paint: {
          'fill-color': [
            'case',
            ['==', ['get', 'status'], 'EXCLUDED'],
            '#9ca3af',
            ['get', 'fill'],
          ],
          'fill-opacity': [
            'case',
            ['==', ['get', 'status'], 'EXCLUDED'],
            0.18,
            ['boolean', ['feature-state', 'sel'], false],
            0.85,
            ['boolean', ['feature-state', 'dim'], false],
            0.18,
            0.55,
          ],
        },
      });
      map.addLayer({
        id: lineId,
        type: 'line',
        source: src,
        paint: {
          'line-color': [
            'case',
            ['==', ['get', 'status'], 'EXCLUDED'],
            '#9ca3af',
            ['get', 'outline'],
          ],
          'line-width': 0.8,
          'line-opacity': 0.55,
        },
      });
    }
    // Zoom-responsive dot: a building-footprint work area is sub-pixel at ward
    // zoom, so the fill/line read as "nothing there". Show a centroid dot at low
    // zoom (coloured like the work area) and fade it out as the real footprint
    // becomes legible (~z14→16), so the plan is visible at every zoom. The dot
    // upserts on every call so it tracks regenerate/recolor. Opt out: opts.dots
    // === false.
    if (opts.dots !== false) {
      const dotSrc = opts.dotSrc || src + '-dot';
      const dotId = opts.dotId || fillId + '-dot';
      _setSource(map, dotSrc, _centroidPoints(opts.data), {
        promoteId: opts.promoteId || 'id',
      });
      if (!map.getLayer(dotId)) {
        map.addLayer({
          id: dotId,
          type: 'circle',
          source: dotSrc,
          paint: {
            'circle-radius': [
              'interpolate',
              ['linear'],
              ['zoom'],
              10,
              2,
              14,
              3.5,
            ],
            'circle-color': [
              'case',
              ['==', ['get', 'status'], 'EXCLUDED'],
              '#9ca3af',
              ['get', 'fill'],
            ],
            'circle-stroke-color': '#ffffff',
            'circle-stroke-width': 0.6,
            // Fade out as the footprint polygon becomes legible.
            'circle-opacity': [
              'interpolate',
              ['linear'],
              ['zoom'],
              14,
              0.9,
              16,
              0,
            ],
            'circle-stroke-opacity': [
              'interpolate',
              ['linear'],
              ['zoom'],
              14,
              0.9,
              16,
              0,
            ],
          },
        });
      }
      return [fillId, lineId, dotId];
    }
    return [fillId, lineId];
  }

  // PSU / cluster convex hulls, two-arm aware (fill + line).
  function hulls(map, opts) {
    opts = opts || {};
    const src = opts.src || 'samp-hulls';
    const fillId = opts.fillId || 'samp-hull-fill';
    const lineId = opts.lineId || 'samp-hull-line';
    const paint = _armColor(opts.armProp || 'arm', opts.colors);
    _setSource(map, src, opts.data);
    if (!map.getLayer(fillId)) {
      map.addLayer({
        id: fillId,
        type: 'fill',
        source: src,
        paint: { 'fill-color': paint, 'fill-opacity': 0.12 },
      });
      map.addLayer({
        id: lineId,
        type: 'line',
        source: src,
        paint: { 'line-color': paint, 'line-width': 1.5 },
      });
    }
    return [fillId, lineId];
  }

  // Sampled household pins: radius / opacity / stroke by sample_type (primary vs
  // alternate), colour by arm.
  function pins(map, opts) {
    opts = opts || {};
    const src = opts.src || 'samp-pins';
    const id = opts.id || 'samp-pins-layer';
    const typeProp = opts.typeProp || 'sample_type';
    _setSource(map, src, opts.data);
    if (!map.getLayer(id)) {
      map.addLayer({
        id: id,
        type: 'circle',
        source: src,
        paint: {
          'circle-radius': ['case', ['==', ['get', typeProp], 'primary'], 5, 3],
          'circle-color': _armColor(opts.armProp || 'arm', opts.colors),
          'circle-opacity': [
            'case',
            ['==', ['get', typeProp], 'primary'],
            0.95,
            0.45,
          ],
          'circle-stroke-width': [
            'case',
            ['==', ['get', typeProp], 'primary'],
            1.2,
            0.5,
          ],
          'circle-stroke-color': '#ffffff',
        },
      });
    }
    return [id];
  }

  // Building footprints: polygon fill where the cache stored geometry, centroid-dot
  // fallback otherwise. `before` keeps them under the work-area lines.
  //
  // Two opt-in modes (default = the editor's plain amber fill, unchanged):
  //   • opts.armProp — colour by arm ('intervention'/'comparison') instead of amber.
  //   • opts.splitByType — encode the sample channel (opts.typeProp, default
  //     'sample_type'): PRIMARY footprints read as a SOLID fill, ALTERNATE
  //     (substituted) ones as a DASHED outline with no fill — the polygon analogue
  //     of the pins' solid-dot / hollow-ring split. Set together for the monitoring
  //     overlay so the designed buildings match the editor's arms + sampling read.
  function footprints(map, opts) {
    opts = opts || {};
    const src = opts.src || 'plan-fp';
    const fillId = opts.fillId || 'plan-fp-fill';
    const altLineId = opts.altLineId || 'plan-fp-alt-line';
    const dotsId = opts.dotsId || 'plan-fp-dots';
    const typeProp = opts.typeProp || 'sample_type';
    const fillColor = opts.armProp
      ? _armColor(opts.armProp, opts.colors)
      : '#f59e0b';
    const lineColor = opts.armProp
      ? _armColor(opts.armProp, opts.colors)
      : '#b45309';
    _setSource(map, src, opts.data);
    const before =
      opts.before || (map.getLayer('wa-line') ? 'wa-line' : undefined);
    const isPolygon = ['==', ['geometry-type'], 'Polygon'];
    const isPrimary = ['!=', ['get', typeProp], 'alternate'];
    if (!map.getLayer(fillId)) {
      map.addLayer(
        {
          id: fillId,
          type: 'fill',
          source: src,
          // When splitting by type, only PRIMARY polygons get the solid fill;
          // alternates render as the dashed outline below.
          filter: opts.splitByType ? ['all', isPolygon, isPrimary] : isPolygon,
          paint: {
            'fill-color': fillColor,
            // In split mode (monitoring) the buildings are the focus and small, so
            // they read more solid with a crisp DARK edge; the editor keeps its
            // lighter amber fill + amber edge.
            'fill-opacity': opts.splitByType ? 0.7 : 0.55,
            'fill-outline-color': opts.splitByType ? '#1f2937' : lineColor,
          },
        },
        before,
      );
      if (opts.splitByType) {
        map.addLayer(
          {
            id: altLineId,
            type: 'line',
            source: src,
            filter: ['all', isPolygon, ['==', ['get', typeProp], 'alternate']],
            paint: {
              'line-color': lineColor,
              'line-width': 1.4,
              'line-opacity': 0.95,
              'line-dasharray': [2, 1.4],
            },
          },
          before,
        );
      }
      map.addLayer(
        {
          id: dotsId,
          type: 'circle',
          source: src,
          filter: ['==', ['geometry-type'], 'Point'],
          paint: {
            'circle-radius': 1.6,
            'circle-color': fillColor,
            'circle-stroke-color': '#fff',
            'circle-stroke-width': 0.4,
          },
        },
        before,
      );
    }
    return opts.splitByType ? [fillId, altLineId, dotsId] : [fillId, dotsId];
  }

  function remove(map, ids) {
    (ids || []).forEach((id) => {
      if (map.getLayer(id)) map.removeLayer(id);
    });
  }

  window.PlanLayers = {
    ARM_COLOR,
    workAreas,
    hulls,
    pins,
    footprints,
    remove,
    setSource: _setSource,
  };
})();
