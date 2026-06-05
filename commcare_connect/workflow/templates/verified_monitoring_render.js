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
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V30
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
  // selected surveyor (drives the back-check section); null = round-level view
  var [selSurv, setSelSurv] = React.useState(null);
  // hovered trend point (for the tooltip) and selected back-check type drill
  var [hoverPt, setHoverPt] = React.useState(null);
  var [bcType, setBcType] = React.useState('type1');

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
    var SERIES = [
      { arr: cp, color: COMP, label: 'comparison' },
      { arr: srr, color: AMBER, label: 'self-reported' },
      { arr: iv, color: INDIGO, label: 'verified' },
    ];
    function markers() {
      return SERIES.map(function (s) {
        return s.arr.map(function (v, i) {
          var on = hoverPt && hoverPt.label === s.label && hoverPt.i === i;
          return (
            <g key={s.label + i}>
              <circle
                cx={X(i)}
                cy={Y(v)}
                r={on ? 5 : 3.2}
                fill={s.color}
                stroke="#fff"
                strokeWidth="1.5"
              />
              <circle
                cx={X(i)}
                cy={Y(v)}
                r="11"
                fill="transparent"
                style={{ cursor: 'pointer' }}
                onMouseEnter={(function (px, py, val, col, lab, idx, rnd) {
                  return function () {
                    setHoverPt({
                      x: px,
                      y: py,
                      v: val,
                      color: col,
                      label: lab,
                      i: idx,
                      r: rnd,
                    });
                  };
                })(X(i), Y(v), v, s.color, s.label, i, rr[i])}
                onMouseLeave={function () {
                  setHoverPt(null);
                }}
                onClick={(function (idx) {
                  return function () {
                    setSel(idx);
                  };
                })(i)}
              />
            </g>
          );
        });
      });
    }
    function tip() {
      if (!hoverPt) return null;
      var tw = 150,
        th = 22;
      var tx = Math.max(2, Math.min(w - tw - 2, hoverPt.x - tw / 2));
      var ty = hoverPt.y - th - 9;
      if (ty < 2) ty = hoverPt.y + 11;
      return (
        <g pointerEvents="none">
          <rect
            x={tx}
            y={ty}
            width={tw}
            height={th}
            rx="4"
            fill="#0f172a"
            opacity="0.93"
          />
          <circle cx={tx + 12} cy={ty + th / 2} r="3.5" fill={hoverPt.color} />
          <text
            x={tx + 21}
            y={ty + 15}
            fill="#fff"
            fontSize="11"
            fontFamily={mono}
          >
            {hoverPt.label + ' · R' + hoverPt.r + ' · ' + pct(hoverPt.v)}
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
        {markers()}
        {endLabel(srr, AMBER, 'self-reported')}
        {endLabel(iv, INDIGO, 'verified')}
        {endLabel(cp, COMP, 'comparison')}
        {tip()}
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
    // Back-check is a cumulative signal — a single cycle's per-surveyor sample
    // is too small. Show each surveyor's all-cycles outcome agreement (the exact
    // number the drill-in headlines as Type 3), so column and drill-in match.
    var sbMap = data.surveyor_backcheck || {};
    rows = rows.map(function (r) {
      var sb = sbMap[r.surveyor];
      return sb
        ? Object.assign({}, r, { backcheck: sb.type3_pct, backcheck_n: sb.n })
        : r;
    });
    var sbVals = Object.keys(sbMap).map(function (k) {
      return sbMap[k];
    });
    var aggBcN = sbVals.reduce(function (a, s) {
      return a + (s.n || 0);
    }, 0);
    var aggBc = aggBcN
      ? sbVals.reduce(function (a, s) {
          return a + (s.type3_pct || 0) * (s.n || 0);
        }, 0) / aggBcN
      : null;
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
      backcheck: aggBc,
      backcheck_n: aggBcN,
    };
    function dataRow(row, isAgg) {
      var fl = !isAgg && rowFlagged(row);
      var on = !isAgg && selSurv === row.surveyor;
      return (
        <tr
          key={row.surveyor}
          onClick={
            isAgg
              ? null
              : function () {
                  setSelSurv(selSurv === row.surveyor ? null : row.surveyor);
                }
          }
          title={isAgg ? null : 'View ' + row.surveyor + "'s back-check"}
          style={{
            cursor: isAgg ? 'default' : 'pointer',
            background: on
              ? '#eef2ff'
              : isAgg
              ? '#f8fafc'
              : fl
              ? '#fff1f2'
              : 'transparent',
            boxShadow: on ? 'inset 3px 0 0 ' + INDIGO : 'none',
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
                    {c[0] === 'backcheck' ? (
                      <div
                        style={{
                          fontSize: 8.5,
                          color: '#94a3b8',
                          fontWeight: 400,
                          textTransform: 'none',
                          letterSpacing: 0,
                        }}
                      >
                        all cycles
                      </div>
                    ) : null}
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

  function bcTable(rowsIn) {
    var rows = (rowsIn || bc.rows || []).slice(0, 8);
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

  function bcLegend() {
    return (
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
          <span style={{ color: ROSE, fontWeight: 700 }}>red {'\u2192'}</span> =
          changed on re-survey
        </span>
        <span>
          each cell: original / re-survey {'\u00b7'} surveyor original (T){' '}
          {'\u2192'} back-check (BC)
        </span>
      </div>
    );
  }

  // The three J-PAL back-check types \u2014 what each checks + how to read it.
  function bcTypeDefs(sb) {
    return [
      {
        key: 'type1',
        label: 'Type 1 \u00b7 Identity',
        v: sb.type1_pct,
        mean: "Stable facts that can't change between two visits \u2014 the child's sex, age, and whether the household exists.",
        read: 'A mismatch is the fabrication signal: the visit may not have happened as recorded.',
      },
      {
        key: 'type2',
        label: 'Type 2 \u00b7 Location',
        v: sb.type2_pct,
        mean: 'How far the independent re-survey landed from the household the surveyor logged.',
        read:
          'A gap beyond ' +
          (sb.t2_thresh_m || 25) +
          ' m (rose) means the recorded GPS location was wrong.',
      },
      {
        key: 'type3',
        label: 'Type 3 \u00b7 Outcome',
        v: sb.type3_pct,
        mean: 'Did the headline result reproduce \u2014 did the re-survey find the same vitamin-A status.',
        read: 'A mismatch (rose) means the coverage this surveyor reported did not hold up.',
      },
    ];
  }

  // clickable type cards: value + plain-language meaning; selected one drills in
  function bcTypeCards(sb) {
    return (
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit,minmax(230px,1fr))',
          gap: 10,
          margin: '6px 0 14px',
        }}
      >
        {bcTypeDefs(sb).map(function (d) {
          var on = bcType === d.key;
          var ok = d.v == null || d.v >= 90;
          return (
            <div
              key={d.key}
              onClick={function () {
                setBcType(d.key);
              }}
              style={{
                cursor: 'pointer',
                padding: '11px 13px',
                borderRadius: 10,
                border: '1px solid ' + (on ? INDIGO : LINE),
                background: on ? '#f5f6ff' : '#fff',
                boxShadow: on ? '0 0 0 1px ' + INDIGO + ' inset' : SHADOW,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'baseline',
                }}
              >
                <span
                  style={{ color: SUBINK, fontWeight: 700, fontSize: 12.5 }}
                >
                  {d.label}
                </span>
                <span
                  style={{
                    color: d.v == null ? MUT : ok ? GREEN : ROSE,
                    fontFamily: mono,
                    fontWeight: 800,
                    fontSize: 17,
                  }}
                >
                  {d.v == null ? '\u2014' : d.v.toFixed(1) + '%'}
                </span>
              </div>
              <div
                style={{
                  color: MUT,
                  fontSize: 11,
                  marginTop: 4,
                  lineHeight: 1.4,
                }}
              >
                {d.mean}
              </div>
              <div
                style={{
                  color: on ? INDIGO : '#94a3b8',
                  fontSize: 10.5,
                  marginTop: 6,
                  fontWeight: 600,
                }}
              >
                {on ? 'households below \u25be' : 'click to drill in'}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // per-type household evidence: Original vs Backcheck, side by side
  function bcEvidence(rows, typeKey, t2thr) {
    rows = (rows || []).slice(0, 8);
    if (!rows.length) return null;
    var th = {
      textAlign: 'left',
      color: MUT,
      fontSize: 10,
      textTransform: 'uppercase',
      letterSpacing: '.04em',
      padding: '6px 9px',
      borderBottom: '1px solid ' + LINE,
    };
    var td = {
      padding: '6px 9px',
      fontSize: 12.5,
      fontFamily: mono,
      verticalAlign: 'middle',
    };
    var hhTd = Object.assign({}, td, {
      fontFamily: 'inherit',
      fontWeight: 600,
      color: SUBINK,
      borderTop: '1px solid ' + LINE,
    });
    var sideTd = Object.assign({}, td, { color: MUT, whiteSpace: 'nowrap' });

    // Type 2: one row per household \u2014 the re-survey distance
    if (typeKey === 'type2') {
      var thr = t2thr || 25;
      return (
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{ borderCollapse: 'collapse', width: '100%', minWidth: 520 }}
          >
            <thead>
              <tr>
                <th style={th}>Household</th>
                <th style={th}>Original \u2192 Backcheck</th>
                <th style={Object.assign({}, th, { textAlign: 'right' })}>
                  Re-survey distance
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map(function (row, ri) {
                var dm = row.gps_delta_m;
                var bad = dm != null && dm > thr;
                var frac = Math.min(1, (dm || 0) / 60);
                return (
                  <tr key={ri} style={{ borderTop: '1px solid ' + LINE }}>
                    <td style={hhTd}>{row.household_id}</td>
                    <td style={sideTd}>
                      Original ({row.enumerator}) \u2192 Backcheck (
                      {row.backcheck_enumerator})
                    </td>
                    <td style={Object.assign({}, td, { textAlign: 'right' })}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          justifyContent: 'flex-end',
                        }}
                      >
                        <div
                          style={{
                            width: 90,
                            height: 7,
                            borderRadius: 4,
                            background: '#eef2f7',
                            overflow: 'hidden',
                          }}
                        >
                          <div
                            style={{
                              height: '100%',
                              width: frac * 100 + '%',
                              background: bad ? ROSE : INDIGO,
                            }}
                          />
                        </div>
                        <span
                          style={{
                            color: bad ? ROSE : SUBINK,
                            fontWeight: bad ? 700 : 500,
                            minWidth: 42,
                          }}
                        >
                          {dm == null ? '\u2014' : dm.toFixed(0) + ' m'}
                        </span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      );
    }

    // Type 1 / Type 3: field comparison, two sub-rows per household
    var FIELDS = {
      type1: [
        ['child_present', 'Present'],
        ['child_sex', 'Sex'],
        ['child_age_months', 'Age (mo)'],
      ],
      type3: [['vitamin_a_received', 'Vitamin-A received']],
    };
    var cols = FIELDS[typeKey] || [];
    function fieldOf(row, key) {
      var fs = row.fields || [];
      for (var i = 0; i < fs.length; i++) if (fs[i].key === key) return fs[i];
      return null;
    }
    return (
      <div style={{ overflowX: 'auto' }}>
        <table
          style={{ borderCollapse: 'collapse', width: '100%', minWidth: 520 }}
        >
          <thead>
            <tr>
              <th style={th}>Household</th>
              <th style={th} />
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
              return [
                <tr key={ri + 'o'}>
                  <td rowSpan={2} style={hhTd}>
                    {row.household_id}
                  </td>
                  <td
                    style={Object.assign({}, sideTd, {
                      borderTop: '1px solid ' + LINE,
                    })}
                  >
                    Original ({row.enumerator})
                  </td>
                  {cols.map(function (c) {
                    var f = fieldOf(row, c[0]);
                    return (
                      <td
                        key={c[0]}
                        style={Object.assign({}, td, {
                          color: SUBINK,
                          borderTop: '1px solid ' + LINE,
                        })}
                      >
                        {f ? yn(f.original) : '\u2014'}
                      </td>
                    );
                  })}
                </tr>,
                <tr key={ri + 'b'}>
                  <td style={sideTd}>Backcheck ({row.backcheck_enumerator})</td>
                  {cols.map(function (c) {
                    var f = fieldOf(row, c[0]);
                    var ch = f && !f.match;
                    return (
                      <td
                        key={c[0]}
                        style={Object.assign({}, td, {
                          color: ch ? ROSE : MUT,
                          fontWeight: ch ? 700 : 400,
                        })}
                      >
                        {f ? yn(f.backcheck) : '\u2014'}
                      </td>
                    );
                  })}
                </tr>,
              ];
            })}
          </tbody>
        </table>
      </div>
    );
  }

  // round-level view (default): the cycle's aggregate back-check
  function roundBackcheck() {
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
          A stratified sample of this cycle&rsquo;s households re-surveyed by a
          different surveyor. The re-survey matched the original vitamin-A
          result on{' '}
          <b>
            {bc.outcome_agreement_pct != null && bc.n_backchecked != null
              ? Math.round((bc.outcome_agreement_pct / 100) * bc.n_backchecked)
              : '\u2014'}
          </b>{' '}
          of {bc.n_backchecked} re-surveyed households.{' '}
          <span style={{ color: MUT, fontWeight: 400 }}>
            Click a surveyor in the scorecard for their Type 1/2/3 back-check.
          </span>
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
        {bcLegend()}
      </div>
    );
  }

  // surveyor view: one surveyor's cumulative back-check across all cycles,
  // broken out by the three J-PAL types (the single-cycle sample is too small).
  // Pick a type to drill into its households (Original vs Backcheck).
  function surveyorBackcheck(sid, sb) {
    var cur =
      bcTypeDefs(sb).filter(function (d) {
        return d.key === bcType;
      })[0] || bcTypeDefs(sb)[0];
    return (
      <div>
        <div
          style={{
            color: SUBINK,
            fontWeight: 700,
            fontSize: 13,
            marginBottom: 2,
          }}
        >
          Surveyor {sid} {'\u00b7'} {sb.n} households independently re-surveyed
          across all cycles
        </div>
        <div style={{ color: MUT, fontSize: 11.5, marginBottom: 4 }}>
          Each % is the share of {sid}&rsquo;s re-surveyed households that
          agreed with the independent check. Pick a type to see the households.
        </div>
        {bcTypeCards(sb)}
        <div
          style={{
            color: SUBINK,
            fontSize: 12.5,
            margin: '0 0 8px',
            paddingLeft: 9,
            borderLeft: '3px solid ' + INDIGO,
          }}
        >
          <b>{cur.label}.</b> {cur.read}
        </div>
        {bcEvidence(sb.rows, bcType, sb.t2_thresh_m)}
        <div
          style={{
            marginTop: 8,
            fontSize: 11,
            color: MUT,
            fontFamily: mono,
          }}
        >
          <span style={{ color: ROSE, fontWeight: 700 }}>rose</span> = the
          re-survey disagreed with what {sid} recorded
        </div>
      </div>
    );
  }

  function backcheckSection() {
    var sbMap = data.surveyor_backcheck || {};
    var sb = selSurv ? sbMap[selSurv] : null;
    return sb ? surveyorBackcheck(selSurv, sb) : roundBackcheck();
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
            quality columns = this cycle · back-check = all cycles (small
            per-cycle sample)
          </span>
          <span style={{ color: INDIGO }}>
            click a surveyor → back-check below
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
          Independent back-check ·{' '}
          {selSurv ? 'Surveyor ' + selSurv : tWard + ' · R' + rd.round}
        </div>
        {backcheckSection()}
      </div>
    </div>
  );
}
