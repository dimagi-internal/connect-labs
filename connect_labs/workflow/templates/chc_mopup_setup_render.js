// CHC Mop-up Setup — render code
//
// A zero-pipeline, action-shaped "pick your opportunity first" front door for
// the CHC Mop-up Candidate Analysis dashboard (chc_mopup_candidates.py). See
// that file's module docstring for why this exists: the dashboard is
// multi-opp-capable and, without this front door, its render code is never
// even mounted until every pipeline source finishes fetching for every
// opportunity in scope (confirmed directly in workflow-runner.tsx --
// `pipelineLoadingStatus ? <spinner> : <DynamicWorkflow ... />`). A mop-up
// round is always run for ONE LLO at a time, so this page exists purely to
// jump a reviewer straight into a dashboard instance dedicated to just one
// opportunity -- no pipelines to wait for here at all, so it loads instantly
// regardless of how much data the dashboard itself would otherwise fetch.
//
// One dashboard PER opportunity, not one shared dashboard narrowed per click
// --------------------------------------------------------------------------
// An earlier version of this page POSTed to UpdateOpportunityIdsView to
// narrow a single SHARED dashboard instance's opportunity_ids down to the
// chosen one before starting a run. That broke live: a workflow's
// pipeline_sources point at pipeline_ids created once, owned by whichever
// opportunity was primary at the dashboard's ORIGINAL creation time, and
// narrowing opportunity_ids afterward to a DIFFERENT single opportunity
// excludes that owner from scope -- so the dashboard's pipelines all 404
// silently and it renders "0 work areas" for every opportunity except the
// one that happened to be primary when it was created. See
// chc_mopup_setup.py's module docstring for the full story.
//
// So this page instead reads definition.config.dashboard_definition_ids --
// a {opportunity_id: dedicated dashboard definition_id} map, one dashboard
// instance per opportunity, each created with THAT opportunity as its only
// one from the start (so its auto-created pipelines are always correctly
// owned, no cross-opp resolution involved at all). Clicking a button does
// exactly one thing against the dashboard dedicated to that opportunity:
// POSTs to /labs/workflow/api/<that dashboard's definition_id>/run/start/
// (start_run_api, connect_labs/workflow/views.py) with program_id --
// FORM-ENCODED body, since start_run_api reads request.POST.get(...), which
// Django only populates from an x-www-form-urlencoded (or multipart) body,
// not a JSON one. Then navigates to the new run, preferring the response's
// own `redirect` field (built server-side from the scope it actually
// resolved) over hand-building the URL.
//
// No shared cross-template fetch primitive exists for this (checked
// WORKFLOW_REFERENCE.md §4b -- window.LabsAudit is a UI panel, not a network
// helper), so csrfToken/postForm are duplicated here in the same
// self-contained-per-file shape as chc_mopup_candidates_render.js's copy.

var ce = React.createElement;

function csrfToken() {
  var root = document.getElementById('workflow-root');
  if (root && root.dataset && root.dataset.csrfToken)
    return root.dataset.csrfToken;
  var el = document.querySelector('[name=csrfmiddlewaretoken]');
  return el ? el.value : '';
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

  // {opportunity_id (string key, JSON object keys always are) : dedicated
  // dashboard definition_id}. Set manually after this workflow instance AND
  // one dashboard instance per opportunity are created -- see
  // chc_mopup_setup.py's module docstring. NOT read from
  // window.WORKFLOW_API_ENDPOINTS, which only ever carries endpoints for
  // whichever workflow definition is currently on screen (this setup
  // workflow's own), not any dashboard's.
  var dashboardDefinitionIds =
    (definition &&
      definition.config &&
      definition.config.dashboard_definition_ids) ||
    {};

  var programId = instance && instance.program_id;

  var _busyOppId = React.useState(null);
  var busyOppId = _busyOppId[0],
    setBusyOppId = _busyOppId[1];
  var _err = React.useState('');
  var errMsg = _err[0],
    setErrMsg = _err[1];

  function handlePick(oppId) {
    var dashboardDefinitionId = dashboardDefinitionIds[String(oppId)];
    if (busyOppId != null || !dashboardDefinitionId) return;
    setBusyOppId(oppId);
    setErrMsg('');

    var oppLabel = oppNames[oppId] || 'Opportunity #' + oppId;

    postForm('/labs/workflow/api/' + dashboardDefinitionId + '/run/start/', {
      program_id: programId,
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
          'A mop-up round is run for one LLO at a time. Pick the opportunity below to jump straight into ' +
            'a "CHC Mop-up Candidate Analysis" dashboard dedicated to just that opportunity -- this skips ' +
            'waiting for every opportunity’s data to load up front.',
        ),
        ce(
          'div',
          { className: 'flex flex-wrap gap-3 pt-1' },
          oppIds.map(function (oppId) {
            var isBusy = busyOppId === oppId;
            var configured = !!dashboardDefinitionIds[String(oppId)];
            var disabled = !configured || busyOppId != null;
            return ce(
              'div',
              { key: oppId, className: 'flex flex-col items-start gap-1' },
              ce(
                'button',
                {
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
                isBusy
                  ? 'Starting…'
                  : oppNames[oppId] || 'Opportunity #' + oppId,
              ),
              !configured
                ? ce(
                    'span',
                    { className: 'text-xs text-amber-700' },
                    'No dashboard configured for this opportunity yet',
                  )
                : null,
            );
          }),
        ),
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
      ),
    ),
  );
}
