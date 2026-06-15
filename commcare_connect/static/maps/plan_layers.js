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
            'fill-opacity': 0.55,
            'fill-outline-color': lineColor,
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
              'line-width': 1.1,
              'line-opacity': 0.9,
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
