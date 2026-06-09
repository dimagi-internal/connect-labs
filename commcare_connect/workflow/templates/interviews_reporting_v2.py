"""Connect Interviews Reporting V2 — pipeline-pure funnel dashboard.

Two pipelines:
  triggers  — CCHQ Trigger Bot forms: who was triggered, for which interview,
               in which cohort.
  sessions  — OCS sessions for the interviews experiment: who started and
               completed each interview conversation.

Both pipelines feed the render layer directly. No Python job handler, no
management command, no build_snapshot hook required.

Funnel logic (JSX):
  Triggered  = unique connect_id per interview in triggers pipeline,
               filtered to definition.config.cohortId.
  Started    = those FLW connect_ids who also appear in sessions with any
               status other than "pending" or "setup".
  Completed  = those FLW connect_ids who appear in sessions with status
               "complete".

Session ↔ trigger join: keyed on connect_id. A single OCS experiment covers
all interviews for the project. Because connect_id is unique per FLW within
a cohort, matching sessions to trigger slots is a cross-pipeline join on
that key — no Nth-match reconciliation needed at this level of aggregation.
"""

INTERVIEWS_OCS_EXPERIMENT_ID = "cc01d032-5931-4bdd-a4b2-6f05f4f72f88"

DEFINITION = {
    "name": "Connect Interviews Funnel",
    "description": "Triggered / Started / Completed funnel per interview, per cohort. Both legs are pipeline-pure.",
    "version": 1,
    "templateType": "interviews_reporting_v2",
    "statuses": [
        {"id": "in_progress", "label": "In Progress", "color": "blue"},
        {"id": "completed", "label": "Completed", "color": "green"},
    ],
    "config": {
        "auth_requires": ["connect", "commcare_hq", "ocs"],
        # cohortId is set per-instance at workflow creation time.
        # Example: "01TRS", "02TRS", "1ABT1CA1"
        "cohortId": "",
    },
    "pipeline_sources": [],
}

# ---------------------------------------------------------------------------
# Pipeline schemas
# ---------------------------------------------------------------------------

TRIGGERS_SCHEMA = {
    # One row per Trigger Bot form submission — one trigger = one FLW invited
    # to one specific interview in a specific cohort.
    "data_source": {
        "type": "cchq_forms",
        "form_name": "Trigger Bot",
        "app_id_source": "opportunity",
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "cohort_id", "path": "form.cohort_id", "aggregation": "first"},
        {"name": "next_interview", "path": "form.next_interview", "aggregation": "first"},
        {"name": "connect_id", "path": "form.connect_id", "aggregation": "first"},
        {"name": "received_on", "path": "form.meta.timeEnd", "aggregation": "first"},
    ],
}

SESSIONS_SCHEMA = {
    # One row per OCS session. Session data is nested under "session.*" in
    # form_json so field paths follow the same dot-notation as other pipelines.
    #
    # OCS StatusCb5Enum values:
    #   setup | pending | pending-pre-survey | active | pending-review | complete | unknown
    #
    # Started  = status in {active, pending-review, complete}  (FLW engaged)
    # Completed = status == "complete"
    "data_source": {
        "type": "ocs_sessions",
        "experiment_id": INTERVIEWS_OCS_EXPERIMENT_ID,
        "api_key": "81G2MJVh.ec8Px6M0UZU96tFbXh2hzlMgh7YOedYI",
    },
    "grouping_key": "username",  # set to participant.identifier by ocs_fetcher
    "terminal_stage": "visit_level",
    "fields": [
        {
            "name": "connect_id",
            "path": "session.participant.identifier",
            "aggregation": "first",
        },
        {
            "name": "status",
            "path": "session.status",
            "aggregation": "first",
        },
        {
            "name": "created_at",
            "path": "session.created_at",
            "aggregation": "first",
        },
        {
            "name": "updated_at",
            "path": "session.updated_at",
            "aggregation": "first",
        },
    ],
}

PIPELINE_SCHEMAS = [
    {
        "alias": "triggers",
        "name": "Interviews Trigger Bot Forms",
        "description": "CCHQ Trigger Bot submissions — one row per FLW per interview trigger",
        "schema": TRIGGERS_SCHEMA,
    },
    {
        "alias": "sessions",
        "name": "Interviews OCS Sessions",
        "description": "OCS interview sessions — one row per session with participant and status",
        "schema": SESSIONS_SCHEMA,
    },
]

# ---------------------------------------------------------------------------
# Render code
# ---------------------------------------------------------------------------

RENDER_CODE = r"""
function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState, view }) {
  var config = definition.config || {};
  var cohortId = config.cohortId || "";

  // Prefer view helper (snapshot-or-live); fall back to raw pipelines prop.
  var triggerRows =
    (view && view.pipelines && view.pipelines.triggers && view.pipelines.triggers.rows) ||
    (pipelines && pipelines.triggers && pipelines.triggers.rows) ||
    [];
  var sessionRows =
    (view && view.pipelines && view.pipelines.sessions && view.pipelines.sessions.rows) ||
    (pipelines && pipelines.sessions && pipelines.sessions.rows) ||
    [];

  // OCS statuses that count as "started" (FLW engaged with the conversation).
  var STARTED_STATUSES = new Set(["active", "pending-review", "complete"]);

  // Build session lookup: connect_id → Set of statuses across all sessions.
  var sessionStatusesByConnectId = {};
  sessionRows.forEach(function (row) {
    var id = row.connect_id;
    if (!id) return;
    if (!sessionStatusesByConnectId[id]) sessionStatusesByConnectId[id] = new Set();
    if (row.status) sessionStatusesByConnectId[id].add(row.status);
  });

  // Interview definitions — add more rows as new interviews are introduced.
  var interviews = [
    { code: "A", label: "Int #1 (A)", topic: "Community Demographics" },
    { code: "B", label: "Int #2 (B)", topic: "Malaria" },
  ];

  // Compute funnel counts for each interview.
  var funnelRows = interviews.map(function (iv) {
    // Triggered: unique connect_ids with this interview code in this cohort.
    var triggeredSet = new Set();
    triggerRows.forEach(function (row) {
      if (cohortId && String(row.cohort_id) !== cohortId) return;
      if (String(row.next_interview) !== iv.code) return;
      var id = row.connect_id || row.username;
      if (id) triggeredSet.add(id);
    });

    // Started / Completed: subset of triggered FLWs with matching session status.
    var startedCount = 0;
    var completedCount = 0;
    triggeredSet.forEach(function (connectId) {
      var statuses = sessionStatusesByConnectId[connectId];
      if (!statuses) return;
      var hasStarted = Array.from(statuses).some(function (s) { return STARTED_STATUSES.has(s); });
      var hasCompleted = statuses.has("complete");
      if (hasStarted) startedCount++;
      if (hasCompleted) completedCount++;
    });

    return {
      code: iv.code,
      label: iv.label,
      topic: iv.topic,
      triggered: triggeredSet.size,
      started: startedCount,
      completed: completedCount,
    };
  });

  // Styles
  var th = "px-6 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider";
  var td = "px-6 py-4 whitespace-nowrap text-sm text-gray-900";
  var tdR = td + " text-right";
  var tdRGreen = tdR + " font-medium text-green-700";

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-lg shadow-sm p-6">
        <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
        <p className="text-gray-600 mt-1">{definition.description}</p>
        {cohortId && (
          <span className="inline-block mt-2 px-2 py-0.5 text-xs font-medium bg-blue-100 text-blue-800 rounded">
            Cohort: {cohortId}
          </span>
        )}
        <p className="text-xs text-gray-400 mt-2">
          {triggerRows.length} trigger rows · {sessionRows.length} session rows loaded
        </p>
      </div>

      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className={th + " text-left"}>Interview</th>
              <th className={th + " text-left"}>Topic</th>
              <th className={th + " text-right"}>Triggered</th>
              <th className={th + " text-right"}>Started</th>
              <th className={th + " text-right"}>Completed</th>
              <th className={th + " text-right"}>Start rate</th>
              <th className={th + " text-right"}>Completion rate</th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {funnelRows.map(function (row) {
              var startRate = row.triggered > 0
                ? Math.round((row.started / row.triggered) * 100) + "%"
                : "—";
              var completeRate = row.triggered > 0
                ? Math.round((row.completed / row.triggered) * 100) + "%"
                : "—";
              return (
                <tr key={row.code} className="hover:bg-gray-50">
                  <td className={td + " font-medium"}>{row.label}</td>
                  <td className={td}>{row.topic}</td>
                  <td className={tdR}>{row.triggered}</td>
                  <td className={tdR}>{row.started}</td>
                  <td className={tdRGreen}>{row.completed}</td>
                  <td className={tdR + " text-gray-500"}>{startRate}</td>
                  <td className={tdR + " text-gray-500"}>{completeRate}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
"""

TEMPLATE = {
    "key": "interviews_reporting_v2",
    "name": "Connect Interviews Reporting V2",
    "description": "Pipeline-pure funnel: Triggered (CCHQ) + Started/Completed (OCS sessions). No Python job handler.",
    "icon": "fa-comments",
    "color": "indigo",
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
