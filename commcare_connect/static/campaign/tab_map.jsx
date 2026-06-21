// tab_map.jsx — Reporting "View map": a geographic-coverage map (region choropleth
// by worker count + worker GPS points colored by KYC), reusing the labs Mapbox setup
// (mapbox-gl CDN + MAPBOX_TOKEN) and the service-delivery GPS circle-layer pattern.
const {
  useState: useStateMap,
  useEffect: useEffectMap,
  useRef: useRefMap,
} = React;

function CoverageMapModal({ open, onClose }) {
  const mapEl = useRefMap(null);
  const [info, setInfo] = useStateMap(null);
  const [error, setError] = useStateMap(null);

  useEffectMap(() => {
    if (!open) return undefined;
    if (!window.mapboxgl || !window.MAPBOX_TOKEN) {
      setError(
        'Map unavailable — no Mapbox token configured for this environment.',
      );
      return undefined;
    }
    setError(null);
    setInfo(null);
    let map;
    // Let the modal mount + size its container before Mapbox measures it.
    const timer = setTimeout(() => {
      mapboxgl.accessToken = window.MAPBOX_TOKEN;
      map = new mapboxgl.Map({
        container: mapEl.current,
        style: 'mapbox://styles/mapbox/light-v11',
        center: [8.7, 9.1],
        zoom: 5.1,
      });
      map.addControl(new mapboxgl.NavigationControl(), 'top-left');
      // The modal sizes its container via a CSS transition; Mapbox can measure a
      // 0-height container and paint blank (notably in a headless recorder). Force
      // a re-measure on load and as the modal settles so the map always paints.
      const bump = () => {
        try {
          map.resize();
        } catch (e) {
          /* map torn down */
        }
      };
      map.on('load', bump);
      map.on('idle', bump);
      [300, 800, 1500, 3000, 5000].forEach((d) => setTimeout(bump, d));
      map.on('load', () => {
        const code = new URLSearchParams(window.location.search).get(
          'campaign',
        );
        fetch(
          '/campaign/api/map/' +
            (code ? '?campaign=' + encodeURIComponent(code) : ''),
        )
          .then((r) => {
            if (!r.ok) throw new Error('map ' + r.status);
            return r.json();
          })
          .then((d) => {
            map.addSource('regions', { type: 'geojson', data: d.boundaries });
            map.addLayer({
              id: 'regions-fill',
              type: 'fill',
              source: 'regions',
              paint: {
                'fill-color': [
                  'interpolate',
                  ['linear'],
                  ['get', 'intensity'],
                  0,
                  '#E5E8FA',
                  1,
                  '#3F50A8',
                ],
                'fill-opacity': 0.55,
              },
            });
            map.addLayer({
              id: 'regions-line',
              type: 'line',
              source: 'regions',
              paint: { 'line-color': '#3F50A8', 'line-width': 0.8 },
            });
            map.addLayer({
              id: 'regions-label',
              type: 'symbol',
              source: 'regions',
              layout: { 'text-field': ['get', 'name'], 'text-size': 11 },
              paint: {
                'text-color': '#16006D',
                'text-halo-color': '#fff',
                'text-halo-width': 1,
              },
            });
            map.addSource('workers', { type: 'geojson', data: d.workers });
            map.addLayer({
              id: 'workers-pts',
              type: 'circle',
              source: 'workers',
              paint: {
                'circle-radius': 2.6,
                'circle-color': ['coalesce', ['get', 'color'], '#5D70D2'],
                'circle-opacity': 0.7,
                'circle-stroke-color': '#fff',
                'circle-stroke-width': 0.3,
              },
            });
            map.on('click', 'regions-fill', (e) => {
              const p = e.features[0].properties;
              new mapboxgl.Popup()
                .setLngLat(e.lngLat)
                .setHTML('<b>' + p.name + '</b><br>' + p.workers + ' workers')
                .addTo(map);
            });
            map.on('mouseenter', 'regions-fill', () => {
              map.getCanvas().style.cursor = 'pointer';
            });
            map.on('mouseleave', 'regions-fill', () => {
              map.getCanvas().style.cursor = '';
            });
            setInfo({
              regions: d.boundaries.features.length,
              total: d.total_workers,
              capped: d.points_capped,
            });
          })
          .catch(() => setError('Could not load map data.'));
      });
    }, 280);
    return () => {
      clearTimeout(timer);
      if (map) map.remove();
    };
  }, [open]);

  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(22,0,109,.35)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '90vw',
          height: '86vh',
          background: '#fff',
          borderRadius: 12,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 20px 60px rgba(0,0,0,.3)',
        }}
      >
        <div
          style={{
            padding: '14px 18px',
            borderBottom: '1px solid ' + CUTC.border,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ fontWeight: 600, color: CUTC.purple, fontSize: 15 }}>
            <i className="fa fa-map" style={{ marginRight: 8 }}></i>
            Geographic coverage
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {info && (
              <div style={{ fontSize: 12, color: CUTC.muted }}>
                {info.regions} states · {info.total.toLocaleString()} workers
                {info.capped ? ' · GPS sample' : ''}
              </div>
            )}
            <button
              onClick={onClose}
              style={{
                border: 'none',
                background: 'transparent',
                cursor: 'pointer',
                fontSize: 18,
                color: CUTC.muted,
              }}
            >
              <i className="fa fa-xmark"></i>
            </button>
          </div>
        </div>
        <div style={{ position: 'relative', flex: 1 }}>
          <div ref={mapEl} style={{ position: 'absolute', inset: 0 }}></div>
          {error && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: CUTC.muted,
                fontSize: 13,
                padding: 24,
                textAlign: 'center',
              }}
            >
              {error}
            </div>
          )}
          <div
            style={{
              position: 'absolute',
              bottom: 14,
              left: 14,
              background: '#fff',
              borderRadius: 8,
              padding: '8px 12px',
              fontSize: 11.5,
              boxShadow: '0 2px 10px rgba(0,0,0,.15)',
            }}
          >
            <div
              style={{ fontWeight: 600, color: CUTC.purple, marginBottom: 4 }}
            >
              State coverage
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                marginBottom: 8,
              }}
            >
              <span
                style={{
                  width: 46,
                  height: 10,
                  borderRadius: 2,
                  background: 'linear-gradient(90deg, #E5E8FA, #3F50A8)',
                }}
              ></span>
              <span style={{ color: CUTC.muted }}>fewer → more workers</span>
            </div>
            <div
              style={{ fontWeight: 600, color: CUTC.purple, marginBottom: 4 }}
            >
              Worker KYC
            </div>
            {[
              ['Approved', '#1E7B33'],
              ['Pending', '#C68A00'],
              ['Review', '#3843D0'],
              ['Rejected', '#E13019'],
            ].map(([l, c]) => (
              <div
                key={l}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  marginTop: 2,
                }}
              >
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: '50%',
                    background: c,
                  }}
                ></span>
                {l}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
