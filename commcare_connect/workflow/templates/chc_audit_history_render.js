// CHC Audit History render — JSX-free React via React.createElement.
// Three tabs: Audit History | Metric Detail | FLW Longitudinal
//
// Pipeline aliases (must match PIPELINE_SCHEMAS in chc_audit_history.py):
//   audit_reports  — one row per AuditReport
//   audit_entries  — one row per AuditReportEntry (FLW × report)
//   tasks          — one row per AssignedTask

// =========================================================================
// Helpers
// =========================================================================

var CHC_METRICS = [
  {
    key: 'camping_visit_building_ratio',
    label: 'Camping',
    full: 'Camping (Visit:Building Ratio)',
    flagFn: function (v) {
      return v > 0;
    },
  },
  {
    key: 'gender_ratio_deviation',
    label: 'Gender Ratio',
    full: 'Gender Ratio Deviation',
    flagFn: null,
  },
  {
    key: 'muac_photo_compliance',
    label: 'MUAC Photo',
    full: 'MUAC Photo Compliance',
    flagFn: function (v) {
      return v < 90;
    },
  },
  {
    key: 'age_heaping',
    label: 'Age Heaping',
    full: 'Age Heaping',
    flagFn: function (v) {
      return v > 15;
    },
  },
  {
    key: 'wa_coverage_to_visit_ratio',
    label: 'WA Coverage:Visit',
    full: 'WA Coverage to Visit Ratio',
    flagFn: function (v) {
      return v > 0.13;
    },
  },
  {
    key: 'inaccessible_wa_rate_early_warning',
    label: 'Inaccess. WA (Early)',
    full: 'Inaccessible WA Rate – Early Warning',
    flagFn: null,
  },
  {
    key: 'inaccessible_wa_rate_last_wag',
    label: 'Inaccess. WA (Last)',
    full: 'Inaccessible WA Rate – Last Completed WAG',
    flagFn: null,
  },
  {
    key: 'vaccine_rate',
    label: 'Vaccine Rate',
    full: 'Vaccine Rate',
    flagFn: null,
  },
  {
    key: 'vaccine_card_photo_compliance',
    label: 'Vaccine Card',
    full: 'Vaccine Card Photo Compliance',
    flagFn: null,
  },
  {
    key: 'muac_distribution_pattern_index',
    label: 'MDPI',
    full: 'MUAC Distribution Pattern Index (MDPI)',
    flagFn: null,
  },
];

function chcDateRange(start, end) {
  if (!start) return '—';
  var s = start.slice(0, 10);
  var e = end ? end.slice(0, 10) : '';
  if (!e) return s;
  // Format as "Apr 7 – May 5, 2026"
  var months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  function fmt(d) {
    var p = d.split('-');
    var mo = months[parseInt(p[1], 10) - 1] || p[1];
    return mo + ' ' + parseInt(p[2], 10);
  }
  function yr(d) {
    return d.split('-')[0];
  }
  return fmt(s) + ' – ' + fmt(e) + ', ' + yr(e);
}

function chcFmtPct(num, den) {
  if (den === 0 || den == null) return '—';
  return Math.round((num / den) * 100) + '% (' + num + '/' + den + ')';
}

function chcParseResults(raw) {
  if (!raw) return {};
  if (typeof raw === 'object') return raw;
  try {
    return JSON.parse(raw);
  } catch (e) {
    return {};
  }
}

function chcIsInPeriod(dateStr, periodStart, periodEnd) {
  if (!dateStr || !periodStart) return false;
  var d = dateStr.slice(0, 10);
  var s = periodStart.slice(0, 10);
  var e = periodEnd ? periodEnd.slice(0, 10) : '9999-12-31';
  return d >= s && d <= e;
}

// =========================================================================
// Sub-components (plain createElement)
// =========================================================================

var ce = React.createElement;

function ChcPill(props) {
  var completed = props.status === 'completed';
  return ce(
    'span',
    {
      className:
        'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wide ' +
        (completed
          ? 'bg-green-100 text-green-800'
          : 'bg-gray-100 text-gray-600'),
    },
    completed ? 'Completed' : 'Pending',
  );
}

function ChcSortTh(props) {
  var active = props.sortCol === props.colKey;
  var icon = active ? (props.sortDir === 'asc' ? '↑' : '↓') : '↕';
  return ce(
    'th',
    {
      className:
        'px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider cursor-pointer select-none whitespace-nowrap ' +
        (active
          ? 'bg-green-900 text-white'
          : 'bg-green-800 text-green-100 hover:bg-green-700'),
      onClick: function () {
        if (active) {
          props.onSort(props.colKey, props.sortDir === 'asc' ? 'desc' : 'asc');
        } else {
          props.onSort(props.colKey, 'asc');
        }
      },
    },
    props.label,
    ' ',
    ce('span', { className: 'opacity-60 text-xs' }, icon),
  );
}

function ChcPctCell(props) {
  var pct = props.den > 0 ? Math.round((props.num / props.den) * 100) : null;
  if (pct == null)
    return ce(
      'td',
      { className: 'px-3 py-2 text-right text-gray-400 text-sm' },
      '—',
    );
  var good = props.higherIsBetter !== false ? pct >= 70 : pct <= 30;
  return ce(
    'td',
    { className: 'px-3 py-2 text-right text-sm tabular-nums' },
    ce(
      'span',
      {
        className:
          'font-semibold ' + (good ? 'text-green-700' : 'text-amber-700'),
      },
      pct + '%',
    ),
    ce(
      'span',
      { className: 'text-gray-400 text-xs ml-1' },
      '(' + props.num + '/' + props.den + ')',
    ),
  );
}

// =========================================================================
// Tab 1 — Audit History
// =========================================================================

function ChcAuditHistory(props) {
  var reportRows = props.reportRows;
  var entryRows = props.entryRows;
  var taskRows = props.taskRows;
  var oppIds = props.oppIds;
  var oppNames = props.oppNames || {};
  var nameMap = props.nameMap || {};

  var _state = React.useState('all');
  var oppFilter = _state[0];
  var setOppFilter = _state[1];

  var _sortState = React.useState({ col: 'date_created', dir: 'desc' });
  var sort = _sortState[0];
  var setSort = _sortState[1];

  function handleSort(col, dir) {
    setSort({ col: col, dir: dir });
  }

  var filtered = React.useMemo(
    function () {
      return oppFilter === 'all'
        ? reportRows
        : reportRows.filter(function (r) {
            return String(r.opportunity_id) === oppFilter;
          });
    },
    [reportRows, oppFilter],
  );

  var enriched = React.useMemo(
    function () {
      return filtered.map(function (r) {
        var entries = entryRows.filter(function (e) {
          return (
            String(e.report_id) === String(r.report_id) ||
            String(e.report_id) === String(r.id)
          );
        });
        var passedN = entries.filter(function (e) {
          return (
            e.is_flagged !== '1' &&
            e.is_flagged !== 'true' &&
            e.is_flagged !== true
          );
        }).length;
        var periodTasks = taskRows.filter(function (t) {
          return (
            chcIsInPeriod(t.date_created, r.period_start, r.period_end) &&
            (oppFilter === 'all' || String(r.opportunity_id) === oppFilter)
          );
        });
        var workerSet = {};
        entries.forEach(function (e) {
          if (e.username) workerSet[e.username] = true;
        });
        var periodWorkerTasks = periodTasks.filter(function (t) {
          return workerSet[t.username];
        });
        var closedTasks = periodWorkerTasks.filter(function (t) {
          return t.status === 'closed' || t.status === 'completed';
        });
        var pendingWorkers = {};
        periodWorkerTasks.forEach(function (t) {
          if (t.status !== 'closed' && t.status !== 'completed') {
            pendingWorkers[t.username] = true;
          }
        });
        return {
          _r: r,
          flwCount: entries.length,
          passedN: passedN,
          totalTasks: periodWorkerTasks.length,
          closedTasks: closedTasks.length,
          pendingWorkers: Object.keys(pendingWorkers).length,
        };
      });
    },
    [filtered, entryRows, taskRows, oppFilter],
  );

  var sorted = React.useMemo(
    function () {
      var copy = enriched.slice();
      copy.sort(function (a, b) {
        var r = a._r,
          s = b._r;
        var va, vb;
        if (sort.col === 'date_created') {
          va = r.date_created || '';
          vb = s.date_created || '';
        } else if (sort.col === 'period') {
          va = r.period_start || '';
          vb = s.period_start || '';
        } else if (sort.col === 'flws') {
          va = a.flwCount;
          vb = b.flwCount;
        } else if (sort.col === 'status') {
          va = r.status || '';
          vb = s.status || '';
        } else if (sort.col === 'passed') {
          va = a.flwCount ? a.passedN / a.flwCount : -1;
          vb = b.flwCount ? b.passedN / b.flwCount : -1;
        } else if (sort.col === 'tasks') {
          va = a.totalTasks ? a.closedTasks / a.totalTasks : -1;
          vb = b.totalTasks ? b.closedTasks / b.totalTasks : -1;
        } else if (sort.col === 'ptask') {
          va = a.flwCount ? a.pendingWorkers / a.flwCount : -1;
          vb = b.flwCount ? b.pendingWorkers / b.flwCount : -1;
        } else if (sort.col === 'runby') {
          va =
            nameMap[r.completed_by_username] || r.completed_by_username || '';
          vb =
            nameMap[s.completed_by_username] || s.completed_by_username || '';
        } else {
          va = '';
          vb = '';
        }
        if (typeof va === 'number' && typeof vb === 'number')
          return sort.dir === 'asc' ? va - vb : vb - va;
        return sort.dir === 'asc'
          ? String(va).localeCompare(String(vb))
          : String(vb).localeCompare(String(va));
      });
      return copy;
    },
    [enriched, sort],
  );

  var thProps = { sortCol: sort.col, sortDir: sort.dir, onSort: handleSort };

  return ce(
    'div',
    { className: 'p-4 space-y-3' },
    // Filter bar
    ce(
      'div',
      { className: 'flex items-center gap-3 flex-wrap' },
      ce(
        'span',
        {
          className:
            'text-xs font-semibold uppercase tracking-wider text-gray-500',
        },
        'Opportunity',
      ),
      ce(
        'select',
        {
          className:
            'text-sm border border-gray-300 rounded px-2 py-1.5 bg-white',
          value: oppFilter,
          onChange: function (e) {
            setOppFilter(e.target.value);
          },
        },
        ce('option', { value: 'all' }, 'All Opportunities'),
        oppIds.map(function (id) {
          return ce(
            'option',
            { key: id, value: String(id) },
            oppNames[id] || 'Opp #' + id,
          );
        }),
      ),
      ce(
        'span',
        { className: 'text-xs text-gray-400' },
        sorted.length + ' report' + (sorted.length !== 1 ? 's' : ''),
      ),
    ),
    // Table
    ce(
      'div',
      {
        className:
          'rounded-lg overflow-hidden shadow-sm border border-gray-200',
      },
      ce(
        'div',
        { className: 'overflow-x-auto' },
        ce(
          'table',
          { className: 'w-full border-collapse bg-white text-sm' },
          ce(
            'thead',
            null,
            ce(
              'tr',
              null,
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: 'date_created', label: 'Created Date' },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: 'period', label: 'Audit Period' },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign({ colKey: 'flws', label: 'FLWs' }, thProps),
              ),
              ce(
                ChcSortTh,
                Object.assign({ colKey: 'status', label: 'Status' }, thProps),
              ),
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: 'passed', label: '% FLWs Passed' },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: 'tasks', label: '% Tasks Completed' },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: 'ptask', label: '% FLWs w/ Pending Task' },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign({ colKey: 'runby', label: 'Run By' }, thProps),
              ),
            ),
          ),
          ce(
            'tbody',
            null,
            sorted.length === 0
              ? ce(
                  'tr',
                  null,
                  ce(
                    'td',
                    {
                      colSpan: 8,
                      className: 'px-4 py-8 text-center text-gray-400 text-sm',
                    },
                    'No audit reports found',
                  ),
                )
              : sorted.map(function (row, i) {
                  var r = row._r;
                  var completed = r.status === 'completed';
                  return ce(
                    'tr',
                    {
                      key: i,
                      className:
                        'border-b border-gray-100 hover:bg-gray-50' +
                        (!completed ? ' text-gray-500' : ''),
                    },
                    ce(
                      'td',
                      { className: 'px-3 py-2 font-medium whitespace-nowrap' },
                      (r.date_created || '—').slice(0, 10),
                    ),
                    ce(
                      'td',
                      { className: 'px-3 py-2 whitespace-nowrap' },
                      chcDateRange(r.period_start, r.period_end),
                    ),
                    ce(
                      'td',
                      { className: 'px-3 py-2 text-right tabular-nums' },
                      completed ? row.flwCount : '—',
                    ),
                    ce(
                      'td',
                      { className: 'px-3 py-2' },
                      ce(ChcPill, { status: r.status }),
                    ),
                    ce(ChcPctCell, { num: row.passedN, den: row.flwCount }),
                    ce(ChcPctCell, {
                      num: row.closedTasks,
                      den: row.totalTasks,
                    }),
                    ce(ChcPctCell, {
                      num: row.pendingWorkers,
                      den: row.flwCount,
                      higherIsBetter: false,
                    }),
                    ce(
                      'td',
                      { className: 'px-3 py-2 text-sm' },
                      nameMap[r.completed_by_username] ||
                        r.completed_by_username ||
                        '—',
                    ),
                  );
                }),
          ),
        ),
      ),
    ),
  );
}

// =========================================================================
// Tab 2 — Metric Detail
// =========================================================================

function ChcMetricDetail(props) {
  var reportRows = props.reportRows;
  var entryRows = props.entryRows;
  var oppIds = props.oppIds;
  var oppNames = props.oppNames || {};
  var nameMap = props.nameMap || {};

  var _repState = React.useState('all');
  var repFilter = _repState[0];
  var setRepFilter = _repState[1];

  var _oppState = React.useState('all');
  var oppFilter = _oppState[0];
  var setOppFilter = _oppState[1];

  var completedReports = React.useMemo(
    function () {
      return reportRows.filter(function (r) {
        return r.status === 'completed';
      });
    },
    [reportRows],
  );

  var filteredEntries = React.useMemo(
    function () {
      var rows = entryRows;
      if (oppFilter !== 'all') {
        var ridsForOpp = {};
        reportRows
          .filter(function (r) {
            return String(r.opportunity_id) === oppFilter;
          })
          .forEach(function (r) {
            ridsForOpp[String(r.report_id || r.id)] = true;
          });
        rows = rows.filter(function (e) {
          return ridsForOpp[String(e.report_id)];
        });
      }
      if (repFilter !== 'all') {
        rows = rows.filter(function (e) {
          return String(e.report_id) === repFilter;
        });
      }
      return rows;
    },
    [entryRows, reportRows, oppFilter, repFilter],
  );

  // One row per username, latest entry wins for metric values
  var flwRows = React.useMemo(
    function () {
      var byUser = {};
      filteredEntries.forEach(function (e) {
        if (!e.username) return;
        byUser[e.username] = e;
      });
      return Object.values(byUser).sort(function (a, b) {
        return (nameMap[a.username] || a.username || '').localeCompare(
          nameMap[b.username] || b.username || '',
        );
      });
    },
    [filteredEntries],
  );

  // Column flag totals
  var colTotals = React.useMemo(
    function () {
      return CHC_METRICS.map(function (m) {
        var total = 0;
        flwRows.forEach(function (row) {
          var res = chcParseResults(row.results);
          var v = res[m.key];
          if (v != null && m.flagFn && m.flagFn(parseFloat(v))) total++;
        });
        return total;
      });
    },
    [flwRows],
  );

  var grandTotal = colTotals.reduce(function (a, b) {
    return a + b;
  }, 0);

  return ce(
    'div',
    { className: 'p-4 space-y-3' },
    // Filters
    ce(
      'div',
      { className: 'flex items-center gap-3 flex-wrap' },
      ce(
        'span',
        {
          className:
            'text-xs font-semibold uppercase tracking-wider text-gray-500',
        },
        'Report',
      ),
      ce(
        'select',
        {
          className:
            'text-sm border border-gray-300 rounded px-2 py-1.5 bg-white',
          value: repFilter,
          onChange: function (e) {
            setRepFilter(e.target.value);
          },
        },
        ce('option', { value: 'all' }, 'All Completed Audits'),
        completedReports.map(function (r, i) {
          return ce(
            'option',
            { key: i, value: String(r.report_id || r.id) },
            (oppNames[r.opportunity_id] || 'Opp #' + r.opportunity_id) +
              ' · ' +
              chcDateRange(r.period_start, r.period_end),
          );
        }),
      ),
      ce(
        'span',
        {
          className:
            'text-xs font-semibold uppercase tracking-wider text-gray-500',
        },
        'Opportunity',
      ),
      ce(
        'select',
        {
          className:
            'text-sm border border-gray-300 rounded px-2 py-1.5 bg-white',
          value: oppFilter,
          onChange: function (e) {
            setOppFilter(e.target.value);
          },
        },
        ce('option', { value: 'all' }, 'All Opportunities'),
        oppIds.map(function (id) {
          return ce(
            'option',
            { key: id, value: String(id) },
            oppNames[id] || 'Opp #' + id,
          );
        }),
      ),
      ce(
        'span',
        { className: 'text-xs text-gray-400' },
        flwRows.length + ' worker' + (flwRows.length !== 1 ? 's' : ''),
      ),
    ),
    // Pivot table
    ce(
      'div',
      {
        className:
          'rounded-lg overflow-hidden shadow-sm border border-gray-200',
      },
      ce(
        'div',
        { className: 'overflow-x-auto' },
        ce(
          'table',
          { className: 'border-collapse bg-white text-xs' },
          ce(
            'thead',
            null,
            ce(
              'tr',
              null,
              ce(
                'th',
                {
                  className:
                    'sticky left-0 z-10 bg-green-800 text-green-100 px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider whitespace-nowrap min-w-40',
                  style: { position: 'sticky', left: 0 },
                },
                'Connect Worker',
              ),
              CHC_METRICS.map(function (m) {
                return ce(
                  'th',
                  {
                    key: m.key,
                    title: m.full,
                    className:
                      'bg-green-800 text-green-100 px-2 py-2 text-center font-semibold uppercase tracking-wider min-w-20 leading-tight',
                    style: { whiteSpace: 'normal', maxWidth: '80px' },
                  },
                  m.label,
                );
              }),
              ce(
                'th',
                {
                  className:
                    'bg-green-900 text-white px-3 py-2 text-center text-xs font-semibold uppercase tracking-wider',
                },
                'Flags',
              ),
            ),
          ),
          ce(
            'tbody',
            null,
            flwRows.length === 0
              ? ce(
                  'tr',
                  null,
                  ce(
                    'td',
                    {
                      colSpan: CHC_METRICS.length + 2,
                      className: 'px-4 py-8 text-center text-gray-400',
                    },
                    'No data for current filters',
                  ),
                )
              : flwRows.map(function (row, i) {
                  var res = chcParseResults(row.results);
                  var rowFlags = 0;
                  var cells = CHC_METRICS.map(function (m) {
                    var rawVal = res[m.key];
                    if (rawVal == null) {
                      return ce(
                        'td',
                        {
                          key: m.key,
                          className:
                            'border border-gray-200 px-2 py-1.5 text-center text-gray-400',
                        },
                        'N/A',
                      );
                    }
                    var v = parseFloat(rawVal);
                    var isFlagged = !isNaN(v) && m.flagFn && m.flagFn(v);
                    if (isFlagged) rowFlags++;
                    return ce(
                      'td',
                      {
                        key: m.key,
                        className:
                          'border border-gray-200 px-2 py-1.5 text-center tabular-nums ' +
                          (isFlagged
                            ? 'bg-amber-100 text-amber-800 font-semibold'
                            : ''),
                      },
                      isNaN(v) ? String(rawVal) : v,
                    );
                  });
                  return ce(
                    'tr',
                    { key: i, className: 'hover:bg-gray-50' },
                    ce(
                      'td',
                      {
                        className:
                          'border border-gray-200 px-3 py-1.5 text-left font-medium whitespace-nowrap sticky left-0 bg-white z-10',
                        style: { position: 'sticky', left: 0 },
                      },
                      nameMap[row.username] || row.username,
                    ),
                    cells,
                    ce(
                      'td',
                      {
                        className:
                          'border border-gray-200 px-2 py-1.5 text-center font-bold bg-green-900 text-white',
                      },
                      rowFlags || '0',
                    ),
                  );
                }),
          ),
          ce(
            'tfoot',
            null,
            ce(
              'tr',
              null,
              ce(
                'td',
                {
                  className:
                    'border border-gray-200 px-3 py-1.5 text-left font-bold text-xs uppercase bg-green-50 text-green-800 sticky left-0',
                  style: { position: 'sticky', left: 0 },
                },
                'Metric totals',
              ),
              colTotals.map(function (n, i) {
                return ce(
                  'td',
                  {
                    key: i,
                    className:
                      'border border-gray-200 px-2 py-1.5 text-center font-bold text-xs ' +
                      (n > 0
                        ? 'bg-amber-100 text-amber-800'
                        : 'bg-green-50 text-green-700'),
                  },
                  n || '0',
                );
              }),
              ce(
                'td',
                {
                  className:
                    'border border-gray-200 px-2 py-1.5 text-center font-bold bg-green-900 text-white',
                },
                grandTotal,
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

// =========================================================================
// Tab 3 — FLW Longitudinal
// =========================================================================

function ChcFLWLongitudinal(props) {
  var reportRows = props.reportRows;
  var entryRows = props.entryRows;
  var taskRows = props.taskRows;
  var workers = props.workers;
  var oppIds = props.oppIds;
  var oppNames = props.oppNames || {};
  var nameMap = props.nameMap || {};

  var _oppState = React.useState('all');
  var oppFilter = _oppState[0];
  var setOppFilter = _oppState[1];

  // Build sorted cycles: newest first
  var cycles = React.useMemo(
    function () {
      var filteredReports =
        oppFilter === 'all'
          ? reportRows
          : reportRows.filter(function (r) {
              return String(r.opportunity_id) === oppFilter;
            });
      var completedReports = filteredReports.filter(function (r) {
        return r.status === 'completed';
      });
      // Group by period_start to identify unique cycles
      var periodMap = {};
      completedReports.forEach(function (r) {
        var key = (r.period_start || '').slice(0, 10);
        if (!periodMap[key]) periodMap[key] = [];
        periodMap[key].push(r);
      });
      // Sort keys descending (newest first)
      return Object.keys(periodMap)
        .sort(function (a, b) {
          return b.localeCompare(a);
        })
        .map(function (key) {
          return { period_start: key, reports: periodMap[key] };
        });
    },
    [reportRows, oppFilter],
  );

  // Build list of unique FLWs
  var flwList = React.useMemo(
    function () {
      var seen = {};
      var list = [];
      entryRows.forEach(function (e) {
        if (e.username && !seen[e.username]) {
          seen[e.username] = true;
          list.push(e.username);
        }
      });
      // Also include workers from worker list
      (workers || []).forEach(function (w) {
        var uname = w.username || '';
        if (uname && !seen[uname]) {
          seen[uname] = true;
          list.push(uname);
        }
      });
      return list.sort(function (a, b) {
        return (nameMap[a] || a).localeCompare(nameMap[b] || b);
      });
    },
    [entryRows, workers],
  );

  // Per-FLW per-cycle stats
  var flwCycleData = React.useMemo(
    function () {
      return flwList.map(function (username) {
        var totalFlags = 0;
        var cycleStats = cycles.map(function (cycle) {
          var cycleReports = cycle.reports;
          var entries = entryRows.filter(function (e) {
            return (
              e.username === username &&
              cycleReports.some(function (r) {
                return String(e.report_id) === String(r.report_id || r.id);
              })
            );
          });
          if (!entries.length) return null;
          var flagCount = entries.filter(function (e) {
            return (
              e.is_flagged === true ||
              e.is_flagged === '1' ||
              e.is_flagged === 'true'
            );
          }).length;
          var metricFlags = 0;
          entries.forEach(function (e) {
            var res = chcParseResults(e.results);
            CHC_METRICS.forEach(function (m) {
              var v = res[m.key];
              if (v != null && m.flagFn && m.flagFn(parseFloat(v)))
                metricFlags++;
            });
          });
          var cycleTasks = taskRows.filter(function (t) {
            return (
              t.username === username &&
              cycleReports.some(function (r) {
                return chcIsInPeriod(
                  t.date_created,
                  r.period_start,
                  r.period_end,
                );
              })
            );
          });
          var closedTasks = cycleTasks.filter(function (t) {
            return t.status === 'closed' || t.status === 'completed';
          }).length;
          totalFlags += metricFlags;
          return {
            metricFlags: metricFlags,
            totalMetrics: entries.length * CHC_METRICS.length,
            totalTasks: cycleTasks.length,
            closedTasks: closedTasks,
            label: chcDateRange(
              cycleReports[0].period_start,
              cycleReports[0].period_end,
            ),
          };
        });
        return {
          username: username,
          totalFlags: totalFlags,
          cycles: cycleStats,
        };
      });
    },
    [flwList, cycles, entryRows, taskRows],
  );

  function severityCls(flags) {
    if (flags === 0) return '';
    if (flags === 1) return 'bg-amber-50';
    if (flags === 2) return 'bg-amber-100';
    return 'bg-amber-200';
  }

  return ce(
    'div',
    { className: 'p-4 space-y-3' },
    // Filter bar
    ce(
      'div',
      { className: 'flex items-center gap-3 flex-wrap' },
      ce(
        'span',
        {
          className:
            'text-xs font-semibold uppercase tracking-wider text-gray-500',
        },
        'Opportunity',
      ),
      ce(
        'select',
        {
          className:
            'text-sm border border-gray-300 rounded px-2 py-1.5 bg-white',
          value: oppFilter,
          onChange: function (e) {
            setOppFilter(e.target.value);
          },
        },
        ce('option', { value: 'all' }, 'All Opportunities'),
        oppIds.map(function (id) {
          return ce(
            'option',
            { key: id, value: String(id) },
            oppNames[id] || 'Opp #' + id,
          );
        }),
      ),
    ),
    // Legend
    ce(
      'div',
      { className: 'flex gap-3 text-xs text-gray-500 flex-wrap items-center' },
      ['0 flags', '1 flag', '2 flags', '3+ flags'].map(function (lbl, i) {
        var bg = [
          'bg-white border-gray-200',
          'bg-amber-50 border-amber-200',
          'bg-amber-100 border-amber-300',
          'bg-amber-200 border-amber-400',
        ][i];
        return ce(
          'span',
          { key: i, className: 'flex items-center gap-1' },
          ce('span', {
            className: 'inline-block w-3 h-3 rounded border ' + bg,
          }),
          lbl,
        );
      }),
      ce('span', { className: 'text-green-700 font-medium ml-2' }, '% passed'),
      ce('span', { className: 'text-amber-700 font-medium' }, '% tasks done'),
    ),
    // Table
    cycles.length === 0
      ? ce(
          'div',
          {
            className:
              'rounded-lg border border-gray-200 p-8 text-center text-gray-400 text-sm bg-white',
          },
          'No completed audit cycles found',
        )
      : ce(
          'div',
          {
            className:
              'rounded-lg overflow-hidden shadow-sm border border-gray-200',
          },
          ce(
            'div',
            { className: 'overflow-x-auto' },
            ce(
              'table',
              { className: 'border-collapse bg-white text-xs w-full' },
              ce(
                'thead',
                null,
                ce(
                  'tr',
                  null,
                  ce(
                    'th',
                    {
                      className:
                        'sticky left-0 z-10 bg-green-900 text-white px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider whitespace-nowrap min-w-40',
                      style: { position: 'sticky', left: 0 },
                    },
                    'Connect Worker',
                  ),
                  ce(
                    'th',
                    {
                      className:
                        'bg-green-900 text-white px-3 py-2 text-center text-xs font-semibold uppercase tracking-wider whitespace-nowrap min-w-20',
                    },
                    'Total Flags',
                  ),
                  cycles.map(function (c, i) {
                    var r = c.reports[0];
                    return ce(
                      'th',
                      {
                        key: i,
                        className:
                          'bg-green-800 text-green-100 px-3 py-2 text-center font-semibold min-w-32',
                      },
                      chcDateRange(r.period_start, r.period_end),
                    );
                  }),
                ),
              ),
              ce(
                'tbody',
                null,
                flwCycleData.map(function (row, i) {
                  return ce(
                    'tr',
                    { key: i, className: 'border-b border-gray-100' },
                    ce(
                      'td',
                      {
                        className:
                          'border-r border-gray-200 px-3 py-2 font-medium whitespace-nowrap sticky left-0 bg-gray-50 z-10',
                        style: { position: 'sticky', left: 0 },
                      },
                      nameMap[row.username] || row.username,
                    ),
                    ce(
                      'td',
                      {
                        className:
                          'border-r border-gray-200 px-3 py-2 text-center font-bold ' +
                          (row.totalFlags > 0
                            ? 'bg-green-50 text-green-800'
                            : 'bg-white text-gray-400'),
                      },
                      row.totalFlags,
                    ),
                    row.cycles.map(function (cs, j) {
                      if (!cs) {
                        return ce(
                          'td',
                          {
                            key: j,
                            className:
                              'border-r border-gray-200 px-3 py-2 text-center text-gray-300',
                          },
                          '—',
                        );
                      }
                      var mPct =
                        cs.totalMetrics > 0
                          ? Math.round(
                              ((cs.totalMetrics - cs.metricFlags) /
                                cs.totalMetrics) *
                                100,
                            )
                          : 100;
                      var tPct =
                        cs.totalTasks > 0
                          ? Math.round((cs.closedTasks / cs.totalTasks) * 100)
                          : null;
                      return ce(
                        'td',
                        {
                          key: j,
                          className:
                            'border-r border-gray-200 px-3 py-2 ' +
                            severityCls(cs.metricFlags),
                        },
                        ce(
                          'div',
                          { className: 'flex flex-col items-center gap-0.5' },
                          ce(
                            'span',
                            { className: 'font-semibold text-green-800' },
                            mPct + '% passed',
                          ),
                          ce(
                            'div',
                            {
                              className:
                                'w-16 h-1.5 bg-gray-200 rounded overflow-hidden',
                            },
                            ce('div', {
                              className: 'h-full bg-green-600 rounded',
                              style: { width: mPct + '%' },
                            }),
                          ),
                          tPct != null
                            ? ce(
                                'span',
                                { className: 'text-amber-700' },
                                tPct + '% tasks',
                              )
                            : ce(
                                'span',
                                { className: 'text-gray-400' },
                                'no tasks',
                              ),
                        ),
                      );
                    }),
                  );
                }),
              ),
            ),
          ),
        ),
  );
}

// =========================================================================
// Main component
// =========================================================================

function WorkflowUI(props) {
  var definition = props.definition;
  var instance = props.instance;
  var workers = props.workers;
  var pipelines = props.pipelines;
  var view = props.view;

  var _tabState = React.useState(0);
  var activeTab = _tabState[0];
  var setTab = _tabState[1];

  var oppIds = React.useMemo(
    function () {
      return (definition && definition.opportunity_ids) || [];
    },
    [definition],
  );

  // Build opp name map from the user-opportunities JSON injected into the page
  var oppNames = React.useMemo(function () {
    var m = {};
    try {
      var el = document.getElementById('user-opportunities');
      if (el)
        JSON.parse(el.textContent).forEach(function (o) {
          m[o.id] = o.name;
        });
    } catch (e) {}
    return m;
  }, []);

  // Build username → display name map from workers
  var nameMap = React.useMemo(
    function () {
      var m = {};
      (workers || []).forEach(function (w) {
        if (w.username) m[w.username] = w.name || w.username;
      });
      return m;
    },
    [workers],
  );

  // Prefer view.pipelines (snapshot-aware) with fallback to live pipelines
  var srcPipelines = React.useMemo(
    function () {
      return (view && view.pipelines) || pipelines || {};
    },
    [view, pipelines],
  );

  var reportRows = React.useMemo(
    function () {
      return (
        (srcPipelines.audit_reports && srcPipelines.audit_reports.rows) || []
      );
    },
    [srcPipelines],
  );

  var entryRows = React.useMemo(
    function () {
      return (
        (srcPipelines.audit_entries && srcPipelines.audit_entries.rows) || []
      );
    },
    [srcPipelines],
  );

  var taskRows = React.useMemo(
    function () {
      return (srcPipelines.tasks && srcPipelines.tasks.rows) || [];
    },
    [srcPipelines],
  );

  var tabs = ['Audit History', 'Metric Detail', 'FLW Longitudinal'];

  return ce(
    'div',
    { className: 'min-h-screen bg-gray-50' },
    // Header
    ce(
      'div',
      { className: 'bg-green-900 text-white px-6 py-4' },
      ce(
        'div',
        { className: 'text-xs uppercase tracking-widest opacity-60 mb-1' },
        'Program 176 · DIMAGI-CHC-RCT · Nigeria',
      ),
      ce(
        'div',
        { className: 'text-xl font-bold tracking-tight' },
        'CHC Audit History',
      ),
      ce(
        'div',
        { className: 'flex gap-2 mt-2 flex-wrap' },
        oppIds.map(function (id) {
          return ce(
            'span',
            {
              key: id,
              className:
                'text-xs px-2 py-0.5 bg-white/10 border border-white/20 rounded',
            },
            oppNames[id] || 'Opp #' + id,
          );
        }),
      ),
    ),
    // Tabs
    ce(
      'div',
      { className: 'bg-white border-b border-gray-200 flex px-4' },
      tabs.map(function (lbl, i) {
        return ce(
          'button',
          {
            key: i,
            className:
              'px-4 py-3 text-sm font-medium border-b-2 -mb-px ' +
              (activeTab === i
                ? 'text-green-800 border-green-700 font-semibold'
                : 'text-gray-500 border-transparent hover:text-green-800'),
            onClick: function () {
              setTab(i);
            },
          },
          lbl,
        );
      }),
    ),
    // Tab content
    activeTab === 0 &&
      ce(ChcAuditHistory, {
        reportRows: reportRows,
        entryRows: entryRows,
        taskRows: taskRows,
        oppIds: oppIds,
        oppNames: oppNames,
        nameMap: nameMap,
      }),
    activeTab === 1 &&
      ce(ChcMetricDetail, {
        reportRows: reportRows,
        entryRows: entryRows,
        oppIds: oppIds,
        oppNames: oppNames,
        nameMap: nameMap,
      }),
    activeTab === 2 &&
      ce(ChcFLWLongitudinal, {
        reportRows: reportRows,
        entryRows: entryRows,
        taskRows: taskRows,
        workers: workers,
        oppIds: oppIds,
        oppNames: oppNames,
        nameMap: nameMap,
      }),
  );
}
