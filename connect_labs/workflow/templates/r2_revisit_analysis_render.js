// R2 Revisit Analysis -- render code (transpiled by Babel in the browser).
//
// Two tabs over the same data:
//   Table -- per organisation and ward: work areas in the original RCT opportunity,
//            in its R1 revisit opportunity, and the R2 candidates (R1 thresholds,
//            suspended workers, approved inaccessible, rejected visits), each
//            "not in R1" where it matters, with a de-duplicated ward total.
//   Map   -- the selected ward's boundary and every one of its work areas,
//            coloured by why it is (or is not) an R2 work area.
//   Download CSV -- one organisation's R2 work areas (chosen wards and rules)
//            in Connect's work-area upload format, with a new Expected Visit
//            Count: buildings x the ward's EVC per building in the main opp.
//
// Layout of this file:
//   1. Pure calculation functions (rr*) -- no React, no DOM; tested by
//      __tests__/r2_revisit_analysis_render.test.mjs.
//   2. Presentational components, declared at the top level so React does not
//      remount them on every render (the map would be rebuilt).
//   3. WorkflowUI: loads the eager sources itself (config.noPipelineStream) per
//      organisation through the pipeline-rows stream, then draws the tabs.
//
// Config (see r2_revisit_analysis.py): orgs [{org, main, r1}] -- each
// organisation's original and R1 opportunity; sources [[kind, alias, label,
// fromR1]]; columns [[key, label, explanation]]; algo -- R1's thresholds.
// The Map tab reads two on-demand sources: `wa_shapes` (work-area polygons,
// queried one ward at a time) and `ward_bounds` (implementation-area polygons).

// ===========================================================================
// 1. Calculation
// ===========================================================================

var RR_ALGO_DEFAULTS = { hsd_evc: 0.4, muac: 0.7, dw: 0.4, ncf_max_bld: 1 };

// Map categories, in the order a work area is assigned one: already in R1 wins,
// then the R2 rules, then the R1 thresholds; everything else is "not selected".
var RR_CATS = [
  { key: 'r1', label: 'In R1 (already revisited)', color: '#10b981' },
  { key: 'susp', label: 'Suspended worker', color: '#dc2626' },
  { key: 'inacc', label: 'Approved inaccessible', color: '#db2777' },
  { key: 'rej', label: 'Rejected HSD/NCF visit', color: '#f59e0b' },
  { key: 'algo', label: 'R2 Work-Area (R1 thresholds)', color: '#3b82f6' },
  { key: 'none', label: 'Not selected', color: '#9ca3af' },
];

function rrCentroidKey(raw) {
  try {
    var c = typeof raw === 'string' ? JSON.parse(raw) : raw;
    return (
      (+c.coordinates[0]).toFixed(7) + ',' + (+c.coordinates[1]).toFixed(7)
    );
  } catch (e) {
    return null;
  }
}

function rrGeometry(raw) {
  if (!raw) return null;
  try {
    return typeof raw === 'string' ? JSON.parse(raw) : raw;
  } catch (e) {
    return null;
  }
}

function rrNormWard(w) {
  return String(w || '')
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function rrFmt(n) {
  return Number(n || 0).toLocaleString();
}

// Keep only what the page needs from each source's rows.
function rrCompact(kind, rows) {
  var out = {};
  if (kind === 'workers') {
    rows.forEach(function (r) {
      if (String(r.suspended).toLowerCase() === 'true') out[r.uname] = 1;
    });
    return out;
  }
  if (kind === 'rejected') {
    rows.forEach(function (r) {
      out[r.wa_case_id || r.entity_id] = 1;
    });
    return out;
  }
  if (kind === 'visits') {
    rows.forEach(function (r) {
      out[r.wa_case_id || r.entity_id] = [
        +r.hsd || 0,
        +r.muac || 0,
        +r.dw || 0,
        +r.ncf || 0,
      ];
    });
    return out;
  }
  // wa_main / wa_r1: Connect's Excluded work areas are not part of the main plan.
  return rows
    .filter(function (r) {
      return (
        kind === 'wa_r1' || String(r.wa_status).toUpperCase() !== 'EXCLUDED'
      );
    })
    .map(function (r) {
      return {
        w: rrNormWard(r.ward),
        raw: String(r.ward || ''),
        st: String(r.wa_status || '').toUpperCase(),
        o: r.owner,
        c: r.wa_case_id,
        k: rrCentroidKey(r.centroid),
        e: +r.evc || 0,
        b: +r.bld || 0,
      };
    });
}

// R1's algorithm: a work area stays in unless ANY threshold says it was covered.
// v = [hsd, muac, dw, ncf] visit counts.
function rrAlgo(w, v, al) {
  al = al || RR_ALGO_DEFAULTS;
  v = v || [0, 0, 0, 0];
  var hsd = v[0];
  if (!w.e || hsd / w.e > al.hsd_evc) return false;
  if (hsd && (v[1] / hsd > al.muac || v[2] / hsd > al.dw)) return false;
  return !(v[3] && w.b <= al.ncf_max_bld);
}

// One organisation: every main work area with its flags, and the per-ward rows.
// d = {wa_main, wa_r1, workers, rejected, visits} (rrCompact output).
function rrClassifyOrg(d, columns, al) {
  var inR1 = {};
  var wards = {};
  var was = {};
  d.wa_r1.forEach(function (w) {
    if (w.k) inR1[w.k] = 1;
  });
  function row(name) {
    if (!wards[name]) {
      wards[name] = { ward: name, raws: {}, tot_bld: 0, tot_evc: 0 };
      columns.forEach(function (c) {
        wards[name][c[0]] = 0;
      });
    }
    return wards[name];
  }
  d.wa_main.forEach(function (w) {
    var r = row(w.w);
    var hit = !!(w.k && inR1[w.k]);
    var v = d.visits[w.c] || [0, 0, 0, 0];
    var f = {
      c: w.c,
      ward: w.w,
      raw: w.raw,
      st: w.st,
      o: w.o,
      e: w.e,
      b: w.b,
      v: v,
      r1: hit,
      susp: !!d.workers[w.o],
      inacc: w.st === 'INACCESSIBLE',
      rej: !!d.rejected[w.c],
      algo: !hit && rrAlgo(w, v, al),
    };
    r.raws[w.raw] = 1;
    r.main++;
    r.tot_bld += w.b;
    r.tot_evc += w.e;
    if (f.susp) {
      r.susp++;
      if (!hit) r.susp_not_r1++;
    }
    if (f.inacc && !hit) r.inacc_not_r1++;
    if (f.rej) {
      r.rejected++;
      if (!hit) r.rejected_not_r1++;
    }
    if (f.algo) r.algo_add++;
    f.r2 = !hit && (f.susp || f.inacc || f.rej || f.algo);
    if (f.r2) r.total_r2++;
    f.cat = rrCategory(f);
    if (w.c) was[w.c] = f;
  });
  d.wa_r1.forEach(function (w) {
    row(w.w).r1++;
  });
  // A new expected visit count for each R2 work area: its buildings times the
  // ward's EVC per building in the main opportunity.
  Object.keys(wards).forEach(function (k) {
    wards[k].evc_ratio = rrRatio(wards[k]);
  });
  Object.keys(was).forEach(function (id) {
    var f = was[id];
    f.evc2 = f.r2 ? Math.round(f.b * wards[f.ward].evc_ratio) : 0;
    wards[f.ward].evc_r2 = (wards[f.ward].evc_r2 || 0) + f.evc2;
  });
  return {
    wards: Object.keys(wards)
      .sort()
      .map(function (k) {
        return wards[k];
      }),
    was: was,
  };
}

function rrCategory(f) {
  if (f.r1) return 'r1';
  if (f.susp) return 'susp';
  if (f.inacc) return 'inacc';
  if (f.rej) return 'rej';
  if (f.algo) return 'algo';
  return 'none';
}

function rrRatio(r) {
  return r.tot_bld ? r.tot_evc / r.tot_bld : 0;
}

function rrSum(rows, columns) {
  var t = {};
  columns
    .map(function (c) {
      return c[0];
    })
    .concat(['tot_bld', 'tot_evc'])
    .forEach(function (k) {
      t[k] = rows.reduce(function (s, r) {
        return s + (r[k] || 0);
      }, 0);
    });
  // A ratio is not summed: it is recomputed from the totals.
  t.evc_ratio = rrRatio(t);
  return t;
}

function rrCell(key, v) {
  return key === 'evc_ratio' ? Number(v || 0).toFixed(2) : rrFmt(v);
}

// Work-area shape rows + the org's classification -> a FeatureCollection.
// Shapes with no classification (Connect-Excluded work areas) are left out.
function rrWorkAreaFeatures(shapes, was) {
  var colors = {};
  RR_CATS.forEach(function (c) {
    colors[c.key] = c.color;
  });
  var features = [];
  shapes.forEach(function (s) {
    var f = was[s.wa_case_id];
    var g = rrGeometry(s.boundary);
    if (!f || !g) return;
    features.push({
      type: 'Feature',
      geometry: g,
      properties: {
        id: s.wa_case_id,
        slug: s.slug || '',
        cat: f.cat,
        color: colors[f.cat],
        status: f.st,
        owner: f.o || '',
        evc: f.e,
        bld: f.b,
        hsd: f.v[0],
        muac: f.v[1],
        dw: f.v[2],
        ncf: f.v[3],
        flags: rrFlagText(f),
      },
    });
  });
  return { type: 'FeatureCollection', features: features };
}

function rrFlagText(f) {
  var out = [];
  if (f.r1) out.push('In R1');
  if (f.susp) out.push('Suspended worker');
  if (f.inacc) out.push('Approved inaccessible');
  if (f.rej) out.push('Rejected HSD/NCF visit');
  if (f.algo) out.push('R2 Work-Area (R1 thresholds)');
  return out.length ? out.join(', ') : 'Not selected';
}

function rrWardFeature(boundRows, ward) {
  var want = rrNormWard(ward).toLowerCase();
  for (var i = 0; i < boundRows.length; i++) {
    if (rrNormWard(boundRows[i].name).toLowerCase() !== want) continue;
    var g = rrGeometry(boundRows[i].boundary);
    if (g) {
      return {
        type: 'FeatureCollection',
        features: [
          { type: 'Feature', geometry: g, properties: { ward: ward } },
        ],
      };
    }
  }
  return null;
}

function rrEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (ch) {
    return {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch];
  });
}

function rrPopupHtml(p) {
  var rows = [
    ['Why', p.flags],
    ['Status', p.status || '—'],
    ['Worker', p.owner || '—'],
    ['EVC', p.evc],
    ['Buildings', p.bld],
    ['HSD visits', p.hsd],
    ['MUAC', p.muac],
    ['Deworming', p.dw],
    ['NCF visits', p.ncf],
  ];
  return (
    '<div style="font-size:12px;line-height:1.4"><div style="font-weight:600;margin-bottom:4px">' +
    rrEsc(p.slug || p.id) +
    '</div>' +
    rows
      .map(function (r) {
        return (
          '<div><span style="color:#6b7280">' +
          rrEsc(r[0]) +
          ':</span> ' +
          rrEsc(r[1]) +
          '</div>'
        );
      })
      .join('') +
    '</div>'
  );
}

// ---------------------------------------------------------------- Connect CSV
//
// The work-area file Connect's "upload work areas" takes (the same columns as
// microplans' export, microplans/core/workarea.py CSV_HEADERS): 9 required
// columns, then Implementation Area and Work Area Group Name. Connect ignores
// unknown columns, so the last two are for the person reviewing the file.

var RR_CSV_HEADERS = [
  'Area Slug',
  'Ward',
  'Centroid',
  'Boundary',
  'Building Count',
  'Expected Visit Count',
  'Target Population',
  'LGA',
  'State',
  'Implementation Area',
  'Work Area Group Name',
  'R2 Reason',
  'Original Expected Visit Count',
];

var RR_RULES = [
  { key: 'algo', label: 'R2 Work-Area (R1 thresholds)' },
  { key: 'susp', label: 'Suspended worker' },
  { key: 'inacc', label: 'Approved inaccessible' },
  { key: 'rej', label: 'Rejected HSD/NCF visit' },
];

// Connect accepts a Polygon only. A MultiPolygon of one part is that polygon;
// anything else is refused (null) so the whole upload does not fail on it.
function rrPolygonWkt(raw) {
  var g = rrGeometry(raw);
  if (!g) return null;
  var rings = null;
  if (g.type === 'Polygon') rings = g.coordinates;
  else if (g.type === 'MultiPolygon' && g.coordinates.length === 1)
    rings = g.coordinates[0];
  if (!rings || !rings.length) return null;
  return (
    'POLYGON ((' +
    rings
      .map(function (ring) {
        return ring
          .map(function (p) {
            return p[0] + ' ' + p[1];
          })
          .join(', ');
      })
      .join('), (') +
    '))'
  );
}

// "lon lat", as Connect reads it.
function rrCentroidText(raw) {
  var g = rrGeometry(raw);
  return g && g.coordinates ? g.coordinates[0] + ' ' + g.coordinates[1] : '';
}

function rrCsvCell(v) {
  var s = String(v == null ? '' : v);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

// The work areas the file should carry: not in R1, in a chosen ward, and
// selected by at least one chosen rule.
function rrSelectWorkAreas(was, wardOn, rules) {
  return Object.keys(was)
    .map(function (id) {
      return was[id];
    })
    .filter(function (f) {
      if (f.r1 || !wardOn[f.ward]) return false;
      return RR_RULES.some(function (r) {
        return rules[r.key] && f[r.key];
      });
    })
    .sort(function (a, b) {
      return a.ward < b.ward ? -1 : a.ward > b.ward ? 1 : 0;
    });
}

function rrReasonText(f, rules) {
  return RR_RULES.filter(function (r) {
    return rules[r.key] && f[r.key];
  })
    .map(function (r) {
      return r.label;
    })
    .join('; ');
}

// selected: rrSelectWorkAreas output; shapes: wa_shapes rows keyed by case id.
// Returns {csv, count, visits, skipped: [{slug, why}]}.
function rrConnectCsv(selected, shapes, rules) {
  var rows = [];
  var skipped = [];
  selected.forEach(function (f) {
    var s = shapes[f.c];
    if (!s) return skipped.push({ slug: f.c, why: 'no shape in Connect' });
    var wkt = rrPolygonWkt(s.boundary);
    if (!wkt)
      return skipped.push({ slug: s.slug, why: 'boundary is not one polygon' });
    rows.push({ f: f, s: s, wkt: wkt });
  });
  // Connect takes Work Area Group Name for every row or for none.
  var groups = rows.every(function (r) {
    return r.s.grp;
  });
  var visits = 0;
  var lines = [RR_CSV_HEADERS.map(rrCsvCell).join(',')];
  rows.forEach(function (r) {
    var ward = r.s.ward || r.f.raw;
    visits += r.f.evc2;
    lines.push(
      [
        r.s.slug,
        ward,
        rrCentroidText(r.s.centroid),
        r.wkt,
        r.f.b,
        r.f.evc2,
        0,
        r.s.lga || '',
        r.s.state || '',
        ward,
        groups ? r.s.grp : '',
        rrReasonText(r.f, rules),
        r.f.e,
      ]
        .map(rrCsvCell)
        .join(','),
    );
  });
  return {
    csv: lines.join('\n') + '\n',
    count: rows.length,
    visits: visits,
    skipped: skipped,
  };
}

// ===========================================================================
// 2. Components
// ===========================================================================

var RR_STYLES = {
  streets: 'mapbox://styles/mapbox/light-v11',
  satellite: 'mapbox://styles/mapbox/satellite-streets-v12',
};

function RRSelect(props) {
  return (
    <label className="text-sm">
      <span className="block text-gray-600 mb-1">{props.label}</span>
      <select
        className="border border-gray-300 rounded-md px-2 py-1.5 text-sm bg-white"
        value={props.value}
        onChange={function (e) {
          props.onPick(e.target.value);
        }}
      >
        {props.options.map(function (o) {
          return (
            <option key={o[0]} value={o[0]}>
              {o[1]}
            </option>
          );
        })}
      </select>
    </label>
  );
}

function RRProgress(props) {
  var badge = {
    done: 'bg-emerald-100 text-emerald-700',
    error: 'bg-red-100 text-red-700',
    queued: 'bg-gray-100 text-gray-600',
    loading: 'bg-indigo-100 text-indigo-700',
  };
  var tasks = props.tasks;
  var pct = Math.round(
    ((props.nDone + props.nErr) / (tasks.length || 1)) * 100,
  );
  return (
    <div className="border border-gray-200 rounded-lg bg-white">
      <div className="px-4 py-3 text-sm font-medium text-gray-900">
        {props.nDone + '/' + tasks.length + ' sources loaded'}
        {props.nErr ? ' · ' + props.nErr + ' failed (Reload to retry)' : ''}
        {props.hard ? ' · hard refresh' : ''}
      </div>
      <div className="h-1.5 bg-gray-100">
        <div
          className={'h-1.5 ' + (props.nErr ? 'bg-red-400' : 'bg-indigo-500')}
          style={{ width: pct + '%' }}
        />
      </div>
      {!props.settled || props.nErr ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 px-4 py-3">
          {props.orgs.map(function (o) {
            return (
              <div key={o.org}>
                <div className="text-xs font-semibold text-gray-700 mb-1">
                  {o.org}
                </div>
                <ul className="space-y-1">
                  {tasks
                    .filter(function (t) {
                      return t.org === o.org;
                    })
                    .map(function (t) {
                      var st = props.status[t.key] || {
                        s: 'queued',
                        m: 'Waiting…',
                      };
                      return (
                        <li
                          key={t.key}
                          className="flex items-start gap-2 text-xs"
                        >
                          <span
                            className={
                              'px-1.5 py-0.5 rounded font-medium shrink-0 ' +
                              badge[st.s]
                            }
                          >
                            {st.s}
                          </span>
                          <span className="text-gray-700 shrink-0">
                            {t.label}
                          </span>
                          <span className="text-gray-500 break-words">
                            {st.m}
                          </span>
                        </li>
                      );
                    })}
                </ul>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function RRTable(props) {
  var cols = props.columns;
  var TS = React.useState(null),
    tip = TS[0],
    setTip = TS[1];
  return (
    <div className="space-y-3">
      {tip ? (
        <div className="border border-indigo-200 bg-indigo-50 text-sm text-gray-800 rounded-md px-3 py-2">
          <b>{tip[1] + ': '}</b>
          {tip[2]}
          <button
            type="button"
            className="ml-2 text-gray-500 cursor-pointer"
            onClick={function () {
              setTip(null);
            }}
          >
            ×
          </button>
        </div>
      ) : null}
      <div className="overflow-x-auto border border-gray-200 rounded-lg bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left px-3 py-2">Org</th>
              <th className="text-left px-3 py-2">Ward</th>
              {cols.map(function (c) {
                return (
                  <th key={c[0]} className="text-right px-3 py-2 align-bottom">
                    {c[1] + ' '}
                    <button
                      type="button"
                      className="cursor-pointer text-indigo-500 font-normal"
                      title={c[2]}
                      onClick={function () {
                        setTip(tip === c ? null : c);
                      }}
                    >
                      ⓘ
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {props.rows.map(function (r, i) {
              var inR1 = !r.sub && r.r1 > 0;
              var cls =
                'border-t border-gray-100 ' +
                (r.sub
                  ? 'bg-indigo-50 font-semibold'
                  : inR1
                    ? 'bg-amber-50'
                    : '');
              return (
                <tr key={i} className={cls}>
                  <td className="px-3 py-1.5">{r.org}</td>
                  <td className="px-3 py-1.5">
                    {r.ward}
                    {inR1 ? (
                      <span
                        className="ml-2 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-200 text-amber-900"
                        title={props.text.r1tag}
                      >
                        R1
                      </span>
                    ) : null}
                    {!r.sub && props.onMap ? (
                      <button
                        type="button"
                        className="ml-2 text-xs text-indigo-600 cursor-pointer"
                        title="Show this ward on the map"
                        onClick={function () {
                          props.onMap(r.org, r.ward);
                        }}
                      >
                        <i className="fa-solid fa-map-location-dot" />
                      </button>
                    ) : null}
                  </td>
                  {cols.map(function (c) {
                    return (
                      <td
                        key={c[0]}
                        className="px-3 py-1.5 text-right tabular-nums"
                      >
                        {rrCell(c[0], r[c[0]])}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-gray-500 space-y-1">
        <p className="text-xs font-semibold text-gray-700 mb-1">Column guide</p>
        {cols.map(function (c) {
          return (
            <p key={c[0]}>
              <b>{c[1] + ': '}</b>
              {c[2]}
            </p>
          );
        })}
        {(props.text.notes || []).map(function (n, i) {
          return <p key={'n' + i}>{n}</p>;
        })}
      </div>
    </div>
  );
}

// The Mapbox map. Built once; data changes upsert the sources. A style swap
// (Streets/Satellite) wipes every layer, so `draw` re-adds them on style.load.
function RRMapCanvas(props) {
  var el = React.useRef(null);
  var mapRef = React.useRef(null);
  var dataRef = React.useRef(props);
  var SS = React.useState('streets'),
    style = SS[0],
    setStyle = SS[1];
  dataRef.current = props;

  function draw(map) {
    var p = dataRef.current;
    var wa = p.workAreas || { type: 'FeatureCollection', features: [] };
    var wardFc = p.ward || { type: 'FeatureCollection', features: [] };
    window.ConnectMap.setSource(map, 'rr-ward', wardFc);
    window.ConnectMap.setSource(map, 'rr-wa', wa);
    if (!map.getLayer('rr-wa-fill')) {
      map.addLayer({
        id: 'rr-wa-fill',
        type: 'fill',
        source: 'rr-wa',
        paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.45 },
      });
      map.addLayer({
        id: 'rr-wa-line',
        type: 'line',
        source: 'rr-wa',
        paint: { 'line-color': ['get', 'color'], 'line-width': 1 },
      });
    }
    if (!map.getLayer('rr-ward-line')) {
      map.addLayer({
        id: 'rr-ward-line',
        type: 'line',
        source: 'rr-ward',
        paint: {
          'line-color': '#1f2937',
          'line-width': 2,
          'line-dasharray': [2, 1],
        },
      });
    }
    var filter = ['in', ['get', 'cat'], ['literal', p.visible]];
    map.setFilter('rr-wa-fill', filter);
    map.setFilter('rr-wa-line', filter);
  }

  React.useEffect(function () {
    if (!window.ConnectMap || !window.mapboxgl || !el.current) return;
    var map = window.ConnectMap.createMap(el.current, {
      center: [11.2, 9.8],
      zoom: 9,
      style: RR_STYLES.streets,
    });
    mapRef.current = map;
    map.addControl(new window.mapboxgl.NavigationControl(), 'top-right');
    map.on('load', function () {
      map._rrReady = true;
      draw(map);
      fitTo(map);
    });
    var popup = new window.mapboxgl.Popup({
      closeButton: true,
      maxWidth: '280px',
    });
    map.on('click', 'rr-wa-fill', function (e) {
      var f = e.features && e.features[0];
      if (!f) return;
      popup.setLngLat(e.lngLat).setHTML(rrPopupHtml(f.properties)).addTo(map);
    });
    map.on('mouseenter', 'rr-wa-fill', function () {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', 'rr-wa-fill', function () {
      map.getCanvas().style.cursor = '';
    });
    return function () {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  function fitTo(map) {
    var p = dataRef.current;
    var target =
      p.ward && p.ward.features.length
        ? p.ward
        : p.workAreas && p.workAreas.features.length
          ? p.workAreas
          : null;
    if (target) window.ConnectMap.fit(map, target, 24);
  }

  // New ward: redraw and refit.
  React.useEffect(
    function () {
      var map = mapRef.current;
      if (map && map._rrReady) {
        draw(map);
        fitTo(map);
      }
    },
    [props.fitKey],
  );

  // Same ward, new data or legend toggles: redraw only.
  React.useEffect(
    function () {
      var map = mapRef.current;
      if (map && map._rrReady) draw(map);
    },
    [props.workAreas, props.ward, props.visible.join(',')],
  );

  function pickStyle(key) {
    var map = mapRef.current;
    if (!map || key === style) return;
    setStyle(key);
    map.once('style.load', function () {
      draw(map);
    });
    map.setStyle(RR_STYLES[key]);
  }

  if (!window.ConnectMap || !window.mapboxgl) {
    return (
      <div className="p-4 text-sm text-red-700">
        The map library did not load on this page.
      </div>
    );
  }
  return (
    <div className="relative">
      <div ref={el} style={{ height: '620px', width: '100%' }} />
      <div className="absolute top-2 left-2 flex rounded-md shadow bg-white text-xs overflow-hidden">
        {['streets', 'satellite'].map(function (k) {
          return (
            <button
              key={k}
              type="button"
              className={
                'px-2 py-1 cursor-pointer ' +
                (style === k ? 'bg-indigo-600 text-white' : 'text-gray-700')
              }
              onClick={function () {
                pickStyle(k);
              }}
            >
              {k === 'streets' ? 'Streets' : 'Satellite'}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function RRMapTab(props) {
  var counts = {};
  RR_CATS.forEach(function (c) {
    counts[c.key] = 0;
  });
  (props.workAreas ? props.workAreas.features : []).forEach(function (f) {
    counts[f.properties.cat]++;
  });
  return (
    <div className="space-y-3">
      {props.message ? (
        <div className="border border-gray-200 rounded-md bg-gray-50 px-3 py-2 text-sm text-gray-700">
          {props.message}
        </div>
      ) : null}
      <div className="flex flex-wrap gap-3 text-xs">
        {RR_CATS.map(function (c) {
          var on = props.visible.indexOf(c.key) >= 0;
          return (
            <label
              key={c.key}
              className="inline-flex items-center gap-1 cursor-pointer select-none"
              style={{ opacity: on ? 1 : 0.45 }}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={function () {
                  props.onToggle(c.key);
                }}
              />
              <span
                style={{
                  display: 'inline-block',
                  width: '10px',
                  height: '10px',
                  borderRadius: '2px',
                  background: c.color,
                }}
              />
              {c.label + ' (' + rrFmt(counts[c.key]) + ')'}
            </label>
          );
        })}
      </div>
      <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
        <RRMapCanvas
          workAreas={props.workAreas}
          ward={props.ward}
          visible={props.visible}
          fitKey={props.fitKey}
        />
      </div>
      <p className="text-xs text-gray-500">
        Colour = the first reason that applies, in legend order (a work area
        already in R1 is green whatever else applies). Click a work area for its
        numbers. The dashed line is the ward boundary from Connect.
      </p>
    </div>
  );
}

// Download one organisation's R2 work areas as Connect's upload file. Two
// steps, because a download must follow the click directly and fetching the
// shapes can take longer than that: Prepare fetches, Download saves.
function RRDownloadTab(props) {
  var orgNames = props.orgs.map(function (o) {
    return o.org;
  });
  var OS = React.useState(props.org || orgNames[0]),
    org = OS[0],
    setOrg = OS[1];
  var WS = React.useState({}),
    wardOff = WS[0],
    setWardOff = WS[1];
  var RS = React.useState({ algo: true, susp: true, inacc: true, rej: true }),
    rules = RS[0],
    setRules = RS[1];
  var PS = React.useState({ key: '', state: 'idle' }),
    prep = PS[0],
    setPrep = PS[1];

  var o = props.orgs.filter(function (x) {
    return x.org === org;
  })[0];
  var info = props.byOrg[org];
  var wardOn = {};
  var perWard = {};
  ((info && info.wards) || []).forEach(function (w) {
    wardOn[w.ward] = !wardOff[w.ward];
    perWard[w.ward] = 0;
  });
  var allWards = {};
  Object.keys(perWard).forEach(function (w) {
    allWards[w] = true;
  });
  // Counts per ward for the chosen rules, whether or not the ward is ticked.
  if (info)
    rrSelectWorkAreas(info.was, allWards, rules).forEach(function (f) {
      perWard[f.ward]++;
    });
  var selected = info ? rrSelectWorkAreas(info.was, wardOn, rules) : [];
  var visits = selected.reduce(function (n, f) {
    return n + f.evc2;
  }, 0);
  var key = org + '|' + JSON.stringify(wardOff) + '|' + JSON.stringify(rules);
  var ready = prep.key === key && prep.state === 'ready';
  var busy = prep.key === key && prep.state === 'loading';

  function prepare() {
    var ids = selected.map(function (f) {
      return f.c;
    });
    var chunks = [];
    for (var i = 0; i < ids.length; i += 400)
      chunks.push(ids.slice(i, i + 400));
    var shapes = {};
    var done = 0;
    setPrep({ key: key, state: 'loading', msg: 'Fetching work-area shapes…' });
    function onStatus(msg) {
      setPrep({ key: key, state: 'loading', msg: String(msg) });
    }
    function step(n) {
      if (n >= chunks.length) return Promise.resolve();
      return props.actions
        .queryPipelineRows('wa_shapes', {
          filters: { wa_case_id: chunks[n] },
          limit: 500,
          opportunity_id: o.main,
          onStatus: onStatus,
        })
        .then(function (res) {
          (res.rows || []).forEach(function (r) {
            shapes[r.wa_case_id] = r;
          });
          done += chunks[n].length;
          setPrep({
            key: key,
            state: 'loading',
            msg:
              'Fetched ' +
              rrFmt(done) +
              ' of ' +
              rrFmt(ids.length) +
              ' work areas…',
          });
          return step(n + 1);
        });
    }
    step(0)
      .then(function () {
        var out = rrConnectCsv(selected, shapes, rules);
        setPrep(Object.assign({ key: key, state: 'ready' }, out));
      })
      .catch(function (e) {
        setPrep({
          key: key,
          state: 'error',
          msg: 'Could not fetch the shapes: ' + ((e && e.message) || e),
        });
      });
  }

  function download() {
    var d = new Date().toISOString().slice(0, 10);
    var name =
      'R2_work_areas_' +
      org.replace(/[^A-Za-z0-9_-]+/g, '_') +
      '_' +
      d +
      '.csv';
    props.actions
      .download({ filename: name, mime: 'text/csv', bytes: prep.csv })
      .catch(function (e) {
        setPrep(
          Object.assign({}, prep, {
            msg:
              'Download refused: ' +
              ((e && e.message) || e) +
              ' (click Download again)',
          }),
        );
      });
  }

  function toggleWard(w) {
    var next = Object.assign({}, wardOff);
    if (next[w]) delete next[w];
    else next[w] = true;
    setWardOff(next);
  }
  function setAllWards(on) {
    var next = {};
    if (!on)
      Object.keys(perWard).forEach(function (w) {
        next[w] = true;
      });
    setWardOff(next);
  }

  var btn =
    'px-3 py-2 text-sm rounded-md border disabled:opacity-50 cursor-pointer ';
  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-600">
        One organisation’s R2 work areas in the file Connect’s “Upload work
        areas” takes (Area Slug, Ward, Centroid, Boundary, Building Count,
        Expected Visit Count, Target Population, LGA, State, Implementation
        Area, Work Area Group Name). Work areas already in R1 are never
        included. Expected Visit Count is the work area’s buildings × its ward’s
        EVC per building in the main opportunity. Slugs, wards, LGA, State and
        group names are the original opportunity’s. The last two columns (R2
        Reason, Original Expected Visit Count) are for review; Connect ignores
        them.
      </p>
      <div className="flex flex-wrap items-end gap-4">
        <RRSelect
          label="Organisation"
          value={org}
          onPick={function (v) {
            setOrg(v);
            setWardOff({});
          }}
          options={orgNames.map(function (n) {
            return [n, n];
          })}
        />
        <div className="text-sm">
          <span className="block text-gray-600 mb-1">
            Include work areas with
          </span>
          <div className="flex flex-wrap gap-3">
            {RR_RULES.map(function (r) {
              return (
                <label
                  key={r.key}
                  className="inline-flex items-center gap-1 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={!!rules[r.key]}
                    onChange={function () {
                      var next = Object.assign({}, rules);
                      next[r.key] = !next[r.key];
                      setRules(next);
                    }}
                  />
                  {r.label}
                </label>
              );
            })}
          </div>
        </div>
      </div>
      {!info ? (
        <div className="border border-gray-200 rounded-md bg-gray-50 px-3 py-2 text-sm text-gray-700">
          {'Waiting for ' + org + '’s data to load…'}
        </div>
      ) : (
        <div className="border border-gray-200 rounded-lg bg-white">
          <div className="flex items-center justify-between px-4 py-2 border-b border-gray-100 text-sm">
            <span className="font-medium text-gray-900">Wards</span>
            <span className="space-x-3">
              <button
                type="button"
                className="text-indigo-600 cursor-pointer"
                onClick={function () {
                  setAllWards(true);
                }}
              >
                All
              </button>
              <button
                type="button"
                className="text-indigo-600 cursor-pointer"
                onClick={function () {
                  setAllWards(false);
                }}
              >
                None
              </button>
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-1 px-4 py-3 text-sm">
            {Object.keys(perWard)
              .sort()
              .map(function (w) {
                return (
                  <label
                    key={w}
                    className="inline-flex items-center gap-2 cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={!!wardOn[w]}
                      onChange={function () {
                        toggleWard(w);
                      }}
                    />
                    <span>{w}</span>
                    <span className="text-gray-500 tabular-nums">
                      {'(' + rrFmt(perWard[w]) + ')'}
                    </span>
                  </label>
                );
              })}
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <div className="text-sm text-gray-800">
          <b>{rrFmt(selected.length)}</b>
          {' work areas · '}
          <b>{rrFmt(visits)}</b>
          {' expected visits'}
        </div>
        <button
          type="button"
          className={btn + 'border-gray-300 bg-white'}
          disabled={
            !info ||
            !selected.length ||
            busy ||
            !props.actions.queryPipelineRows
          }
          onClick={prepare}
        >
          <i className="fa-solid fa-gears mr-1" />
          {ready ? 'Prepare again' : 'Prepare CSV'}
        </button>
        <button
          type="button"
          className={btn + 'border-indigo-600 bg-indigo-600 text-white'}
          disabled={!ready || !props.actions.download}
          onClick={download}
        >
          <i className="fa-solid fa-file-csv mr-1" />
          {ready
            ? 'Download CSV (' + rrFmt(prep.count) + ' rows)'
            : 'Download CSV'}
        </button>
      </div>
      {prep.key === key && prep.msg ? (
        <div className="text-sm text-gray-700">{prep.msg}</div>
      ) : null}
      {ready && prep.skipped.length ? (
        <div className="border border-amber-200 bg-amber-50 rounded-md px-3 py-2 text-sm text-amber-900">
          {rrFmt(prep.skipped.length) + ' work areas left out of the file: '}
          {prep.skipped
            .slice(0, 20)
            .map(function (x) {
              return x.slug + ' (' + x.why + ')';
            })
            .join(', ')}
          {prep.skipped.length > 20 ? ' …' : ''}
        </div>
      ) : null}
      {prep.key && prep.key !== key ? (
        <div className="text-xs text-gray-500">
          The choices changed since the last Prepare: prepare again to download
          them.
        </div>
      ) : null}
    </div>
  );
}

// ===========================================================================
// 3. Page
// ===========================================================================

function WorkflowUI(props) {
  var def = props.definition;
  var inst = props.instance;
  var actions = props.actions || {};
  var cfg = (def && def.config) || {};
  var TX = cfg.text || {};
  var ORGS = cfg.orgs || [];
  var SRC = cfg.sources || [];
  var COLS = cfg.columns || [];
  var AL = Object.assign({}, RR_ALGO_DEFAULTS, cfg.algo || {});
  var LIMIT = cfg.rows_limit || 20000;
  var PID = cfg.program_id;

  var tasks = React.useMemo(function () {
    var out = [];
    ORGS.forEach(function (o) {
      SRC.forEach(function (s) {
        out.push({
          key: o.org + ':' + s[0],
          org: o.org,
          kind: s[0],
          alias: s[1],
          label: s[2],
          opp: s[3] ? o.r1 : o.main,
        });
      });
    });
    return out;
  }, []);

  var LS = React.useState({ n: 0, hard: false }),
    load = LS[0],
    setLoad = LS[1];
  var STS = React.useState({}),
    status = STS[0],
    setStatus = STS[1];
  var data = React.useRef({});
  var TK = React.useState(0),
    tick = TK[0],
    setTick = TK[1];
  var OF = React.useState('ALL'),
    orgF = OF[0],
    setOrgF = OF[1];
  var WF = React.useState('ALL'),
    wardF = WF[0],
    setWardF = WF[1];
  var TB = React.useState('table'),
    tab = TB[0],
    setTab = TB[1];
  var VS = React.useState(
      RR_CATS.map(function (c) {
        return c.key;
      }),
    ),
    visible = VS[0],
    setVisible = VS[1];
  var MS = React.useState({ key: '', shapes: null, ward: null, msg: '' }),
    mapData = MS[0],
    setMapData = MS[1];
  var bounds = React.useRef({});

  function patch(key, v) {
    setStatus(function (prev) {
      var next = Object.assign({}, prev);
      next[key] = Object.assign({}, prev[key], v);
      return next;
    });
  }

  // Load every (org, source) through the pipeline-rows stream, a few at a time.
  React.useEffect(
    function () {
      var m = String(location.pathname).match(/\/workflow\/(\d+)\//);
      var id = (inst && inst.definition_id) || (m && +m[1]);
      var queue = tasks.slice();
      var stopped = false;
      var live = [];
      data.current = {};
      setStatus({});
      function next() {
        var t = !stopped && queue.shift();
        if (!t) return;
        patch(t.key, { s: 'loading', m: 'Starting…' });
        var es = new EventSource(
          '/labs/workflow/api/' +
            id +
            '/pipeline-rows/stream/?program_id=' +
            PID +
            '&owning_program_id=' +
            PID +
            '&alias=' +
            t.alias +
            '&rows_opportunity_id=' +
            t.opp +
            '&limit=' +
            LIMIT +
            (load.hard ? '&refresh=1' : ''),
          { withCredentials: true },
        );
        live.push(es);
        function end(v) {
          es.close();
          if (!stopped) {
            patch(t.key, v);
            next();
          }
        }
        es.addEventListener('message', function (ev) {
          var d = JSON.parse(ev.data);
          if (d.error) return end({ s: 'error', m: d.error });
          if (d.data && d.data.rows) {
            var rows = d.data.rows;
            data.current[t.key] = rrCompact(t.kind, rows);
            setTick(function (n) {
              return n + 1;
            });
            return end({
              s: 'done',
              m:
                rrFmt(rows.length) +
                ' rows' +
                (rows.length >= LIMIT ? ' — ROW LIMIT, incomplete' : ''),
            });
          }
          if (d.message && !stopped) patch(t.key, { m: d.message });
        });
        es.addEventListener('error', function () {
          end({ s: 'error', m: 'Connection lost' });
        });
      }
      for (var i = 0; i < (cfg.concurrency || 3); i++) next();
      return function () {
        stopped = true;
        live.forEach(function (es) {
          es.close();
        });
      };
    },
    [load.n],
  );

  var byOrg = React.useMemo(
    function () {
      var out = {};
      ORGS.forEach(function (o) {
        var d = {};
        var ok = ['wa_main', 'wa_r1', 'workers', 'rejected', 'visits'].every(
          function (k) {
            d[k] = data.current[o.org + ':' + k];
            return !!d[k];
          },
        );
        if (ok) out[o.org] = rrClassifyOrg(d, COLS, AL);
      });
      return out;
    },
    [tick],
  );

  var orgs = ORGS.map(function (o) {
    return o.org;
  }).filter(function (o) {
    return orgF === 'ALL' || o === orgF;
  });
  var wardOpts = [];
  orgs.forEach(function (o) {
    ((byOrg[o] || {}).wards || []).forEach(function (w) {
      wardOpts.push([
        o + '|' + w.ward,
        (orgF === 'ALL' ? o + ' · ' : '') + w.ward,
      ]);
    });
  });
  var wf = wardOpts.some(function (w) {
    return w[0] === wardF;
  })
    ? wardF
    : 'ALL';

  var trs = [];
  var all = [];
  orgs.forEach(function (o) {
    if (!byOrg[o]) return;
    var rows = byOrg[o].wards.filter(function (w) {
      return wf === 'ALL' || wf === o + '|' + w.ward;
    });
    rows.forEach(function (w) {
      trs.push(Object.assign({ org: o }, w));
      all.push(w);
    });
    if (wf === 'ALL')
      trs.push(
        Object.assign(
          { org: o, sub: 1, ward: o + ' total' },
          rrSum(rows, COLS),
        ),
      );
  });
  if (orgs.length > 1 && all.length)
    trs.push(
      Object.assign({ org: 'All', sub: 2, ward: 'Total' }, rrSum(all, COLS)),
    );

  var nDone = 0;
  var nErr = 0;
  tasks.forEach(function (t) {
    var s = (status[t.key] || {}).s;
    if (s === 'done') nDone++;
    if (s === 'error') nErr++;
  });
  var settled = nDone + nErr === tasks.length;

  // ---- Map tab: the selected ward's shapes and boundary, fetched on demand.
  var sel = wf === 'ALL' ? null : wf.split('|');
  var selOrg = sel
    ? ORGS.filter(function (o) {
        return o.org === sel[0];
      })[0]
    : null;
  var selInfo = sel && byOrg[sel[0]];
  var selWard = selInfo
    ? selInfo.wards.filter(function (w) {
        return w.ward === sel[1];
      })[0]
    : null;
  var mapKey = sel && selInfo && selWard ? wf : '';

  React.useEffect(
    function () {
      // Skip only when this ward's shapes are already here: a fetch cut short
      // (the tab left, or a failure) leaves none, so coming back starts it again.
      if (tab !== 'map' || !mapKey) return;
      if (mapData.key === mapKey && mapData.shapes) return;
      if (!actions.queryPipelineRows) {
        setMapData({
          key: mapKey,
          shapes: [],
          ward: null,
          msg: 'This page cannot query map data.',
        });
        return;
      }
      var cancelled = false;
      var raws = Object.keys(selWard.raws);
      setMapData({
        key: mapKey,
        shapes: null,
        ward: null,
        msg: 'Loading work-area shapes…',
      });
      function onStatus(msg) {
        if (!cancelled)
          setMapData(function (p) {
            return Object.assign({}, p, { msg: String(msg) });
          });
      }
      // Every row of an on-demand source, 500 at a time.
      function pageAll(alias, opts, offset, acc) {
        return actions
          .queryPipelineRows(
            alias,
            Object.assign(
              { limit: 500, offset: offset, opportunity_id: selOrg.main },
              opts,
            ),
          )
          .then(function (res) {
            var got = res.rows || [];
            var rows = acc.concat(got);
            if (got.length === 500 && rows.length < (res.total || 0))
              return pageAll(alias, opts, offset + 500, rows);
            return rows;
          });
      }
      // The ward boundary is extra: if it fails, say so and still draw the
      // work areas. Cached per organisation only once it has loaded.
      function wardBounds() {
        var cached = bounds.current[selOrg.org];
        if (cached) return Promise.resolve({ rows: cached });
        return pageAll('ward_bounds', {}, 0, [])
          .then(function (rows) {
            bounds.current[selOrg.org] = rows;
            return { rows: rows };
          })
          .catch(function (e) {
            return { rows: [], error: (e && e.message) || String(e) };
          });
      }
      Promise.all([
        pageAll(
          'wa_shapes',
          { filters: { ward: raws }, onStatus: onStatus },
          0,
          [],
        ),
        wardBounds(),
      ])
        .then(function (r) {
          if (cancelled) return;
          var wardFc = rrWardFeature(r[1].rows, sel[1]);
          setMapData({
            key: mapKey,
            shapes: r[0],
            ward: wardFc,
            msg: r[1].error
              ? 'Could not load the ward boundary: ' + r[1].error
              : wardFc
                ? ''
                : 'No ward boundary found in Connect for this ward.',
          });
        })
        .catch(function (e) {
          if (!cancelled)
            setMapData({
              key: mapKey,
              shapes: null,
              ward: null,
              msg:
                'Could not load map data: ' +
                ((e && e.message) || e) +
                ' (open the Map tab again to retry)',
            });
        });
      return function () {
        cancelled = true;
        // Drop an unfinished fetch's placeholder so the next visit refetches.
        setMapData(function (p) {
          return p.key === mapKey && !p.shapes
            ? { key: '', shapes: null, ward: null, msg: '' }
            : p;
        });
      };
    },
    [tab, mapKey],
  );

  var waFc = React.useMemo(
    function () {
      if (!selInfo || mapData.key !== mapKey || !mapData.shapes) return null;
      return rrWorkAreaFeatures(mapData.shapes, selInfo.was);
    },
    [mapData, mapKey, tick],
  );

  var mapMessage = !sel
    ? 'Pick an organisation and a ward above to see it on the map.'
    : !selInfo
      ? 'Waiting for ' + sel[0] + '’s data to load…'
      : mapData.key === mapKey
        ? mapData.msg
        : 'Loading…';

  function toggleCat(k) {
    setVisible(function (v) {
      return v.indexOf(k) >= 0
        ? v.filter(function (x) {
            return x !== k;
          })
        : v.concat([k]);
    });
  }

  function showOnMap(org, ward) {
    setOrgF(org);
    setWardF(org + '|' + ward);
    setTab('map');
  }

  function button(label, hard, cls) {
    return (
      <button
        type="button"
        className={cls}
        disabled={!settled}
        title={hard ? TX.hard : TX.reload}
        onClick={function () {
          bounds.current = {};
          setMapData({ key: '', shapes: null, ward: null, msg: '' });
          setLoad({ n: load.n + 1, hard: hard });
        }}
      >
        {label}
      </button>
    );
  }

  function tabButton(key, label, icon) {
    var on = tab === key;
    return (
      <button
        type="button"
        className={
          'px-4 py-2 text-sm font-medium border-b-2 cursor-pointer ' +
          (on
            ? 'border-indigo-600 text-indigo-700'
            : 'border-transparent text-gray-500 hover:text-gray-700')
        }
        onClick={function () {
          setTab(key);
        }}
      >
        <i className={'fa-solid ' + icon + ' mr-1'} />
        {label}
      </button>
    );
  }

  return (
    <div className="space-y-5 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-3xl">
          <h1 className="text-2xl font-bold text-gray-900">{def.name}</h1>
          <p className="text-sm text-gray-600 mt-1">{TX.intro}</p>
        </div>
        <div className="flex gap-2">
          {button(
            'Reload',
            false,
            'px-3 py-2 text-sm rounded-md border border-gray-300 bg-white disabled:opacity-50',
          )}
          {button(
            'Hard refresh',
            true,
            'px-3 py-2 text-sm rounded-md border border-indigo-600 bg-indigo-600 text-white disabled:opacity-50',
          )}
        </div>
      </div>
      <RRProgress
        tasks={tasks}
        status={status}
        orgs={ORGS}
        nDone={nDone}
        nErr={nErr}
        settled={settled}
        hard={load.hard}
      />
      <div className="flex flex-wrap items-end gap-3">
        <RRSelect
          label="Organisation"
          value={orgF}
          onPick={function (v) {
            setOrgF(v);
            setWardF('ALL');
          }}
          options={[['ALL', 'All organisations']].concat(
            ORGS.map(function (o) {
              return [o.org, o.org];
            }),
          )}
        />
        <RRSelect
          label="Ward"
          value={wf}
          onPick={setWardF}
          options={[['ALL', 'All wards']].concat(wardOpts)}
        />
      </div>
      <div className="flex gap-1 border-b border-gray-200">
        {tabButton('table', 'Table', 'fa-table')}
        {tabButton('map', 'Map', 'fa-map')}
        {tabButton('download', 'Download CSV', 'fa-file-csv')}
      </div>
      {tab === 'table' ? (
        <RRTable rows={trs} columns={COLS} text={TX} onMap={showOnMap} />
      ) : tab === 'download' ? (
        <RRDownloadTab
          orgs={ORGS}
          byOrg={byOrg}
          actions={actions}
          org={orgF !== 'ALL' ? orgF : null}
        />
      ) : (
        <RRMapTab
          message={mapMessage}
          workAreas={waFc}
          ward={mapData.key === mapKey ? mapData.ward : null}
          visible={visible}
          onToggle={toggleCat}
          fitKey={mapKey + (waFc ? ':' + waFc.features.length : '')}
        />
      )}
    </div>
  );
}
