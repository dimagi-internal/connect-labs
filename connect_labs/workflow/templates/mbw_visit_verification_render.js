function WorkflowUI({
  definition,
  instance,
  workers,
  pipelines,
  links,
  actions,
  onUpdateState,
}) {
  // --- Domain filter (default: production only) --------------------------
  // Which CommCare project space(s) to include: this opp's real production
  // domain (the default, so the dashboard shows real data with zero clicks
  // once the verification block ships there), the test domain the block was
  // built and is being piloted in, or both. Gates BOTH the FLW-eligibility
  // set and the visit rows below, so the two domains are never silently
  // mixed unless "Both domains" is explicitly picked.
  var TEST_DOMAIN_NAME = 'ccc-mbw-experiments-1';
  var PROD_DOMAIN_NAME = 'ccc-mbw-production';
  var DOMAIN_FILTER_OPTIONS = [
    { key: 'production', label: 'Production only' },
    { key: 'test', label: 'Test domain only' },
    { key: 'both', label: 'Both domains' },
  ];
  var _domainFilter = React.useState('production');
  var domainFilter = _domainFilter[0];
  var setDomainFilter = _domainFilter[1];
  var wantedDomain =
    domainFilter === 'production'
      ? PROD_DOMAIN_NAME
      : domainFilter === 'test'
        ? TEST_DOMAIN_NAME
        : null; // null => both domains, no filtering

  // --- Eligible-FLW set (commcare-user cases, visit_verification='yes') ---
  // entity_name is the built-in row field cchq_cases populates from each
  // case's case_name, which for commcare-user cases is the FLW's username.
  var ELIGIBLE_PIPELINE_ALIASES = [
    { alias: 'eligible_flws', domain: TEST_DOMAIN_NAME },
    { alias: 'eligible_flws_prod', domain: PROD_DOMAIN_NAME },
  ];
  var eligibleRows = [];
  ELIGIBLE_PIPELINE_ALIASES.forEach(function (p) {
    if (wantedDomain && p.domain !== wantedDomain) return;
    var rows = (pipelines && pipelines[p.alias] && pipelines[p.alias].rows) || [];
    eligibleRows = eligibleRows.concat(rows);
  });
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
  // Twelve pipelines -- one per visit-type form, times two domains. Gated by
  // the same domain filter as the eligibility set above -- windowing never
  // mixes rows across domains even in "Both domains" mode (their
  // mother_case_ids come from different CommCare project spaces and can't
  // collide in practice, so this is a safety property more than a behavior
  // change from before the filter existed). cchq_forms fetches one
  // form_name at a time, unlike connect_csv, which merges across form types
  // automatically. Keep this list in sync with the aliases in
  // PIPELINE_SCHEMAS. Forms/domains that haven't grown the verification
  // block yet just contribute an empty rows array -- harmless.
  var VISIT_PIPELINE_ALIASES = [
    { alias: 'visits_anc_visit', domain: TEST_DOMAIN_NAME },
    { alias: 'visits_post_delivery_visit', domain: TEST_DOMAIN_NAME },
    { alias: 'visits_1_week_visit', domain: TEST_DOMAIN_NAME },
    { alias: 'visits_1_month_visit', domain: TEST_DOMAIN_NAME },
    { alias: 'visits_3_month_visit', domain: TEST_DOMAIN_NAME },
    { alias: 'visits_6_month_visit', domain: TEST_DOMAIN_NAME },
    { alias: 'visits_prod_anc_visit', domain: PROD_DOMAIN_NAME },
    { alias: 'visits_prod_post_delivery_visit', domain: PROD_DOMAIN_NAME },
    { alias: 'visits_prod_1_week_visit', domain: PROD_DOMAIN_NAME },
    { alias: 'visits_prod_1_month_visit', domain: PROD_DOMAIN_NAME },
    { alias: 'visits_prod_3_month_visit', domain: PROD_DOMAIN_NAME },
    { alias: 'visits_prod_6_month_visit', domain: PROD_DOMAIN_NAME },
  ];

  var allVisitRows = [];
  VISIT_PIPELINE_ALIASES.forEach(function (p) {
    if (wantedDomain && p.domain !== wantedDomain) return;
    var rows = (pipelines && pipelines[p.alias] && pipelines[p.alias].rows) || [];
    rows.forEach(function (row) {
      allVisitRows.push(Object.assign({}, row, { domain: p.domain }));
    });
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
          return (a.visit_datetime || a.visit_date || '').localeCompare(
            b.visit_datetime || b.visit_date || '',
          );
        });

        var passCount = 0;
        var failCount = 0;
        group.forEach(function (row, idx) {
          var denom = passCount + failCount;
          var priorPassRate =
            denom > 0
              ? Math.round((passCount / denom) * 100) + '% (' + denom + ')'
              : 'N/A (0)';

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

  // visit_datetime is form.meta.timeEnd, an ISO string (e.g.
  // "2026-09-11T14:32:07.123000Z") -- slice rather than parse as a Date to
  // avoid any local-timezone shift, since the raw value is already in
  // whatever timezone the form was submitted in.
  function formatVisitDateTime(iso) {
    if (!iso || typeof iso !== 'string' || iso.length < 19) return 'NA';
    return iso.slice(0, 10) + ' ' + iso.slice(11, 19);
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

  function qrOutcome(row) {
    if (row.qr_code_visit_verification) return row.qr_code_visit_verification;
    // Confirmed by scanning real submissions: whenever the mother didn't
    // have her QR code photo available at the visit, qr_code_visit_verification
    // is always blank -- that's "not applicable this visit", not "NA" (which
    // otherwise reads as "no data for this row").
    if (row.mother_has_qr_code_available === 'no') return 'Not available';
    return 'NA';
  }

  function signatureOutcome(row) {
    return blankOrNA(row.mother_initial_visit_verification);
  }

  function motherQuestionsOutcome(row) {
    if (row.show_mother_questions === '0') return 'NA';
    if (row.show_mother_questions === '1')
      return blankOrNA(row.mother_questions_visit_verification);
    return 'NA';
  }

  function ancCardOutcome(row) {
    return blankOrNA(row.capture_anc_card_visit_verification);
  }

  // Shared list of the 5 per-method outcomes -- drives both the "Final
  // verification method(s)" column and the summary chart, so they can never
  // drift apart on what counts as a method.
  var METHODS = [
    { label: 'GPS', getOutcome: gpsOutcome },
    { label: 'QR', getOutcome: qrOutcome },
    { label: 'Signature', getOutcome: signatureOutcome },
    { label: 'Mother Questions', getOutcome: motherQuestionsOutcome },
    { label: 'ANC Card', getOutcome: ancCardOutcome },
  ];

  // 'Attempted' = the FLW provided information for that method AND it
  // produced an outcome of Pass, Fail, or Pending Audit -- NA/Not
  // available/ERROR/blank all mean the method wasn't meaningfully attempted.
  function wasAttempted(value) {
    return (
      value === 'Pass' ||
      value === 'Fail' ||
      (typeof value === 'string' && value.indexOf('Pending') !== -1)
    );
  }

  function finalVerificationMethods(row) {
    var methods = [];
    METHODS.forEach(function (m) {
      if (wasAttempted(m.getOutcome(row))) methods.push(m.label);
    });
    return methods.length > 0 ? methods.join(', ') : 'NA';
  }

  // --- Cell coloring: NA grey, Pass green, Fail red, Pending* yellow -----
  function outcomeColorClass(value) {
    if (value === 'Pass') return 'bg-green-100 text-green-800';
    if (value === 'Fail') return 'bg-red-100 text-red-800';
    if (value === 'NA' || value === 'Not available')
      return 'bg-gray-100 text-gray-600';
    if (typeof value === 'string' && value.indexOf('Pending') !== -1)
      return 'bg-yellow-100 text-yellow-800';
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
    { key: 'domain', label: 'CC Domain' },
    { key: 'mother_case_id', label: 'Mother ID' },
    { key: 'form_instance_id', label: 'Visit ID' },
    { key: 'visit_datetime', label: 'Visit date' },
    { key: 'form_name', label: 'Visit type' },
    { key: 'visit_number', label: 'Visit #' },
    { key: 'where_is_the_visit_being_conducted', label: 'GPS location' },
    { key: 'gps_outcome', label: 'GPS outcome' },
    { key: 'qr_outcome', label: 'QR outcome' },
    { key: 'signature_outcome', label: 'Signature outcome' },
    { key: 'mother_questions_outcome', label: 'Mother questions outcome' },
    { key: 'anc_card_outcome', label: 'ANC card outcome' },
    {
      key: 'final_verification_methods',
      label: 'Final verification method(s)',
    },
    { key: 'visit_verification_outcome', label: 'Final verification outcome' },
    {
      key: 'prior_verification_pass_rate',
      label: 'Previous verification pass rate',
    },
  ];

  function cellValue(row, key) {
    if (key === 'visit_datetime')
      return formatVisitDateTime(row.visit_datetime);
    if (key === 'gps_outcome') return gpsOutcome(row);
    if (key === 'qr_outcome') return qrOutcome(row);
    if (key === 'signature_outcome') return signatureOutcome(row);
    if (key === 'mother_questions_outcome') return motherQuestionsOutcome(row);
    if (key === 'anc_card_outcome') return ancCardOutcome(row);
    if (key === 'final_verification_methods')
      return finalVerificationMethods(row);
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
        if (
          !isNaN(an) &&
          !isNaN(bn) &&
          av !== null &&
          bv !== null &&
          av !== '' &&
          bv !== ''
        ) {
          return (an - bn) * dir;
        }
        var as = av === null || av === undefined ? '' : String(av);
        var bs = bv === null || bv === undefined ? '' : String(bv);
        return as.localeCompare(bs) * dir;
      });
    },
    [displayRows, sort],
  );

  // --- Summary metrics (over the displayed/filtered set) ------------------
  var summary = React.useMemo(
    function () {
      var total = displayRows.length;
      var passCount = 0;
      var failCount = 0;
      var pendingCount = 0;
      displayRows.forEach(function (row) {
        if (row.visit_verification_outcome === 'Pass') passCount += 1;
        else if (row.visit_verification_outcome === 'Fail') failCount += 1;
        else if (row.visit_verification_outcome === 'Pending Audit')
          pendingCount += 1;
      });
      function pct(n) {
        return total > 0 ? Math.round((n / total) * 100) : 0;
      }
      return {
        total: total,
        passCount: passCount,
        failCount: failCount,
        pendingCount: pendingCount,
        passPct: pct(passCount),
        failPct: pct(failCount),
        pendingPct: pct(pendingCount),
      };
    },
    [displayRows],
  );

  // --- Per-method Pass/Pending/Fail counts, for the summary chart --------
  var methodStats = React.useMemo(
    function () {
      return METHODS.map(function (m) {
        var pass = 0;
        var fail = 0;
        var pending = 0;
        displayRows.forEach(function (row) {
          var v = m.getOutcome(row);
          if (v === 'Pass') pass += 1;
          else if (v === 'Fail') fail += 1;
          else if (typeof v === 'string' && v.indexOf('Pending') !== -1)
            pending += 1;
        });
        return { label: m.label, pass: pass, pending: pending, fail: fail };
      });
    },
    [displayRows],
  );

  // --- CSV export -----------------------------------------------------------
  function csvEscape(value) {
    var s = value === null || value === undefined ? '' : String(value);
    if (
      s.indexOf(',') !== -1 ||
      s.indexOf('"') !== -1 ||
      s.indexOf('\n') !== -1
    ) {
      s = '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  }

  function handleExportCSV() {
    var lines = [
      columns
        .map(function (c) {
          return csvEscape(c.label);
        })
        .join(','),
    ];
    sortedRows.forEach(function (row) {
      lines.push(
        columns
          .map(function (col) {
            return csvEscape(cellValue(row, col.key));
          })
          .join(','),
      );
    });
    var csvContent = lines.join('\n');
    var blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download =
      'mbw_visit_verification_opp_' + (instance.opportunity_id || '') + '.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // --- Definitions tab content --------------------------------------------
  // One place documenting exactly what each column/metric means and how it's
  // calculated, kept next to the logic it describes so the two don't drift.
  var DEFINITION_SECTIONS = [
    {
      title: 'Which visits appear in this report',
      body: "A visit only shows up if ALL are true: (1) it's from the CommCare domain(s) selected in the \"CommCare domain\" toggle at the top (Production only by default), (2) the FLW who conducted it is a commcare-user case with the property visit_verification set to 'yes' in that same domain, and (3) the visit's form has the verification block at all, detected via visit_location_has_prev_home_gps being present/non-blank. Visits from FLWs not flagged for verification, from a domain not selected in the toggle, or submitted before the verification questions existed on that form, are excluded entirely -- not shown as blank rows.",
      items: [
        {
          name: 'Domain filter',
          field:
            'domainFilter state (\'production\' default / \'test\' / \'both\') -- gates which pipeline aliases are read at all: production = only the _prod pipelines (visits_prod_*, eligible_flws_prod), test = only the non-_prod pipelines, both = every pipeline, unfiltered.',
        },
        {
          name: 'FLW eligibility gate',
          field:
            "pipelines: eligible_flws (test domain) and/or eligible_flws_prod (production domain), per the domain filter (cchq_cases, case_type='commcare-user'). Fields: visit_verification (case.properties.visit_verification), entity_name (built-in, = case_name = FLW username). Joined to each visit row on username === entity_name.",
        },
        {
          name: 'Verification-block-present gate',
          field:
            'visit_location_has_prev_home_gps (form.gps_verification.location_check.visit_location_has_prev_home_gps) must be non-null/undefined/empty-string.',
        },
      ],
    },
    {
      title: 'Table Columns',
      items: [
        {
          name: 'FLW ID',
          def: "The FLW's CommCare username.",
          field: 'username (built-in row field)',
        },
        {
          name: 'CC Domain',
          def: 'Which CommCare project space this visit was submitted in.',
          field:
            "Computed client-side (domain) -- tagged onto each row from which pipeline alias it came from (visits_prod_* -> \"ccc-mbw-production\", the rest -> \"ccc-mbw-experiments-1\"), not a raw pipeline field.",
        },
        {
          name: 'Mother ID',
          def: 'The mother case this visit was made for. Drives the per-mother grouping used for Visit # and Previous verification pass rate.',
          field:
            'mother_case_id -- primary path form.parents.parent.case.@case_id, with 7 trailing fallback paths for older submissions: form.confirm_visit_information.{postnatal,one_week,one_month,three_month,six_month}_visit_logic.mother_case_id, form.visit_rescheduling.visit_rescheduling.mother_case_id, form.visit_rescheduling.postnatal_visit_logic.mother_case_id (first non-blank wins).',
        },
        {
          name: 'Visit ID',
          def: 'The form submission ID -- unique per visit.',
          field: 'form_instance_id (form.meta.instanceID)',
        },
        {
          name: 'Visit date',
          def: 'Shown as "YYYY-MM-DD HH:MM:SS" (date and time to the second, exactly as submitted -- no timezone conversion).',
          field: 'visit_datetime (form.meta.timeEnd)',
        },
        {
          name: 'Visit type',
          def: 'Which visit-type form was submitted.',
          field:
            'form_name (form.@name) -- one of: "ANC Visit ", "Post delivery visit", "1 Week Visit", "1 Month Visit", "3 Month Visit", "6 Month Visit"',
        },
        {
          name: 'Visit #',
          def: "This visit's position in the mother's own visit history, oldest first (1st, 2nd, 3rd...) -- counted per MOTHER across all her visits and visit types, not per FLW.",
          field:
            'Computed client-side (visit_number) -- not a raw pipeline field: rows are grouped by mother_case_id, sorted by visit_datetime (fallback visit_date) ascending, then 1-indexed within each group.',
        },
        {
          name: 'GPS location',
          def: "Where the FLW indicated the visit took place: the mother's home, a health facility, or other. Determines which GPS outcome logic applies (see below).",
          field:
            'where_is_the_visit_being_conducted (form.visit_location.where_is_the_visit_being_conducted) -- values: mothers_home / health_facility / other',
        },
      ],
    },
    {
      title: 'Outcome Columns & Their Logic',
      items: [
        {
          name: 'GPS outcome',
          def: "NA if the location was 'other' (GPS verification doesn't apply there), or if there was no prior-visit GPS point on record for that location type to compare against. Otherwise Pass if the visit's GPS matched the prior point on file, Fail if it didn't. ERROR means the location was home/health-facility with a prior GPS point on record, but the match field itself was missing -- flags a data issue worth investigating.",
          field:
            'where_is_the_visit_being_conducted (form.visit_location.where_is_the_visit_being_conducted); visit_location_has_prev_home_gps (form.gps_verification.location_check.visit_location_has_prev_home_gps); visit_location_has_prev_health_facility_gps (form.gps_verification.location_check.visit_location_has_prev_health_facility_gps); gps_visit_verification_matches (form.gps_verification.location_check.gps_visit_verification_matches)',
        },
        {
          name: 'QR outcome',
          def: "The FLW's direct answer when a value is present. If blank AND the FLW separately indicated the mother didn't have her QR code photo available at this visit, shown as 'Not available' rather than NA (it wasn't skipped -- it genuinely couldn't be done). NA otherwise.",
          field:
            'qr_code_visit_verification (form.qr_code_verification.qr_code_visit_verification); mother_has_qr_code_available (form.qr_code_verification.qr_code_scan.Does_the_mother_have__the_QR_code_photo_she_took_at_registration)',
        },
        {
          name: 'Signature outcome',
          def: "The FLW's direct answer for the mother's initial/signature verification; NA if blank.",
          field:
            'mother_initial_visit_verification (form.additional_visit_verification_block.mother_initial_visit_verification)',
        },
        {
          name: 'Mother questions outcome',
          def: "Only applicable when the form's show_mother_questions flag is '1' for this visit; the answer is shown when applicable, NA otherwise (including when the flag is '0', meaning the question block didn't apply to this visit).",
          field:
            'show_mother_questions (form.additional_visit_verification_block.show_mother_questions); mother_questions_visit_verification (form.additional_visit_verification_block.mother_questions_visit_verification)',
        },
        {
          name: 'ANC card outcome',
          def: "The FLW's direct answer for ANC card verification; NA if blank.",
          field:
            'capture_anc_card_visit_verification (form.additional_visit_verification_block.capture_anc_card_visit_verification)',
        },
        {
          name: 'Final verification method(s)',
          def: "Lists every method above (GPS / QR / Signature / Mother Questions / ANC Card) that was 'attempted' for this visit -- meaning the FLW provided information for it AND it resolved to Pass, Fail, or a Pending outcome (NA / Not available / ERROR / blank don't count as an attempt). Shows 'NA' if no method was attempted.",
          field:
            'Computed client-side from the 5 outcome columns above (gpsOutcome/qrOutcome/signatureOutcome/motherQuestionsOutcome/ancCardOutcome) -- no raw field of its own.',
        },
        {
          name: 'Final verification outcome',
          def: 'The overall verification result recorded on the form itself -- typically Pass, Fail, or Pending Audit. This is a single value the form/reviewer sets, independent of the per-method outcomes above.',
          field:
            'visit_verification_outcome -- primary path form.verification_properties.visit_verification_outcome, fallback form.visit_verification_outcome (used on submissions where the verification_properties group is absent entirely).',
        },
        {
          name: 'Previous verification pass rate',
          def: 'For this mother, the Pass/Fail record across all her PRIOR visits only (not including the current row), shown as "X% (N)" where N is how many prior visits had a Pass/Fail final outcome. Reads "N/A (0)" for a mother\'s first visit or when no prior visit has a Pass/Fail outcome yet. Pending/blank prior outcomes don\'t count toward N.',
          field:
            "Computed client-side (prior_verification_pass_rate) from visit_verification_outcome across this mother's earlier rows (grouped by mother_case_id, ordered by visit_datetime) -- not a raw pipeline field.",
        },
      ],
    },
    {
      title: 'Color Coding',
      items: [
        { name: 'Green', def: 'Pass' },
        { name: 'Red', def: 'Fail' },
        {
          name: 'Yellow',
          def: 'Any outcome containing "Pending" (e.g. Pending Audit)',
        },
        { name: 'Grey', def: 'NA or Not available' },
        { name: 'Orange', def: 'ERROR (GPS outcome only -- see above)' },
      ],
    },
    {
      title: 'Verification Summary Tab',
      body: 'The three percentages and the "n=" counts are all computed over the same filtered/eligible visit set as the table (see "Which visits appear" above), using each visit\'s Final verification outcome:',
      items: [
        {
          name: '% Passed Verification',
          def: 'Share of visits with Final verification outcome = Pass.',
          field: 'visit_verification_outcome === "Pass"',
        },
        {
          name: '% Pending Audit',
          def: 'Share of visits with Final verification outcome = Pending Audit.',
          field: 'visit_verification_outcome === "Pending Audit"',
        },
        {
          name: '% Failed Verification',
          def: 'Share of visits with Final verification outcome = Fail.',
          field: 'visit_verification_outcome === "Fail"',
        },
        {
          name: 'Stacked bar chart',
          def: "One bar per verification method (GPS, QR, Signature, Mother Questions, ANC Card), showing how many visits landed Pass (green) / Pending (yellow) / Fail (red) for that specific method -- independent of the overall Final verification outcome above. A single visit can fail one method and pass another (e.g. fail GPS but pass QR), so it's counted in more than one bar. That means these counts are NOT meant to add up to the % Passed/Pending/Failed totals above -- a bar's Fail count can be, and usually is, larger than the overall Failed Verification n= at the top, since one visit's failure can show up in several bars at once.",
          field:
            'Per row, per method: gpsOutcome() / qrOutcome() / signatureOutcome() / motherQuestionsOutcome() / ancCardOutcome() (same functions and underlying fields as the Outcome Columns section above), tallied into Pass/Pending/Fail counts.',
        },
      ],
    },
  ];

  // --- Tabs ----------------------------------------------------------------
  var TABS = [
    { key: 'summary', label: 'Verification Summary' },
    { key: 'table', label: 'Per FLW Verification View' },
    { key: 'definitions', label: 'Definitions' },
  ];
  var _tab = React.useState('summary');
  var activeTab = _tab[0];
  var setActiveTab = _tab[1];

  // --- Stacked horizontal bar chart (Chart.js) ------------------------------
  var chartRef = React.useRef(null);
  var chartInstance = React.useRef(null);

  React.useEffect(
    function () {
      if (activeTab !== 'summary') return;
      if (!chartRef.current || !window.Chart) return;
      if (chartInstance.current) chartInstance.current.destroy();

      chartInstance.current = new window.Chart(chartRef.current, {
        type: 'bar',
        data: {
          labels: methodStats.map(function (m) {
            return m.label;
          }),
          datasets: [
            {
              label: 'Passed',
              data: methodStats.map(function (m) {
                return m.pass;
              }),
              backgroundColor: '#22c55e',
            },
            {
              label: 'Pending Audit',
              data: methodStats.map(function (m) {
                return m.pending;
              }),
              backgroundColor: '#eab308',
            },
            {
              label: 'Failed',
              data: methodStats.map(function (m) {
                return m.fail;
              }),
              backgroundColor: '#ef4444',
            },
          ],
        },
        options: {
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
            y: { stacked: true },
          },
          plugins: { legend: { position: 'bottom' } },
        },
      });

      return function () {
        if (chartInstance.current) chartInstance.current.destroy();
      };
    },
    [methodStats, activeTab],
  );

  var summaryCards = (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <div className="rounded-lg border border-green-200 bg-green-50 p-4 shadow-sm">
        <div className="text-3xl font-bold text-green-700">
          {summary.passPct}%
        </div>
        <div className="text-gray-600">
          Passed Verification (n={summary.passCount})
        </div>
      </div>
      <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-4 shadow-sm">
        <div className="text-3xl font-bold text-yellow-700">
          {summary.pendingPct}%
        </div>
        <div className="text-gray-600">
          Pending Audit (n={summary.pendingCount})
        </div>
      </div>
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 shadow-sm">
        <div className="text-3xl font-bold text-red-700">
          {summary.failPct}%
        </div>
        <div className="text-gray-600">
          Failed Verification (n={summary.failCount})
        </div>
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">{definition.name}</h1>
        <p className="text-gray-600">{definition.description}</p>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
        <span className="text-sm font-medium text-gray-700">
          CommCare domain:
        </span>
        {DOMAIN_FILTER_OPTIONS.map(function (opt) {
          var isActive = domainFilter === opt.key;
          return (
            <button
              key={opt.key}
              onClick={function () {
                setDomainFilter(opt.key);
              }}
              className={
                'rounded border px-3 py-1 text-sm font-medium ' +
                (isActive
                  ? 'border-blue-600 bg-blue-600 text-white'
                  : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50')
              }
            >
              {opt.label}
            </button>
          );
        })}
      </div>

      {domainFilter === 'production' && summary.total === 0 && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">
          No visits yet from the production domain ({PROD_DOMAIN_NAME}) -- the
          verification block hasn't shipped there yet. Switch to "Test domain
          only" or "Both domains" above to see current data.
        </div>
      )}

      <div className="border-b border-gray-200">
        <nav className="-mb-px flex gap-4">
          {TABS.map(function (t) {
            var isActive = activeTab === t.key;
            return (
              <button
                key={t.key}
                onClick={function () {
                  setActiveTab(t.key);
                }}
                className={
                  'whitespace-nowrap border-b-2 px-1 py-2 text-sm font-medium ' +
                  (isActive
                    ? 'border-blue-600 text-blue-700'
                    : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700')
                }
              >
                {t.label}
              </button>
            );
          })}
        </nav>
      </div>

      {activeTab === 'summary' && (
        <div className="space-y-4">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">
              Overall Verification Outcome (per visit)
            </h3>
            <p className="text-xs text-gray-500">
              Based on each visit's single Final verification outcome.
            </p>
          </div>
          {summaryCards}
          <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900">
              Per-Method Outcome Breakdown
            </h3>
            <p className="mb-2 text-xs text-gray-500">
              Each visit can appear in more than one bar below -- e.g. it may
              fail GPS but pass QR -- so these method counts don't need to add
              up to the totals above, and can be larger.
            </p>
            <div style={{ height: '320px' }}>
              <canvas ref={chartRef}></canvas>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'table' && (
        <div className="space-y-4">
          <div className="flex items-start justify-between">
            <div className="text-sm text-gray-500">
              {sortedRows.length} visits shown
            </div>
            <button
              onClick={handleExportCSV}
              className="whitespace-nowrap rounded border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Export CSV
            </button>
          </div>
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
                        var text =
                          v === null || v === undefined ? '' : String(v);
                        var colorClass = OUTCOME_COLUMN_KEYS[col.key]
                          ? outcomeColorClass(v)
                          : '';
                        return (
                          <td
                            key={col.key}
                            className={
                              'whitespace-nowrap px-3 py-2 text-gray-800 ' +
                              colorClass
                            }
                          >
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
      )}

      {activeTab === 'definitions' && (
        <div className="max-w-3xl space-y-6">
          {DEFINITION_SECTIONS.map(function (section, i) {
            return (
              <div
                key={i}
                className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
              >
                <h3 className="mb-3 text-lg font-semibold text-gray-900">
                  {section.title}
                </h3>
                {section.body && (
                  <p className="whitespace-pre-line text-sm text-gray-700">
                    {section.body}
                  </p>
                )}
                {section.items && (
                  <dl className={section.body ? 'mt-3 space-y-2' : 'space-y-2'}>
                    {section.items.map(function (item, j) {
                      return (
                        <div key={j}>
                          <dt className="text-sm font-medium text-gray-900">
                            {item.name}
                          </dt>
                          {item.def && (
                            <dd className="ml-4 text-sm text-gray-600">
                              {item.def}
                            </dd>
                          )}
                          {item.field && (
                            <dd className="ml-4 font-mono text-xs text-gray-500">
                              {item.field}
                            </dd>
                          )}
                        </div>
                      );
                    })}
                  </dl>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
