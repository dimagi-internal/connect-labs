// Verified Monitoring (N1) — funder-facing verified-coverage dashboard.
// Self-contained: reads everything from instance.state (seeded by survey_sim;
// every KPI computed from row-level records via the survey_quality library) and
// never fetches. Light, Connect-aligned styling.
//
// Layout: the six-cycle TREND is the page hero (edge-to-edge, top; hover a point
// for its value); a cycle selector pivots the page; per cycle a full-width map
// that moves to that cycle's two real wards; a per-surveyor survey-quality
// scorecard; and an independent back-check that opens on one surveyor (click a
// scorecard row to switch) — one row per re-surveyed household, columns grouped
// under Identity / Location / Outcome sections with info buttons (method +
// source). Objective copy; the viewer draws the conclusion.
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V34
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
  // hovered trend point (for the tooltip) and which back-check section's info is open
  var [hoverPt, setHoverPt] = React.useState(null);
  var [bcInfo, setBcInfo] = React.useState(null);

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
  // The back-check always opens on a surveyor — default to the one whose work
  // most needs review (lowest outcome agreement); clicking a scorecard row
  // selects a different one. No confusing round-level mode.
  var sbMap = data.surveyor_backcheck || {};
  var bcIds = Object.keys(sbMap);
  function _t3(k) {
    return sbMap[k] && sbMap[k].type3_pct != null ? sbMap[k].type3_pct : 100;
  }
  var effSurv =
    selSurv && sbMap[selSurv]
      ? selSurv
      : bcIds.length
      ? bcIds.reduce(function (a, b) {
          return _t3(b) < _t3(a) ? b : a;
        }, bcIds[0])
      : null;

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
      { arr: cp, color: COMP, label: 'control arm survey' },
      { arr: srr, color: AMBER, label: 'service-delivery data' },
      { arr: iv, color: INDIGO, label: 'intervention arm survey' },
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
      var label = hoverPt.label + ' · R' + hoverPt.r + ' · ' + pct(hoverPt.v);
      var fs = 8.5,
        th = 16,
        textX = 16; // left pad: dot + gap
      // size the box to the text so white text never spills past the dark fill
      var tw = textX + label.length * fs * 0.6 + 8;
      var tx = Math.max(2, Math.min(w - tw - 2, hoverPt.x - tw / 2));
      var ty = hoverPt.y - th - 8;
      if (ty < 2) ty = hoverPt.y + 10;
      return (
        <g pointerEvents="none">
          <rect
            x={tx}
            y={ty}
            width={tw}
            height={th}
            rx="3"
            fill="#0f172a"
            opacity="0.93"
          />
          <circle cx={tx + 8} cy={ty + th / 2} r="2.8" fill={hoverPt.color} />
          <text
            x={tx + textX}
            y={ty + th / 2 + 3}
            fill="#fff"
            fontSize={fs}
            fontFamily={mono}
          >
            {label}
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
        {endLabel(srr, AMBER, 'service-delivery')}
        {endLabel(iv, INDIGO, 'intervention')}
        {endLabel(cp, COMP, 'control')}
        {tip()}
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
      var on = !isAgg && effSurv === row.surveyor;
      return (
        <tr
          key={row.surveyor}
          onClick={
            isAgg
              ? null
              : function () {
                  setSelSurv(row.surveyor);
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

  // Back-check sections — descriptive names up front (the J-PAL/IPA
  // "Type 1/2/3" labels are specialist jargon, so the provenance lives behind
  // an info button instead of in the column header).
  function bcSections(sb) {
    return [
      {
        key: 'identity',
        label: 'Identity match',
        pct: sb.type1_pct,
        thr: 90,
        fields: [
          ['child_present', 'Present'],
          ['child_sex', 'Sex'],
          ['child_age_months', 'Age'],
        ],
        info: "Stable facts that can't change between two visits — the child's sex, age, and whether the household exists. Disagreement here is the strongest fabrication signal. In J-PAL/IPA back-check terms these are “Type 1” variables: a difference can trigger action against the surveyor.",
      },
      {
        key: 'location',
        label: 'Location check',
        pct: sb.type2_pct,
        thr: 90,
        mode: 'distance',
        info: 'Distance between the GPS the surveyor logged and where the independent re-survey found the household. A large gap means the recorded location was wrong — a fraud-detection back-check.',
      },
      {
        key: 'outcome',
        label: 'Outcome agreement',
        pct: sb.type3_pct,
        thr: 90,
        fields: [['vitamin_a_received', 'Vitamin-A']],
        info: 'Whether the headline result — did the child receive vitamin A — reproduced on the independent re-survey. In J-PAL/IPA back-check terms this is a “Type 3” variable: the key outcome whose stability is of interest.',
      },
    ];
  }

  // surveyor view: ONE row per re-surveyed household; columns grouped under the
  // three back-check sections, each with an info button (method + source).
  function surveyorBackcheck(sid, sb) {
    var sections = bcSections(sb);
    var rows = (sb.rows || []).slice(0, 12);
    var thr = sb.t2_thresh_m || 25;
    function fieldOf(row, key) {
      var fs = row.fields || [];
      for (var i = 0; i < fs.length; i++) if (fs[i].key === key) return fs[i];
      return null;
    }
    var th = {
      color: MUT,
      fontSize: 10,
      textTransform: 'uppercase',
      letterSpacing: '.03em',
      padding: '5px 9px',
      textAlign: 'left',
      borderBottom: '1px solid ' + LINE,
      whiteSpace: 'nowrap',
    };
    var td = {
      padding: '6px 9px',
      fontSize: 12.5,
      fontFamily: mono,
      borderBottom: '1px solid ' + LINE,
      whiteSpace: 'nowrap',
    };
    var groupTh = {
      padding: '6px 9px 4px',
      borderBottom: '2px solid ' + LINE,
      borderLeft: '1px solid ' + LINE,
      textAlign: 'left',
      verticalAlign: 'bottom',
    };
    function ncols(s) {
      return s.mode === 'distance' ? 1 : s.fields.length;
    }
    function cmpCell(row, key, sectKey, first) {
      var f = fieldOf(row, key);
      var st = Object.assign(
        {},
        td,
        first ? { borderLeft: '1px solid ' + LINE } : {},
      );
      if (!f)
        return (
          <td key={sectKey + key} style={st}>
            —
          </td>
        );
      if (f.match)
        return (
          <td
            key={sectKey + key}
            style={Object.assign({}, st, { color: SUBINK })}
          >
            {yn(f.original)}
          </td>
        );
      return (
        <td
          key={sectKey + key}
          style={Object.assign({}, st, { color: ROSE, fontWeight: 700 })}
        >
          {yn(f.original)} {'→'} {yn(f.backcheck)}
        </td>
      );
    }
    function distCell(row) {
      var dm = row.gps_delta_m;
      var bad = dm != null && dm > thr;
      return (
        <td
          key="loc"
          style={Object.assign({}, td, {
            borderLeft: '1px solid ' + LINE,
            color: bad ? ROSE : SUBINK,
            fontWeight: bad ? 700 : 400,
          })}
        >
          {dm == null ? '—' : dm.toFixed(0) + ' m'}
        </td>
      );
    }
    function infoBtn(s) {
      var on = bcInfo === s.key;
      return (
        <button
          onClick={function () {
            setBcInfo(on ? null : s.key);
          }}
          title="What this checks + where it comes from"
          style={{
            cursor: 'pointer',
            marginLeft: 4,
            border: '1px solid ' + (on ? INDIGO : LINE),
            background: on ? INDIGO : '#fff',
            color: on ? '#fff' : MUT,
            borderRadius: 999,
            width: 16,
            height: 16,
            fontSize: 10,
            fontWeight: 800,
            lineHeight: '14px',
            padding: 0,
            fontFamily: sans,
          }}
        >
          i
        </button>
      );
    }
    var openSec = sections.filter(function (s) {
      return s.key === bcInfo;
    })[0];
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
          Surveyor {sid} {'·'} {sb.n} households independently re-surveyed
          across all cycles
        </div>
        <div style={{ color: MUT, fontSize: 11.5, marginBottom: 8 }}>
          One row per re-surveyed household (mismatches first, showing{' '}
          {Math.min(rows.length, sb.n)} of {sb.n}). Each section header shows
          the share that agreed with the independent check {'·'} tap{' '}
          <b style={{ fontFamily: mono }}>i</b> for what it means.
        </div>
        {openSec ? (
          <div
            style={{
              background: '#f5f6ff',
              border: '1px solid ' + LINE,
              borderRadius: 9,
              padding: '9px 12px',
              fontSize: 12,
              color: SUBINK,
              lineHeight: 1.5,
              marginBottom: 10,
            }}
          >
            <b>{openSec.label}.</b> {openSec.info}{' '}
            <span style={{ color: MUT }}>
              Method: independent back-checks per J-PAL/IPA (bcstats) and World
              Bank DIME {'—'}{' '}
              <a
                href="https://dimewiki.worldbank.org/Back_Checks"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: INDIGO }}
              >
                dimewiki.worldbank.org/Back_Checks
              </a>
              .
            </span>
          </div>
        ) : null}
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{
              borderCollapse: 'collapse',
              width: '100%',
              minWidth: 640,
            }}
          >
            <thead>
              <tr>
                <th
                  style={Object.assign({}, groupTh, { borderLeft: 'none' })}
                  colSpan={2}
                />
                {sections.map(function (s) {
                  var ok = s.pct == null || s.pct >= s.thr;
                  return (
                    <th key={s.key} style={groupTh} colSpan={ncols(s)}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 5,
                          flexWrap: 'wrap',
                        }}
                      >
                        <span
                          style={{
                            color: SUBINK,
                            fontWeight: 700,
                            fontSize: 12,
                          }}
                        >
                          {s.label}
                        </span>
                        <span
                          style={{
                            color: s.pct == null ? MUT : ok ? GREEN : ROSE,
                            fontFamily: mono,
                            fontWeight: 800,
                            fontSize: 12.5,
                          }}
                        >
                          {s.pct == null ? '—' : s.pct.toFixed(0) + '%'}
                        </span>
                        {infoBtn(s)}
                      </div>
                    </th>
                  );
                })}
              </tr>
              <tr>
                <th style={th}>Household</th>
                <th style={th}>Original {'→'} Re-survey</th>
                {sections.map(function (s) {
                  if (s.mode === 'distance')
                    return (
                      <th
                        key={s.key}
                        style={Object.assign({}, th, {
                          borderLeft: '1px solid ' + LINE,
                        })}
                      >
                        Distance
                      </th>
                    );
                  return s.fields.map(function (c, ci) {
                    return (
                      <th
                        key={s.key + c[0]}
                        style={Object.assign(
                          {},
                          th,
                          ci === 0 ? { borderLeft: '1px solid ' + LINE } : {},
                        )}
                      >
                        {c[1]}
                      </th>
                    );
                  });
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map(function (row, ri) {
                return (
                  <tr key={ri}>
                    <td
                      style={Object.assign({}, td, {
                        fontFamily: 'inherit',
                        fontWeight: 600,
                        color: SUBINK,
                      })}
                    >
                      {row.household_id}
                    </td>
                    <td style={Object.assign({}, td, { color: MUT })}>
                      {row.enumerator} {'→'} {row.backcheck_enumerator}
                    </td>
                    {sections.map(function (s) {
                      if (s.mode === 'distance') return distCell(row);
                      return s.fields.map(function (c, ci) {
                        return cmpCell(row, c[0], s.key, ci === 0);
                      });
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div
          style={{ marginTop: 8, fontSize: 11, color: MUT, fontFamily: mono }}
        >
          <span style={{ color: SUBINK }}>value</span> = original & re-survey
          agree {'·'}{' '}
          <span style={{ color: ROSE, fontWeight: 700 }}>
            orig {'→'} re-survey
          </span>{' '}
          = changed on re-survey
        </div>
      </div>
    );
  }

  function backcheckSection() {
    if (!effSurv || !sbMap[effSurv]) return null;
    return surveyorBackcheck(effSurv, sbMap[effSurv]);
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
        program rotates wards each cycle, each intervention ward verified
        against an adjacent control ward
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
          Service-delivery data vs independent survey — all{' '}
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
          <span>{sw(AMBER, true)}service-delivery data</span>
          <span>{sw(INDIGO)}intervention arm survey</span>
          <span>{sw(COMP)}control arm survey</span>
          <span style={{ color: '#94a3b8' }}>
            · shaded = service-delivery − intervention survey · click a cycle to
            open it
          </span>
        </div>
      </div>

      {roundTabs()}

      {/* per-cycle: the moving map (full width) */}
      <div style={{ marginTop: 14 }}>
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
              Map · {tWard} (intervention) vs {cWard} (control)
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
              height: 420,
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
              {tWard} (intervention)
            </span>
            <span>
              {sw(COMP)}
              {cWard} (control)
            </span>
            <span>
              <span style={{ color: '#16a34a' }}>●</span> service delivery
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
          Independent back-check{effSurv ? ' · Surveyor ' + effSurv : ''}
        </div>
        {backcheckSection()}
      </div>
    </div>
  );
}
