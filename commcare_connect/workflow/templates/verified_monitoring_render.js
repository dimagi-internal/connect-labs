// Verified Monitoring (N1) — funder-facing verified-coverage dashboard.
// Self-contained: reads everything from instance.state (seeded payload); never
// fetches. Financial-dashboard styling with a two-ward Leaflet overlay.
// Show-don't-tell: presents results neutrally; no causal claims, no caveat
// banner — the viewer draws the conclusion.
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V9
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
  // Land on the clean service-delivery view; the survey-pin layer is toggled
  // on to add the independent-survey story (avoids a "both at once" confetti
  // default).
  var [pinsOn, setPinsOn] = React.useState(false);
  var mapDivRef = React.useRef(null);
  var mapRef = React.useRef(null);
  React.useEffect(function () {
    if (!document.getElementById('vm-map-style')) {
      var st = document.createElement('style');
      st.id = 'vm-map-style';
      st.textContent =
        '.leaflet-tooltip.vm-ward-label{background:rgba(11,16,32,.82);border:1px solid #334155;color:#e2e8f0;font:600 11px ui-monospace,Menlo,monospace;box-shadow:none;padding:2px 7px}' +
        '.leaflet-tooltip.vm-ward-label:before{display:none}' +
        '.leaflet-container{background:#0b1020}';
      document.head.appendChild(st);
    }
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
        // Self-contained: no external tile layer (clean dark canvas, no CSP/attribution dependency).
        mapRef.current = L.map(mapDivRef.current, {
          scrollWheelZoom: false,
          attributionControl: false,
          zoomControl: true,
        });
      }
      var map = mapRef.current;
      map.eachLayer(function (l) {
        if (l._ov) map.removeLayer(l);
      });
      var tw = prog.treatment_ward;
      var wards = L.geoJSON(overlay.ward_boundaries, {
        style: function (f) {
          var t = f.properties && f.properties.ward === tw;
          return {
            color: t ? '#34d399' : '#64748b',
            weight: 1.5,
            fill: true,
            fillColor: t ? '#16351f' : '#1a2236',
            fillOpacity: 0.55,
          };
        },
        onEachFeature: function (f, layer) {
          var w = f.properties && f.properties.ward;
          var t = w === tw;
          var cnt = sd[w] != null ? sd[w] : 0;
          layer.bindTooltip(
            w +
              (t ? ' · treatment' : ' · control') +
              ' — ' +
              cnt.toLocaleString() +
              ' program visits',
            { permanent: true, direction: 'top', className: 'vm-ward-label' },
          );
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
              radius: 3,
              weight: 0,
              fillColor: '#16a34a',
              fillOpacity: 0.7,
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
              radius: 3.5,
              weight: 0.6,
              color: '#0b1020',
              fillColor: (f.properties && f.properties.color) || '#a78bfa',
              fillOpacity: 0.92,
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
    gap = latest.gap_pp;
  function _lastN(arr) {
    return arr && arr.length ? arr[arr.length - 1].n : null;
  }
  var cN = _lastN(byArm.comparison);
  var tWard = prog.treatment_ward || 'Treatment',
    cWard = prog.control_ward || 'Control';
  function _delta(arr) {
    return arr && arr.length >= 2
      ? arr[arr.length - 1].coverage_pct - arr[arr.length - 2].coverage_pct
      : null;
  }
  var tDelta = _delta(byArm.intervention),
    cDelta = _delta(byArm.comparison);

  function pct(x) {
    return x == null ? '—' : x.toFixed(1) + '%';
  }
  function pp(x) {
    return x == null ? '—' : (x >= 0 ? '+' : '') + x.toFixed(1) + ' pts';
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

  function deltaChip(d) {
    if (d == null) return null;
    var up = d >= 0;
    return (
      <span
        style={{
          display: 'inline-block',
          marginTop: 6,
          padding: '1px 7px',
          borderRadius: 6,
          fontFamily: mono,
          fontSize: 11,
          color: up ? '#34d399' : '#fca5a5',
          background: up ? '#0f2a1c' : '#2a1212',
        }}
      >
        {(up ? '▲ +' : '▼ ') + Math.abs(d).toFixed(1) + ' pts vs last round'}
      </span>
    );
  }

  function tile(label, ward, value, sub, color, series, delta) {
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
        {delta != null ? <div>{deltaChip(delta)}</div> : null}
        {series ? (
          <div style={{ marginTop: 8 }}>{spark(series, color)}</div>
        ) : null}
      </div>
    );
  }

  // --- dual-line trend: gap band + round labels + emphasized latest point ---
  function trend() {
    var ti = byArm.intervention || [],
      tc = byArm.comparison || [];
    var n = Math.max(ti.length, tc.length);
    if (n < 2) return null;
    var w = 560,
      h = 200,
      pad = 30,
      padB = 26;
    function X(i) {
      return pad + (i / (n - 1)) * (w - 2 * pad);
    }
    function Y(v) {
      return h - padB - ((v || 0) / 100) * (h - pad - padB);
    }
    function poly(series) {
      return series
        .map(function (r, i) {
          return X(i) + ',' + Y(r.coverage_pct);
        })
        .join(' ');
    }
    var band =
      ti
        .map(function (r, i) {
          return X(i) + ',' + Y(r.coverage_pct);
        })
        .join(' ') +
      ' ' +
      tc
        .slice()
        .reverse()
        .map(function (r, i) {
          return X(n - 1 - i) + ',' + Y(r.coverage_pct);
        })
        .join(' ');
    var grid = [0, 25, 50, 75, 100].map(function (g) {
      var y = Y(g);
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
            {g + '%'}
          </text>
        </g>
      );
    });
    var xlabels = ti.map(function (r, i) {
      return (
        <text
          key={i}
          x={X(i)}
          y={h - 8}
          fill={MUT}
          fontSize="9"
          fontFamily={mono}
          textAnchor="middle"
        >
          {'R' + (r.round || i + 1)}
        </text>
      );
    });
    var last = n - 1;
    return (
      <svg width={w} height={h} style={{ maxWidth: '100%' }}>
        {grid}
        <polygon points={band} fill={PURPLE} fillOpacity="0.08" stroke="none" />
        <polyline
          points={poly(ti)}
          fill="none"
          stroke={PURPLE}
          strokeWidth="2.5"
        />
        <polyline
          points={poly(tc)}
          fill="none"
          stroke={PINK}
          strokeWidth="2.5"
        />
        {xlabels}
        {ti[last] ? (
          <circle
            cx={X(last)}
            cy={Y(ti[last].coverage_pct)}
            r="4.5"
            fill={PURPLE}
            stroke={INK}
            strokeWidth="1.5"
          />
        ) : null}
        {tc[last] ? (
          <circle
            cx={X(last)}
            cy={Y(tc[last].coverage_pct)}
            r="4.5"
            fill={PINK}
            stroke={INK}
            strokeWidth="1.5"
          />
        ) : null}
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
          color: MUT,
          fontSize: 13,
          marginTop: 4,
          marginBottom: 16,
          lineHeight: 1.5,
          fontFamily: mono,
        }}
      >
        Independent rooftop survey · {prog.cadence || 'bi-monthly'} · latest
        round R{latest.round || (byArm.intervention || []).length} ·{' '}
        <b style={{ color: PURPLE }}>{tWard}</b> (treatment) vs{' '}
        <b style={{ color: PINK }}>{cWard}</b> (control)
      </div>

      {/* Hero KPI tiles — the two ward coverages. The difference is shown
          small + neutral below (not a hero tile) so it reads as a measured
          number, not a causal-impact claim. */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        {tile(
          'Verified vitamin-A coverage',
          tWard + ' (treatment)',
          pct(tCov),
          latest.intervention_n != null
            ? latest.intervention_n + ' children surveyed'
            : 'independent survey',
          PURPLE,
          byArm.intervention,
          tDelta,
        )}
        {tile(
          'Verified vitamin-A coverage',
          cWard + ' (control)',
          pct(cCov),
          cN != null ? cN + ' children surveyed' : 'independent survey',
          PINK,
          byArm.comparison,
          cDelta,
        )}
      </div>
      <div
        style={{
          marginTop: 10,
          color: MUT,
          fontFamily: mono,
          fontSize: 13,
        }}
      >
        Measured difference, latest round:{' '}
        <span style={{ color: '#cbd5e1', fontWeight: 700 }}>{pp(gap)}</span>
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
        <div style={{ color: MUT, fontSize: 11, marginBottom: 8 }}>
          y-axis: % of surveyed children with confirmed vitamin-A
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
              {sd[tWard] != null ? sd[tWard].toLocaleString() : 0} · {cWard}{' '}
              {sd[cWard] != null ? sd[cWard].toLocaleString() : 0}) with
              independent survey pins on top
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
          <div
            style={{
              display: 'flex',
              gap: 18,
              marginTop: 8,
              fontSize: 11,
              color: MUT,
              fontFamily: mono,
            }}
          >
            <span>
              <span style={{ color: GREEN }}>●</span> service-delivery visit
            </span>
            <span>
              <span style={{ color: PURPLE }}>●</span> survey: vitamin-A
              confirmed
            </span>
            <span>
              <span style={{ color: PINK }}>●</span> survey: absent
            </span>
          </div>
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

      {/* Self-reported vs independently verified */}
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
          Self-reported vs independently verified ({tWard})
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
            <div style={{ color: MUT, fontSize: 12 }}>
              Self-report overstatement
            </div>
            <div style={{ fontSize: 24, color: GREEN }}>
              +{(sr.premium_pp || 0).toFixed(1)} pts
            </div>
          </div>
        </div>
        <div
          style={{ color: MUT, fontSize: 12, marginTop: 10, fontFamily: mono }}
        >
          Program-logged visits — {tWard}:{' '}
          {sd[tWard] != null ? sd[tWard].toLocaleString() : '—'} · {cWard}:{' '}
          {sd[cWard] != null ? sd[cWard].toLocaleString() : '—'}
        </div>
      </div>
    </div>
  );
}
