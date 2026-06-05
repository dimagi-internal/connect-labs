// Verified Monitoring (N1) — funder-facing verified-coverage dashboard.
// Self-contained: reads everything from instance.state (seeded by survey_sim;
// every KPI computed from row-level records via the survey_quality library) and
// never fetches. Light, Connect-aligned styling.
//
// Layout (A+): the six-cycle TREND is the page hero (edge-to-edge, top); a round
// selector pivots the page; per cycle a compact self-vs-verified readout + a map
// that moves to that cycle's two real wards; and ONE drillable-metric block where
// every metric — the survey-quality checks AND the independent back-check — opens
// its own evidence below when clicked. Objective copy; the viewer draws the conclusion.
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V28
function WorkflowUI(props) {
  var instance = props.instance || {};
  var data = instance.state || {};
  var prog = data.program || {};
  var rounds = data.rounds || [];
  var trend = data.trend || {};

  var INK = '#111827',
    SUBINK = '#1e293b',
    PANEL = '#ffffff',
    LINE = '#e6e7f0',
    MUT = '#6b7280',
    INDIGO = '#4f46e5',
    AMBER = '#f59e0b',
    ROSE = '#e11d48',
    COMP = '#64748b',
    GREEN = '#059669',
    SLATE = '#94a3b8';
  var sans = "'Work Sans', Inter, system-ui, sans-serif";
  var mono = 'ui-monospace, SFMono-Regular, Menlo, monospace';
  var SHADOW = '0 1px 2px rgba(16,24,40,0.06), 0 1px 3px rgba(16,24,40,0.04)';

  var [sel, setSel] = React.useState(
    Math.max(0, (data.current_round || rounds.length) - 1),
  );
  if (sel > rounds.length - 1) sel = Math.max(0, rounds.length - 1);
  var rd = rounds[sel] || null;

  // ---- per-round map (shared ConnectMap; moves each round) ----
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
      var overlay = rd && rd.overlay;
      if (!mapLibReady || !overlay || !mapDivRef.current) return undefined;
      var CM = window.ConnectMap;
      var progWard = rd.treatment_ward;
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
          activeWard: progWard,
          activeColor: INDIGO,
          mutedColor: COMP,
          labelColor: SUBINK,
          labelHalo: '#ffffff',
        });
        try {
          map.setPaintProperty('vm-wards-fill', 'fill-color', [
            'case',
            ['==', ['get', 'ward'], progWard],
            INDIGO,
            COMP,
          ]);
          map.setPaintProperty('vm-wards-fill', 'fill-opacity', 0.14);
          map.setPaintProperty('vm-wards-line', 'line-color', [
            'case',
            ['==', ['get', 'ward'], progWard],
            INDIGO,
            COMP,
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
            radius: 3.0,
          });
        }
        CM.fit(map, overlay.ward_boundaries, 48);
      }
      if (mapLoadedRef.current && map.isStyleLoaded()) draw();
      else
        map.once('load', function () {
          mapLoadedRef.current = true;
          draw();
        });
      return undefined;
    },
    [mapLibReady, sel, sdOn, pinsOn],
  );

  if (!rd) {
    return (
      <div style={{ padding: '2rem', color: MUT, fontFamily: sans }}>
        Verified Monitoring — no data yet. Seed this run via regenerate.py.
      </div>
    );
  }

  // ---- per-round data ----
  var tWard = rd.treatment_ward || 'Program ward';
  var cWard = rd.comparison_ward || 'Comparison ward';
  var sd = rd.service_delivery_counts || {};
  var ver = rd.intervention_pct,
    self_ = rd.self_report_pct,
    prem = rd.premium_pp;
  var indN = rd.intervention_n || 0;
  var _indP = (ver || 0) / 100;
  var indCI =
    indN > 0 ? 1.96 * Math.sqrt((_indP * (1 - _indP)) / indN) * 100 : null;
  var q = rd.quality || {};
  var bc = rd.backcheck || {};

  function pct(x) {
    return x == null ? '—' : x.toFixed(1) + '%';
  }
  function pp(x) {
    return x == null ? '—' : (x >= 0 ? '+' : '') + x.toFixed(1) + ' pts';
  }
  function yn(v) {
    return v === true ? 'yes' : v === false ? 'no' : v == null ? '—' : '' + v;
  }
  function metricVal(m) {
    if (!m || m.value == null) return '—';
    if (m.unit === 'count') return '' + m.value;
    return (typeof m.value === 'number' ? m.value.toFixed(1) : m.value) + '%';
  }
  var cardStyle = {
    background: PANEL,
    border: '1px solid ' + LINE,
    borderRadius: 12,
    boxShadow: SHADOW,
  };

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

  // ---- PAGE HERO: the six-cycle trend (edge-to-edge, clickable cycles) ----
  function trendChart() {
    var iv = trend.intervention || [],
      cp = trend.comparison || [],
      srr = trend.self_report || [],
      rr = trend.rounds || [];
    var n = Math.max(iv.length, cp.length, srr.length);
    if (n < 2) return null;
    var w = 1040,
      h = 230,
      padL = 34,
      padR = 150,
      padT = 14,
      padB = 34;
    function X(i) {
      return padL + (i / (n - 1)) * (w - padL - padR);
    }
    function Y(v) {
      return h - padB - ((v || 0) / 100) * (h - padT - padB);
    }
    function poly(a) {
      return a
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
      return (
        <g key={g}>
          <line x1={padL} y1={Y(g)} x2={w - padR} y2={Y(g)} stroke={LINE} />
          <text x={6} y={Y(g) + 3} fill={MUT} fontSize="10" fontFamily={mono}>
            {g + '%'}
          </text>
        </g>
      );
    });
    function endLabel(arr, color, label) {
      if (!arr.length) return null;
      var i = arr.length - 1;
      return (
        <g>
          <circle
            cx={X(i)}
            cy={Y(arr[i])}
            r="4.5"
            fill={color}
            stroke="#fff"
            strokeWidth="1.5"
          />
          <text
            x={X(i) + 10}
            y={Y(arr[i]) + 4}
            fill={color}
            fontSize="11"
            fontWeight="700"
          >
            {label + ' ' + pct(arr[i])}
          </text>
        </g>
      );
    }
    return (
      <svg
        width="100%"
        viewBox={'0 0 ' + w + ' ' + h}
        style={{ display: 'block' }}
      >
        {grid}
        <rect
          x={X(sel) - 26}
          y={padT}
          width="52"
          height={h - padB - padT}
          fill={INDIGO}
          opacity="0.06"
        />
        {band ? (
          <polygon points={band} fill={AMBER} fillOpacity="0.12" />
        ) : null}
        <polyline
          points={poly(cp)}
          fill="none"
          stroke={COMP}
          strokeWidth="2.2"
        />
        <polyline
          points={poly(srr)}
          fill="none"
          stroke={AMBER}
          strokeWidth="2.6"
          strokeDasharray="6 4"
        />
        <polyline
          points={poly(iv)}
          fill="none"
          stroke={INDIGO}
          strokeWidth="3"
        />
        {rr.map(function (r, i) {
          return (
            <g key={i}>
              <text
                x={X(i)}
                y={h - 14}
                fill={i === sel ? INDIGO : MUT}
                fontWeight={i === sel ? '700' : '400'}
                fontSize="10"
                fontFamily={mono}
                textAnchor="middle"
              >
                {'R' + r}
              </text>
              <text
                x={X(i)}
                y={h - 3}
                fill="#94a3b8"
                fontSize="8.5"
                textAnchor="middle"
              >
                {(rounds[i] || {}).treatment_ward || ''}
              </text>
              <rect
                x={X(i) - 26}
                y={padT}
                width="52"
                height={h - padB - padT}
                fill="transparent"
                style={{ cursor: 'pointer' }}
                onClick={(function (idx) {
                  return function () {
                    setSel(idx);
                  };
                })(i)}
              />
            </g>
          );
        })}
        {endLabel(srr, AMBER, 'self-reported')}
        {endLabel(iv, INDIGO, 'verified')}
        {endLabel(cp, COMP, 'comparison')}
      </svg>
    );
  }

  // ---- minimized dumbbell: a slim self↔verified bar (no labels — numbers above) ----
  function slimDumbbell() {
    var W = 520,
      H = 34,
      padL = 10,
      padR = 10,
      dLo = 40,
      dHi = 100,
      yT = 16;
    if (self_ == null || ver == null) return null;
    function X(v) {
      return (
        padL +
        ((Math.max(dLo, Math.min(dHi, v)) - dLo) / (dHi - dLo)) *
          (W - padL - padR)
      );
    }
    var xs = X(self_),
      xv = X(ver),
      ci = indCI || 0,
      cl = X(Math.max(dLo, ver - ci)),
      ch = X(Math.min(dHi, ver + ci));
    return (
      <svg
        width="100%"
        viewBox={'0 0 ' + W + ' ' + H}
        style={{ display: 'block', maxWidth: 560 }}
      >
        <line x1={padL} y1={yT} x2={W - padR} y2={yT} stroke={LINE} />
        <line x1={xv} y1={yT} x2={xs} y2={yT} stroke={COMP} strokeWidth="3" />
        <rect
          x={cl}
          y={yT - 5}
          width={ch - cl}
          height="10"
          rx="3"
          fill={INDIGO}
          opacity="0.18"
        />
        <circle
          cx={xv}
          cy={yT}
          r="6"
          fill={INDIGO}
          stroke="#fff"
          strokeWidth="2"
        />
        <circle
          cx={xs}
          cy={yT}
          r="6"
          fill={AMBER}
          stroke="#fff"
          strokeWidth="2"
        />
        <text x={padL} y={H - 2} fill={MUT} fontSize="8" fontFamily={mono}>
          40%
        </text>
        <text
          x={W - padR}
          y={H - 2}
          fill={MUT}
          fontSize="8"
          fontFamily={mono}
          textAnchor="end"
        >
          100%
        </text>
      </svg>
    );
  }

  // ---- shared small label ----
  function dlbl(t) {
    return (
      <div
        style={{
          color: MUT,
          fontSize: 11,
          textTransform: 'uppercase',
          letterSpacing: '.04em',
        }}
      >
        {t}
      </div>
    );
  }

  // ---- per-surveyor quality scorecard ----
  // One row per program-ward surveyor; KPI columns computed by
  // commcare_connect.labs.survey_quality over THAT surveyor's records
  // (their primaries + the back-checks of their work). Cells turn rose when
  // they fall below the column threshold; a surveyor whose integrity signals
  // fail together is tagged REVIEW.
  function scorecardTable() {
    var rows = rd.surveyor_scorecard || [];
    if (!rows.length) return null;
    // [key, label, threshold, lowerIsBetter, isCount]
    var COLS = [
      ['evidence', 'Evidence', 90, false, false],
      ['gps', 'GPS ≤15m', 90, false, false],
      ['completeness', 'Complete', 98, false, false],
      ['duration', 'Duration', 90, false, false],
      ['consistency', 'Consistency', 98, false, false],
      ['duplicates', 'Dupes', 0, true, true],
      ['backcheck', 'Back-check', 90, false, false],
    ];
    function fail(v, thr, lower) {
      if (v == null) return false;
      return lower ? v > thr : v < thr;
    }
    function cellTxt(row, c) {
      var v = row[c[0]];
      if (v == null) return '—';
      if (c[4]) return String(v);
      var s = v.toFixed(1) + '%';
      if (c[0] === 'backcheck' && row.backcheck_n != null)
        s += ' ·' + row.backcheck_n;
      return s;
    }
    // flag a surveyor for review when >=2 integrity signals fail together
    function rowFlagged(row) {
      var n = 0;
      if (fail(row.evidence, 90, false)) n++;
      if (fail(row.gps, 90, false)) n++;
      if (fail(row.backcheck, 90, false)) n++;
      return n >= 2;
    }
    var th = {
      textAlign: 'right',
      color: MUT,
      fontSize: 10,
      textTransform: 'uppercase',
      letterSpacing: '.04em',
      padding: '7px 10px',
      borderBottom: '1px solid ' + LINE,
      whiteSpace: 'nowrap',
    };
    var th0 = Object.assign({}, th, { textAlign: 'left' });
    var td = {
      textAlign: 'right',
      padding: '7px 10px',
      fontSize: 12.5,
      fontFamily: mono,
      borderBottom: '1px solid ' + LINE,
    };
    var td0 = Object.assign({}, td, {
      textAlign: 'left',
      fontFamily: 'inherit',
    });
    var agg = {
      surveyor: '__agg__',
      n: indN,
      evidence: q.evidence_capture && q.evidence_capture.value,
      gps: q.gps_within_15m && q.gps_within_15m.value,
      completeness: q.field_completeness && q.field_completeness.value,
      duration: q.duration_plausibility && q.duration_plausibility.value,
      consistency: q.consistency_pass && q.consistency_pass.value,
      duplicates:
        q.duplicate_integrity && q.duplicate_integrity.detail
          ? (q.duplicate_integrity.detail.dup_household_id || 0) +
            (q.duplicate_integrity.detail.dup_gps_time || 0)
          : 0,
      backcheck: bc.outcome_agreement_pct,
      backcheck_n: bc.n_backchecked,
    };
    function dataRow(row, isAgg) {
      var fl = !isAgg && rowFlagged(row);
      return (
        <tr
          key={row.surveyor}
          style={{
            background: isAgg ? '#f8fafc' : fl ? '#fff1f2' : 'transparent',
          }}
        >
          <td
            style={Object.assign({}, td0, {
              fontWeight: isAgg ? 700 : 600,
              color: SUBINK,
            })}
          >
            {isAgg ? 'Round · all surveyors' : 'Surveyor ' + row.surveyor}
            {fl ? (
              <span
                style={{
                  marginLeft: 7,
                  fontSize: 10,
                  color: ROSE,
                  fontFamily: mono,
                  fontWeight: 700,
                  letterSpacing: '.04em',
                }}
              >
                REVIEW
              </span>
            ) : null}
          </td>
          <td style={Object.assign({}, td, { color: MUT })}>{row.n}</td>
          {COLS.map(function (c) {
            var v = row[c[0]];
            var bad = fail(v, c[2], c[3]);
            return (
              <td
                key={c[0]}
                style={Object.assign({}, td, {
                  color: v == null ? MUT : bad ? ROSE : GREEN,
                  fontWeight: bad ? 700 : 500,
                })}
              >
                {cellTxt(row, c)}
              </td>
            );
          })}
        </tr>
      );
    }
    return (
      <div style={{ overflowX: 'auto' }}>
        <table
          style={{ borderCollapse: 'collapse', width: '100%', minWidth: 660 }}
        >
          <thead>
            <tr>
              <th style={th0}>Surveyor</th>
              <th style={th}>n</th>
              {COLS.map(function (c) {
                return (
                  <th key={c[0]} style={th}>
                    {c[1]}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map(function (row) {
              return dataRow(row, false);
            })}
            {dataRow(agg, true)}
          </tbody>
        </table>
      </div>
    );
  }

  function bcTable() {
    var rows = (bc.rows || []).slice(0, 8);
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
          style={{ borderCollapse: 'collapse', width: '100%', minWidth: 560 }}
        >
          <thead>
            <tr>
              <th style={th}>Household · original → back-check surveyor</th>
              {cols.map(function (c) {
                return (
                  <th key={c[0]} style={th}>
                    {c[1]}
                  </th>
                );
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
                    var ch = !f.match;
                    return (
                      <td key={c[0]} style={td}>
                        <div style={{ color: SUBINK }}>{yn(f.original)}</div>
                        <div
                          style={{
                            color: ch ? ROSE : MUT,
                            fontWeight: ch ? 700 : 400,
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
      </div>
    );
  }

  function backcheckSection() {
    return (
      <div>
        <div
          style={{
            color: SUBINK,
            fontWeight: 600,
            fontSize: 13,
            marginBottom: 8,
          }}
        >
          Independent back-check &mdash; a stratified sample of this
          cycle&rsquo;s households re-surveyed by a different surveyor. The
          re-survey matched the original vitamin-A result on{' '}
          <b>
            {bc.outcome_agreement_pct != null && bc.n_backchecked != null
              ? Math.round((bc.outcome_agreement_pct / 100) * bc.n_backchecked)
              : '\u2014'}
          </b>{' '}
          of {bc.n_backchecked} re-surveyed households.
        </div>
        <div
          style={{
            display: 'flex',
            gap: 22,
            flexWrap: 'wrap',
            fontFamily: mono,
            marginBottom: 10,
          }}
        >
          {[
            [
              'sample re-surveyed',
              bc.coverage_pct + '% \u00b7 n=' + bc.n_backchecked,
            ],
            ['outcome agreement', bc.outcome_agreement_pct + '%'],
            [
              'identity match',
              bc.type1_error_pct == null
                ? '\u2014'
                : (100 - bc.type1_error_pct).toFixed(1) + '%',
            ],
            ['re-survey vs original', 'p=' + bc.prtest_p],
          ].map(function (s) {
            return (
              <div key={s[0]}>
                {dlbl(s[0])}
                <div style={{ color: SUBINK, fontSize: 16, fontWeight: 700 }}>
                  {s[1]}
                </div>
              </div>
            );
          })}
        </div>
        {bcTable()}
        <div
          style={{
            display: 'flex',
            gap: 16,
            marginTop: 8,
            fontSize: 11,
            color: MUT,
            fontFamily: mono,
            flexWrap: 'wrap',
          }}
        >
          <span>
            <span style={{ color: SUBINK }}>value</span> = original / re-survey
            agree
          </span>
          <span>
            <span style={{ color: ROSE, fontWeight: 700 }}>red {'\u2192'}</span>{' '}
            = changed on re-survey
          </span>
          <span>
            each cell: original / re-survey {'\u00b7'} surveyor original (T){' '}
            {'\u2192'} back-check (BC)
          </span>
        </div>
      </div>
    );
  }

  function roundTabs() {
    return (
      <div
        style={{
          display: 'flex',
          gap: 6,
          flexWrap: 'wrap',
          marginTop: 12,
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
          Cycle
        </span>
        {rounds.map(function (r, i) {
          var on = i === sel;
          return (
            <button
              key={i}
              onClick={function () {
                setSel(i);
              }}
              title={r.treatment_ward + ' vs ' + r.comparison_ward}
              style={{
                cursor: 'pointer',
                fontFamily: mono,
                fontSize: 13,
                padding: '5px 12px',
                borderRadius: 8,
                border: '1px solid ' + (on ? INDIGO : LINE),
                background: on ? INDIGO : '#fff',
                color: on ? '#fff' : SUBINK,
                fontWeight: on ? 700 : 600,
              }}
            >
              {'R' + r.round}
            </button>
          );
        })}
        <span style={{ color: MUT, fontSize: 13, marginLeft: 6 }}>
          {rd.label} · <b style={{ color: INDIGO }}>{tWard}</b> vs{' '}
          <b style={{ color: COMP }}>{cWard}</b>
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
      <div style={{ fontSize: 18, fontWeight: 700 }}>
        {prog.name || 'Verified Monitoring'}
      </div>
      <div style={{ color: MUT, fontSize: 13, marginTop: 4, lineHeight: 1.5 }}>
        Independent rooftop survey · {prog.cadence || 'bi-monthly'} · the
        program rotates wards each cycle, each verified against an adjacent
        comparison ward
      </div>

      {/* PAGE HERO — the six-cycle trend, edge-to-edge */}
      <div
        style={Object.assign(
          { marginTop: 12, padding: '14px 16px' },
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
          Self-reported vs independently verified — all{' '}
          {(trend.rounds || []).length} cycles
        </div>
        <div style={{ marginTop: 8 }}>{trendChart()}</div>
        <div
          style={{
            display: 'flex',
            gap: 16,
            flexWrap: 'wrap',
            fontSize: 11,
            color: SUBINK,
            marginTop: 2,
          }}
        >
          <span>{sw(AMBER, true)}self-reported (program ward)</span>
          <span>{sw(INDIGO)}independently verified (program ward)</span>
          <span>{sw(COMP)}verified (comparison ward)</span>
          <span style={{ color: '#94a3b8' }}>
            · shaded = self-reported − verified · click a cycle to open it
          </span>
        </div>
        <div
          style={{ fontSize: 15, fontWeight: 600, color: SUBINK, marginTop: 8 }}
        >
          Across all {(trend.rounds || []).length} cycles, self-report sits
          above the independent survey — {(trend.rounds || []).length} different
          program wards, comparison wards stay low.
        </div>
      </div>

      {roundTabs()}

      {/* per-cycle: compact readout + the moving map */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 0.9fr',
          gap: 16,
          marginTop: 14,
          alignItems: 'start',
        }}
      >
        <div style={Object.assign({ padding: '16px 18px' }, cardStyle)}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              flexWrap: 'wrap',
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
              Self-reported vs independently verified · {tWard} · R{rd.round}
            </div>
            <div style={{ color: MUT, fontSize: 11, fontFamily: mono }}>
              as of {rd.label}
            </div>
          </div>
          <div
            style={{
              display: 'flex',
              gap: 26,
              flexWrap: 'wrap',
              alignItems: 'baseline',
              marginTop: 8,
            }}
          >
            <div>
              <div
                style={{
                  color: AMBER,
                  fontFamily: mono,
                  fontSize: 26,
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
                  fontSize: 26,
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
                  fontSize: 26,
                  fontWeight: 800,
                }}
              >
                {pp(prem)}
              </div>
              <div style={{ color: MUT, fontSize: 11 }}>
                difference
                {indCI != null ? ' · 95% CI ±' + indCI.toFixed(1) : ''}
              </div>
            </div>
          </div>
          <div style={{ marginTop: 10 }}>{slimDumbbell()}</div>
          <div
            style={{
              color: MUT,
              fontSize: 11,
              marginTop: 8,
              lineHeight: 1.6,
              fontFamily: mono,
            }}
          >
            both measure the same indicator — % of under-5 children with
            confirmed vitamin-A · self-reported = the program's coverage
            estimate ({sd[tWard] != null ? sd[tWard].toLocaleString() : '—'}{' '}
            children visited) · verified = survey of n={indN} children
          </div>
        </div>
        <div style={Object.assign({ padding: 12 }, cardStyle)}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 6,
              flexWrap: 'wrap',
              gap: 6,
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
              Map · {tWard} vs {cWard}
            </div>
            <div
              style={{
                display: 'flex',
                gap: 12,
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
                delivery
              </label>
              <label style={{ color: INDIGO, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={pinsOn}
                  onChange={function (e) {
                    setPinsOn(e.target.checked);
                  }}
                />{' '}
                survey
              </label>
            </div>
          </div>
          <div
            ref={mapDivRef}
            style={{
              height: 300,
              borderRadius: 8,
              overflow: 'hidden',
              background: '#eef2f7',
              border: '1px solid ' + LINE,
            }}
          />
          {!mapLibReady ? (
            <div style={{ color: MUT, fontSize: 12, padding: 6 }}>
              loading map…
            </div>
          ) : null}
          <div
            style={{
              display: 'flex',
              gap: 12,
              marginTop: 6,
              fontSize: 10.5,
              color: MUT,
              fontFamily: mono,
              flexWrap: 'wrap',
            }}
          >
            <span>
              {sw(INDIGO)}
              {tWard}
            </span>
            <span>
              {sw(COMP)}
              {cWard}
            </span>
            <span>
              <span style={{ color: '#16a34a' }}>●</span> delivery
            </span>
            <span>
              <span style={{ color: INDIGO }}>●</span> confirmed
            </span>
            <span>
              <span style={{ color: SLATE }}>●</span> not
            </span>
          </div>
        </div>
      </div>

      {/* PER-SURVEYOR SCORECARD — one row per program-ward surveyor, KPI columns */}
      <div style={Object.assign({ marginTop: 16, padding: 14 }, cardStyle)}>
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
            marginBottom: 10,
          }}
        >
          Survey-quality scorecard · {tWard} · R{rd.round} — one row per
          surveyor
        </div>
        {scorecardTable()}
        <div
          style={{
            display: 'flex',
            gap: 16,
            marginTop: 10,
            fontSize: 11,
            color: MUT,
            fontFamily: mono,
            flexWrap: 'wrap',
          }}
        >
          <span>
            <span style={{ color: GREEN, fontWeight: 700 }}>green</span> =
            within threshold
          </span>
          <span>
            <span style={{ color: ROSE, fontWeight: 700 }}>rose</span> = below
            threshold
          </span>
          <span>
            back-check cell shows agreement % ·n re-surveyed for that surveyor
          </span>
        </div>
      </div>

      {/* INDEPENDENT BACK-CHECK — household side-by-side, original vs re-survey */}
      <div style={Object.assign({ marginTop: 16, padding: 14 }, cardStyle)}>
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
            marginBottom: 10,
          }}
        >
          Independent back-check · {tWard} · R{rd.round}
        </div>
        {backcheckSection()}
      </div>
    </div>
  );
}
