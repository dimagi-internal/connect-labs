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
  var last = cycles.length ? cycles[cycles.length - 1] : null;
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
      <div className="grid grid-cols-5 gap-3 mb-2">
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
      <p className="text-xs text-gray-600 mb-6">
        Goal: {Math.round(goal * 100)}% of the target. Green is at or above the
        goal, amber 75% to the goal, red below 75%, purple over 110% (the plan
        counted fewer children than were found). A cycle's target is that of the
        health centres that reported in it.
      </p>

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
              <th className="px-3 py-2">District, then health centre</th>
              <th className="px-3 py-2 text-right">Target</th>
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
        Districts are listed lowest first, by the latest cycle. Select one to
        see its health centres. A health centre with no plan on record has no
        target, so its children are not counted in any percentage.
      </p>

      <div className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-700">
        <div className="font-semibold text-gray-900 mb-1">How to read this</div>
        <p className="mb-2">
          <b>Reported coverage</b> is the number of distinct children recorded
          as given the dose in a cycle, divided by the target population of the
          health centres that reported in that cycle. A child seen twice in a
          cycle counts once. The target is each health centre's latest plan
          record (children aged 3 to 11 months plus 12 to 59 months).
        </p>
        <p className="mb-2">
          This is reported coverage against the administrative plan, not against
          the true number of children, so it can pass 100% where the plan
          counted too few. Comparing it with satellite population and with
          buildings reached is the next step on this page.
        </p>
        <p>
          {noPlan
            ? noPlan +
              ' of ' +
              (meta.health_centres || 0) +
              ' health centres have no plan record and are left out of the percentages. '
            : 'Every health centre that reported has a plan record. '}
          {meta.names_note ? meta.names_note : ''}
        </p>
      </div>
    </div>
  );
}
