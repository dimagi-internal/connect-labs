// Verified Monitoring (N1) — funder-facing verified-coverage dashboard.
// Self-contained: reads everything from instance.state (seeded payload); never
// fetches. Financial-dashboard styling. Map (Leaflet) added in a later iteration.
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V1
function WorkflowUI(props) {
  var instance = props.instance || {};
  var data = instance.state || {};
  var cov = data.coverage || {};
  var latest = cov.latest || null;
  var byArm = cov.by_arm || {};
  var gapSeries = cov.gap_series || [];
  var prog = data.program || {};
  var verif = data.verification || {};
  var sr = data.self_report || {};
  var sd = data.service_delivery_counts || {};
  var overlay = data.overlay || null;

  // --- Leaflet two-ward map (dynamic load; data fed via props, never fetched) ---
  var [leafletReady, setLeafletReady] = React.useState(false);
  var [sdOn, setSdOn] = React.useState(true);
  var [pinsOn, setPinsOn] = React.useState(true);
  var mapDivRef = React.useRef(null);
  var mapRef = React.useRef(null);
  React.useEffect(function () {
    if (window.L) {
      setLeafletReady(true);
      return;
    }
    var css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    document.head.appendChild(css);
    var s = document.createElement('script');
    s.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    s.onload = function () {
      setLeafletReady(true);
    };
    document.body.appendChild(s);
  }, []);
  React.useEffect(
    function () {
      if (!leafletReady || !overlay || !mapDivRef.current) return;
      var L = window.L;
      if (!mapRef.current) {
        mapRef.current = L.map(mapDivRef.current, {
          scrollWheelZoom: false,
          attributionControl: false,
        });
        L.tileLayer(
          'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
          {},
        ).addTo(mapRef.current);
      }
      var map = mapRef.current;
      map.eachLayer(function (l) {
        if (l._ov) map.removeLayer(l);
      });
      var wards = L.geoJSON(overlay.ward_boundaries, {
        style: function () {
          return { color: '#94a3b8', weight: 1.5, fill: false };
        },
      });
      wards._ov = true;
      wards.addTo(map);
      try {
        map.fitBounds(wards.getBounds(), { padding: [20, 20] });
      } catch (e) {}
      if (sdOn && overlay.service_delivery) {
        var sdl = L.geoJSON(overlay.service_delivery, {
          pointToLayer: function (f, ll) {
            return L.circleMarker(ll, {
              radius: 2,
              weight: 0,
              fillColor: '#16a34a',
              fillOpacity: 0.5,
            });
          },
        });
        sdl._ov = true;
        sdl.addTo(map);
      }
      if (pinsOn && overlay.survey_pins) {
        var pl = L.geoJSON(overlay.survey_pins, {
          pointToLayer: function (f, ll) {
            return L.circleMarker(ll, {
              radius: 4,
              weight: 0.5,
              color: '#0b1020',
              fillColor: (f.properties && f.properties.color) || '#a78bfa',
              fillOpacity: 0.95,
            });
          },
        });
        pl._ov = true;
        pl.addTo(map);
      }
    },
    [leafletReady, overlay, sdOn, pinsOn],
  );

  var INK = '#0b1020',
    PANEL = '#121a2e',
    LINE = '#1e2a44',
    MUT = '#8a96b3',
    PURPLE = '#a78bfa',
    PINK = '#f472b6',
    GREEN = '#34d399';
  var mono = 'ui-monospace, SFMono-Regular, Menlo, monospace';

  if (!latest) {
    return (
      <div
        style={{
          padding: '2rem',
          color: MUT,
          fontFamily: mono,
          background: INK,
        }}
      >
        Verified Monitoring — no data yet. Seed this run via the
        verified-monitoring recipe.
      </div>
    );
  }

  var tCov = latest.intervention_pct,
    cCov = latest.comparison_pct,
    gap = latest.gap_pp,
    ci = latest.gap_ci || [];
  var tWard = prog.treatment_ward || 'Treatment',
    cWard = prog.control_ward || 'Control';

  function pct(x) {
    return x == null ? '—' : x.toFixed(1) + '%';
  }
  function pp(x) {
    return x == null ? '—' : (x >= 0 ? '+' : '') + x.toFixed(1) + ' pp';
  }

  // --- inline sparkline for an arm's per-round series ---
  function spark(series, color) {
    var vals = (series || []).map(function (r) {
      return r.coverage_pct || 0;
    });
    if (vals.length < 2) return null;
    var w = 120,
      h = 28,
      max = Math.max.apply(null, vals),
      min = Math.min.apply(null, vals),
      rng = max - min || 1;
    var pts = vals
      .map(function (v, i) {
        var x = (i / (vals.length - 1)) * w;
        var y = h - ((v - min) / rng) * h;
        return x.toFixed(1) + ',' + y.toFixed(1);
      })
      .join(' ');
    return (
      <svg width={w} height={h} style={{ display: 'block' }}>
        <polyline points={pts} fill="none" stroke={color} strokeWidth="2" />
      </svg>
    );
  }

  function tile(label, ward, value, sub, color, series) {
    return (
      <div
        style={{
          background: PANEL,
          border: '1px solid ' + LINE,
          borderRadius: 10,
          padding: '14px 16px',
          minWidth: 180,
          flex: '1 1 180px',
        }}
      >
        <div
          style={{
            color: MUT,
            fontSize: 11,
            letterSpacing: '.05em',
            textTransform: 'uppercase',
          }}
        >
          {label}
        </div>
        <div style={{ color: '#cbd5e1', fontSize: 13, marginTop: 2 }}>
          {ward}
        </div>
        <div
          style={{
            color: color,
            fontFamily: mono,
            fontSize: 30,
            fontWeight: 700,
            marginTop: 6,
          }}
        >
          {value}
        </div>
        <div
          style={{ color: MUT, fontFamily: mono, fontSize: 12, marginTop: 2 }}
        >
          {sub}
        </div>
        {series ? (
          <div style={{ marginTop: 8 }}>{spark(series, color)}</div>
        ) : null}
      </div>
    );
  }

  // --- dual-line trend over rounds ---
  function trend() {
    var ti = byArm.intervention || [],
      tc = byArm.comparison || [];
    var n = Math.max(ti.length, tc.length);
    if (n < 2) return null;
    var w = 520,
      h = 180,
      pad = 28;
    function line(series, color) {
      var pts = series
        .map(function (r, i) {
          var x = pad + (i / (n - 1)) * (w - 2 * pad);
          var y = h - pad - ((r.coverage_pct || 0) / 100) * (h - 2 * pad);
          return x.toFixed(1) + ',' + y.toFixed(1);
        })
        .join(' ');
      return (
        <polyline points={pts} fill="none" stroke={color} strokeWidth="2.5" />
      );
    }
    var grid = [0, 25, 50, 75, 100].map(function (g) {
      var y = h - pad - (g / 100) * (h - 2 * pad);
      return (
        <g key={g}>
          <line
            x1={pad}
            y1={y}
            x2={w - pad}
            y2={y}
            stroke={LINE}
            strokeWidth="1"
          />
          <text x={4} y={y + 3} fill={MUT} fontSize="9" fontFamily={mono}>
            {g}
          </text>
        </g>
      );
    });
    return (
      <svg width={w} height={h} style={{ maxWidth: '100%' }}>
        {grid}
        {line(ti, PURPLE)}
        {line(tc, PINK)}
      </svg>
    );
  }

  var chip = function (label, val, ok) {
    return (
      <div
        key={label}
        style={{
          background: PANEL,
          border: '1px solid ' + (ok ? '#1f5132' : LINE),
          borderRadius: 8,
          padding: '8px 12px',
          fontFamily: mono,
        }}
      >
        <div
          style={{
            color: MUT,
            fontSize: 10,
            textTransform: 'uppercase',
            letterSpacing: '.04em',
          }}
        >
          {label}
        </div>
        <div
          style={{
            color: ok ? GREEN : '#cbd5e1',
            fontSize: 18,
            fontWeight: 700,
          }}
        >
          {val}
        </div>
      </div>
    );
  };

  return (
    <div
      style={{
        background: INK,
        color: '#e2e8f0',
        fontFamily: 'Inter, system-ui, sans-serif',
        padding: 20,
      }}
    >
      <div style={{ fontSize: 18, fontWeight: 700 }}>
        {prog.name || 'Verified Monitoring'}
      </div>
      <div
        style={{
          marginTop: 6,
          marginBottom: 14,
          color: '#fcd34d',
          background: '#3a2f0b',
          border: '1px solid #5b4a12',
          borderRadius: 8,
          padding: '6px 10px',
          fontSize: 12,
        }}
      >
        {data.caveat ||
          'Repeated cross-sectional snapshot — not a difference-in-differences estimate.'}
      </div>

      {/* Hero KPI tiles */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        {tile(
          'Verified vitamin-A coverage',
          tWard + ' (treatment)',
          pct(tCov),
          latest.intervention_n != null
            ? 'n=' + latest.intervention_n
            : 'independent survey',
          PURPLE,
          byArm.intervention,
        )}
        {tile(
          'Verified vitamin-A coverage',
          cWard + ' (control)',
          pct(cCov),
          'independent survey',
          PINK,
          byArm.comparison,
        )}
        {tile(
          'Cross-sectional gap',
          tWard + ' − ' + cWard,
          pp(gap),
          ci.length === 2
            ? '95% CI [' + ci[0] + ', ' + ci[1] + ']'
            : 'snapshot',
          GREEN,
          null,
        )}
      </div>

      {/* Trend */}
      <div
        style={{
          marginTop: 18,
          background: PANEL,
          border: '1px solid ' + LINE,
          borderRadius: 10,
          padding: 14,
        }}
      >
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
            marginBottom: 6,
          }}
        >
          Coverage across {byArm.intervention ? byArm.intervention.length : 0}{' '}
          bi-monthly rounds —<span style={{ color: PURPLE }}> {tWard}</span> vs{' '}
          <span style={{ color: PINK }}>{cWard}</span>
        </div>
        {trend()}
      </div>

      {/* Two-ward map overlay */}
      {overlay ? (
        <div
          style={{
            marginTop: 18,
            background: PANEL,
            border: '1px solid ' + LINE,
            borderRadius: 10,
            padding: 14,
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 8,
            }}
          >
            <div
              style={{
                color: MUT,
                fontSize: 11,
                textTransform: 'uppercase',
                letterSpacing: '.05em',
              }}
            >
              Two adjacent wards — program service delivery ({tWard}{' '}
              {sd[tWard] != null ? sd[tWard] : 0} · {cWard}{' '}
              {sd[cWard] != null ? sd[cWard] : 0}) with independent survey pins
              on top
            </div>
            <div
              style={{
                display: 'flex',
                gap: 14,
                fontSize: 11,
                fontFamily: mono,
              }}
            >
              <label style={{ color: GREEN, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={sdOn}
                  onChange={function (e) {
                    setSdOn(e.target.checked);
                  }}
                />{' '}
                service delivery
              </label>
              <label style={{ color: PURPLE, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={pinsOn}
                  onChange={function (e) {
                    setPinsOn(e.target.checked);
                  }}
                />{' '}
                survey pins
              </label>
            </div>
          </div>
          <div
            ref={mapDivRef}
            style={{
              height: 360,
              borderRadius: 8,
              overflow: 'hidden',
              background: '#0b1020',
            }}
          />
          {!leafletReady ? (
            <div style={{ color: MUT, fontSize: 12, padding: 8 }}>
              loading map…
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Verification strip */}
      <div
        style={{ marginTop: 18, display: 'flex', gap: 10, flexWrap: 'wrap' }}
      >
        {chip('GPS within 15m', pct(verif.gps_within_15m_pct), true)}
        {chip('Evidence complete', pct(verif.evidence_complete_pct), true)}
        {chip('Back-check pass', pct(verif.backcheck_pass_pct), true)}
        {chip(
          'Anomaly flags',
          (verif.flags_raised || 0) +
            ' / ' +
            (verif.flags_resolved || 0) +
            ' resolved',
          true,
        )}
      </div>

      {/* Trust, then verify */}
      <div
        style={{
          marginTop: 18,
          background: PANEL,
          border: '1px solid ' + LINE,
          borderRadius: 10,
          padding: 14,
        }}
      >
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
          }}
        >
          Trust, then verify ({tWard})
        </div>
        <div
          style={{
            display: 'flex',
            gap: 24,
            marginTop: 8,
            fontFamily: mono,
            alignItems: 'baseline',
          }}
        >
          <div>
            <div style={{ color: MUT, fontSize: 12 }}>
              Implementer self-report
            </div>
            <div style={{ fontSize: 24, color: '#fca5a5' }}>
              {pct(sr.intervention_pct)}
            </div>
          </div>
          <div>
            <div style={{ color: MUT, fontSize: 12 }}>Independent survey</div>
            <div style={{ fontSize: 24, color: PURPLE }}>
              {pct(sr.independent_pct)}
            </div>
          </div>
          <div>
            <div style={{ color: MUT, fontSize: 12 }}>Self-report premium</div>
            <div style={{ fontSize: 24, color: GREEN }}>
              +{(sr.premium_pp || 0).toFixed(1)} pp
            </div>
          </div>
        </div>
        <div
          style={{ color: MUT, fontSize: 12, marginTop: 10, fontFamily: mono }}
        >
          Program-logged visits — {tWard}: {sd[tWard] != null ? sd[tWard] : '—'}{' '}
          · {cWard}: {sd[cWard] != null ? sd[cWard] : '—'}
        </div>
      </div>
    </div>
  );
}
