function WorkflowUI(props) {
  var definition = props.definition;
  var pipelines = props.pipelines;

  var MONTH_NAMES = [
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December',
  ];

  function monthKey(dateStr) {
    if (!dateStr) return null;
    var d = new Date(dateStr);
    if (isNaN(d.getTime())) return null;
    return MONTH_NAMES[d.getUTCMonth()] + ' ' + d.getUTCFullYear();
  }

  function monthSortValue(monthLabel) {
    var parts = monthLabel.split(' ');
    var mIdx = MONTH_NAMES.indexOf(parts[0]);
    var year = parseInt(parts[1], 10);
    return year * 100 + mIdx;
  }

  function usageLabel(valid, visited) {
    if (!visited) return 'Low';
    var r = valid / visited;
    if (r >= 0.75) return 'High';
    if (r >= 0.25) return 'Mixed';
    return 'Low';
  }

  function visitLabel(compliance) {
    if (compliance === 'on_time') return 'On time';
    if (compliance === 'delayed') return 'Delayed';
    if (compliance === 'missed') return 'Missed';
    return compliance || 'Unknown';
  }

  function derivePriority(r) {
    if (
      r.visit === 'Missed' ||
      r.functionality === 'Non-functional' ||
      r.stock === 'Empty'
    )
      return 'Urgent action';
    if (
      r.functionality === 'Functional with issue' ||
      r.unresolved ||
      r.stock === 'Low' ||
      r.visit === 'Delayed' ||
      r.usage === 'Low' ||
      r.usage === 'Mixed'
    ) {
      return 'Follow up';
    }
    return 'Monitor';
  }

  function reasonLabel(r) {
    if (r.stock === 'Empty') return 'No chlorine at visit';
    if (r.functionality === 'Non-functional') return 'Broken / unusable';
    if (r.visit === 'Missed') return 'Missed visit';
    if (r.openIssue === 'Tap' && r.functionality !== 'Non-functional')
      return 'Needs minor repair (tap)';
    if (r.openIssue === 'Tap') return 'Broken / unusable (tap)';
    if (r.openIssue === 'Frame' && r.functionality !== 'Non-functional')
      return 'Needs minor repair (frame)';
    if (r.openIssue === 'Frame') return 'Broken / unusable (frame)';
    if (r.visit === 'Delayed') return 'Late visit';
    if (r.usage === 'Low') return 'Low uptake';
    if (r.usage === 'Mixed') return 'Mixed uptake';
    if (r.waterpoint === 'Waterpoint not functional') return 'Waterpoint down';
    return 'Monitor';
  }

  var monthlyRows = React.useMemo(
    function () {
      var raw = (pipelines && pipelines.visits && pipelines.visits.rows) || [];
      var byEntity = {};
      raw.forEach(function (row) {
        var eid = row.entity_id || row.community || 'unknown';
        if (!byEntity[eid]) byEntity[eid] = [];
        byEntity[eid].push(row);
      });

      var out = [];
      Object.keys(byEntity).forEach(function (eid) {
        var visits = byEntity[eid].slice().sort(function (a, b) {
          return new Date(a.visit_date) - new Date(b.visit_date);
        });

        var lastKnown = null;
        var byMonth = {};
        var monthOrder = [];

        visits.forEach(function (v) {
          var mk = monthKey(v.visit_date);
          if (!mk) return;
          var visitLbl = visitLabel(v.compliance);
          var effective;
          if (visitLbl === 'Missed' && lastKnown) {
            effective = lastKnown;
          } else {
            effective = {
              functionality: v.functionality,
              stock: v.stock,
              open_issue: v.open_issue,
              waterpoint: v.waterpoint,
              hh_valid: v.hh_valid,
              hh_visited: v.hh_visited,
            };
            lastKnown = effective;
          }

          if (!byMonth[mk]) {
            byMonth[mk] = { visitsThisMonth: [] };
            monthOrder.push(mk);
          }
          byMonth[mk].visitsThisMonth.push({
            v: v,
            effective: effective,
            visitLbl: visitLbl,
          });
        });

        monthOrder.forEach(function (mk) {
          var entries = byMonth[mk].visitsThisMonth;
          var primary = entries[entries.length - 1];
          var hh_visited = parseInt(primary.effective.hh_visited, 10) || 0;
          var hh_valid = parseInt(primary.effective.hh_valid, 10) || 0;
          var usage = usageLabel(hh_valid, hh_visited);
          var unresolved =
            primary.effective.open_issue !== 'None' ||
            primary.effective.functionality !== 'Functional';

          var r = {
            entityId: eid,
            ward: primary.v.ward,
            community: primary.v.community,
            lga: primary.v.lga,
            month: mk,
            visit: primary.visitLbl,
            functionality: primary.effective.functionality,
            stock: primary.effective.stock,
            openIssue: primary.effective.open_issue,
            waterpoint: primary.effective.waterpoint,
            usage: usage,
            hhChecks:
              hh_visited +
              ' visited / ' +
              hh_valid +
              ' valid FCR ' +
              (hh_valid === 1 ? 'test' : 'tests'),
            unresolved: unresolved,
            secondVisitThisMonth: entries.length > 1,
            visitDate: primary.v.visit_date,
          };
          r.priority = derivePriority(r);
          r.reason = reasonLabel(r);
          out.push(r);
        });
      });

      return out;
    },
    [pipelines],
  );

  var months = React.useMemo(
    function () {
      var set = {};
      monthlyRows.forEach(function (r) {
        set[r.month] = true;
      });
      return Object.keys(set).sort(function (a, b) {
        return monthSortValue(a) - monthSortValue(b);
      });
    },
    [monthlyRows],
  );

  var _selectedMonth = React.useState('');
  var selectedMonth = _selectedMonth[0];
  var setSelectedMonth = _selectedMonth[1];
  var effectiveMonth = selectedMonth || months[months.length - 1] || '';

  var _ward = React.useState('ALL');
  var selectedWard = _ward[0];
  var setSelectedWard = _ward[1];

  var _dispenser = React.useState('ALL');
  var selectedDispenser = _dispenser[0];
  var setSelectedDispenser = _dispenser[1];

  var _search = React.useState('');
  var search = _search[0];
  var setSearch = _search[1];

  var _detail = React.useState(null);
  var activeDetail = _detail[0];
  var setActiveDetail = _detail[1];

  var wards = React.useMemo(
    function () {
      var set = {};
      monthlyRows.forEach(function (r) {
        set[r.ward] = true;
      });
      return Object.keys(set).sort();
    },
    [monthlyRows],
  );

  var baseFiltered = React.useMemo(
    function () {
      var s = (search || '').trim().toLowerCase();
      return monthlyRows.filter(function (r) {
        return (
          (selectedWard === 'ALL' || r.ward === selectedWard) &&
          (!s || r.community.toLowerCase().indexOf(s) !== -1)
        );
      });
    },
    [monthlyRows, selectedWard, search],
  );

  var dispensers = React.useMemo(
    function () {
      var set = {};
      baseFiltered.forEach(function (r) {
        set[r.community] = true;
      });
      return Object.keys(set).sort();
    },
    [baseFiltered],
  );

  var filteredAll = React.useMemo(
    function () {
      return baseFiltered.filter(function (r) {
        return r.month === effectiveMonth;
      });
    },
    [baseFiltered, effectiveMonth],
  );

  var filtered = React.useMemo(
    function () {
      if (selectedDispenser === 'ALL') return filteredAll;
      return filteredAll.filter(function (r) {
        return r.community === selectedDispenser;
      });
    },
    [filteredAll, selectedDispenser],
  );

  function pct(n, d) {
    return d ? Math.round((n / d) * 100) : 0;
  }

  var pillClass = {
    green: 'bg-green-100 text-green-800 border border-green-200',
    yellow: 'bg-yellow-100 text-yellow-800 border border-yellow-200',
    red: 'bg-red-100 text-red-800 border border-red-200',
  };

  function priorityPill(p) {
    return p === 'Urgent action'
      ? pillClass.red
      : p === 'Follow up'
      ? pillClass.yellow
      : pillClass.green;
  }
  function visitPill(v) {
    return v === 'On time'
      ? pillClass.green
      : v === 'Delayed'
      ? pillClass.yellow
      : pillClass.red;
  }
  function functionalityPill(f) {
    return f === 'Functional'
      ? pillClass.green
      : f === 'Functional with issue'
      ? pillClass.yellow
      : pillClass.red;
  }
  function stockPill(s) {
    return s === 'Full'
      ? pillClass.green
      : s === 'Low'
      ? pillClass.yellow
      : pillClass.red;
  }
  function usagePill(u) {
    return u === 'High'
      ? pillClass.green
      : u === 'Mixed'
      ? pillClass.yellow
      : pillClass.red;
  }

  if (activeDetail) {
    var history = monthlyRows
      .filter(function (r) {
        return r.community === activeDetail;
      })
      .sort(function (a, b) {
        return monthSortValue(a.month) - monthSortValue(b.month);
      });

    return (
      <div className="max-w-6xl mx-auto p-4 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-bold text-blue-800">
              {activeDetail} &mdash; visit history
            </h3>
            <div className="text-sm text-gray-500">
              {history.length ? history[0].ward : ''},{' '}
              {history.length ? history[0].lga : ''} &middot; synthetic data
            </div>
          </div>
          <button
            className="bg-white border border-blue-200 text-blue-700 rounded-lg px-3 py-2 text-sm font-semibold"
            onClick={function () {
              setActiveDetail(null);
            }}
          >
            Back to main list
          </button>
        </div>
        <div className="overflow-auto border border-gray-200 rounded-lg bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs">
                <th className="p-3">Month</th>
                <th className="p-3">Visit</th>
                <th className="p-3">Functionality</th>
                <th className="p-3">Stock</th>
                <th className="p-3">Waterpoint</th>
                <th className="p-3">Usage</th>
                <th className="p-3">Priority</th>
                <th className="p-3">Reason</th>
              </tr>
            </thead>
            <tbody>
              {history.map(function (r, idx) {
                return (
                  <tr key={idx} className="border-t border-gray-100">
                    <td className="p-3">{r.month}</td>
                    <td className="p-3">
                      <span
                        className={
                          'inline-flex px-2 py-1 rounded-full text-xs font-semibold ' +
                          visitPill(r.visit)
                        }
                      >
                        {r.visit}
                      </span>
                    </td>
                    <td className="p-3">
                      <span
                        className={
                          'inline-flex px-2 py-1 rounded-full text-xs font-semibold ' +
                          functionalityPill(r.functionality)
                        }
                      >
                        {r.functionality}
                      </span>
                    </td>
                    <td className="p-3">
                      <span
                        className={
                          'inline-flex px-2 py-1 rounded-full text-xs font-semibold ' +
                          stockPill(r.stock)
                        }
                      >
                        {r.stock}
                      </span>
                    </td>
                    <td className="p-3 text-xs text-gray-600">
                      {r.waterpoint}
                    </td>
                    <td className="p-3">
                      <span
                        className={
                          'inline-flex px-2 py-1 rounded-full text-xs font-semibold ' +
                          usagePill(r.usage)
                        }
                      >
                        {r.usage}
                      </span>
                    </td>
                    <td className="p-3">
                      <span
                        className={
                          'inline-flex px-2 py-1 rounded-full text-xs font-semibold ' +
                          priorityPill(r.priority)
                        }
                      >
                        {r.priority}
                      </span>
                    </td>
                    <td className="p-3 text-xs text-gray-600">{r.reason}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  var p1Rows = filtered
    .filter(function (r) {
      return r.priority === 'Urgent action';
    })
    .sort(function (a, b) {
      return a.community.localeCompare(b.community);
    });
  var p2Rows = filtered
    .filter(function (r) {
      return r.priority === 'Follow up';
    })
    .sort(function (a, b) {
      return a.community.localeCompare(b.community);
    });

  var missedCount = filteredAll.filter(function (r) {
    return r.visit === 'Missed';
  }).length;
  var urgentWards = {};
  filteredAll
    .filter(function (r) {
      return r.priority === 'Urgent action';
    })
    .forEach(function (r) {
      urgentWards[r.ward] = true;
    });

  var p1Kpis = [
    {
      name: 'Visited on time',
      value:
        pct(
          filteredAll.filter(function (r) {
            return r.visit === 'On time';
          }).length,
          filteredAll.length,
        ) + '%',
      note: '% of dispensers visited within 0-2 days of schedule',
      cls: 'text-green-700',
    },
    {
      name: 'Working dispenser',
      value:
        pct(
          filteredAll.filter(function (r) {
            return r.functionality !== 'Non-functional';
          }).length,
          filteredAll.length,
        ) + '%',
      note: '% dispensers functional at latest verified visit',
      cls: 'text-green-700',
    },
    {
      name: 'Empty when visited',
      value:
        pct(
          filteredAll.filter(function (r) {
            return r.stock === 'Empty';
          }).length,
          filteredAll.length,
        ) + '%',
      note: '% visits where dispenser was empty on arrival',
      cls: 'text-red-700',
    },
    {
      name: 'Non-functional',
      value:
        pct(
          filteredAll.filter(function (r) {
            return r.functionality === 'Non-functional';
          }).length,
          filteredAll.length,
        ) + '%',
      note: '% dispensers unusable at latest verified visit',
      cls: 'text-red-700',
    },
    {
      name: 'Urgent action wards',
      value: String(Object.keys(urgentWards).length),
      note: 'Wards with a dispenser needing immediate repair, refill, or missed-visit follow-up',
      cls: 'text-red-700',
    },
  ];

  var p2Kpis = [
    {
      name: 'Visited but late',
      value:
        pct(
          filteredAll.filter(function (r) {
            return r.visit === 'Delayed';
          }).length,
          filteredAll.length,
        ) + '%',
      note: '% visits completed 3+ days after schedule',
      cls: 'text-orange-700',
    },
    {
      name: 'Routine visits delayed',
      value:
        pct(
          filteredAll.filter(function (r) {
            return r.visit !== 'On time';
          }).length,
          filteredAll.length,
        ) + '%',
      note: '% dispensers with delayed or missed visits',
      cls: 'text-orange-700',
    },
    {
      name: 'Low uptake',
      value:
        pct(
          filteredAll.filter(function (r) {
            return r.usage === 'Low';
          }).length,
          filteredAll.length,
        ) + '%',
      note: '% dispensers where household checks suggest weak chlorine use',
      cls: 'text-orange-700',
    },
    {
      name: 'Mixed uptake',
      value:
        pct(
          filteredAll.filter(function (r) {
            return r.usage === 'Mixed';
          }).length,
          filteredAll.length,
        ) + '%',
      note: '% dispensers where household checks show mixed chlorine use',
      cls: 'text-orange-700',
    },
    {
      name: 'Multiple visits logged',
      value: String(
        filteredAll.filter(function (r) {
          return r.secondVisitThisMonth;
        }).length,
      ),
      note: 'Dispensers with more than one recorded visit this period',
      cls: 'text-blue-700',
    },
  ];

  function renderKpiGrid(items) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {items.map(function (k, idx) {
          return (
            <div
              key={idx}
              className="border border-gray-200 rounded-xl p-3 bg-white min-h-[150px]"
            >
              <div className="text-xs font-bold text-gray-700 min-h-[32px]">
                {k.name}
              </div>
              <div className={'text-2xl font-extrabold my-2 ' + k.cls}>
                {k.value}
              </div>
              <div className="text-xs text-gray-500 leading-snug">{k.note}</div>
            </div>
          );
        })}
      </div>
    );
  }

  function renderTable(rows) {
    if (!rows.length) {
      return (
        <div className="text-sm text-gray-500 p-3">
          No dispensers in this section for the selected filters.
        </div>
      );
    }
    return (
      <div className="overflow-auto border border-gray-200 rounded-lg bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-xs">
              <th className="p-3">#</th>
              <th className="p-3">Dispenser</th>
              <th className="p-3">Flag reason</th>
              <th className="p-3">Priority</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(function (r, idx) {
              return (
                <tr
                  key={r.community}
                  className="border-t border-gray-100 cursor-pointer hover:bg-blue-50"
                  onClick={function () {
                    setActiveDetail(r.community);
                  }}
                >
                  <td className="p-3">{idx + 1}</td>
                  <td className="p-3 font-semibold text-blue-700">
                    {r.community}
                  </td>
                  <td className="p-3">{r.reason}</td>
                  <td className="p-3">
                    <span
                      className={
                        'inline-flex px-2 py-1 rounded-full text-xs font-semibold ' +
                        priorityPill(r.priority)
                      }
                    >
                      {r.priority}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto p-4 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            {definition.name}
          </h1>
          <div className="text-sm text-gray-500">
            Priority-based operational overview &middot; 1 rider &middot; 25
            dispensers &middot; Jakusko LGA
          </div>
        </div>
      </div>

      <div className="bg-yellow-50 border border-yellow-300 text-yellow-800 rounded-xl px-4 py-2 text-center text-xs font-bold">
        &#9888; SYNTHETIC TEST DATA &mdash; generated for internal review only,
        does not reflect real Connect data
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 bg-white border border-gray-200 rounded-xl p-4">
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">
            Month
          </label>
          <select
            className="w-full border border-gray-300 rounded-lg h-10 px-2 text-sm"
            value={effectiveMonth}
            onChange={function (e) {
              setSelectedMonth(e.target.value);
              setSelectedDispenser('ALL');
              setActiveDetail(null);
            }}
          >
            {months.map(function (m) {
              return (
                <option key={m} value={m}>
                  {m}
                </option>
              );
            })}
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">
            Ward
          </label>
          <select
            className="w-full border border-gray-300 rounded-lg h-10 px-2 text-sm"
            value={selectedWard}
            onChange={function (e) {
              setSelectedWard(e.target.value);
              setSelectedDispenser('ALL');
            }}
          >
            <option value="ALL">All wards</option>
            {wards.map(function (w) {
              return (
                <option key={w} value={w}>
                  {w}
                </option>
              );
            })}
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">
            Select dispenser
          </label>
          <select
            className="w-full border border-gray-300 rounded-lg h-10 px-2 text-sm"
            value={selectedDispenser}
            onChange={function (e) {
              setSelectedDispenser(e.target.value);
            }}
          >
            <option value="ALL">All dispensers</option>
            {dispensers.map(function (d) {
              return (
                <option key={d} value={d}>
                  {d}
                </option>
              );
            })}
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">
            Search dispenser
          </label>
          <input
            className="w-full border border-gray-300 rounded-lg h-10 px-2 text-sm"
            placeholder="Search Settlement..."
            value={search}
            onChange={function (e) {
              setSearch(e.target.value);
              setSelectedDispenser('ALL');
            }}
          />
        </div>
      </div>

      <div className="bg-blue-50 border border-blue-200 text-blue-900 rounded-xl p-4 text-sm">
        <div className="font-bold mb-1">How to read this</div>
        <ul className="list-disc ml-5 space-y-1">
          <li>
            Priority 1 focuses on whether communities currently have access to
            chlorine, including missed rider visits where current status is
            unknown.
          </li>
          <li>Priority 2 focuses on delays and weaker performance signals.</li>
        </ul>
      </div>

      <div className="border border-red-200 rounded-xl overflow-hidden">
        <div className="bg-red-50 p-4">
          <h2 className="text-lg font-bold text-red-700">
            Priority 1 &mdash; Functionality and access
          </h2>
          <div className="text-sm text-gray-700 mt-1">
            Communities may not be getting access to chlorine due to refill or
            maintenance issues, including missed rider visits where current
            status is unknown.
          </div>
        </div>
        <div className="p-4 space-y-3">
          {renderKpiGrid(p1Kpis)}
          <div className="border border-red-200 bg-red-50 rounded-xl p-3">
            <div className="text-xs font-bold text-red-800">
              Priority 1 headline flag
            </div>
            <div className="text-lg font-extrabold text-red-600">
              {missedCount} missed visit{missedCount === 1 ? '' : 's'}
            </div>
            <div className="text-xs text-gray-500">
              Missed rider visits are treated as Priority 1 because current
              dispenser status may be unknown.
            </div>
          </div>
          <div>
            <div className="text-sm font-bold mb-1">
              High-priority dispensers
            </div>
            {renderTable(p1Rows)}
          </div>
        </div>
      </div>

      <div className="border border-yellow-300 rounded-xl overflow-hidden">
        <div className="bg-yellow-50 p-4">
          <h2 className="text-lg font-bold text-yellow-700">
            Priority 2 &mdash; Performance delays and usage concerns
          </h2>
          <div className="text-sm text-gray-700 mt-1">
            Maintenance delays or weak household use signals that need
            follow-up.
          </div>
        </div>
        <div className="p-4 space-y-3">
          {renderKpiGrid(p2Kpis)}
          <div>
            <div className="text-sm font-bold mb-1">Follow-up dispensers</div>
            {renderTable(p2Rows)}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap justify-center gap-6 text-xs text-gray-600 border border-gray-200 rounded-xl p-3 bg-white">
        <span>
          <span className="inline-block w-3 h-3 rounded-full bg-green-500 mr-1"></span>
          Green = acceptable
        </span>
        <span>
          <span className="inline-block w-3 h-3 rounded-full bg-yellow-400 mr-1"></span>
          Yellow = needs attention
        </span>
        <span>
          <span className="inline-block w-3 h-3 rounded-full bg-red-500 mr-1"></span>
          Red = urgent risk
        </span>
      </div>
    </div>
  );
}
