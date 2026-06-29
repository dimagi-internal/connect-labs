// tab_map.jsx — Reporting "View map": a geographic-coverage map of Nigeria —
// states shaded by worker count (choropleth) + worker GPS points colored by KYC.
//
// Rendered as inline SVG (NOT WebGL/Mapbox). SVG paints synchronously into the DOM,
// so it captures reliably in ANY context — including a headless walkthrough recorder
// where a live WebGL canvas + active video screencast on a CPU (SwiftShader) renderer
// cannot be screenshotted. It also drops the Mapbox token / CDN dependency. The data
// (state polygons + worker points) comes from /api/map/, the same as before.
const { useState: useStateMap, useEffect: useEffectMap } = React;

// Linear lon/lat -> SVG projection over a bounding box (Nigeria is small enough
// that an equirectangular projection reads correctly; y is flipped so north is up).
function projector(bbox, w, h, pad) {
  const [minX, minY, maxX, maxY] = bbox;
  const sx = (w - 2 * pad) / (maxX - minX || 1);
  const sy = (h - 2 * pad) / (maxY - minY || 1);
  const s = Math.min(sx, sy); // uniform scale keeps the country's shape true
  const ox = pad + (w - 2 * pad - s * (maxX - minX)) / 2;
  const oy = pad + (h - 2 * pad - s * (maxY - minY)) / 2;
  return (lon, lat) => [ox + (lon - minX) * s, oy + (maxY - lat) * s];
}

// One GeoJSON Polygon/MultiPolygon -> an SVG path string.
function geomToPath(geom, project) {
  const polys =
    geom.type === 'MultiPolygon' ? geom.coordinates : [geom.coordinates];
  let d = '';
  polys.forEach((poly) => {
    poly.forEach((ring) => {
      ring.forEach((pt, i) => {
        const [x, y] = project(pt[0], pt[1]);
        d += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
      });
      d += 'Z';
    });
  });
  return d;
}

function shade(intensity) {
  // interpolate #E5E8FA (low) -> #3F50A8 (high)
  const a = [229, 232, 250];
  const b = [63, 80, 168];
  const t = Math.max(0, Math.min(1, intensity || 0));
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
}

function CoverageMapModal({ open, onClose }) {
  const [data, setData] = useStateMap(null);
  const [error, setError] = useStateMap(null);

  useEffectMap(() => {
    if (!open) return undefined;
    setData(null);
    setError(null);
    const code = new URLSearchParams(window.location.search).get('campaign');
    let alive = true;
    fetch(
      '/campaign/api/map/' +
        (code ? '?campaign=' + encodeURIComponent(code) : ''),
    )
      .then((r) => {
        if (!r.ok) throw new Error('map ' + r.status);
        return r.json();
      })
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e) => {
        console.error('COVERAGE-MAP-FETCH-FAIL', e && (e.message || e), e);
        if (alive) setError('Could not load map data.');
      });
    return () => {
      alive = false;
    };
  }, [open]);

  if (!open) return null;

  const W = 1000;
  const H = 760;
  let svg = null;
  if (data) {
    // bbox over all state polygons
    let minX = 180;
    let minY = 90;
    let maxX = -180;
    let maxY = -90;
    (data.boundaries.features || []).forEach((f) => {
      const polys =
        f.geometry.type === 'MultiPolygon'
          ? f.geometry.coordinates
          : [f.geometry.coordinates];
      polys.forEach((poly) =>
        poly.forEach((ring) =>
          ring.forEach((p) => {
            if (p[0] < minX) minX = p[0];
            if (p[0] > maxX) maxX = p[0];
            if (p[1] < minY) minY = p[1];
            if (p[1] > maxY) maxY = p[1];
          }),
        ),
      );
    });
    const project = projector([minX, minY, maxX, maxY], W, H, 24);
    svg = (
      <svg
        viewBox={'0 0 ' + W + ' ' + H}
        style={{ width: '100%', height: '100%', display: 'block' }}
        data-testid="coverage-map-svg"
      >
        <rect x="0" y="0" width={W} height={H} fill="#F2F4FB" />
        {(data.boundaries.features || []).map((f, i) => (
          <path
            key={'r' + i}
            d={geomToPath(f.geometry, project)}
            fill={shade(f.properties.intensity)}
            stroke="#3F50A8"
            strokeWidth="0.8"
            strokeOpacity="0.5"
          >
            <title>
              {f.properties.name + ' — ' + f.properties.workers + ' workers'}
            </title>
          </path>
        ))}
        {(data.workers.features || []).map((f, i) => {
          const [x, y] = project(
            f.geometry.coordinates[0],
            f.geometry.coordinates[1],
          );
          return (
            <circle
              key={'w' + i}
              cx={x.toFixed(1)}
              cy={y.toFixed(1)}
              r="2.4"
              fill={f.properties.color || '#5D70D2'}
              fillOpacity="0.78"
            />
          );
        })}
      </svg>
    );
  }

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
            {data && (
              <div style={{ fontSize: 12, color: CUTC.muted }}>
                {data.boundaries.features.length} states ·{' '}
                {data.total_workers.toLocaleString()} workers
                {data.points_capped ? ' · GPS sample' : ''}
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
        <div style={{ position: 'relative', flex: 1, background: '#F2F4FB' }}>
          {svg}
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
              }}
            >
              {error}
            </div>
          )}
          {!data && !error && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: CUTC.muted,
                fontSize: 13,
              }}
            >
              Loading coverage…
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
