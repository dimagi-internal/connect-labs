function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
  // --- Eligible-FLW set (commcare-user cases, visit_verification='yes') ---
  // entity_name is the built-in row field cchq_cases populates from each
  // case's case_name, which for commcare-user cases is the FLW's username.
  var eligibleRows = (pipelines && pipelines.eligible_flws && pipelines.eligible_flws.rows) || [];
  var eligibleUsernames = React.useMemo(
    function () {
      var set = {};
      eligibleRows.forEach(function (row) {
        if (row.visit_verification === 'yes' && row.entity_name) {
          set[row.entity_name] = true;
        }
      });
      return set;
    },
    [eligibleRows],
  );

  // --- Per-mother expanding-window computations -------------------------
  // visit_number and prior_verification_pass_rate can't be expressed by the
  // pipeline engine's window_fields today (only lag_haversine is supported),
  // so they're computed here over the FULL unfiltered visit set per mother
  // -- the true 1st/2nd/3rd... sequence, independent of which rows the
  // verification-data filter below ends up keeping.
  //
  // Six separate pipelines, one per visit-type form (cchq_forms fetches one
  // form_name at a time). Keep this list in sync with the aliases in
  // PIPELINE_SCHEMAS. Forms that haven't grown the verification block yet
  // just contribute an empty rows array -- harmless.
  var VISIT_PIPELINE_ALIASES = [
    'visits_anc_visit',
    'visits_post_delivery_visit',
    'visits_1_week_visit',
    'visits_1_month_visit',
    'visits_3_month_visit',
    'visits_6_month_visit',
  ];

  var allVisitRows = [];
  VISIT_PIPELINE_ALIASES.forEach(function (alias) {
    var rows = (pipelines && pipelines[alias] && pipelines[alias].rows) || [];
    allVisitRows = allVisitRows.concat(rows);
  });

  var enrichedRows = React.useMemo(
    function () {
      // Grouped by mother_case_id -- visit_number and the prior-pass-rate are
      // per MOTHER (how many times has this mother been visited, and what was
      // her prior verification track record), not per FLW.
      var byMother = {};
      allVisitRows.forEach(function (row) {
        var key = row.mother_case_id || '';
        if (!byMother[key]) byMother[key] = [];
        byMother[key].push(row);
      });

      var result = [];
      Object.keys(byMother).forEach(function (key) {
        var group = byMother[key].slice().sort(function (a, b) {
          return (a.visit_date || '').localeCompare(b.visit_date || '');
        });

        var passCount = 0;
        var failCount = 0;
        group.forEach(function (row, idx) {
          var denom = passCount + failCount;
          var priorPassRate =
            denom > 0 ? Math.round((passCount / denom) * 100) + '% (' + denom + ')' : 'N/A (0)';

          result.push(
            Object.assign({}, row, {
              visit_number: idx + 1,
              prior_verification_pass_rate: priorPassRate,
            }),
          );

          if (row.visit_verification_outcome === 'Pass') passCount += 1;
          else if (row.visit_verification_outcome === 'Fail') failCount += 1;
        });
      });
      return result;
    },
    [allVisitRows],
  );

  // --- Row filter: eligible FLW + verification block present ------------
  var displayRows = React.useMemo(
    function () {
      return enrichedRows.filter(function (row) {
        var hasVerificationData =
          row.visit_location_has_prev_home_gps !== null &&
          row.visit_location_has_prev_home_gps !== undefined &&
          row.visit_location_has_prev_home_gps !== '';
        return hasVerificationData && eligibleUsernames[row.username];
      });
    },
    [enrichedRows, eligibleUsernames],
  );

  // --- Per-row outcome helpers -------------------------------------------
  function blankOrNA(value) {
    return value === null || value === undefined || value === '' ? 'NA' : value;
  }

  function gpsOutcome(row) {
    var locType = row.where_is_the_visit_being_conducted;
    // 'other' (neither the mother's home nor a health facility) has no
    // applicable prior-GPS field at all -- GPS verification doesn't apply
    // there, same as a missing prior GPS at home/health-facility.
    if (locType === 'other') return 'NA';

    var hasPrevGps =
      locType === 'mothers_home'
        ? row.visit_location_has_prev_home_gps
        : locType === 'health_facility'
          ? row.visit_location_has_prev_health_facility_gps
          : null;

    if (hasPrevGps === 'no') return 'NA';
    if (row.gps_visit_verification_matches === 'no') return 'Fail';
    if (row.gps_visit_verification_matches === 'yes') return 'Pass';
    return 'ERROR';
  }

  function motherQuestionsOutcome(row) {
    if (row.show_mother_questions === '0') return 'NA';
    if (row.show_mother_questions === '1') return blankOrNA(row.mother_questions_visit_verification);
    return 'NA';
  }

  // --- Cell coloring: NA grey, Pass green, Fail red, Pending* yellow -----
  function outcomeColorClass(value) {
    if (value === 'Pass') return 'bg-green-100 text-green-800';
    if (value === 'Fail') return 'bg-red-100 text-red-800';
    if (value === 'NA') return 'bg-gray-100 text-gray-600';
    if (typeof value === 'string' && value.indexOf('Pending') !== -1) return 'bg-yellow-100 text-yellow-800';
    if (value === 'ERROR') return 'bg-orange-100 text-orange-800';
    return '';
  }

  var OUTCOME_COLUMN_KEYS = {
    gps_outcome: true,
    qr_outcome: true,
    signature_outcome: true,
    mother_questions_outcome: true,
    anc_card_outcome: true,
    visit_verification_outcome: true,
  };

  var columns = [
    { key: 'username', label: 'FLW ID' },
    { key: 'mother_case_id', label: 'Mother ID' },
    { key: 'form_instance_id', label: 'Visit ID' },
    { key: 'visit_date', label: 'Visit date' },
    { key: 'form_name', label: 'Visit type' },
    { key: 'visit_number', label: 'Visit #' },
    { key: 'where_is_the_visit_being_conducted', label: 'GPS location' },
    { key: 'gps_outcome', label: 'GPS outcome' },
    { key: 'qr_outcome', label: 'QR outcome' },
    { key: 'signature_outcome', label: 'Signature outcome' },
    { key: 'mother_questions_outcome', label: 'Mother questions outcome' },
    { key: 'anc_card_outcome', label: 'ANC card outcome' },
    { key: 'visit_verification_outcome', label: 'Final verification outcome' },
    { key: 'prior_verification_pass_rate', label: 'Previous verification pass rate' },
  ];

  function cellValue(row, key) {
    if (key === 'gps_outcome') return gpsOutcome(row);
    if (key === 'qr_outcome') return blankOrNA(row.qr_code_visit_verification);
    if (key === 'signature_outcome') return blankOrNA(row.mother_initial_visit_verification);
    if (key === 'mother_questions_outcome') return motherQuestionsOutcome(row);
    if (key === 'anc_card_outcome') return blankOrNA(row.capture_anc_card_visit_verification);
    return row[key];
  }

  // --- Sortable columns ---------------------------------------------------
  var _sort = React.useState({ key: null, dir: 'asc' });
  var sort = _sort[0];
  var setSort = _sort[1];

  function handleSortClick(key) {
    setSort(function (prev) {
      if (prev.key !== key) return { key: key, dir: 'asc' };
      return { key: key, dir: prev.dir === 'asc' ? 'desc' : 'asc' };
    });
  }

  var sortedRows = React.useMemo(
    function () {
      if (!sort.key) return displayRows;
      var key = sort.key;
      var dir = sort.dir === 'asc' ? 1 : -1;
      return displayRows.slice().sort(function (a, b) {
        var av = cellValue(a, key);
        var bv = cellValue(b, key);
        var an = typeof av === 'number' ? av : parseFloat(av);
        var bn = typeof bv === 'number' ? bv : parseFloat(bv);
        if (!isNaN(an) && !isNaN(bn) && av !== null && bv !== null && av !== '' && bv !== '') {
          return (an - bn) * dir;
        }
        var as = av === null || av === undefined ? '' : String(av);
        var bs = bv === null || bv === undefined ? '' : String(bv);
        return as.localeCompare(bs) * dir;
      });
    },
    [displayRows, sort],
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">{definition.name}</h1>
        <p className="text-gray-600">{definition.description}</p>
      </div>

      <div className="text-sm text-gray-500">{sortedRows.length} visits shown</div>
      <div className="overflow-x-auto rounded border border-gray-200">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              {columns.map(function (col) {
                var isSorted = sort.key === col.key;
                return (
                  <th
                    key={col.key}
                    onClick={function () {
                      handleSortClick(col.key);
                    }}
                    className="cursor-pointer select-none whitespace-nowrap px-3 py-2 text-left font-medium text-gray-700 hover:bg-gray-100"
                  >
                    {col.label}
                    {isSorted ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {sortedRows.map(function (row, i) {
              return (
                <tr key={row.form_instance_id || i}>
                  {columns.map(function (col) {
                    var v = cellValue(row, col.key);
                    var text = v === null || v === undefined ? '' : String(v);
                    var colorClass = OUTCOME_COLUMN_KEYS[col.key] ? outcomeColorClass(v) : '';
                    return (
                      <td key={col.key} className={'whitespace-nowrap px-3 py-2 text-gray-800 ' + colorClass}>
                        {text}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
