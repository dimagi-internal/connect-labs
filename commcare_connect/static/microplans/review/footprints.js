(function () {
  'use strict';
  window.MPReview = window.MPReview || {};
  // Building-footprints overlay: fetch + paint the buildings inside the plan
  // area (saved plan: cached endpoint; new plan: preview-by-boundary) and toggle
  // layer visibility. Live map / plan state via ctx accessors so review.js stays
  // the single source of truth; static deps destructure by value.
  window.MPReview.footprints = function (ctx) {
    const {
      FOOTPRINTS_URL,
      PREVIEW_FOOTPRINTS_URL,
      CSRF,
      collectAreas,
      setStatus,
    } = ctx;

    async function loadFootprints() {
      const t0 = performance.now();
      try {
        let data;
        if (FOOTPRINTS_URL) {
          // Existing plan: HTTP-cached plan footprints (rev busts on regenerate).
          if (ctx.fpLayer) ctx.fpLayer.setMeta('Loading footprints…');
          setStatus('Fetching footprints…');
          const fpUrl =
            FOOTPRINTS_URL +
            (FOOTPRINTS_URL.includes('?') ? '&' : '?') +
            'rev=' +
            (ctx.planRevision == null ? '' : ctx.planRevision);
          data = await (await fetch(fpUrl)).json();
        } else if (PREVIEW_FOOTPRINTS_URL) {
          // New plan: buildings INSIDE the currently-selected boundary/area. Reloads
          // when the boundaries change (refreshAreaStats re-calls this while on).
          const areas = collectAreas();
          if (!areas.length) {
            const s0 = ctx.map.getSource('plan-fp');
            if (s0) s0.setData({ type: 'FeatureCollection', features: [] });
            if (ctx.fpLayer)
              ctx.fpLayer.setMeta('Select a boundary to see its buildings');
            setStatus('');
            return true; // stay on; reloads once an area is selected
          }
          if (ctx.fpLayer) ctx.fpLayer.setMeta('Loading footprints…');
          data = await Microplans.enqueueAndPoll(
            PREVIEW_FOOTPRINTS_URL,
            { areas },
            {
              csrf: CSRF,
              onProgress: (m) => {
                if (ctx.fpLayer) ctx.fpLayer.setMeta(m);
              },
            },
          );
        } else {
          return false;
        }
        if (!data || data.status !== 'ok')
          throw new Error((data && data.detail) || 'footprints failed');
        const src = ctx.map.getSource('plan-fp');
        if (src) {
          src.setData(data.footprints);
        } else {
          ctx.map.addSource('plan-fp', {
            type: 'geojson',
            data: data.footprints,
          });
          const before = ctx.map.getLayer('wa-line') ? 'wa-line' : undefined;
          // Polygon fill (for buildings whose geometry the cache has stored).
          ctx.map.addLayer(
            {
              id: 'plan-fp-fill',
              type: 'fill',
              source: 'plan-fp',
              filter: ['==', ['geometry-type'], 'Polygon'],
              paint: {
                'fill-color': '#f59e0b',
                'fill-opacity': 0.55,
                'fill-outline-color': '#b45309',
              },
            },
            before,
          );
          // Centroid dot fallback (for legacy cache rows with no polygon stored).
          ctx.map.addLayer(
            {
              id: 'plan-fp-dots',
              type: 'circle',
              source: 'plan-fp',
              filter: ['==', ['geometry-type'], 'Point'],
              paint: {
                'circle-radius': 1.6,
                'circle-color': '#f59e0b',
                'circle-stroke-color': '#fff',
                'circle-stroke-width': 0.4,
              },
            },
            before,
          );
        }
        ctx.footprintsLoaded = true;
        const dt = ((performance.now() - t0) / 1000).toFixed(1);
        if (ctx.fpLayer)
          ctx.fpLayer.setMeta(`${data.count.toLocaleString()} buildings`);
        setStatus(
          `${data.count.toLocaleString()} footprints loaded in ${dt}s.`,
        );
        return true;
      } catch (e) {
        if (ctx.fpLayer) ctx.fpLayer.setMeta('Failed — toggle to retry');
        setStatus('Footprints failed: ' + e.message);
        return false;
      }
    }
    function setFootprintsVisible(isOn) {
      if (!ctx.map || !ctx.mapReady) return;
      ['plan-fp-fill', 'plan-fp-dots'].forEach((id) => {
        if (ctx.map.getLayer(id))
          ctx.map.setLayoutProperty(
            id,
            'visibility',
            isOn ? 'visible' : 'none',
          );
      });
    }

    return { loadFootprints, setFootprintsVisible };
  };
})();
