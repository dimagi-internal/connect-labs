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
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V51
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
  // hovered trend point (for the tooltip), back-check info popup {key,x,y},
  // and the selected scorecard quality metric {key,surveyor,value}
  var [hoverPt, setHoverPt] = React.useState(null);
  var [bcInfo, setBcInfo] = React.useState(null);
  var [qSel, setQSel] = React.useState(null);

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
        // Two overlapping point layers, separated by WEIGHT (not shape): the
        // program's delivery visits read as the solid, larger, opaque green
        // layer; the independent survey reads as faint, smaller pins underneath.
        // So the intervention ward fills with green and the control ward —
        // which has survey pins but no delivery — stays visibly green-free.
        if (sdOn && overlay.service_delivery) {
          CM.points(map, 'vm-sd', overlay.service_delivery, {
            color: '#16a34a',
            radius: 3.6,
            opacity: 0.95,
          });
        }
        if (pinsOn && overlay.survey_pins) {
          // Survey pins stay SUBORDINATE to the solid green delivery layer, but
          // need a crisp outline so 'the survey covered both wards' reads — in
          // the control ward (no delivery) the pins are the only marks, so if
          // they're too faint the gap looks like 'nobody surveyed control'.
          CM.pins(map, 'vm-pins', overlay.survey_pins, {
            confirmedColor: INDIGO,
            // Three clearly distinct hues so the survey reads apart from the
            // program's green delivery: confirmed = indigo, surveyed-but-not-
            // reached = rose. A dark slate was too close to the indigo confirmed
            // pins (and to the basemap), so the two survey states blurred and the
            // control ward (mostly not-reached) was illegible.
            absentColor: ROSE,
            radius: 3.4,
            opacity: 0.9,
            strokeWidth: 1.2,
            strokeColor: 'rgba(255,255,255,0.95)',
          });
          try {
            map.setPaintProperty('vm-pins', 'circle-opacity', 0.95);
            map.setPaintProperty('vm-pins', 'circle-stroke-width', 1.2);
            map.setPaintProperty('vm-pins', 'circle-stroke-color', 'rgba(255,255,255,0.95)');
          } catch (e) {}
        }
        CM.fit(map, overlay.ward_boundaries, 64);
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

  // scorecard quality metrics: what each checks + the library detail key, so a
  // clicked cell can open a relevant info panel below the table.
  var QMETA = {
    evidence: {
      lib: 'evidence_capture',
      label: 'Evidence capture',
      blurb:
        'A proof photo on every "received" record — the auditable evidence behind a coverage claim.',
    },
    gps: {
      lib: 'gps_within_15m',
      label: 'GPS within 15 m',
      blurb:
        "The capture's GPS within 15 m of the assigned household — confirms the surveyor was actually there.",
    },
    completeness: {
      lib: 'field_completeness',
      label: 'Field completeness',
      blurb:
        'Every required field present on the record (no blanks left behind).',
    },
    duration: {
      lib: 'duration_plausibility',
      label: 'Interview duration',
      blurb:
        'Interview length within a plausible band — flags records too fast to be real.',
    },
    consistency: {
      lib: 'consistency_pass',
      label: 'Consistency checks',
      blurb:
        'Internal edit rules pass (e.g. a "received" record must have an eligible child present).',
    },
    duplicates: {
      lib: 'duplicate_integrity',
      label: 'Duplicate integrity',
      blurb:
        'No duplicate household IDs and no repeated (GPS, timestamp) — catches copy-pasted records.',
    },
  };

  // metric drill-through: one row per survey for the clicked quality cell, with
  // that metric's per-record value + flag. Fills the bottom widget (replaces the
  // back-check view while a quality cell is selected).
  function qmetricDrill(surveyor, key) {
    var m = QMETA[key];
    if (!m) return null;
    var scRows = rd.surveyor_scorecard || [];
    var row = surveyor
      ? scRows.filter(function (r) {
          return r.surveyor === surveyor;
        })[0]
      : null;
    var recs = row
      ? (row.records || []).slice()
      : scRows.reduce(function (a, r) {
          return a.concat(r.records || []);
        }, []);
    var val = row ? row[key] : null;
    var valTxt =
      val == null
        ? '—'
        : key === 'duplicates'
        ? val + ' dup'
        : Number(val).toFixed(1) + '%';

    function flagged(r) {
      if (key === 'evidence') return r.recv && r.photo !== true;
      if (key === 'gps') return r.gps != null && r.gps > 15;
      if (key === 'completeness') return (r.miss || []).length > 0;
      if (key === 'duration') return !!r.short;
      if (key === 'consistency') return !r.cons;
      if (key === 'duplicates') return !!r.dup;
      return false;
    }
    function sortVal(r) {
      if (key === 'gps') return -(r.gps || 0);
      if (key === 'duration') return r.dur == null ? 1e9 : r.dur;
      return flagged(r) ? 0 : 1;
    }
    recs.sort(function (a, b) {
      return sortVal(a) - sortVal(b);
    });
    var nFlag = recs.filter(flagged).length;
    var nTotal = recs.length;

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
    var thR = Object.assign({}, th, { textAlign: 'right' });
    var td = {
      padding: '6px 9px',
      fontSize: 12.5,
      fontFamily: mono,
      borderBottom: '1px solid ' + LINE,
      whiteSpace: 'nowrap',
    };
    var tdR = Object.assign({}, td, { textAlign: 'right' });
    var hhTd = Object.assign({}, td, {
      color: SUBINK,
      fontWeight: 600,
      fontFamily: 'inherit',
    });
    function bar(frac, color, thrFrac) {
      return (
        <span
          style={{
            position: 'relative',
            display: 'inline-block',
            width: 84,
            height: 7,
            borderRadius: 4,
            background: '#eef2f7',
            overflow: 'hidden',
            verticalAlign: 'middle',
            marginRight: 8,
          }}
        >
          <span
            style={{
              display: 'block',
              height: '100%',
              width: Math.max(0, Math.min(1, frac)) * 100 + '%',
              background: color,
            }}
          />
          {thrFrac != null ? (
            <span
              style={{
                position: 'absolute',
                top: -1,
                bottom: -1,
                left: Math.max(0, Math.min(1, thrFrac)) * 100 + '%',
                width: 0,
                borderLeft: '1.5px solid #475569',
              }}
            />
          ) : null}
        </span>
      );
    }

    var head, rowCells;
    if (key === 'gps') {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={th}>GPS offset from assigned</th>
          <th style={thR}>≤ 15 m</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td
            key="gps"
            style={Object.assign({}, td, {
              color: bad ? ROSE : SUBINK,
              fontWeight: bad ? 700 : 400,
              whiteSpace: 'nowrap',
            })}
          >
            {bar((r.gps || 0) / 60, bad ? ROSE : INDIGO, 15 / 60)}
            {r.gps == null ? '—' : r.gps.toFixed(0) + ' m'}
          </td>,
          <td
            key="ok"
            style={Object.assign({}, tdR, {
              color: bad ? ROSE : GREEN,
              fontWeight: 700,
            })}
          >
            {bad ? 'no' : 'yes'}
          </td>,
        ];
      };
    } else if (key === 'evidence') {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={th}>Received vit-A</th>
          <th style={th}>Proof photo</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td key="recv" style={Object.assign({}, td, { color: SUBINK })}>
            {r.recv ? 'yes' : 'no'}
          </td>,
          <td
            key="photo"
            style={Object.assign({}, td, {
              color: bad ? ROSE : r.recv ? GREEN : MUT,
              fontWeight: bad ? 700 : 400,
            })}
          >
            {r.recv ? (r.photo ? 'yes' : 'MISSING') : 'n/a'}
          </td>,
        ];
      };
    } else if (key === 'duration') {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={thR}>Interview duration</th>
          <th style={thR}>Too fast</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td
            key="dur"
            style={Object.assign({}, tdR, {
              color: bad ? ROSE : SUBINK,
              fontWeight: bad ? 700 : 400,
            })}
          >
            {bar((r.dur || 0) / 30, bad ? ROSE : INDIGO)}
            {r.dur == null ? '—' : r.dur.toFixed(1) + ' min'}
          </td>,
          <td
            key="ok"
            style={Object.assign({}, tdR, {
              color: bad ? ROSE : GREEN,
              fontWeight: 700,
            })}
          >
            {bad ? 'yes' : 'no'}
          </td>,
        ];
      };
    } else if (key === 'completeness') {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={th}>Missing required fields</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td
            key="miss"
            style={Object.assign({}, td, {
              color: bad ? ROSE : GREEN,
              fontWeight: bad ? 700 : 400,
            })}
          >
            {bad ? (r.miss || []).join(', ') : 'complete'}
          </td>,
        ];
      };
    } else if (key === 'consistency') {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={th}>Received</th>
          <th style={th}>Edit checks</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td key="recv" style={Object.assign({}, td, { color: MUT })}>
            {r.recv ? 'yes' : 'no'}
          </td>,
          <td
            key="ok"
            style={Object.assign({}, td, {
              color: bad ? ROSE : GREEN,
              fontWeight: 700,
            })}
          >
            {bad ? 'violation' : 'pass'}
          </td>,
        ];
      };
    } else {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={th}>Duplicate record</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td
            key="dup"
            style={Object.assign({}, td, {
              color: bad ? ROSE : GREEN,
              fontWeight: 700,
            })}
          >
            {bad ? 'duplicate' : 'unique'}
          </td>,
        ];
      };
    }
    var who = surveyor ? 'Surveyor ' + surveyor : 'all surveyors';
    return (
      <div>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            gap: 10,
            flexWrap: 'wrap',
            marginBottom: 2,
          }}
        >
          <div style={{ color: SUBINK, fontWeight: 700, fontSize: 13 }}>
            {m.label}{' '}
            <span style={{ color: MUT, fontWeight: 400 }}>
              · {who} · this cycle
            </span>{' '}
            <span style={{ fontFamily: mono, color: INDIGO }}>{valTxt}</span>
          </div>
          <button
            onClick={function () {
              setQSel(null);
            }}
            style={{
              cursor: 'pointer',
              border: '1px solid ' + LINE,
              background: '#fff',
              color: INDIGO,
              borderRadius: 7,
              fontSize: 11,
              padding: '3px 9px',
              fontFamily: sans,
            }}
          >
            ← back-check
          </button>
        </div>
        <div style={{ color: MUT, fontSize: 11.5, marginBottom: 8 }}>
          {m.blurb} One row per survey — all {nTotal} this cycle ({nFlag}{' '}
          flagged).
        </div>
        <div style={{ overflow: 'auto', maxHeight: 380 }}>
          <table
            style={{ borderCollapse: 'collapse', width: '100%', minWidth: 440 }}
          >
            <thead style={{ position: 'sticky', top: 0, background: '#fff' }}>
              {head}
            </thead>
            <tbody>
              {recs.map(function (r, i) {
                return <tr key={i}>{rowCells(r)}</tr>;
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

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
    // Clear the host app's sticky top bar when a section is scrolled to, so its
    // header row isn't clipped under the Connect Labs nav.
    scrollMarginTop: 84,
  };

  // floating popup for a back-check section's info (method + source). Fixed
  // position at the click point so it overlays without reflowing the table.
  function bcInfoPopup() {
    if (!bcInfo) return null;
    var W = 300;
    var vw = typeof window !== 'undefined' ? window.innerWidth || 1200 : 1200;
    var EST_H = 170;
    // Center the popover on its trigger 'i' and prefer opening ABOVE it (over the
    // section header, never the back-check data rows below); flip below only when
    // there isn't room above the host sticky nav. A caret ties it to the trigger.
    var cx = bcInfo.x || vw / 2;
    var ty = bcInfo.y || 120;
    var left = Math.min(Math.max(8, cx - W / 2), vw - W - 8);
    var caretX = Math.min(Math.max(14, cx - left), W - 14);
    var above = ty - EST_H > 96;
    var top = above ? ty - 12 : ty + 18;
    return (
      <div>
        <div
          onClick={function () {
            setBcInfo(null);
          }}
          style={{ position: 'fixed', inset: 0, zIndex: 50 }}
        />
        <div
          style={{
            position: 'fixed',
            left: left,
            top: top,
            transform: above ? 'translateY(-100%)' : 'none',
            width: W,
            zIndex: 51,
            background: '#fff',
            border: '1px solid ' + LINE,
            borderRadius: 10,
            boxShadow: '0 10px 30px rgba(16,24,40,0.20)',
            padding: '12px 14px',
          }}
        >
          <div
            style={{
              position: 'absolute',
              left: caretX - 7,
              bottom: above ? -7 : 'auto',
              top: above ? 'auto' : -7,
              width: 0,
              height: 0,
              borderLeft: '7px solid transparent',
              borderRight: '7px solid transparent',
              borderTop: above ? '7px solid #fff' : 'none',
              borderBottom: above ? 'none' : '7px solid #fff',
              filter: 'drop-shadow(0 1px 1px rgba(16,24,40,0.12))',
            }}
          />
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              marginBottom: 5,
            }}
          >
            <b style={{ color: SUBINK, fontSize: 13 }}>{bcInfo.label}</b>
            <button
              onClick={function () {
                setBcInfo(null);
              }}
              style={{
                cursor: 'pointer',
                border: 'none',
                background: 'transparent',
                color: MUT,
                fontSize: 16,
                lineHeight: 1,
                padding: 0,
              }}
            >
              ×
            </button>
          </div>
          <div style={{ color: SUBINK, fontSize: 12.5, lineHeight: 1.5 }}>
            {bcInfo.info}
          </div>
          <div
            style={{
              color: MUT,
              fontSize: 11.5,
              lineHeight: 1.5,
              marginTop: 7,
            }}
          >
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
          </div>
        </div>
      </div>
    );
  }

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
        {(function () {
          var hx0 = Math.max(padL, X(sel) - 26);
          var hx1 = Math.min(w - padR, X(sel) + 26);
          return (
            <rect
              x={hx0}
              y={padT}
              width={Math.max(0, hx1 - hx0)}
              height={h - padB - padT}
              fill={INDIGO}
              opacity="0.06"
            />
          );
        })()}
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
                fill={i === sel ? INDIGO : '#94a3b8'}
                fontSize="8.5"
                textAnchor="middle"
              >
                {(rounds[i] || {}).label || (rounds[i] || {}).treatment_ward || ''}
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
          style={{
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
            onClick={
              isAgg
                ? null
                : function () {
                    setQSel(null);
                    setSelSurv(row.surveyor);
                  }
            }
            title={isAgg ? null : "Show this surveyor's back-check below"}
            style={Object.assign({}, td0, {
              fontWeight: isAgg ? 700 : 600,
              color: SUBINK,
              cursor: isAgg ? 'default' : 'pointer',
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
            var isBc = c[0] === 'backcheck';
            // back-check cell selects the surveyor (drives the section below);
            // a quality cell opens the metric info panel for that surveyor.
            var clickable = isBc ? !isAgg : !!QMETA[c[0]];
            var selCell =
              !isBc &&
              qSel &&
              qSel.key === c[0] &&
              qSel.surveyor === (isAgg ? null : row.surveyor);
            function onCell() {
              if (isBc) {
                if (!isAgg) {
                  setQSel(null);
                  setSelSurv(row.surveyor);
                }
              } else if (QMETA[c[0]]) {
                if (!isAgg) setSelSurv(row.surveyor);
                setQSel({
                  key: c[0],
                  surveyor: isAgg ? null : row.surveyor,
                  value: v,
                });
              }
            }
            return (
              <td
                key={c[0]}
                data-cell={(isAgg ? 'all' : row.surveyor) + ':' + c[0]}
                onClick={clickable ? onCell : null}
                title={
                  clickable
                    ? isBc
                      ? "Show this surveyor's back-check below"
                      : 'What this metric checks'
                    : null
                }
                style={Object.assign({}, td, {
                  color: v == null ? MUT : bad ? ROSE : GREEN,
                  fontWeight: bad ? 700 : 500,
                  cursor: clickable ? 'pointer' : 'default',
                  boxShadow: selCell ? 'inset 0 0 0 1.5px ' + INDIGO : 'none',
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
          ['roof_type', 'Roof'],
        ],
        info: "Stable facts that can't change between two visits — the child's sex and age, whether the household exists, and the household's roof type. If the re-survey disagrees here, it's the strongest sign the original record was made up.",
      },
      {
        key: 'location',
        label: 'GPS location match',
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
        info: 'Whether the headline result — did the child receive vitamin A — held up when an independent surveyor re-visited. This is the key outcome the whole survey exists to measure.',
      },
    ];
  }

  // surveyor view: TWO rows per re-surveyed household (Original / Backcheck),
  // columns grouped under the three back-check sections. Section info opens as a
  // floating popup (does not reflow the table).
  function surveyorBackcheck(sid, sb) {
    var sections = bcSections(sb);
    var rows = sb.rows || [];
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
    var groupTh = {
      padding: '6px 9px 4px',
      borderBottom: '2px solid ' + LINE,
      borderLeft: '1px solid ' + LINE,
      textAlign: 'left',
      verticalAlign: 'bottom',
    };
    var cellBase = {
      padding: '6px 9px',
      fontSize: 12.5,
      fontFamily: mono,
      whiteSpace: 'nowrap',
    };
    // original row: no bottom border (groups the pair); backcheck row: solid
    function cell(extra, bottom) {
      return Object.assign(
        {},
        cellBase,
        { borderBottom: bottom ? '1px solid ' + LINE : 'none' },
        extra || {},
      );
    }
    function ncols(s) {
      return s.mode === 'distance' ? 1 : s.fields.length;
    }
    // identity / outcome value cell for one side of one household
    function vcell(row, key, which, first, bottom) {
      var f = fieldOf(row, key);
      var st = cell(first ? { borderLeft: '1px solid ' + LINE } : {}, bottom);
      if (!f)
        return (
          <td key={which + key} style={st}>
            —
          </td>
        );
      if (which === 'original')
        return (
          <td
            key={which + key}
            style={Object.assign({}, st, { color: SUBINK })}
          >
            {yn(f.original)}
          </td>
        );
      var ch = !f.match;
      return (
        <td
          key={which + key}
          style={Object.assign({}, st, {
            color: ch ? ROSE : MUT,
            fontWeight: ch ? 700 : 400,
          })}
        >
          {yn(f.backcheck)}
        </td>
      );
    }
    function infoBtn(s) {
      var on = bcInfo && bcInfo.key === s.key;
      return (
        <button
          data-bcinfo={s.key}
          onClick={function (e) {
            e.stopPropagation();
            setBcInfo(
              on
                ? null
                : {
                    key: s.key,
                    label: s.label,
                    info: s.info,
                  },
            );
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
          Two rows per household — what the surveyor recorded vs the independent
          re-survey. Back-checks are a stratified sample of surveys (n={sb.n} of{' '}
          this surveyor's work across cycles); showing{' '}
          {Math.min(rows.length, sb.n)}, mismatches first. Each section header
          shows the share that agreed {'·'} tap{' '}
          <b style={{ fontFamily: mono }}>i</b> for what it means.
        </div>
        {bcInfo ? (
          <div
            style={{
              position: 'relative',
              background: '#f8fafc',
              border: '1px solid ' + LINE,
              borderLeft: '3px solid ' + INDIGO,
              borderRadius: 8,
              padding: '10px 34px 10px 12px',
              marginBottom: 10,
            }}
          >
            <button
              onClick={function () {
                setBcInfo(null);
              }}
              style={{
                position: 'absolute',
                top: 6,
                right: 8,
                cursor: 'pointer',
                border: 'none',
                background: 'transparent',
                color: MUT,
                fontSize: 16,
                lineHeight: 1,
                padding: 0,
              }}
            >
              ×
            </button>
            <div
              style={{
                color: SUBINK,
                fontWeight: 700,
                fontSize: 12.5,
                marginBottom: 4,
              }}
            >
              {bcInfo.label}
            </div>
            <div style={{ color: SUBINK, fontSize: 12.5, lineHeight: 1.5 }}>
              {bcInfo.info}
            </div>
            <div
              style={{
                color: MUT,
                fontSize: 11,
                lineHeight: 1.5,
                marginTop: 6,
              }}
            >
              An independent surveyor re-visits a sample of households and
              re-records the same facts. Standard back-check method (J-PAL/IPA;
              World Bank DIME —{' '}
              <a
                href="https://dimewiki.worldbank.org/Back_Checks"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: INDIGO }}
              >
                method reference
              </a>
              ).
            </div>
          </div>
        ) : null}
        <div style={{ overflow: 'auto', maxHeight: 460 }}>
          <table
            style={{
              borderCollapse: 'collapse',
              width: '100%',
              minWidth: 680,
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
                <th style={th}>Record</th>
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
                var dm = row.gps_delta_m;
                var distBad = dm != null && dm > thr;
                return [
                  <tr key={ri + 'o'}>
                    <td
                      rowSpan={2}
                      style={cell(
                        {
                          fontFamily: 'inherit',
                          fontWeight: 600,
                          color: SUBINK,
                        },
                        true,
                      )}
                    >
                      {row.household_id}
                    </td>
                    <td style={cell({ color: MUT }, false)}>
                      Original ({row.enumerator})
                    </td>
                    {sections.map(function (s) {
                      if (s.mode === 'distance')
                        return (
                          <td
                            key="loc"
                            rowSpan={2}
                            style={cell(
                              {
                                borderLeft: '1px solid ' + LINE,
                                color: distBad ? ROSE : SUBINK,
                                fontWeight: distBad ? 700 : 400,
                                verticalAlign: 'middle',
                              },
                              true,
                            )}
                          >
                            {dm == null ? '—' : dm.toFixed(0) + ' m'}
                          </td>
                        );
                      return s.fields.map(function (c, ci) {
                        return vcell(row, c[0], 'original', ci === 0, false);
                      });
                    })}
                  </tr>,
                  <tr key={ri + 'b'}>
                    <td style={cell({ color: MUT }, true)}>
                      Backcheck ({row.backcheck_enumerator})
                    </td>
                    {sections.map(function (s) {
                      if (s.mode === 'distance') return null;
                      return s.fields.map(function (c, ci) {
                        return vcell(row, c[0], 'backcheck', ci === 0, true);
                      });
                    })}
                  </tr>,
                ];
              })}
            </tbody>
          </table>
        </div>
        <div
          style={{ marginTop: 8, fontSize: 11, color: MUT, fontFamily: mono }}
        >
          <span style={{ color: ROSE, fontWeight: 700 }}>rose</span> = the
          re-survey disagreed with what {sid} recorded
        </div>
      </div>
    );
  }

  function backcheckSection() {
    // a clicked quality cell takes over the widget with a metric drill-through
    if (qSel && QMETA[qSel.key]) return qmetricDrill(qSel.surveyor, qSel.key);
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
          Service-delivery data vs independent survey — {(trend.rounds || []).length}{' '}
          bi-monthly rounds over time
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
            · amber band = service-delivery − survey gap · highlighted column =
            selected cycle · click a cycle to open it
          </span>
        </div>
        <div
          style={{
            marginTop: 6,
            fontSize: 10.5,
            color: '#94a3b8',
            fontFamily: mono,
            lineHeight: 1.5,
          }}
        >
          {(trend.rounds || []).length} bi-monthly survey rounds over time —
          earliest at left, most recent at right. The independent survey's coverage
          tracked against the program's self-report at each round; every round
          verifies a rotating ward against its adjacent control.
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
          <div style={{ position: 'relative' }}>
            <div
              ref={mapDivRef}
              style={{
                height: 480,
                borderRadius: 8,
                overflow: 'hidden',
                background: '#eef2f7',
                border: '1px solid ' + LINE,
              }}
            />
            <div
              style={{
                position: 'absolute',
                top: 8,
                left: 8,
                background: 'rgba(255,255,255,0.97)',
                border: '1px solid ' + LINE,
                borderRadius: 8,
                padding: '8px 11px',
                fontSize: 11.5,
                fontFamily: mono,
                color: MUT,
                display: 'flex',
                flexDirection: 'column',
                gap: 3,
                lineHeight: 1.5,
                boxShadow: '0 2px 8px rgba(16,24,40,0.14)',
                pointerEvents: 'none',
              }}
            >
              <span
                style={{ color: SUBINK, fontWeight: 700, marginBottom: 2 }}
              >
                Independent survey · both wards
              </span>
              <span style={{ color: SUBINK }}>
                <span style={{ color: INDIGO }}>▰</span> {tWard} (intervention) —{' '}
                <b style={{ color: INDIGO }}>{pct(ver)}</b> confirmed
              </span>
              <span style={{ color: SUBINK }}>
                <span style={{ color: COMP }}>▰</span> {cWard} (control) —{' '}
                <b style={{ color: COMP }}>
                  {pct((trend.comparison || [])[sel])}
                </b>{' '}
                confirmed
              </span>
              <span>
                <span style={{ color: '#16a34a' }}>●</span> service delivery
                (program)
              </span>
              <span>
                <span style={{ color: INDIGO }}>●</span> survey confirmed &nbsp;
                <span style={{ color: ROSE }}>●</span> surveyed · not reached
              </span>
            </div>
          </div>
          {!mapLibReady ? (
            <div style={{ color: MUT, fontSize: 12, padding: 6 }}>
              loading map…
            </div>
          ) : null}
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
            // scroll target: land the whole scorecard clear of the host sticky
            // nav so all surveyor rows (the peers that make T6 read as an
            // outlier) frame together, not sheared at the top edge.
            scrollMarginTop: 96,
          }}
        >
          Survey-quality scorecard · {tWard} · R{rd.round} — one row per
          surveyor
        </div>
        <div
          style={{
            display: 'flex',
            gap: 16,
            marginTop: 0,
            marginBottom: 10,
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
            click a quality cell → detail · click a surveyor → back-check below
          </span>
        </div>
        {scorecardTable()}
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
          {qSel && QMETA[qSel.key]
            ? 'Survey-quality detail'
            : 'Independent back-check' +
              (effSurv ? ' · Surveyor ' + effSurv : '')}
        </div>
        {backcheckSection()}
      </div>
    </div>
  );
}
