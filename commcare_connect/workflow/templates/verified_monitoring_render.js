// Verified Monitoring (N1) — funder-facing verified-coverage dashboard.
// Self-contained: reads everything from instance.state (seeded payload); never
// fetches. Light, Connect-aligned styling (white cards on the stone page, Work
// Sans, indigo/amber/teal accents drawn from the in-app funder charts) so the
// dashboard reads as part of Connect, not a dark island. The two-ward map uses
// the shared ConnectMap module (Mapbox GL light basemap + real admin
// boundaries) loaded by the runner.
// Verification-first: the hero is the defensible claim (an independent survey
// checked the implementer's self-report). The treatment/comparison ward
// coverage is supporting context, framed descriptively — not a causal estimate.
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V20
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

  // --- Connect-aligned light palette (sourced from static/js/funder-charts.js
  // + the brand tokens): white cards on the stone page, indigo = verified /
  // program ward, amber = self-reported (the claim being checked), rose = the
  // overstatement gap (the finding), teal = comparison ward. ---
  var INK = '#111827', // primary text — matches the runner's text-gray-900
    SUBINK = '#1e293b', // strong secondary
    PANEL = '#ffffff', // card surface
    LINE = '#e6e7f0', // brand hairline
    MUT = '#6b7280', // muted label (gray-500)
    INDIGO = '#4f46e5', // independently verified / program ward (Connect primary)
    AMBER = '#f59e0b', // self-reported (program's own records)
    ROSE = '#e11d48', // the gap / overstatement (alert)
    TEAL = '#0d9488', // comparison ward
    GREEN = '#059669', // QA pass
    SLATE = '#64748b'; // not-confirmed / neutral
  var sans = "'Work Sans', Inter, system-ui, sans-serif";
  var mono = 'ui-monospace, SFMono-Regular, Menlo, monospace';
  var SHADOW = '0 1px 2px rgba(16,24,40,0.06), 0 1px 3px rgba(16,24,40,0.04)';

  // --- Two-ward map via the shared ConnectMap module (Mapbox GL light basemap
  // + real admin boundaries). mapboxgl + ConnectMap are loaded by the workflow
  // runner page; boundary GeoJSON is fed via props (instance.state). ---
  var [mapLibReady, setMapLibReady] = React.useState(
    typeof window !== 'undefined' && !!window.ConnectMap && !!window.mapboxgl,
  );
  var [sdOn, setSdOn] = React.useState(true);
  // Land on the clean service-delivery view; the survey-pin layer is toggled
  // on to add the independent-survey story (avoids a "both at once" confetti
  // default).
  var [pinsOn, setPinsOn] = React.useState(false);
  var mapDivRef = React.useRef(null);
  var mapRef = React.useRef(null);
  var mapLoadedRef = React.useRef(false);

  // Wait for the shared map module to be present on the page.
  React.useEffect(
    function () {
      if (mapLibReady) return undefined;
      var t = setInterval(function () {
        if (window.ConnectMap && window.mapboxgl) {
          setMapLibReady(true);
          clearInterval(t);
        }
      }, 150);
      return function () {
        clearInterval(t);
      };
    },
    [mapLibReady],
  );

  React.useEffect(
    function () {
      if (!mapLibReady || !overlay || !mapDivRef.current) return undefined;
      var CM = window.ConnectMap;
      if (!mapRef.current) {
        var ctr = CM.bounds(overlay.ward_boundaries).getCenter();
        mapRef.current = CM.createMap(mapDivRef.current, {
          center: [ctr.lng, ctr.lat],
          zoom: 10,
          style: 'mapbox://styles/mapbox/light-v11',
        });
      }
      var map = mapRef.current;
      function draw() {
        CM.remove(map, ['vm-sd', 'vm-pins', 'vm-wards']);
        CM.boundary(map, 'vm-wards', overlay.ward_boundaries, {
          activeWard: prog.treatment_ward,
          activeColor: INDIGO,
          mutedColor: '#94a3b8',
          activeFill: INDIGO,
          mutedFill: '#cbd5e1',
          fillOpacity: 0.14,
          labelColor: SUBINK,
          labelHalo: '#ffffff',
        });
        // Light-theme paint overrides — take effect even on the currently
        // deployed ConnectMap (which may predate the themeable boundary opts).
        try {
          map.setPaintProperty('vm-wards-fill', 'fill-color', [
            'case',
            ['==', ['get', 'ward'], prog.treatment_ward],
            INDIGO,
            '#cbd5e1',
          ]);
          map.setPaintProperty('vm-wards-fill', 'fill-opacity', 0.14);
          map.setPaintProperty('vm-wards-line', 'line-color', [
            'case',
            ['==', ['get', 'ward'], prog.treatment_ward],
            INDIGO,
            '#94a3b8',
          ]);
          map.setPaintProperty('vm-wards-label', 'text-color', SUBINK);
          map.setPaintProperty('vm-wards-label', 'text-halo-color', '#ffffff');
        } catch (e) {}
        if (sdOn && overlay.service_delivery) {
          CM.points(map, 'vm-sd', overlay.service_delivery, {
            color: '#16a34a',
            radius: 2.2,
            opacity: 0.5,
          });
        }
        if (pinsOn && overlay.survey_pins) {
          CM.pins(map, 'vm-pins', overlay.survey_pins, {
            confirmedColor: INDIGO,
            absentColor: SLATE,
          });
        }
        CM.fit(map, overlay.ward_boundaries, 55);
      }
      if (mapLoadedRef.current && map.isStyleLoaded()) {
        draw();
      } else {
        map.once('load', function () {
          mapLoadedRef.current = true;
          draw();
        });
      }
      return undefined;
    },
    [mapLibReady, overlay, sdOn, pinsOn],
  );

  if (!latest) {
    return (
      <div
        style={{
          padding: '2rem',
          color: MUT,
          fontFamily: sans,
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

  function pct(x) {
    return x == null ? '—' : x.toFixed(1) + '%';
  }
  function pp(x) {
    return x == null ? '—' : (x >= 0 ? '+' : '') + x.toFixed(1) + ' pts';
  }

  // --- small inline swatch for legends ---
  function sw(color, dashed) {
    return (
      <span
        style={{
          display: 'inline-block',
          width: 14,
          height: dashed ? 0 : 10,
          borderTop: dashed ? '2px dashed ' + color : 'none',
          background: dashed ? 'none' : color,
          borderRadius: dashed ? 0 : 3,
          marginRight: 5,
          verticalAlign: 'middle',
        }}
      />
    );
  }

  // --- triple-line trend: verified treatment (solid indigo), self-reported
  // (dashed amber), comparison (solid teal), with the overstatement region —
  // between self-report and verified treatment — shaded amber. Ties the trend
  // back to the hero: the program over-reports every round, not just once. ---
  function trend() {
    var ti = byArm.intervention || [], // independently verified — program ward
      tc = byArm.comparison || [], // independently verified — comparison ward
      ts = byArm.self_report || []; // program self-reported — program ward
    var n = Math.max(ti.length, tc.length, ts.length);
    if (n < 2) return null;
    var w = 560,
      h = 214,
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
    // overstatement band: self-report (top edge) → verified treatment (bottom).
    var band = '';
    if (ts.length && ti.length) {
      band =
        ts
          .map(function (r, i) {
            return X(i) + ',' + Y(r.coverage_pct);
          })
          .join(' ') +
        ' ' +
        ti
          .slice()
          .reverse()
          .map(function (r, i) {
            return X(ti.length - 1 - i) + ',' + Y(r.coverage_pct);
          })
          .join(' ');
    }
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
    var rowsForX = ti.length ? ti : ts.length ? ts : tc;
    var xlabels = rowsForX.map(function (r, i) {
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
    function endDot(series, color) {
      var r = series[series.length - 1];
      if (!r) return null;
      return (
        <circle
          cx={X(series.length - 1)}
          cy={Y(r.coverage_pct)}
          r="4.5"
          fill={color}
          stroke="#ffffff"
          strokeWidth="1.5"
        />
      );
    }
    function endLabel(series, color, label, dy) {
      var r = series[series.length - 1];
      if (!r) return null;
      return (
        <text
          x={X(series.length - 1) - 8}
          y={Y(r.coverage_pct) + (dy || -8)}
          fill={color}
          fontSize="11"
          fontWeight="700"
          textAnchor="end"
        >
          {label + ' ' + pct(r.coverage_pct)}
        </text>
      );
    }
    return (
      <svg width={w} height={h} style={{ maxWidth: '100%' }}>
        {grid}
        {band ? (
          <polygon
            points={band}
            fill={AMBER}
            fillOpacity="0.12"
            stroke="none"
          />
        ) : null}
        <polyline points={poly(tc)} fill="none" stroke={TEAL} strokeWidth="2" />
        <polyline
          points={poly(ts)}
          fill="none"
          stroke={AMBER}
          strokeWidth="2.25"
          strokeDasharray="5 4"
        />
        <polyline
          points={poly(ti)}
          fill="none"
          stroke={INDIGO}
          strokeWidth="2.5"
        />
        {xlabels}
        {endDot(tc, TEAL)}
        {endDot(ts, AMBER)}
        {endDot(ti, INDIGO)}
        {endLabel(ts, AMBER, 'self-reported', -8)}
        {endLabel(ti, INDIGO, tWard, 14)}
        {endLabel(tc, TEAL, cWard, -8)}
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
        <circle cx={padL + 4} cy={11} r="4" fill={AMBER} />
        <text x={padL + 12} y={14} fill={MUT} fontSize="10">
          self-reported (program records)
        </text>
        <circle cx={padL + 215} cy={11} r="4" fill={INDIGO} />
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
        {/* the gap = the finding. Rose, not green (green elsewhere = passing). */}
        <line
          x1={xVer}
          y1={yT}
          x2={xSelf}
          y2={yT}
          stroke={ROSE}
          strokeWidth="4"
        />
        {/* 95% CI as a shaded band so the verified dot clearly sits on top */}
        <rect
          x={ciLo}
          y={yT - 7}
          width={ciHi - ciLo}
          height="14"
          rx="4"
          fill={INDIGO}
          opacity="0.18"
        />
        <line
          x1={ciLo}
          y1={yT - 7}
          x2={ciLo}
          y2={yT + 7}
          stroke={INDIGO}
          strokeWidth="1.5"
          opacity="0.8"
        />
        <line
          x1={ciHi}
          y1={yT - 7}
          x2={ciHi}
          y2={yT + 7}
          stroke={INDIGO}
          strokeWidth="1.5"
          opacity="0.8"
        />
        <circle
          cx={xSelf}
          cy={yT}
          r="7"
          fill={AMBER}
          stroke="#ffffff"
          strokeWidth="2"
        />
        <circle
          cx={xVer}
          cy={yT}
          r="7"
          fill={INDIGO}
          stroke="#ffffff"
          strokeWidth="2"
        />
        {/* gap label (above); role+value labels at each dot (below) so the
            direction — self-report higher than verified — is unmistakable. */}
        <text
          x={clampX((xVer + xSelf) / 2, 70)}
          y={yT - 12}
          fill={ROSE}
          fontSize="12"
          fontWeight="700"
          textAnchor="middle"
        >
          self-report {pp(sr.premium_pp)} too high
        </text>
        <text
          x={clampX(xVer, 40)}
          y={yT + 22}
          fill={INDIGO}
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
          fill={AMBER}
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
          border: '1px solid ' + (ok ? '#a7f3d0' : LINE),
          borderRadius: 8,
          padding: '8px 12px',
          fontFamily: mono,
          boxShadow: SHADOW,
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
            color: ok ? GREEN : SUBINK,
            fontSize: 18,
            fontWeight: 700,
          }}
        >
          {val}
        </div>
      </div>
    );
  };

  function tile(label, ward, value, sub, color) {
    return (
      <div
        style={{
          background: PANEL,
          border: '1px solid ' + LINE,
          borderRadius: 10,
          padding: '14px 16px',
          minWidth: 180,
          flex: '1 1 180px',
          boxShadow: SHADOW,
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
        <div style={{ color: SUBINK, fontSize: 13, marginTop: 2 }}>{ward}</div>
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
      </div>
    );
  }

  var cardStyle = {
    background: PANEL,
    border: '1px solid ' + LINE,
    borderRadius: 12,
    boxShadow: SHADOW,
  };

  return (
    <div
      style={{
        background: 'transparent',
        color: INK,
        fontFamily: sans,
        paddingBottom: 8,
      }}
    >
      <div style={{ fontSize: 18, fontWeight: 700, color: INK }}>
        {prog.name || 'Verified Monitoring'}
      </div>
      <div
        style={{
          color: MUT,
          fontSize: 13,
          marginTop: 4,
          marginBottom: 16,
          lineHeight: 1.5,
        }}
      >
        Independent rooftop survey · {prog.cadence || 'bi-monthly'} · latest
        round R{latest.round || (byArm.intervention || []).length} ·{' '}
        <b style={{ color: INDIGO }}>{tWard}</b> (program ward) vs{' '}
        <b style={{ color: TEAL }}>{cWard}</b> (comparison ward)
      </div>

      {/* HERO — the defensible claim: an independent survey checked the
          implementer's self-report. Needs no control group or baseline. */}
      <div style={Object.assign({ padding: '18px 20px' }, cardStyle)}>
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
            color: INK,
            marginTop: 8,
            lineHeight: 1.35,
          }}
        >
          Program records overstate coverage by{' '}
          <span style={{ color: ROSE }}>{pp(sr.premium_pp)}</span> —
          self-reported{' '}
          <span style={{ color: AMBER }}>{pct(sr.intervention_pct)}</span>,
          independently verified{' '}
          <span style={{ color: INDIGO }}>{pct(sr.independent_pct)}</span>.
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
            INDIGO,
          )}
          {tile(
            'Comparison ward',
            cWard,
            pct(cCov),
            cN != null ? cN + ' children surveyed' : 'independent survey',
            TEAL,
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
      <div style={Object.assign({ marginTop: 18, padding: 14 }, cardStyle)}>
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
            marginBottom: 6,
          }}
        >
          Self-reported vs independently-verified coverage across{' '}
          {byArm.intervention ? byArm.intervention.length : 0} bi-monthly rounds
        </div>
        <div
          style={{
            display: 'flex',
            gap: 16,
            flexWrap: 'wrap',
            fontSize: 11,
            color: SUBINK,
            marginBottom: 8,
          }}
        >
          <span>
            {sw(AMBER, true)}self-reported ({tWard})
          </span>
          <span>
            {sw(INDIGO)}independently verified ({tWard})
          </span>
          <span>
            {sw(TEAL)}independently verified ({cWard})
          </span>
        </div>
        <div style={{ color: MUT, fontSize: 11, marginBottom: 8 }}>
          y-axis: % of surveyed children with confirmed vitamin-A · the shaded
          band is the self-report overstatement
        </div>
        {trend()}
      </div>

      {/* Two-ward map overlay */}
      {overlay ? (
        <div style={Object.assign({ marginTop: 18, padding: 14 }, cardStyle)}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 8,
            }}
          >
            <div style={{ color: SUBINK, fontSize: 13, maxWidth: 560 }}>
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
              <label style={{ color: '#16a34a', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={sdOn}
                  onChange={function (e) {
                    setSdOn(e.target.checked);
                  }}
                />{' '}
                service delivery
              </label>
              <label style={{ color: INDIGO, cursor: 'pointer' }}>
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
              background: '#eef2f7',
              border: '1px solid ' + LINE,
            }}
          />
          {!mapLibReady ? (
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
              <span style={{ color: INDIGO }}>●</span> survey: vitamin-A
              confirmed
            </span>
            <span>
              <span style={{ color: SLATE }}>●</span> survey: not confirmed
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
