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
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V27
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
  var [kpi, setKpi] = React.useState('backcheck');

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

  // ---- drillable metrics: cards + per-metric evidence ----
  var KCARDS = [
    ['evidence_capture', 'Evidence capture'],
    ['gps_within_15m', 'GPS within 15 m'],
    ['field_completeness', 'Field completeness'],
    ['duration_plausibility', 'Duration plausible'],
    ['consistency_pass', 'Consistency'],
    ['duplicate_integrity', 'Duplicates'],
    ['backcheck', 'Back-check agreement'],
  ];
  function cardValue(k) {
    if (k === 'backcheck')
      return bc.outcome_agreement_pct != null
        ? bc.outcome_agreement_pct.toFixed(1) + '%'
        : '—';
    return metricVal(q[k]);
  }
  function cardOk(k) {
    if (k === 'backcheck') return (bc.outcome_agreement_pct || 0) >= 95;
    return q[k] && q[k].passed;
  }
  function bar(p) {
    return (
      <div
        style={{
          height: 9,
          borderRadius: 5,
          background: '#eef2f7',
          overflow: 'hidden',
          maxWidth: 320,
        }}
      >
        <div
          style={{ height: '100%', width: (p || 0) + '%', background: INDIGO }}
        />
      </div>
    );
  }
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
  function dline(node) {
    return (
      <div
        style={{
          color: SUBINK,
          fontSize: 13,
          margin: '8px 0',
          lineHeight: 1.5,
        }}
      >
        {node}
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

  function kpiDetail() {
    var m = q[kpi],
      d = (m && m.detail) || {};
    if (kpi === 'evidence_capture') {
      var bys = d.by_surveyor || {};
      return (
        <div>
          {dlbl('Evidence capture — a proof photo on every "received" record')}
          {dline(
            <span>
              <b>{d.with_photo}</b> of {m.n} "received" records carry a proof
              photo ({pct(m.value)}). <b>{d.n_missing}</b> missing, flagged for
              review.
            </span>,
          )}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill,minmax(160px,1fr))',
              gap: 10,
              marginTop: 6,
            }}
          >
            {Object.keys(bys).map(function (e) {
              return (
                <div key={e}>
                  {dlbl('surveyor ' + e + ' · ' + bys[e] + '%')}
                  {bar(bys[e])}
                </div>
              );
            })}
          </div>
        </div>
      );
    }
    if (kpi === 'gps_within_15m') {
      return (
        <div>
          {dlbl('GPS within 15 m of the assigned household')}
          {dline(
            <span>
              {pct(m.value)} of captures within 15 m. Median offset{' '}
              <b>{d.median_offset_m} m</b> · <b>{d.n_beyond}</b> beyond 15 m
              (max {d.max_offset_m} m), flagged.
            </span>,
          )}
          {bar(m.value)}
        </div>
      );
    }
    if (kpi === 'field_completeness') {
      var miss = d.missing_by_field || {};
      var ftd = {
        padding: '5px 10px',
        fontSize: 12,
        borderBottom: '1px solid ' + LINE,
        fontFamily: mono,
      };
      return (
        <div>
          {dlbl('Required-field completeness — per field')}
          <table
            style={{ borderCollapse: 'collapse', marginTop: 8, minWidth: 280 }}
          >
            <tbody>
              {Object.keys(miss).map(function (f) {
                return (
                  <tr key={f}>
                    <td style={ftd}>{f}</td>
                    <td
                      style={Object.assign(
                        {
                          textAlign: 'right',
                          color: (miss[f] || 0) > 1 ? ROSE : GREEN,
                        },
                        ftd,
                      )}
                    >
                      {(100 - (miss[f] || 0)).toFixed(1)}% present
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      );
    }
    if (kpi === 'duration_plausibility') {
      return (
        <div>
          {dlbl('Interview-duration plausibility')}
          {dline(
            <span>
              {pct(m.value)} within the plausible band. Median{' '}
              <b>{d.median_min} min</b> · <b>{d.n_too_short}</b> under {d.floor}{' '}
              min, flagged as too fast.
            </span>,
          )}
          {bar(m.value)}
        </div>
      );
    }
    if (kpi === 'consistency_pass') {
      return (
        <div>
          {dlbl('Internal-consistency edit checks')}
          {dline(
            <span>
              {pct(m.value)} pass all edit rules. <b>{d.n_violations}</b> of{' '}
              {m.n} records flagged (e.g. "received" with no eligible child
              present).
            </span>,
          )}
          {bar(m.value)}
        </div>
      );
    }
    if (kpi === 'duplicate_integrity') {
      return (
        <div>
          {dlbl('Duplicate records')}
          {dline(
            <span>
              <b>{d.dup_household_id}</b> duplicate household IDs ·{' '}
              <b>{d.dup_gps_time}</b> duplicate (GPS, timestamp) signatures.{' '}
              {(d.dup_household_id || 0) + (d.dup_gps_time || 0) === 0
                ? 'Clean.'
                : 'Flagged.'}
            </span>,
          )}
        </div>
      );
    }
    // back-check
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
          Independent back-check — re-survey by a different surveyor. Matched
          the original vitamin-A result on{' '}
          <b>
            {bc.outcome_agreement_pct != null && bc.n_backchecked != null
              ? Math.round((bc.outcome_agreement_pct / 100) * bc.n_backchecked)
              : '—'}
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
              bc.coverage_pct + '% · n=' + bc.n_backchecked,
            ],
            ['outcome agreement', bc.outcome_agreement_pct + '%'],
            [
              'identity match',
              bc.type1_error_pct == null
                ? '—'
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
            <span style={{ color: ROSE, fontWeight: 700 }}>red →</span> =
            changed on re-survey
          </span>
          <span>
            each cell: original / re-survey · surveyor original (T) → back-check
            (BC)
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

      {/* DRILLABLE METRICS — one block; every metric opens its evidence below */}
      <div style={Object.assign({ marginTop: 16, padding: 14 }, cardStyle)}>
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
            marginBottom: 8,
          }}
        >
          Verification metrics · {tWard} · R{rd.round} — click any to drill into
          how it was computed
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {KCARDS.map(function (c) {
            var k = c[0],
              on = kpi === k,
              ok = cardOk(k);
            return (
              <div
                key={k}
                onClick={function () {
                  setKpi(k);
                }}
                style={{
                  cursor: 'pointer',
                  padding: '10px 12px',
                  borderRadius: 9,
                  minWidth: 124,
                  background: on ? '#f3f3ff' : '#fff',
                  border: '1px solid ' + (on ? INDIGO : LINE),
                  boxShadow: on ? '0 0 0 1px ' + INDIGO + ' inset' : SHADOW,
                }}
              >
                <div
                  style={{
                    color: MUT,
                    fontSize: 10,
                    textTransform: 'uppercase',
                    letterSpacing: '.04em',
                    display: 'flex',
                    justifyContent: 'space-between',
                  }}
                >
                  <span>{c[1]}</span>
                  <span style={{ color: on ? INDIGO : '#94a3b8' }}>▸</span>
                </div>
                <div
                  style={{
                    color: ok ? GREEN : ROSE,
                    fontFamily: mono,
                    fontSize: 18,
                    fontWeight: 700,
                    marginTop: 3,
                  }}
                >
                  {cardValue(k)}
                </div>
              </div>
            );
          })}
        </div>
        <div
          style={{
            marginTop: 14,
            borderTop: '1px solid ' + LINE,
            paddingTop: 14,
          }}
        >
          {kpiDetail()}
        </div>
      </div>
    </div>
  );
}
