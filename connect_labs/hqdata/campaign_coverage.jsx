function WorkflowUI({ definition, pipelines }) {
  var source = (pipelines || {}).coverage || {};
  var rows = source.rows || [];
  var meta = source.metadata || {};
  var failure = Object.values(meta.per_opp || {})
    .map(function (m) {
      return m.error;
    })
    .filter(Boolean)[0];
  var goal = meta.goal || 0.95;
  var cycles = meta.cycles || [];
  var [open, setOpen] = React.useState({});
  var [filter, setFilter] = React.useState('');
  var [cycle, setCycle] = React.useState(null);
  var reach = (pipelines || {}).reach || {};
  var reachRows = reach.rows || [];
  var reachMeta = reach.metadata || {};
  var reachFailure = Object.values(reachMeta.per_opp || {})
    .map(function (m) {
      return m.error;
    })
    .filter(Boolean)[0];

  function pct(v) {
    return v === null || v === undefined ? '–' : Math.round(v * 100) + '%';
  }
  function num(v) {
    return Math.round(v || 0).toLocaleString();
  }
  // Colour by how far the cycle is from the goal. Above 110% means the plan counted too few children.
  function shade(v) {
    if (v === null || v === undefined) return { bg: '#f3f4f6', fg: '#6b7280' };
    if (v > 1.1) return { bg: '#ede9fe', fg: '#5b21b6' };
    if (v >= goal) return { bg: '#dcfce7', fg: '#166534' };
    if (v >= 0.75) return { bg: '#fef3c7', fg: '#92400e' };
    return { bg: '#fee2e2', fg: '#991b1b' };
  }

  // One row = one health centre in one cycle. Coverage is children served over the plan of the centres that took part.
  function roll(list) {
    var by = {};
    list.forEach(function (r) {
      var c = (by[r.cycle] = by[r.cycle] || {
        served: 0,
        target: 0,
        centres: {},
        planned: {},
      });
      c.served += r.children_served || 0;
      c.centres[r.csi_id] = true;
      if (r.target !== null && r.target !== undefined && !c.planned[r.csi_id]) {
        c.planned[r.csi_id] = true;
        c.target += r.target;
      }
    });
    var out = {};
    Object.keys(by).forEach(function (k) {
      var c = by[k];
      out[k] = {
        served: c.served,
        target: c.target,
        centres: Object.keys(c.centres).length,
        planned: Object.keys(c.planned).length,
        share: c.target > 0 ? c.served / c.target : null,
      };
    });
    return out;
  }

  var all = roll(rows);
  var districts = {};
  rows.forEach(function (r) {
    (districts[r.district_id] = districts[r.district_id] || {
      id: r.district_id,
      name: r.district,
      rows: [],
    }).rows.push(r);
  });
  var list = Object.values(districts).map(function (d) {
    var centres = {};
    d.rows.forEach(function (r) {
      (centres[r.csi_id] = centres[r.csi_id] || {
        id: r.csi_id,
        name: r.csi,
        rows: [],
      }).rows.push(r);
    });
    return {
      id: d.id,
      name: d.name,
      by: roll(d.rows),
      centres: Object.values(centres)
        .map(function (c) {
          var plan = c.rows.find(function (r) {
            return r.target !== null && r.target !== undefined;
          });
          return {
            id: c.id,
            name: c.name,
            target: plan ? plan.target : null,
            by: roll(c.rows),
          };
        })
        .sort(function (a, b) {
          return String(a.name).localeCompare(String(b.name));
        }),
    };
  });
  // Order by the latest cycle most districts took part in (a cycle a few districts ran alone would scatter the list).
  var last = null;
  var mostRan = 0;
  cycles.forEach(function (c) {
    var ran = list.filter(function (d) {
      return d.by[c];
    }).length;
    if (ran >= mostRan) {
      mostRan = ran;
      last = c;
    }
  });
  list.sort(function (a, b) {
    var x = a.by[last] ? a.by[last].share : null;
    var y = b.by[last] ? b.by[last].share : null;
    if (x === null && y === null)
      return String(a.name).localeCompare(String(b.name));
    if (x === null) return 1;
    if (y === null) return -1;
    return x - y;
  });
  var q = filter.trim().toLowerCase();
  var shown = list.filter(function (d) {
    return (
      !q ||
      String(d.name).toLowerCase().indexOf(q) >= 0 ||
      d.centres.some(function (c) {
        return String(c.name).toLowerCase().indexOf(q) >= 0;
      })
    );
  });

  function cell(c) {
    var s = shade(c ? c.share : null);
    return (
      <td
        className="px-3 py-2 text-right text-sm"
        style={{ background: s.bg, color: s.fg }}
        title={
          c
            ? num(c.served) +
              ' children given the dose of a target of ' +
              num(c.target)
            : 'No records this cycle'
        }
      >
        {c ? pct(c.share) : '–'}
      </td>
    );
  }

  // The plan figure is the one in the district table above, so the two can never disagree.
  function reported(district, c) {
    var d = list.find(function (x) {
      return x.name === district;
    });
    return d ? d.by[c] : null;
  }
  var reachCycles = reachMeta.cycles || [];
  var busiest = null;
  var busiestN = 0;
  reachCycles.forEach(function (c) {
    var n = reachRows.filter(function (r) {
      return r.cycle === c;
    }).length;
    if (n >= busiestN) {
      busiestN = n;
      busiest = c;
    }
  });
  var activeCycle = cycle || busiest;
  var reachShown = reachRows
    .filter(function (r) {
      return r.cycle === activeCycle;
    })
    .sort(function (a, b) {
      var x =
        a.coverage_plan === null || a.coverage_plan === undefined
          ? 9
          : a.coverage_plan;
      var y =
        b.coverage_plan === null || b.coverage_plan === undefined
          ? 9
          : b.coverage_plan;
      return x - y;
    });
  function pcell(v, count, of) {
    var s2 = shade(v);
    return (
      <td
        className="px-3 py-2 text-right text-sm"
        style={{ background: s2.bg, color: s2.fg }}
        title={
          count !== undefined && count !== null
            ? num(count) + ' of ' + num(of)
            : ''
        }
      >
        {pct(v)}
      </td>
    );
  }

  var through = meta.data_through
    ? String(meta.data_through).slice(0, 10)
    : null;
  var noPlan = meta.health_centres_without_plan || 0;

  if (failure) {
    return (
      <div className="max-w-3xl rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
        {failure}
      </div>
    );
  }
  if (!rows.length) {
    return (
      <div className="max-w-3xl">
        <h1 className="text-2xl font-bold text-brand-indigo mb-2">
          {(definition && definition.name) || 'Campaign coverage'}
        </h1>
        <div className="rounded-md border border-yellow-300 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          No distribution records were found in this project. Sync the project
          and reload; if it has synced, the form paths this page reads are in
          its settings.
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl">
      <h1 className="text-2xl font-bold text-brand-indigo mb-1">
        {(definition && definition.name) || 'Campaign coverage'}
      </h1>
      <p className="text-sm text-gray-600 mb-4">
        Children given the dose, out of the target population each health centre
        planned for, in each cycle of the campaign.
        {through ? ' Records received up to ' + through + '.' : ''}
      </p>

      <h2 className="text-sm font-semibold text-gray-700 mb-2">
        Whole campaign, by cycle
      </h2>
      <div
        className="mb-2"
        style={{
          display: 'grid',
          gap: 12,
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        }}
      >
        {cycles.map(function (c) {
          var v = all[c];
          var s = shade(v ? v.share : null);
          return (
            <div
              key={c}
              className="rounded-lg border border-gray-200 p-3"
              style={{ background: s.bg }}
            >
              <div
                className="text-xs uppercase tracking-wide"
                style={{ color: s.fg }}
              >
                Cycle {c}
              </div>
              <div className="text-3xl font-bold" style={{ color: s.fg }}>
                {v ? pct(v.share) : '–'}
              </div>
              <div className="text-xs text-gray-700 mt-1">
                {v
                  ? num(v.served) + ' of ' + num(v.target) + ' children'
                  : 'no records'}
              </div>
              <div className="text-xs text-gray-600">
                {v ? v.centres + ' health centres reported' : ''}
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs mb-6">
        <span className="text-gray-600">Goal {Math.round(goal * 100)}%:</span>
        {[
          ['at or above the goal', 1],
          ['75% to the goal', 0.8],
          ['below 75%', 0.5],
          ['over 110%: the plan counted too few', 1.2],
        ].map(function (l) {
          var c = shade(l[1]);
          return (
            <span
              key={l[0]}
              className="rounded px-2 py-0.5"
              style={{ background: c.bg, color: c.fg }}
            >
              {l[0]}
            </span>
          );
        })}
      </div>

      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-gray-700">
          By health district
        </h2>
        <input
          value={filter}
          onChange={function (e) {
            setFilter(e.target.value);
          }}
          placeholder="Find a district or health centre"
          className="rounded border border-gray-300 px-2 py-1 text-sm w-64"
        />
      </div>
      <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto mb-2">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-gray-600 border-b border-gray-200">
              <th className="px-3 py-2" style={{ minWidth: 190 }}>
                District, then health centre
              </th>
              <th
                className="px-3 py-2 text-right"
                title="Children aged 3 to 59 months the health centres planned for"
              >
                Plan
              </th>
              {cycles.map(function (c) {
                return (
                  <th key={c} className="px-3 py-2 text-right">
                    Cycle {c}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.map(function (d) {
              var isOpen =
                open[d.id] ||
                (q &&
                  d.centres.some(function (c) {
                    return String(c.name).toLowerCase().indexOf(q) >= 0;
                  }));
              var planned = d.centres.reduce(function (n, c) {
                return n + (c.target || 0);
              }, 0);
              return [
                <tr
                  key={'d' + d.id}
                  className="border-t border-gray-100 cursor-pointer hover:bg-gray-50"
                  onClick={function () {
                    var next = Object.assign({}, open);
                    next[d.id] = !open[d.id];
                    setOpen(next);
                  }}
                >
                  <td className="px-3 py-2 font-medium text-gray-900">
                    {isOpen ? '▾ ' : '▸ '}
                    {d.name}{' '}
                    <span className="text-xs text-gray-500">
                      ({d.centres.length} health centres)
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-gray-700">
                    {num(planned)}
                  </td>
                  {cycles.map(function (c) {
                    return cell(d.by[c]);
                  })}
                </tr>,
              ].concat(
                isOpen
                  ? d.centres.map(function (c) {
                      return (
                        <tr
                          key={'c' + c.id}
                          className="border-t border-gray-50"
                        >
                          <td className="pl-8 pr-3 py-1 text-gray-800">
                            {c.name}
                          </td>
                          <td className="px-3 py-1 text-right text-gray-600">
                            {c.target === null ? 'no plan' : num(c.target)}
                          </td>
                          {cycles.map(function (cy) {
                            return cell(c.by[cy]);
                          })}
                        </tr>
                      );
                    })
                  : [],
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-gray-600 mb-6">
        Districts are listed lowest first, by cycle {last}. Select one to see
        its health centres. A health centre with no plan on record has no
        target, so its children are not counted in any percentage.
      </p>

      <h2 className="text-sm font-semibold text-gray-700 mb-1">
        Checking the plan against the satellite picture
      </h2>
      <p className="text-xs text-gray-600 mb-3">
        The same children, counted against three other things. Where they
        disagree is where to look.
      </p>
      {reachFailure ? (
        <div className="mb-6 rounded-md border border-yellow-300 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          {reachFailure}
        </div>
      ) : !reachRows.length ? (
        <div className="mb-6 rounded-md border border-gray-200 bg-white px-4 py-3 text-sm text-gray-600">
          Not available yet. The first time it takes a few minutes to compute,
          and it then updates after each sync.
        </div>
      ) : (
        <div className="mb-6">
          <div className="flex gap-2 mb-2">
            {reachCycles.map(function (c) {
              return (
                <button
                  key={c}
                  onClick={function () {
                    setCycle(c);
                  }}
                  className={
                    'rounded px-3 py-1 text-sm border ' +
                    (c === activeCycle
                      ? 'bg-brand-indigo text-white border-brand-indigo'
                      : 'bg-white text-gray-700 border-gray-300')
                  }
                >
                  Cycle {c}
                </button>
              );
            })}
          </div>
          <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-gray-600 border-b border-gray-200">
                  <th className="px-3 py-2" style={{ minWidth: 120 }}>
                    Health district
                  </th>
                  <th className="px-3 py-2 text-right">Against the plan</th>
                  <th className="px-3 py-2 text-right">
                    Against satellite estimate
                  </th>
                  <th className="px-3 py-2 text-right">
                    Against the whole district
                  </th>
                  <th className="px-3 py-2 text-right">Buildings reached</th>
                  <th
                    className="px-3 py-2 text-right"
                    title="Children given the dose whose form recorded a usable location: the satellite and buildings columns count only these"
                  >
                    Doses placed
                  </th>
                </tr>
              </thead>
              <tbody>
                {reachShown.map(function (r) {
                  return (
                    <tr key={r.district} className="border-t border-gray-100">
                      <td className="px-3 py-2 font-medium text-gray-900">
                        {r.district}
                      </td>
                      {pcell(
                        reported(r.district, r.cycle)
                          ? reported(r.district, r.cycle).share
                          : null,
                        reported(r.district, r.cycle)
                          ? reported(r.district, r.cycle).served
                          : null,
                        reported(r.district, r.cycle)
                          ? reported(r.district, r.cycle).target
                          : null,
                      )}
                      {pcell(
                        r.coverage_squares,
                        r.children_served,
                        r.eligible_squares,
                      )}
                      {pcell(
                        r.coverage_unit,
                        r.children_served,
                        r.eligible_unit,
                      )}
                      {pcell(
                        r.coverage_buildings,
                        r.buildings_reached,
                        r.buildings,
                      )}
                      {pcell(
                        r.served_total
                          ? r.served_placed / r.served_total
                          : null,
                        r.served_placed,
                        r.served_total,
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {reachMeta.stale ? (
            <p className="text-xs text-amber-700 mt-2">
              Updating: showing the previous result until the new data is
              processed.
            </p>
          ) : null}
          <details className="mt-2 text-xs text-gray-600">
            <summary className="cursor-pointer">What each column means</summary>
            <ul className="list-disc pl-5 mt-1 space-y-1">
              <li>Against the plan: the same figure as the table above.</li>
              <li>
                Against satellite estimate: children under five a population map
                puts in the 1 km squares where doses were recorded, times the
                eligible share.
              </li>
              <li>
                Against the whole district: the same over the whole admin
                district; shown only where the recorded doses cover at least
                half of its children.
              </li>
              <li>
                Buildings reached: of the nearest {reachMeta.catchment_k || 260}{' '}
                buildings to each health centre, the share with a recorded dose
                location within {reachMeta.max_dist_m || 30} m. The catchment is
                an approximation: no catchment boundaries exist.
              </li>
              <li>
                Doses placed: the share of children given the dose whose form
                recorded a usable location. The satellite and buildings columns
                count only those, so they are a floor where this is low.
              </li>
              <li>
                A dash means that comparison is not available for the district.
              </li>
            </ul>
          </details>
        </div>
      )}

      <details className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-700">
        <summary className="font-semibold text-gray-900 cursor-pointer">
          How these numbers are made
        </summary>
        <ul className="list-disc pl-5 mt-2 space-y-1">
          <li>
            Reported coverage: children recorded as given the dose in a cycle,
            out of the plan of the health centres that reported that cycle.
          </li>
          <li>A child seen twice in a cycle counts once.</li>
          <li>
            The plan is each health centre's latest plan record: children aged 3
            to 11 months plus 12 to 59 months.
          </li>
          <li>
            It is coverage against the plan, not against the true number of
            children, so it can pass 100% where the plan counted too few.
          </li>
          <li>
            {noPlan
              ? noPlan +
                ' of ' +
                (meta.health_centres || 0) +
                ' health centres have no plan record and are left out of the percentages.'
              : 'Every health centre that reported has a plan record.'}
          </li>
          {meta.names_note ? <li>{meta.names_note}</li> : null}
        </ul>
      </details>
    </div>
  );
}
