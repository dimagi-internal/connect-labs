// Verified Monitoring (N1) — funder-facing verified-coverage dashboard.
// Self-contained: reads everything from instance.state (seeded payload); never
// fetches. Financial-dashboard styling with a two-ward Leaflet overlay.
// Verification-first: the hero is the defensible claim (an independent survey
// checked the implementer's self-report). The treatment/comparison ward
// coverage is supporting context, framed descriptively — not a causal estimate.
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V17
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
          layer.bindTooltip(w + ' · ' + cnt.toLocaleString() + ' visits', {
            permanent: true,
            direction: t ? 'left' : 'right',
            className: 'vm-ward-label',
          });
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
            // Small, semi-transparent: reads as ward-fill saturation/texture,
            // not individual confetti. The survey pins are the foreground signal.
            return L.circleMarker(ll, {
              radius: 2,
              weight: 0,
              fillColor: '#16a34a',
              fillOpacity: 0.45,
            });
          },
        });
        sdl._ov = true;
        sdl.addTo(map);
      }
      if (pinsOn && overlay.survey_pins) {
        var pl = L.geoJSON(overlay.survey_pins, {
          pointToLayer: function (f, ll) {
            // Distinct hues: confirmed = purple, absent = slate (not a near-pink).
            var ok = f.properties && f.properties.confirmed;
            return L.circleMarker(ll, {
              radius: 3.5,
              weight: 0.6,
              color: '#0b1020',
              fillColor: ok ? '#a78bfa' : '#94a3b8',
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
    GREEN = '#34d399',
    AMBER = '#fbbf24',
    REDISH = '#fca5a5';
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
  // 95% CI on the independently-verified coverage (binomial, n surveyed).
  var _indP = (sr.independent_pct || 0) / 100;
  var _indN = latest.intervention_n || 0;
  var indCI =
    _indN > 0 ? 1.96 * Math.sqrt((_indP * (1 - _indP)) / _indN) * 100 : null;
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
        {ti[last] ? (
          <text
            x={X(last) - 8}
            y={Y(ti[last].coverage_pct) - 8}
            fill={PURPLE}
            fontSize="11"
            fontWeight="700"
            textAnchor="end"
          >
            {tWard + ' ' + pct(ti[last].coverage_pct)}
          </text>
        ) : null}
        {tc[last] ? (
          <text
            x={X(last) - 8}
            y={Y(tc[last].coverage_pct) - 8}
            fill={PINK}
            fontSize="11"
            fontWeight="700"
            textAnchor="end"
          >
            {cWard + ' ' + pct(tc[last].coverage_pct)}
          </text>
        ) : null}
      </svg>
    );
  }

  // --- hero dumbbell: self-reported vs independently-verified, with the gap
  // shaded and a 95% CI whisker on the verified estimate. Makes the gap the
  // visual instead of a prose sentence. ---
  function dumbbell() {
    var W = 560,
      H = 106,
      padL = 14,
      padR = 14;
    var self = sr.intervention_pct,
      ver = sr.independent_pct;
    if (self == null || ver == null) return null;
    // Zoom the axis to a window around the two values so they spread across
    // the full width (not bunched in the right third of a 0-100 axis).
    var dLo = Math.max(0, Math.floor((Math.min(self, ver) - 10) / 10) * 10);
    var dHi = 100;
    function X(v) {
      return padL + ((v - dLo) / (dHi - dLo)) * (W - padL - padR);
    }
    var yT = 50;
    var xSelf = X(self),
      xVer = X(ver);
    var AMBER = '#fbbf24';
    var ci = indCI || 0;
    var ciLo = X(Math.max(dLo, ver - ci)),
      ciHi = X(Math.min(dHi, ver + ci));
    var ticks = [];
    for (var tk = dLo; tk <= dHi + 0.001; tk += 10) ticks.push(tk);
    // Clamp a label x so its box never overflows the chart edges.
    function clampX(x, halfW) {
      return Math.max(padL + halfW, Math.min(W - padR - halfW, x));
    }
    return (
      <svg
        width="100%"
        viewBox={'0 0 ' + W + ' ' + H}
        style={{ display: 'block', maxWidth: 600 }}
      >
        {/* legend (top) — so the dot values below never collide or clip */}
        <circle cx={padL + 4} cy={11} r="4" fill="#fca5a5" />
        <text x={padL + 12} y={14} fill={MUT} fontSize="10">
          self-reported (program records)
        </text>
        <circle cx={padL + 215} cy={11} r="4" fill={PURPLE} />
        <text x={padL + 223} y={14} fill={MUT} fontSize="10">
          independently verified (survey, 95% CI)
        </text>
        {ticks.map(function (t) {
          var x = X(t);
          return (
            <g key={t}>
              <line x1={x} y1={yT - 3} x2={x} y2={yT + 3} stroke={LINE} />
              <text
                x={x}
                y={H - 5}
                fill={MUT}
                fontSize="9"
                fontFamily={mono}
                textAnchor="middle"
              >
                {t + '%'}
              </text>
            </g>
          );
        })}
        <line x1={padL} y1={yT} x2={W - padR} y2={yT} stroke={LINE} />
        {/* the gap = the finding. Amber, not green (green elsewhere = passing). */}
        <line
          x1={xVer}
          y1={yT}
          x2={xSelf}
          y2={yT}
          stroke={AMBER}
          strokeWidth="4"
        />
        {/* 95% CI as a shaded band so the verified dot clearly sits on top */}
        <rect
          x={ciLo}
          y={yT - 7}
          width={ciHi - ciLo}
          height="14"
          rx="4"
          fill={PURPLE}
          opacity="0.22"
        />
        <line
          x1={ciLo}
          y1={yT - 7}
          x2={ciLo}
          y2={yT + 7}
          stroke={PURPLE}
          strokeWidth="1.5"
          opacity="0.85"
        />
        <line
          x1={ciHi}
          y1={yT - 7}
          x2={ciHi}
          y2={yT + 7}
          stroke={PURPLE}
          strokeWidth="1.5"
          opacity="0.85"
        />
        <circle
          cx={xSelf}
          cy={yT}
          r="7"
          fill="#fca5a5"
          stroke={INK}
          strokeWidth="2"
        />
        <circle
          cx={xVer}
          cy={yT}
          r="7"
          fill={PURPLE}
          stroke={INK}
          strokeWidth="2"
        />
        {/* gap label (above); role+value labels at each dot (below) so the
            direction — self-report higher than verified — is unmistakable. */}
        <text
          x={clampX((xVer + xSelf) / 2, 70)}
          y={yT - 12}
          fill={AMBER}
          fontSize="12"
          fontWeight="700"
          textAnchor="middle"
        >
          self-report {pp(sr.premium_pp)} too high
        </text>
        <text
          x={clampX(xVer, 40)}
          y={yT + 22}
          fill={PURPLE}
          fontSize="14"
          fontWeight="800"
          textAnchor="middle"
        >
          {pct(ver)}
        </text>
        <text
          x={clampX(xVer, 40)}
          y={yT + 36}
          fill={MUT}
          fontSize="10"
          textAnchor="middle"
        >
          verified (survey)
        </text>
        <text
          x={clampX(xSelf, 40)}
          y={yT + 22}
          fill="#fca5a5"
          fontSize="14"
          fontWeight="800"
          textAnchor="middle"
        >
          {pct(self)}
        </text>
        <text
          x={clampX(xSelf, 40)}
          y={yT + 36}
          fill={MUT}
          fontSize="10"
          textAnchor="middle"
        >
          self-reported
        </text>
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
        <b style={{ color: PURPLE }}>{tWard}</b> (program ward) vs{' '}
        <b style={{ color: PINK }}>{cWard}</b> (comparison ward)
      </div>

      {/* HERO — the defensible claim: an independent survey checked the
          implementer's self-report. Needs no control group or baseline. */}
      <div
        style={{
          background: PANEL,
          border: '1px solid ' + LINE,
          borderRadius: 12,
          padding: '18px 20px',
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
          Independent verification — {tWard} (program ward)
        </div>
        <div
          style={{
            fontSize: 18,
            fontWeight: 700,
            color: '#e2e8f0',
            marginTop: 8,
            lineHeight: 1.35,
          }}
        >
          Program records overstate coverage by{' '}
          <span style={{ color: AMBER }}>{pp(sr.premium_pp)}</span> —
          self-reported{' '}
          <span style={{ color: REDISH }}>{pct(sr.intervention_pct)}</span>,
          independently verified{' '}
          <span style={{ color: PURPLE }}>{pct(sr.independent_pct)}</span>.
        </div>
        <div style={{ marginTop: 12 }}>{dumbbell()}</div>
        <div
          style={{
            color: MUT,
            fontSize: 12,
            marginTop: 8,
            lineHeight: 1.5,
            maxWidth: 600,
          }}
        >
          Both estimate the same rate — the share of under-5 children reached
          with confirmed vitamin-A — by different methods. Self-report is the
          program's own records (
          {sd[tWard] != null ? sd[tWard].toLocaleString() : '—'} logged visits);
          the verified figure is an independent rooftop survey of {_indN}{' '}
          children (95% CI ±{indCI != null ? indCI.toFixed(1) : '—'} pts).
        </div>
      </div>

      {/* QA strip — how the survey held its line (backs the 'verified' claim) */}
      <div
        style={{
          marginTop: 16,
          color: MUT,
          fontSize: 11,
          textTransform: 'uppercase',
          letterSpacing: '.05em',
          marginBottom: 6,
        }}
      >
        Independent survey — data quality
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {chip('GPS within 15m', pct(verif.gps_within_15m_pct), true)}
        {chip('Evidence complete', pct(verif.evidence_complete_pct), true)}
        {chip('Back-check pass', pct(verif.backcheck_pass_pct), true)}
        {chip(
          'Anomaly flags',
          (verif.flags_raised || 0) +
            ' raised · ' +
            ((verif.flags_raised || 0) - (verif.flags_resolved || 0)) +
            ' open',
          true,
        )}
      </div>

      {/* Supporting context — coverage by ward (descriptive, not an impact
          estimate). Demoted below the verification hero. */}
      <div style={{ marginTop: 18 }}>
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
            marginBottom: 8,
          }}
        >
          Coverage by ward, latest round (descriptive)
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {tile(
            'Program ward',
            tWard,
            pct(tCov),
            latest.intervention_n != null
              ? latest.intervention_n + ' children surveyed'
              : 'independent survey',
            PURPLE,
            null,
            null,
          )}
          {tile(
            'Comparison ward',
            cWard,
            pct(cCov),
            cN != null ? cN + ' children surveyed' : 'independent survey',
            PINK,
            null,
            null,
          )}
        </div>
        <div
          style={{ marginTop: 10, color: MUT, fontSize: 12, lineHeight: 1.4 }}
        >
          {cWard} received no program activity (0 logged visits) — an
          observational neighbouring reference, not a randomised control, so the
          two wards aren't directly comparable.
        </div>
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
            <div style={{ color: '#cbd5e1', fontSize: 13, maxWidth: 560 }}>
              Where the program delivered, and where the survey checked —{' '}
              {tWard} logged{' '}
              {sd[tWard] != null ? sd[tWard].toLocaleString() : 0} visits,{' '}
              {cWard} {sd[cWard] != null ? sd[cWard].toLocaleString() : 0}; the
              independent survey covered both wards.
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
              <span style={{ color: '#16a34a' }}>●</span> program
              service-delivery visit
            </span>
            <span>
              <span style={{ color: PURPLE }}>●</span> survey: vitamin-A
              confirmed
            </span>
            <span>
              <span style={{ color: '#94a3b8' }}>●</span> survey: not confirmed
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
