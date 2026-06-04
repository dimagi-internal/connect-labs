// Verified Monitoring (N1) — funder-facing verified-coverage dashboard.
// Self-contained: reads everything from instance.state (seeded by the survey_sim
// generator; every KPI computed from row-level records via the survey_quality
// library) and never fetches. Light, Connect-aligned styling.
//
// Layout: a round selector drives a verification-first hero (self-reported vs
// independently-verified dumbbell), a Layer-1 survey-quality strip, descriptive
// ward tiles, the independent back-check drill-down (J-PAL Type-1/2/3 — the
// side-by-side that proves the survey), a six-round trend, and the two-ward
// Mapbox map (shared ConnectMap module + real admin boundaries).
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V24
function WorkflowUI(props) {
  var instance = props.instance || {};
  var data = instance.state || {};
  var prog = data.program || {};
  var rounds = data.rounds || [];
  var trend = data.trend || {};
  var overlay = data.overlay || null;
  var sd = data.service_delivery_counts || {};
  var tWard = prog.treatment_ward || 'Treatment';
  var cWard = prog.control_ward || 'Control';

  // --- Connect-aligned light palette (from static/js/funder-charts.js) ---
  var INK = '#111827',
    SUBINK = '#1e293b',
    PANEL = '#ffffff',
    LINE = '#e6e7f0',
    MUT = '#6b7280',
    INDIGO = '#4f46e5',
    AMBER = '#f59e0b',
    ROSE = '#e11d48',
    TEAL = '#0d9488',
    GREEN = '#059669',
    SLATE = '#64748b';
  var sans = "'Work Sans', Inter, system-ui, sans-serif";
  var mono = 'ui-monospace, SFMono-Regular, Menlo, monospace';
  var SHADOW = '0 1px 2px rgba(16,24,40,0.06), 0 1px 3px rgba(16,24,40,0.04)';

  var [sel, setSel] = React.useState(
    Math.max(0, (data.current_round || rounds.length) - 1),
  );
  if (sel > rounds.length - 1) sel = Math.max(0, rounds.length - 1);
  var rd = rounds[sel] || null;

  // ---- two-ward map (shared ConnectMap; latest-round pins) ----
  var [mapLibReady, setMapLibReady] = React.useState(
    typeof window !== 'undefined' && !!window.ConnectMap && !!window.mapboxgl,
  );
  var [sdOn, setSdOn] = React.useState(true);
  var [pinsOn, setPinsOn] = React.useState(true);
  var mapDivRef = React.useRef(null);
  var mapRef = React.useRef(null);
  var mapLoadedRef = React.useRef(false);

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
          activeWard: tWard,
          activeColor: INDIGO,
          mutedColor: '#94a3b8',
          activeFill: INDIGO,
          mutedFill: '#cbd5e1',
          fillOpacity: 0.14,
          labelColor: SUBINK,
          labelHalo: '#ffffff',
        });
        try {
          map.setPaintProperty('vm-wards-fill', 'fill-color', [
            'case',
            ['==', ['get', 'ward'], tWard],
            INDIGO,
            '#cbd5e1',
          ]);
          map.setPaintProperty('vm-wards-fill', 'fill-opacity', 0.14);
          map.setPaintProperty('vm-wards-line', 'line-color', [
            'case',
            ['==', ['get', 'ward'], tWard],
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

  if (!rd) {
    return (
      <div style={{ padding: '2rem', color: MUT, fontFamily: sans }}>
        Verified Monitoring — no data yet. Seed this run via the
        verified-monitoring recipe (regenerate.py).
      </div>
    );
  }

  // ---- derived ----
  function pct(x) {
    return x == null ? '—' : x.toFixed(1) + '%';
  }
  function pp(x) {
    return x == null ? '—' : (x >= 0 ? '+' : '') + x.toFixed(1) + ' pts';
  }
  function yn(v) {
    return v === true ? 'yes' : v === false ? 'no' : v == null ? '—' : '' + v;
  }

  var ver = rd.intervention_pct,
    self_ = rd.self_report_pct,
    prem = rd.premium_pp;
  var indN = rd.intervention_n || 0;
  var _indP = (ver || 0) / 100;
  var indCI =
    indN > 0 ? 1.96 * Math.sqrt((_indP * (1 - _indP)) / indN) * 100 : null;
  var q = rd.quality || {};
  var bc = rd.backcheck || {};

  var cardStyle = {
    background: PANEL,
    border: '1px solid ' + LINE,
    borderRadius: 12,
    boxShadow: SHADOW,
  };

  // ---- small inline legend swatch ----
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

  // ---- QA chip (Layer-1 metric) ----
  function metricVal(m) {
    if (!m || m.value == null) return '—';
    if (m.unit === 'count') return '' + m.value;
    if (m.unit === 'pvalue') return 'p=' + m.value;
    return (typeof m.value === 'number' ? m.value.toFixed(1) : m.value) + '%';
  }
  function chip(label, m) {
    var ok = m && m.passed;
    var bad = m && m.passed === false;
    var col = ok ? GREEN : bad ? ROSE : SUBINK;
    return (
      <div
        key={label}
        style={Object.assign(
          {
            padding: '8px 12px',
            fontFamily: mono,
            minWidth: 120,
            borderColor: ok ? '#a7f3d0' : bad ? '#fecdd3' : LINE,
          },
          cardStyle,
          { borderRadius: 8 },
        )}
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
        <div style={{ color: col, fontSize: 18, fontWeight: 700 }}>
          {metricVal(m)}
        </div>
        {m && m.threshold != null ? (
          <div style={{ color: MUT, fontSize: 9, marginTop: 1 }}>
            target {m.direction === 'lower_better' ? '≤' : '≥'} {m.threshold}
            {m.unit === 'pct' ? '%' : ''}
          </div>
        ) : null}
      </div>
    );
  }

  // ---- hero dumbbell: self-reported vs independently-verified ----
  function dumbbell() {
    var W = 560,
      H = 106,
      padL = 14,
      padR = 14;
    if (self_ == null || ver == null) return null;
    var dLo = Math.max(0, Math.floor((Math.min(self_, ver) - 10) / 10) * 10);
    var dHi = 100;
    function X(v) {
      return padL + ((v - dLo) / (dHi - dLo)) * (W - padL - padR);
    }
    var yT = 50;
    var xSelf = X(self_),
      xVer = X(ver);
    var ci = indCI || 0;
    var ciLo = X(Math.max(dLo, ver - ci)),
      ciHi = X(Math.min(dHi, ver + ci));
    var ticks = [];
    for (var tk = dLo; tk <= dHi + 0.001; tk += 10) ticks.push(tk);
    function clampX(x, halfW) {
      return Math.max(padL + halfW, Math.min(W - padR - halfW, x));
    }
    return (
      <svg
        width="100%"
        viewBox={'0 0 ' + W + ' ' + H}
        style={{ display: 'block', maxWidth: 600 }}
      >
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
        <line
          x1={xVer}
          y1={yT}
          x2={xSelf}
          y2={yT}
          stroke={SLATE}
          strokeWidth="4"
        />
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
          {pct(self_)}
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

  // ---- six-round trend (3 series + overstatement band + selected marker) ----
  function trendChart() {
    var iv = trend.intervention || [],
      cp = trend.comparison || [],
      srr = trend.self_report || [],
      rr = trend.rounds || [];
    var n = Math.max(iv.length, cp.length, srr.length);
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
    function poly(arr) {
      return arr
        .map(function (v, i) {
          return X(i) + ',' + Y(v);
        })
        .join(' ');
    }
    var band = '';
    if (srr.length && iv.length) {
      band =
        srr
          .map(function (v, i) {
            return X(i) + ',' + Y(v);
          })
          .join(' ') +
        ' ' +
        iv
          .slice()
          .reverse()
          .map(function (v, i) {
            return X(iv.length - 1 - i) + ',' + Y(v);
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
    function endDot(arr, color) {
      if (!arr.length) return null;
      var i = arr.length - 1;
      return (
        <circle
          cx={X(i)}
          cy={Y(arr[i])}
          r="4.5"
          fill={color}
          stroke="#ffffff"
          strokeWidth="1.5"
        />
      );
    }
    function endLabel(arr, color, label, dy) {
      if (!arr.length) return null;
      var i = arr.length - 1;
      return (
        <text
          x={X(i) - 8}
          y={Y(arr[i]) + (dy || -8)}
          fill={color}
          fontSize="11"
          fontWeight="700"
          textAnchor="end"
        >
          {label + ' ' + pct(arr[i])}
        </text>
      );
    }
    return (
      <svg width={w} height={h} style={{ maxWidth: '100%' }}>
        {grid}
        {/* selected-round marker */}
        <rect
          x={X(sel) - 9}
          y={pad - 8}
          width="18"
          height={h - padB - pad + 8}
          fill={INDIGO}
          opacity="0.06"
        />
        <line
          x1={X(sel)}
          y1={pad - 8}
          x2={X(sel)}
          y2={h - padB}
          stroke={INDIGO}
          strokeWidth="1"
          opacity="0.5"
          strokeDasharray="3 3"
        />
        {band ? (
          <polygon
            points={band}
            fill={AMBER}
            fillOpacity="0.12"
            stroke="none"
          />
        ) : null}
        <polyline points={poly(cp)} fill="none" stroke={TEAL} strokeWidth="2" />
        <polyline
          points={poly(srr)}
          fill="none"
          stroke={AMBER}
          strokeWidth="2.25"
          strokeDasharray="5 4"
        />
        <polyline
          points={poly(iv)}
          fill="none"
          stroke={INDIGO}
          strokeWidth="2.5"
        />
        {rr.map(function (r, i) {
          return (
            <text
              key={i}
              x={X(i)}
              y={h - 8}
              fill={i === sel ? INDIGO : MUT}
              fontWeight={i === sel ? '700' : '400'}
              fontSize="9"
              fontFamily={mono}
              textAnchor="middle"
            >
              {'R' + r}
            </text>
          );
        })}
        {endDot(cp, TEAL)}
        {endDot(srr, AMBER)}
        {endDot(iv, INDIGO)}
        {endLabel(srr, AMBER, 'self-reported', -8)}
        {endLabel(iv, INDIGO, tWard, 14)}
        {endLabel(cp, TEAL, cWard, -8)}
      </svg>
    );
  }

  // ---- back-check side-by-side table ----
  function bcTable() {
    var rows = (bc.rows || []).slice(0, 9);
    if (!rows.length) return null;
    var cols = [
      ['vitamin_a_received', 'Vitamin-A'],
      ['child_present', 'Present'],
      ['child_sex', 'Sex'],
      ['child_age_months', 'Age (mo)'],
    ];
    function fieldOf(row, key) {
      var fs = row.fields || [];
      for (var i = 0; i < fs.length; i++) if (fs[i].key === key) return fs[i];
      return null;
    }
    var th = {
      textAlign: 'left',
      color: MUT,
      fontSize: 10,
      textTransform: 'uppercase',
      letterSpacing: '.04em',
      padding: '6px 8px',
      borderBottom: '1px solid ' + LINE,
    };
    var td = {
      padding: '6px 8px',
      fontSize: 12,
      borderBottom: '1px solid ' + LINE,
      fontFamily: mono,
      verticalAlign: 'top',
    };
    return (
      <div style={{ overflowX: 'auto' }}>
        <table
          style={{ borderCollapse: 'collapse', width: '100%', minWidth: 620 }}
        >
          <thead>
            <tr>
              <th style={th}>Household · original enum. → back-check enum.</th>
              {cols.map(function (c) {
                return (
                  <th key={c[0]} style={th}>
                    {c[1]}
                  </th>
                );
              })}
            </tr>
            <tr>
              <th
                style={Object.assign({}, th, { color: '#94a3b8', fontSize: 9 })}
              >
                each cell: original / re-survey
              </th>
              {cols.map(function (c) {
                return <th key={c[0]} style={th} />;
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map(function (row, ri) {
              return (
                <tr
                  key={ri}
                  style={{
                    background: row.flagged ? '#fff1f2' : 'transparent',
                  }}
                >
                  <td style={td}>
                    <div style={{ color: SUBINK, fontWeight: 600 }}>
                      {row.household_id}
                    </div>
                    <div style={{ color: MUT, fontSize: 11 }}>
                      {row.enumerator} → {row.backcheck_enumerator}
                    </div>
                  </td>
                  {cols.map(function (c) {
                    var f = fieldOf(row, c[0]);
                    if (!f)
                      return (
                        <td key={c[0]} style={td}>
                          —
                        </td>
                      );
                    var changed = !f.match;
                    return (
                      <td key={c[0]} style={td}>
                        <div style={{ color: SUBINK }}>{yn(f.original)}</div>
                        <div
                          style={{
                            color: changed ? ROSE : MUT,
                            fontWeight: changed ? 700 : 400,
                            fontSize: 11,
                          }}
                        >
                          {yn(f.backcheck)}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
        {(bc.rows || []).length > rows.length ? (
          <div style={{ color: MUT, fontSize: 11, marginTop: 6 }}>
            showing {rows.length} of {(bc.rows || []).length} back-checked
            households (mismatches first)
          </div>
        ) : null}
      </div>
    );
  }

  // ---- round selector ----
  function roundTabs() {
    return (
      <div
        style={{
          display: 'flex',
          gap: 6,
          flexWrap: 'wrap',
          marginTop: 10,
          alignItems: 'center',
        }}
      >
        <span
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
            marginRight: 4,
          }}
        >
          Survey round
        </span>
        {rounds.map(function (r, i) {
          var on = i === sel;
          return (
            <button
              key={i}
              onClick={function () {
                setSel(i);
              }}
              style={{
                cursor: 'pointer',
                fontFamily: mono,
                fontSize: 12,
                padding: '4px 10px',
                borderRadius: 7,
                border: '1px solid ' + (on ? INDIGO : LINE),
                background: on ? INDIGO : '#ffffff',
                color: on ? '#ffffff' : SUBINK,
                fontWeight: on ? 700 : 500,
              }}
            >
              {'R' + r.round}
            </button>
          );
        })}
        <span style={{ color: MUT, fontSize: 12, marginLeft: 6 }}>
          {rd.label}
        </span>
      </div>
    );
  }

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
      <div style={{ color: MUT, fontSize: 13, marginTop: 4, lineHeight: 1.5 }}>
        Independent rooftop survey · {prog.cadence || 'bi-monthly'} ·{' '}
        <b style={{ color: INDIGO }}>{tWard}</b> (program ward) vs{' '}
        <b style={{ color: TEAL }}>{cWard}</b> (comparison ward)
      </div>
      {roundTabs()}

      {/* HERO */}
      <div
        style={Object.assign(
          { padding: '18px 20px', marginTop: 14 },
          cardStyle,
        )}
      >
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
          }}
        >
          Self-reported vs independently verified · {tWard} · R{rd.round}
        </div>
        <div
          style={{
            display: 'flex',
            gap: 28,
            flexWrap: 'wrap',
            alignItems: 'baseline',
            marginTop: 10,
          }}
        >
          <div>
            <div
              style={{
                color: AMBER,
                fontFamily: mono,
                fontSize: 28,
                fontWeight: 800,
              }}
            >
              {pct(self_)}
            </div>
            <div style={{ color: MUT, fontSize: 11 }}>self-reported</div>
          </div>
          <div>
            <div
              style={{
                color: INDIGO,
                fontFamily: mono,
                fontSize: 28,
                fontWeight: 800,
              }}
            >
              {pct(ver)}
            </div>
            <div style={{ color: MUT, fontSize: 11 }}>
              independently verified
            </div>
          </div>
          <div>
            <div
              style={{
                color: SUBINK,
                fontFamily: mono,
                fontSize: 28,
                fontWeight: 800,
              }}
            >
              {pp(prem)}
            </div>
            <div style={{ color: MUT, fontSize: 11 }}>difference</div>
          </div>
        </div>
        <div style={{ marginTop: 12 }}>{dumbbell()}</div>
        <div
          style={{
            color: MUT,
            fontSize: 11,
            marginTop: 10,
            lineHeight: 1.6,
            fontFamily: mono,
          }}
        >
          indicator: under-5 children with confirmed vitamin-A · self-reported =
          program records (
          {sd[tWard] != null ? sd[tWard].toLocaleString() : '—'} logged visits)
          · independently verified = rooftop survey, n={indN}, 95% CI ±
          {indCI != null ? indCI.toFixed(1) : '—'} pts
        </div>
      </div>

      {/* QA STRIP — Layer 1 survey-quality metrics, computed from records */}
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
        Independent survey — data quality (round R{rd.round})
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {chip('Evidence capture', q.evidence_capture)}
        {chip('GPS within 15 m', q.gps_within_15m)}
        {chip('Field completeness', q.field_completeness)}
        {chip('Duration plausible', q.duration_plausibility)}
        {chip('Consistency', q.consistency_pass)}
        {chip('Duplicates', q.duplicate_integrity)}
      </div>

      {/* WARD TILES */}
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
          Independent survey estimate by ward · R{rd.round}
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {[
            ['Program ward', tWard, ver, rd.intervention_n, INDIGO],
            [
              'Comparison ward',
              cWard,
              rd.comparison_pct,
              rd.comparison_n,
              TEAL,
            ],
          ].map(function (t) {
            return (
              <div
                key={t[1]}
                style={Object.assign(
                  { padding: '14px 16px', minWidth: 180, flex: '1 1 180px' },
                  cardStyle,
                  { borderRadius: 10 },
                )}
              >
                <div
                  style={{
                    color: MUT,
                    fontSize: 11,
                    letterSpacing: '.05em',
                    textTransform: 'uppercase',
                  }}
                >
                  {t[0]}
                </div>
                <div style={{ color: SUBINK, fontSize: 13, marginTop: 2 }}>
                  {t[1]}
                </div>
                <div
                  style={{
                    color: t[4],
                    fontFamily: mono,
                    fontSize: 30,
                    fontWeight: 700,
                    marginTop: 6,
                  }}
                >
                  {pct(t[2])}
                </div>
                <div
                  style={{
                    color: MUT,
                    fontFamily: mono,
                    fontSize: 12,
                    marginTop: 2,
                  }}
                >
                  {t[3]} children surveyed
                </div>
              </div>
            );
          })}
        </div>
        <div
          style={{ marginTop: 10, color: MUT, fontSize: 12, lineHeight: 1.4 }}
        >
          {cWard}: independent-survey estimate in a neighbouring ward with 0
          logged program visits (background reference)
        </div>
      </div>

      {/* BACK-CHECK DRILL-DOWN */}
      <div style={Object.assign({ marginTop: 18, padding: 16 }, cardStyle)}>
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
          }}
        >
          Independent back-check · {tWard} · R{rd.round} — re-survey by a
          different enumerator
        </div>
        <div
          style={{
            display: 'flex',
            gap: 22,
            flexWrap: 'wrap',
            marginTop: 12,
            fontFamily: mono,
          }}
        >
          {[
            [
              'sample re-surveyed',
              bc.coverage_pct + '% · n=' + bc.n_backchecked,
              SUBINK,
            ],
            ['outcome agreement', bc.outcome_agreement_pct + '%', SUBINK],
            [
              'identity match',
              bc.type1_error_pct == null
                ? '—'
                : (100 - bc.type1_error_pct).toFixed(1) + '%',
              SUBINK,
            ],
            ['re-survey vs original', 'p=' + bc.prtest_p, SUBINK],
          ].map(function (s) {
            return (
              <div key={s[0]}>
                <div
                  style={{
                    color: MUT,
                    fontSize: 10,
                    textTransform: 'uppercase',
                    letterSpacing: '.04em',
                  }}
                >
                  {s[0]}
                </div>
                <div style={{ color: s[2], fontSize: 16, fontWeight: 700 }}>
                  {s[1]}
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ marginTop: 12 }}>{bcTable()}</div>
        <div
          style={{
            display: 'flex',
            gap: 16,
            marginTop: 8,
            fontSize: 11,
            color: MUT,
            fontFamily: mono,
          }}
        >
          <span>
            <span style={{ color: SUBINK }}>value</span> = original / re-survey
            agree
          </span>
          <span>
            <span style={{ color: ROSE, fontWeight: 700 }}>red →</span> =
            changed on re-survey
          </span>
        </div>
        <div
          style={{
            marginTop: 8,
            fontSize: 11,
            color: MUT,
            lineHeight: 1.6,
            maxWidth: 720,
          }}
        >
          outcome agreement = share of the {bc.n_backchecked} re-surveyed
          households whose vitamin-A result matched the original · identity
          match = sex / age / presence unchanged · re-survey vs original =
          two-proportion test on the re-surveyed subsample (p &gt; 0.05 = no
          significant difference; the subsample rate is not the ward estimate
          above)
        </div>
      </div>

      {/* TREND */}
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
          {(trend.rounds || []).length} bi-monthly rounds
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
          y-axis: % of surveyed children with confirmed vitamin-A · shaded =
          self-reported − independently verified · highlighted column = selected
          round
        </div>
        {trendChart()}
      </div>

      {/* MAP */}
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
              Program service delivery (logged visits) and independent survey
              locations · {tWard}:{' '}
              {sd[tWard] != null ? sd[tWard].toLocaleString() : 0} visits ·{' '}
              {cWard}: {sd[cWard] != null ? sd[cWard].toLocaleString() : 0}
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
