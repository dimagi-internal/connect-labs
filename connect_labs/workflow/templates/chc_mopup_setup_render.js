// CHC Mop-up Setup — render code
//
// A zero-pipeline, action-shaped "pick your opportunity first" front door for
// the CHC Mop-up Candidate Analysis dashboard (chc_mopup_candidates.py). See
// that file's module docstring for why this exists: the dashboard is
// multi-opp and, before Part 1 of this change, its render code was never
// even mounted until every pipeline source finished fetching for every
// opportunity in scope (confirmed directly in workflow-runner.tsx --
// `pipelineLoadingStatus ? <spinner> : <DynamicWorkflow ... />`). A mop-up
// round is always run for ONE LLO at a time, so this page exists purely to
// let a reviewer narrow the dashboard's opportunity scope down to one opp
// BEFORE creating a run against it -- no pipelines to wait for here at all,
// so it loads instantly regardless of how much data the dashboard itself
// would otherwise have to fetch.
//
// Clicking a button does two things against the DASHBOARD workflow's own
// definition_id (read from definition.config.dashboard_definition_id --
// NEVER hardcoded, since the dashboard's definition_id can change if it's
// ever recreated; it already has once this project):
//   1. POST /labs/workflow/api/<dashboard_definition_id>/opportunity-ids/
//      (UpdateOpportunityIdsView, connect_labs/workflow/views.py) with a
//      single-element opportunity_ids list -- narrows the dashboard's own
//      scope to just the chosen opp. JSON body.
//   2. POST /labs/workflow/api/<dashboard_definition_id>/run/start/
//      (start_run_api, same file) with program_id -- creates a fresh run
//      against the now-narrowed dashboard. FORM-ENCODED body: start_run_api
//      reads request.POST.get(...), which Django only populates from a
//      x-www-form-urlencoded (or multipart) body, not a JSON one -- this is
//      why postForm() below is a distinct helper from apiPost(), not just a
//      second call to it.
// Then navigates to the new run. See postForm()'s own comment for exactly
// what start_run_api returns and why its own `redirect` field is used
// instead of hand-building the URL from scratch.
//
// Deliberately NOT using window.WORKFLOW_API_ENDPOINTS.updateOpportunityIds:
// that global is populated from *this* setup workflow's own apiEndpoints
// (see workflow-runner.tsx), i.e. it always points at THIS definition_id,
// never the dashboard's -- using it here would silently narrow the wrong
// workflow's opportunity scope. Both URLs below are built directly from
// dashboardDefinitionId instead.
//
// No shared cross-template fetch primitive exists for this (checked
// WORKFLOW_REFERENCE.md §4b -- window.LabsAudit is a UI panel, not a network
// helper), so apiPost/csrfToken are duplicated here in the same ~15-line
// shape as chc_mopup_candidates_render.js's copy, matching how self-contained
// every render_code file in this codebase already is.

var ce = React.createElement;

function csrfToken() {
  var root = document.getElementById('workflow-root');
  if (root && root.dataset && root.dataset.csrfToken)
    return root.dataset.csrfToken;
  var el = document.querySelector('[name=csrfmiddlewaretoken]');
  return el ? el.value : '';
}

// JSON POST helper (same shape as chc_mopup_candidates_render.js's apiPost)
// -- used for the opportunity-ids narrowing call, which is UpdateOpportunityIdsView
// and reads a JSON body.
function apiPost(url, body) {
  return fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-CSRFToken': csrfToken(),
    },
    body: JSON.stringify(body || {}),
  }).then(function (r) {
    return r.json().then(
      function (data) {
        return { ok: r.ok, status: r.status, data: data };
      },
      function () {
        return { ok: r.ok, status: r.status, data: null };
      },
    );
  });
}

// Form-encoded POST helper -- used for start_run_api, which reads
// request.POST.get(...) (form-encoded, NOT JSON -- see module comment
// above). `Accept: application/json` makes start_run_api return its JSON
// body ({success, run_id, status, redirect}) instead of an HTTP redirect
// response (its only other existing caller is a plain HTML <form> submit in
// templates/workflow/list.html, which relies on the browser's default
// text/html Accept to get a real 302 -- fetch's default Accept of "*/*"
// would already fall through to the JSON branch too, but this is set
// explicitly so that stays true regardless of any future change to that
// fallback).
function postForm(url, fields) {
  var params = new URLSearchParams();
  Object.keys(fields || {}).forEach(function (k) {
    if (fields[k] != null) params.append(k, String(fields[k]));
  });
  return fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      Accept: 'application/json',
      'X-CSRFToken': csrfToken(),
    },
    body: params.toString(),
  }).then(function (r) {
    return r.json().then(
      function (data) {
        return { ok: r.ok, status: r.status, data: data };
      },
      function () {
        return { ok: r.ok, status: r.status, data: null };
      },
    );
  });
}

function errorDetail(res, fallback) {
  if (res && res.data && res.data.error) return res.data.error;
  if (res && res.status) return fallback + ' (HTTP ' + res.status + ')';
  return fallback + ' -- network error';
}

function WorkflowUI(props) {
  var definition = props.definition,
    instance = props.instance;

  // Same accessor pattern as chc_mopup_candidates_render.js's own opp-chip
  // header: instance.opportunity_ids is the documented multi-opp accessor,
  // definition.opportunity_ids is the create-time fallback, then a
  // single-opp array as a last resort. Zero fetch cost -- these are always
  // available on the props, no pipeline involved.
  var oppIds =
    (instance && instance.opportunity_ids) ||
    (definition && definition.opportunity_ids) ||
    (instance && [instance.opportunity_id]) ||
    [];

  var oppNames = React.useMemo(function () {
    var m = {};
    try {
      var el = document.getElementById('user-opportunities');
      if (el)
        JSON.parse(el.textContent).forEach(function (o) {
          m[o.id] = o.name;
        });
    } catch (e) {
      // no-op: falls back to "Opportunity #<id>" labels
    }
    return m;
  }, []);

  // Config value only -- set manually after this workflow instance is
  // created (its real value depends on the dashboard workflow's
  // definition_id, which can change if that workflow is ever recreated).
  // NOT read from window.WORKFLOW_API_ENDPOINTS -- see module comment above.
  var dashboardDefinitionId =
    definition &&
    definition.config &&
    definition.config.dashboard_definition_id;

  var programId = instance && instance.program_id;

  var _busyOppId = React.useState(null);
  var busyOppId = _busyOppId[0],
    setBusyOppId = _busyOppId[1];
  var _err = React.useState('');
  var errMsg = _err[0],
    setErrMsg = _err[1];

  function handlePick(oppId) {
    if (busyOppId != null || !dashboardDefinitionId) return;
    setBusyOppId(oppId);
    setErrMsg('');

    var oppLabel = oppNames[oppId] || 'Opportunity #' + oppId;

    apiPost(
      '/labs/workflow/api/' + dashboardDefinitionId + '/opportunity-ids/',
      {
        opportunity_ids: [oppId],
      },
    )
      .then(function (res) {
        if (!res.ok || !res.data || res.data.success !== true) {
          throw new Error(
            "Couldn't narrow the opportunity scope to " +
              oppLabel +
              ': ' +
              errorDetail(res, 'the request failed'),
          );
        }
        return postForm(
          '/labs/workflow/api/' + dashboardDefinitionId + '/run/start/',
          {
            program_id: programId,
          },
        );
      })
      .then(function (res) {
        if (
          !res.ok ||
          !res.data ||
          res.data.success !== true ||
          !res.data.run_id
        ) {
          throw new Error(
            "Couldn't start the report for " +
              oppLabel +
              ': ' +
              errorDetail(res, 'the request failed'),
          );
        }
        // Prefer the server's own redirect (start_run_api builds it from the
        // scope it actually resolved -- see that view's docstring) over
        // hand-building the URL, so this stays correct even if the exact
        // query-param shape ever changes server-side. Falls back to the
        // documented shape only if `redirect` is somehow absent.
        var url =
          res.data.redirect ||
          '/labs/workflow/' +
            dashboardDefinitionId +
            '/run/?run_id=' +
            res.data.run_id +
            '&program_id=' +
            programId;
        window.location.href = url;
        // Deliberately no finally{} clearing busyOppId here -- we're
        // navigating away, and clearing it would just let the button
        // flicker back to enabled for the instant before the page unloads.
      })
      .catch(function (e) {
        setErrMsg(e.message || String(e));
        setBusyOppId(null);
      });
  }

  return ce(
    'div',
    { className: 'min-h-screen bg-gray-50' },
    ce(
      'div',
      { className: 'bg-orange-900 text-white px-6 py-4' },
      ce(
        'div',
        { className: 'text-xs uppercase tracking-widest opacity-60 mb-1' },
        'Program 217 · CHC - NG - RCT - Aug 2026',
      ),
      ce(
        'div',
        { className: 'text-xl font-bold tracking-tight' },
        definition.name || 'CHC Mop-up Setup',
      ),
    ),
    ce(
      'div',
      { className: 'p-6 space-y-4 max-w-2xl' },
      ce(
        'div',
        {
          className:
            'bg-white rounded-lg shadow-sm border border-gray-200 p-4 space-y-3',
        },
        ce(
          'p',
          { className: 'text-sm text-gray-600' },
          'A mop-up round is run for one LLO at a time. Pick the opportunity below to scope the ' +
            '"CHC Mop-up Candidate Analysis" dashboard to just that opportunity and jump straight into ' +
            'a new run of it -- this skips waiting for every opportunity’s data to load up front.',
        ),
        !dashboardDefinitionId
          ? ce(
              'div',
              {
                className:
                  'bg-amber-50 border border-amber-200 text-amber-800 text-sm rounded p-3',
              },
              "Setup workflow isn't configured with a target dashboard yet.",
            )
          : null,
        errMsg
          ? ce(
              'div',
              {
                className:
                  'bg-red-50 border border-red-200 text-red-700 text-sm rounded p-3',
              },
              errMsg,
            )
          : null,
        ce(
          'div',
          { className: 'flex flex-wrap gap-3 pt-1' },
          oppIds.map(function (oppId) {
            var isBusy = busyOppId === oppId;
            var disabled = !dashboardDefinitionId || busyOppId != null;
            return ce(
              'button',
              {
                key: oppId,
                type: 'button',
                disabled: disabled,
                onClick: function () {
                  handlePick(oppId);
                },
                className:
                  'px-4 py-3 rounded-lg text-sm font-medium border transition-colors ' +
                  (disabled && !isBusy
                    ? 'bg-gray-100 text-gray-400 border-gray-200 cursor-not-allowed'
                    : 'bg-orange-700 text-white border-orange-700 hover:bg-orange-800'),
              },
              isBusy ? 'Starting…' : oppNames[oppId] || 'Opportunity #' + oppId,
            );
          }),
        ),
      ),
    ),
  );
}
