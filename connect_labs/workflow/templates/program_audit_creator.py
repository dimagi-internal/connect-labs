"""Program Audit Creator — program-level, trackable, saved-runs workflow.

Generates a program's weekly audits by fanning out to the per-opp
``weekly_dual_track_audit`` creator instances and recording what was generated
into the PROGRAM run's state so the run TRACKS the fan-out. Completing the
program week is gated on every per-opp audit being finished.

This is the program-wide counterpart of ``weekly_dual_track_audit`` (one per
opp). The fan-out that used to live on the ``audit_par`` REPORT lives here — the
report is a pure viewer again.

Global constraints honoured here:
- **Opp-scoping:** every read/write goes through a ``WorkflowDataAccess`` /
  ``AuditDataAccess`` scoped to a single owning opportunity — never one
  unscoped client. (Root cause of PRs #777/#779/#783.)
- **Idempotency:** ``run_default`` never creates a second PROGRAM run for a
  window that already has one; each per-opp creator is itself idempotent per
  (opp, window), so re-fanning-out is safe.
"""

import logging

from connect_labs.audit.data_access import AuditDataAccess
from connect_labs.workflow.data_access import WorkflowDataAccess

logger = logging.getLogger(__name__)


def _program_opp_id(definition):
    return definition.opportunity_id or (definition.opportunity_ids or [None])[0]


def _program_owner(definition):
    """Resolve the owner SCOPE for this creator's PROGRAM run.

    The Program Creator is program-owned when its LabsRecord carries a program
    FK (``definition.program_id``). In that case the PROGRAM run it
    creates/tracks must be PROGRAM-scoped — no owning opportunity. A creator
    that is still opp-owned (``program_id`` is None) falls back to the legacy
    opp path so pre-existing instances keep working.

    Returns ``("program", program_id)`` or ``("opportunity", opp_id)``. Note
    this governs only the PROGRAM run's own scope; the per-opp creators the
    fan-out dispatches stay opp-scoped (they generate the audits).
    """
    from connect_labs.workflow.program_view import program_id_of

    pid = program_id_of(definition)
    if pid is not None:
        return ("program", pid)
    return ("opportunity", _program_opp_id(definition))


def _program_run_dao(definition, access_token):
    """WorkflowDataAccess for the PROGRAM run — program-scoped when
    program-owned, else opp-scoped (legacy)."""
    kind, owner_id = _program_owner(definition)
    if kind == "program":
        return WorkflowDataAccess(access_token=access_token, program_id=owner_id)
    return WorkflowDataAccess(access_token=access_token, opportunity_id=owner_id)


def _program_run_owner_kwargs(definition):
    """``create_run`` owner kwarg for the PROGRAM run (exactly one of
    program_id / opportunity_id)."""
    kind, owner_id = _program_owner(definition)
    return {"program_id": owner_id} if kind == "program" else {"opportunity_id": owner_id}


def _resolve_instances(definition):
    """Config's per-opp creator instances: ``[{opportunity_id, workflow_definition_id}]``."""
    config = definition.data.get("config") or {}
    return [
        {"opportunity_id": s.get("opportunity_id"), "workflow_definition_id": s.get("workflow_definition_id")}
        for s in (config.get("per_opp_instances") or [])
        if s.get("opportunity_id") is not None and s.get("workflow_definition_id") is not None
    ]


def _run_has_window(run, window_start):
    return ((run.data or {}).get("state", {}) or {}).get("window_start") == window_start


# =============================================================================
# Shared fan-out
# =============================================================================


def fan_out_generate(
    *,
    definition,
    run_id,
    access_token,
    request=None,
    window=None,
    progress_callback=None,
    only_opportunity_id=None,
    criteria_overrides=None,
) -> dict:
    """Fire this program run's audit generation into each per-opp creator.

    Firing is an EXECUTION: for each targeted ``per_opp_instances`` entry it loads
    the per-opp creator definition (opp-scoped ``WorkflowDataAccess``) and
    dispatches it via ``run_default_for_definition``, which always creates a fresh
    per-opp run and fires its batch. The exact run it spawned is recorded into the
    PROGRAM run's ``state.generation`` (per opp: ``run_id``, ``session_count``,
    ``status``, ``order``) so "open run" points at the run this fire executed.

    Single-fire: a program run is fired ONCE. If ``state.generation`` is already
    populated and this is a full fan-out (``only_opportunity_id is None``), we do
    NOT re-fire — recovery is per-opp. Pass ``only_opportunity_id`` to (re-)run a
    single opportunity; its entry is merged into the existing record.

    ``criteria_overrides`` (optional dict with ``pass_threshold``,
    ``deliver_unit_types``, ``visit_statuses``) is applied to every audit created
    by this fire — persisted onto the PROGRAM run's state so a later per-opp
    re-run reuses the same filters. See PR #884 for these three filters'
    original (Django wizard) implementation; ``AuditCriteria.from_dict`` already
    understands these keys unchanged.

    Returns ``{"per_opp": {opp_id: result}, "generation", "window_start",
    "window_end"}``.
    """
    from connect_labs.workflow.audit_generation import dispatch_batch, sample_overrides_for

    window_start, window_end = window if window else (None, None)
    criteria_overrides = criteria_overrides or {}
    all_sources = _resolve_instances(definition)
    order_of = {s["opportunity_id"]: i for i, s in enumerate(all_sources)}

    # Load the program run's existing generation so a per-opp re-run MERGES into
    # it (rather than clobbering) and so a full fire is single-shot.
    pwda = _program_run_dao(definition, access_token)
    try:
        prun = pwda.get_run(run_id)
        prun_state = (getattr(prun, "data", None) or {}).get("state", {}) or {} if prun else {}
        existing = prun_state.get("generation") or {}
    finally:
        pwda.close()

    # A per-opp re-run doesn't re-send filters (the render only sends them on the
    # initial fire) — reuse whatever was persisted from the program run's first
    # fire so recovery stays consistent with the rest of the run.
    if not criteria_overrides:
        criteria_overrides = {
            k: prun_state[k] for k in ("pass_threshold", "deliver_unit_types", "visit_statuses") if k in prun_state
        }

    if only_opportunity_id is None and existing:
        # Already fired — do not re-create the whole fan-out. Recovery is per-opp.
        return {
            "per_opp": {},
            "generation": existing,
            "already_fired": True,
            "window_start": window_start,
            "window_end": window_end,
        }

    sources = (
        [s for s in all_sources if s["opportunity_id"] == only_opportunity_id]
        if only_opportunity_id is not None
        else all_sources
    )
    total = len(sources)

    def _emit(msg, processed, item_result):
        # Stream the per-opp entry (with its task_id) on the program dispatch job's
        # progress channel so the render learns each opp's task to poll. The rows
        # themselves are painted from the runner's run-state refetch + each row's
        # own poll of its opp job — not from these items.
        if progress_callback:
            progress_callback(msg, processed=processed, total=total, item_result=item_result)

    def _persist(generation):
        # Persist the accumulating fan-out record onto the PROGRAM run, scoped to
        # the PROGRAM run's owner (program-scoped when program-owned, else the
        # creator's owning opp), so a reload reflects each opp's run + status.
        # Filters are persisted too so a later per-opp re-run reuses them.
        pwda = _program_run_dao(definition, access_token)
        try:
            pwda.update_run_state(
                run_id,
                {
                    "generation": dict(generation),
                    "window_start": window_start,
                    "window_end": window_end,
                    **criteria_overrides,
                },
            )
        finally:
            pwda.close()

    per_opp = {}
    generation = dict(existing)
    for idx, source in enumerate(sources):
        opp_id = source["opportunity_id"]
        def_id = source["workflow_definition_id"]
        order = order_of.get(opp_id, idx)

        # Opp-scoped read of the per-opp creator definition (Global Constraint).
        wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opp_id)
        try:
            creator_def = wda.get_definition(def_id)
        finally:
            wda.close()
        if creator_def is None:
            entry = {
                "id": opp_id,
                "opportunity_id": opp_id,
                "workflow_definition_id": def_id,
                "status": "failed",
                "order": order,
            }
            generation[str(opp_id)] = entry
            _persist(generation)
            _emit(f"Opportunity #{opp_id}: creator not found", idx + 1, entry)
            continue

        # DISPATCH the opp's audit-creation job ASYNC (the same job the per-opp
        # workflow page runs) and record its task_id. We do NOT wait — every opp is
        # dispatched, so they run in PARALLEL (governed by the worker pool), and
        # each row polls its own task's status to glide. A reload reconnects because
        # the task_ids are persisted here.
        dispatched = dispatch_batch(
            creator_def,
            window_start,
            window_end,
            access_token=access_token,
            sample_overrides=sample_overrides_for(creator_def),
            criteria_overrides=criteria_overrides,
        )
        per_opp[opp_id] = dispatched
        entry = {
            "id": opp_id,
            "opportunity_id": opp_id,
            "workflow_definition_id": def_id,
            "run_id": dispatched.get("run_id"),
            "task_id": dispatched.get("task_id"),
            "status": "running",
            "order": order,
        }
        generation[str(opp_id)] = entry
        _persist(generation)
        _emit(f"Opportunity #{opp_id}: dispatched", idx + 1, entry)

    return {"per_opp": per_opp, "generation": generation, "window_start": window_start, "window_end": window_end}


# =============================================================================
# Default-run (cron path)
# =============================================================================


def run_default(*, definition, run=None, access_token, request=None, window=None, **_):
    """Default-run hook: generate the whole program's week with no UI.

    Resolves the window (default ``last_week``), creates or reuses ONE PROGRAM
    run for that window (idempotent per window, like the per-opp creator), then
    fans out synchronously via ``fan_out_generate``. Returns its result.
    """
    from datetime import date

    from connect_labs.workflow.audit_generation import resolve_window

    if window is None:
        window_start, window_end = resolve_window("last_week", date.today())
    else:
        window_start, window_end = window

    def_id = definition.id

    if run is None:
        wda = _program_run_dao(definition, access_token)
        try:
            run = next(
                (r for r in wda.list_runs(def_id) if _run_has_window(r, window_start)),
                None,
            )
            if run is None:  # idempotent per window
                run = wda.create_run(
                    def_id,
                    period_start=window_start,
                    period_end=window_end,
                    initial_state={"window_start": window_start, "window_end": window_end},
                    **_program_run_owner_kwargs(definition),
                )
        finally:
            wda.close()

    return fan_out_generate(
        definition=definition,
        run_id=run.id,
        access_token=access_token,
        request=request,
        window=(window_start, window_end),
    )


# =============================================================================
# Saved-runs completion gate (program level)
# =============================================================================


def build_snapshot(*, pipelines, state, opportunity_id, run_id=None, request=None, access_token=None, **_):
    """Saved-runs completion hook. PROGRAM-LEVEL GATE.

    Reads every per-opp instance's audit sessions (each source's generated
    ``run_id`` comes from ``state.generation``, opp-scoped via ``AuditDataAccess``)
    and RAISES until ALL are ``completed`` across the whole program. Otherwise
    returns a snapshot with the generation record + a per-opp completion rollup.
    """
    generation = (state or {}).get("generation") or {}

    per_opp_completion = {}
    total_audits = 0
    open_audits = 0
    empty_opps = []
    for gen in generation.values():
        opp_id = gen.get("opportunity_id")
        gen_run_id = gen.get("run_id")
        if opp_id is None or gen_run_id is None:
            continue
        ada = AuditDataAccess(request=request, access_token=access_token, opportunity_id=opp_id)
        try:
            sessions = ada.get_sessions_by_workflow_run(gen_run_id)
        finally:
            ada.close()
        total = len(sessions)
        done = sum(1 for s in sessions if s.status == "completed")
        incomplete = total - done
        total_audits += total
        open_audits += incomplete
        if total == 0:
            empty_opps.append(opp_id)
        per_opp_completion[str(opp_id)] = {
            "opportunity_id": opp_id,
            "workflow_definition_id": gen.get("workflow_definition_id"),
            "run_id": gen_run_id,
            "total_audits": total,
            "open_audits": incomplete,
            "status": "completed" if (total > 0 and incomplete == 0) else "in_progress",
        }

    # A run that produced no audits didn't finish — re-run it before completing.
    if empty_opps:
        raise ValueError(
            f"{len(empty_opps)} opportunit{'y' if len(empty_opps) == 1 else 'ies'} produced no audits "
            f"({', '.join('#' + str(o) for o in empty_opps)}) — re-run before completing the program week."
        )
    if open_audits > 0:
        raise ValueError(
            f"{open_audits} of {total_audits} audits still open across the program — "
            "every org must finish before the program week can be completed."
        )

    return {
        "generation": generation,
        "per_opp_completion": per_opp_completion,
        "completed_counts": {"total": total_audits, "open": open_audits},
        "window_start": state.get("window_start"),
        "window_end": state.get("window_end"),
    }


DEFINITION = {
    "name": "Program Audit Creator",
    "description": "Generate a program's weekly audits across every opportunity, and track the fan-out to completion.",
    "version": 1,
    "templateType": "program_audit_creator",
    "statuses": [
        {"id": "config", "label": "Configuring", "color": "gray"},
        {"id": "generating", "label": "Generating", "color": "blue"},
        {"id": "generated", "label": "Generated", "color": "green"},
    ],
    "config": {
        # One entry per per-opp weekly_dual_track_audit creator instance to
        # generate into: {opportunity_id, workflow_definition_id}.
        "per_opp_instances": [],
        # Optional: the audit_par report instance to link to from the runner.
        "report_definition_id": None,
    },
    "pipeline_sources": [],
}


RENDER_CODE = r"""function WorkflowUI({ definition, instance, view, actions, onUpdateState }) {
    var config = (definition && definition.config) || {};
    var sources = config.per_opp_instances || [];
    var reportDefId = config.report_definition_id || null;

    var runState = (view && view.state) || instance.state || {};
    var generation = runState.generation || {};

    var [datePreset, setDatePreset] = React.useState(runState.date_preset || 'last_week');
    var [startDate, setStartDate] = React.useState(runState.window_start || '');
    var [endDate, setEndDate] = React.useState(runState.window_end || '');
    // Audit-quality filters (PR #884, ported from the Django creation wizard).
    // Applied identically across every opp/track this program run fires.
    var [passThreshold, setPassThreshold] = React.useState(runState.pass_threshold != null ? runState.pass_threshold : 100);
    var [deliverUnitTypes, setDeliverUnitTypes] = React.useState(runState.deliver_unit_types || []);
    var [visitStatuses, setVisitStatuses] = React.useState(runState.visit_statuses || []);
    // Deliver unit types (form.@name) unioned across every source opportunity's
    // visits — same discovery endpoint the Django wizard uses.
    var [availableDeliverUnitTypes, setAvailableDeliverUnitTypes] = React.useState([]);
    var [deliverUnitTypesLoading, setDeliverUnitTypesLoading] = React.useState(false);
    // Visit status is a small fixed enum (VisitValidationStatus) — no discovery call needed.
    var VISIT_STATUS_OPTIONS = [
        { value: 'pending', label: 'Pending' },
        { value: 'approved', label: 'Approved' },
        { value: 'rejected', label: 'Rejected' },
        { value: 'over_limit', label: 'Over Limit' },
        { value: 'duplicate', label: 'Duplicate' },
        { value: 'trial', label: 'Trial' }
    ];
    var [isRunning, setIsRunning] = React.useState(false);
    var [busyOpp, setBusyOpp] = React.useState(null);
    var [progress, setProgress] = React.useState(null);
    var [jobError, setJobError] = React.useState(null);
    // Per-opp status the program dispatch stream reports (keyed by opportunity_id):
    // {status, run_id, task_id, session_count, ...}. Merged over persisted generation.
    var [liveItems, setLiveItems] = React.useState({});
    // Live PROGRESS of each opp's own audit job (opportunity_id -> {processed,total,
    // message}), fetched by polling that job's status — the same job + status the
    // per-opp workflow page shows. Poll-first holds no connection, so N opps in
    // parallel each poll independently without contention.
    var [oppProgress, setOppProgress] = React.useState({});
    // Inline "open the opp view here" expand: which opp rows are expanded, and the
    // sessions each has lazy-fetched (keyed by opportunity_id). The breakdown
    // itself is drawn by the shared window.LabsAudit primitive — the SAME renderer
    // the opp-level run page uses — so expanding a row shows exactly the opp view.
    var [expandedOpps, setExpandedOpps] = React.useState({});
    var [oppSessions, setOppSessions] = React.useState({});      // opp_id -> sessions[]
    var [oppSessionsLoading, setOppSessionsLoading] = React.useState({});
    var cleanupRef = React.useRef(null);
    var oppStreamsRef = React.useRef({}); // opportunity_id -> {taskId, cleanup}
    var activeTaskRef = React.useRef(null); // program job task_id, for Cancel
    var reconcileRef = React.useRef(0);     // count of opps we've asked the server to reconcile

    var isCompleted = view && view.isCompleted;

    var mergedGen = Object.assign({}, generation, liveItems);
    // A program run is fired ONCE: once it has (or is) generating its per-opp runs,
    // the Generate button is retired and recovery happens per-opp.
    var hasFired = Object.keys(mergedGen).length > 0 || isRunning;
    var genList = Object.keys(mergedGen).map(function (k) { return mergedGen[k]; });
    var anyOppRunning = isRunning || genList.some(function (g) { return g && g.status === 'running'; });
    var oppsDone = genList.filter(function (g) { return g && g.status && g.status !== 'running'; }).length;

    // Turn a raw job message into a clear two-step label. run_workflow_job always
    // prefixes "Stage 1/1:" (audit jobs have no pipeline stage, so that count is
    // meaningless) — the real phases are Creating audits → AI review, both already
    // present in the message text. Strip the noise prefix and stamp the real step.
    function stepLabel(msg) {
        if (!msg) return 'Step 1 of 2 · Creating audits…';
        var m = String(msg).replace(/^Stage \d+\/\d+:\s*/, '');
        var isReview = /AI review/i.test(m);
        return 'Step ' + (isReview ? 2 : 1) + ' of 2 · ' + m;
    }

    // Merge one streamed per-opp update into the live map.
    function applyItem(item) {
        if (!item || item.opportunity_id == null) return;
        setLiveItems(function (m) {
            var n = Object.assign({}, m);
            var k = String(item.opportunity_id);
            n[k] = Object.assign({}, n[k], item);
            return n;
        });
    }

    // The opp jobs run async and write completion only to THEIR OWN run's active_job.
    // A client state write can't reliably persist it back to the program generation
    // (the runner's refetch + prod read-after-write lag revert it). So ask the SERVER
    // to reconcile: it reads each opp run's authoritative state and writes the program
    // generation server-side (monotonic, program-scoped). Fire-and-forget; called when
    // the number of finished opps changes so the persisted state + its export catch up.
    function reconcileGeneration() {
        var el = document.getElementById('workflow-root');
        var t = (el && el.dataset) ? el.dataset.csrfToken : '';
        fetch('/labs/workflow/api/run/' + instance.id + '/reconcile-generation/', {
            method: 'POST', headers: { 'X-CSRFToken': t }
        }).catch(function () {});
    }

    // Whenever more opps have reached a terminal state than the server has been told
    // about, trigger a server-side reconcile so the persisted generation catches up.
    // This fires while the page is open AND on a later reload (the polls re-detect
    // completion), so a run whose page was closed mid-flight is fixed on next visit.
    React.useEffect(function () {
        if (oppsDone > reconcileRef.current) {
            reconcileRef.current = oppsDone;
            reconcileGeneration();
        }
    });

    // Belt-and-suspenders: on first mount of an in-progress run that still shows any
    // opp as "running", reconcile once unconditionally. The per-opp polls above can
    // never resolve for an OLD run (Celery task results expire, so status.json goes
    // unknown and onComplete never fires) — but the server reads each opp run's
    // DURABLE active_job, so this heals a long-closed run the moment it's reopened.
    var didMountReconcileRef = React.useRef(false);
    React.useEffect(function () {
        if (didMountReconcileRef.current) return;
        if (!isCompleted && hasFired && anyOppRunning) {
            didMountReconcileRef.current = true;
            reconcileGeneration();
        }
    }, []);

    // For each opp that has a running audit job (a task_id), poll that job's own
    // status so its row glides — reusing the exact job + status the per-opp workflow
    // page uses. Poll-first (streamJobProgress defaults to polling) holds no
    // connection, so all opps poll in parallel without starving the worker pool.
    // Guarded by task_id so a per-opp Re-run reconnects to its NEW task; a reload
    // reconnects because task_ids are persisted in the generation record.
    React.useEffect(function () {
        Object.keys(mergedGen).forEach(function (k) {
            var g = mergedGen[k];
            if (!g || g.status !== 'running' || !g.task_id) return;
            var ex = oppStreamsRef.current[k];
            if (ex && ex.taskId === g.task_id) return; // already polling this task
            if (ex && ex.cleanup) ex.cleanup();          // task changed (re-run) → drop old
            var oppId = g.opportunity_id;
            var oppRunId = g.run_id;
            var cleanup = actions.streamJobProgress(
                g.task_id,
                function (p) { setOppProgress(function (m) { var n = Object.assign({}, m); n[k] = p; return n; }); },
                null,
                function (results) {
                    // Done — read the authoritative audit count from the same
                    // sessions endpoint the per-opp page uses, then reflect it in the
                    // row AND persist it into the program generation.
                    fetch('/audit/api/workflow/' + oppRunId + '/sessions/?opportunity_id=' + oppId)
                        .then(function (r) { return r.json(); })
                        .then(function (d) {
                            var count = (d && d.success && d.sessions) ? d.sessions.length : ((results && results.sessions_created) || 0);
                            applyItem({ opportunity_id: oppId, run_id: oppRunId, status: 'ready', session_count: count });
                        })
                        .catch(function () {
                            applyItem({ opportunity_id: oppId, run_id: oppRunId, status: 'ready', session_count: (results && results.sessions_created) || 0 });
                        });
                },
                function (err) { applyItem({ opportunity_id: oppId, run_id: oppRunId, status: 'failed' }); },
                function () { applyItem({ opportunity_id: oppId, run_id: oppRunId, status: 'cancelled' }); },
                oppRunId // run_id — lets the status endpoint terminate a dead/finished task
            );
            oppStreamsRef.current[k] = { taskId: g.task_id, cleanup: cleanup };
        });
    });
    React.useEffect(function () {
        return function () { Object.keys(oppStreamsRef.current).forEach(function (k) { var e = oppStreamsRef.current[k]; if (e && e.cleanup) e.cleanup(); }); };
    }, []);

    function calculateDateRange(preset) {
        var today = new Date(); today.setHours(0, 0, 0, 0);
        var start, end;
        switch (preset) {
            case 'last_week': {
                var dow = today.getDay();
                var thisSun = new Date(today); thisSun.setDate(today.getDate() - dow);
                end = new Date(thisSun); end.setDate(thisSun.getDate() - 1);
                start = new Date(thisSun); start.setDate(thisSun.getDate() - 7);
                break;
            }
            case 'last_7_days':
                end = new Date(today); end.setDate(today.getDate() - 1);
                start = new Date(end); start.setDate(end.getDate() - 6); break;
            case 'last_14_days':
                end = new Date(today); end.setDate(today.getDate() - 1);
                start = new Date(end); start.setDate(end.getDate() - 13); break;
            case 'last_30_days':
                end = new Date(today); end.setDate(today.getDate() - 1);
                start = new Date(end); start.setDate(end.getDate() - 29); break;
            case 'last_month':
                start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                end = new Date(today.getFullYear(), today.getMonth(), 0); break;
            default: return null;
        }
        return { start: start.toISOString().split('T')[0], end: end.toISOString().split('T')[0] };
    }

    function applyPreset(preset) {
        setDatePreset(preset);
        if (preset !== 'custom') {
            var range = calculateDateRange(preset);
            if (range) { setStartDate(range.start); setEndDate(range.end); }
        }
    }

    React.useEffect(function () { if (!startDate && !endDate) applyPreset('last_week'); }, []);
    React.useEffect(function () { return function () { if (cleanupRef.current) cleanupRef.current(); }; }, []);

    // Discover deliver unit types (form.@name) across every source opportunity's
    // visits, union them, and drop any previously-selected type that's no longer
    // available. Runs once — sources are fixed config, not user-editable here.
    React.useEffect(function () {
        var oppIds = sources.map(function (s) { return s.opportunity_id; }).filter(function (id) { return id != null; });
        if (oppIds.length === 0) return;
        setDeliverUnitTypesLoading(true);
        Promise.all(oppIds.map(function (oppId) {
            return fetch('/audit/api/opportunity/' + oppId + '/deliver-unit-types/')
                .then(function (r) { return r.ok ? r.json() : []; })
                .catch(function () { return []; });
        })).then(function (lists) {
            var unique = {};
            lists.forEach(function (list) { (list || []).forEach(function (name) { if (name) unique[name] = true; }); });
            var types = Object.keys(unique).sort();
            setAvailableDeliverUnitTypes(types);
            setDeliverUnitTypes(function (prev) { return prev.filter(function (t) { return types.indexOf(t) !== -1; }); });
        }).finally(function () { setDeliverUnitTypesLoading(false); });
    }, []);

    function attachStream(taskId) {
        activeTaskRef.current = taskId;
        var cleanup = actions.streamJobProgress(
            taskId,
            function (p) { setProgress(p); },
            applyItem, // per-opp rows (status + progress) stream in on this one stream
            function (results) {
                // On completion apply the returned generation as the source of
                // truth (covers any item that raced the finish). No page reload.
                setIsRunning(false); setProgress(null); activeTaskRef.current = null;
                var gen = (results && results.generation) || {};
                Object.keys(gen).forEach(function (k) { applyItem(gen[k]); });
                onUpdateState({ active_job: { job_id: taskId, status: 'completed' } }).catch(function () {});
            },
            function (err) {
                setIsRunning(false); setJobError(err || 'Generation failed'); setProgress(null); activeTaskRef.current = null;
                onUpdateState({ active_job: { job_id: taskId, status: 'failed' } }).catch(function () {});
            },
            function () {
                // Cancelled: any opp still 'running' is marked cancelled so its row
                // stops spinning and offers a Re-run.
                setIsRunning(false); setProgress(null); activeTaskRef.current = null;
                setLiveItems(function (m) {
                    var n = Object.assign({}, m);
                    Object.keys(n).forEach(function (k) { if (n[k] && n[k].status === 'running') n[k] = Object.assign({}, n[k], { status: 'cancelled' }); });
                    return n;
                });
            },
            instance.id // run_id — lets the server unstick a reconnect to a dead job
        );
        cleanupRef.current = cleanup;
    }

    // Cancel the running program job (revokes the Celery task). Rows still
    // 'running' settle to 'cancelled' via the stream's cancel callback.
    function cancelRun() {
        setJobError(null);
        // Cancel the program dispatch job (if still dispatching) AND every opp's
        // own running audit job — they run as independent tasks now.
        if (activeTaskRef.current) actions.cancelJob(activeTaskRef.current, instance.id).catch(function () {});
        Object.keys(mergedGen).forEach(function (k) {
            var g = mergedGen[k];
            if (g && g.status === 'running' && g.task_id) {
                actions.cancelJob(g.task_id, g.run_id).catch(function () {});
                applyItem({ opportunity_id: g.opportunity_id, run_id: g.run_id, status: 'cancelled' });
            }
        });
    }

    // Reconnect to a still-running job after a page reload — but guard against a
    // ZOMBIE job (worker killed mid-fire leaves active_job 'running' forever, and
    // Celery can't tell a dead task from a queued one). Trust started_at: a fire
    // finishes in minutes, so a 'running' flag older than the staleness window is
    // dead — clear it instead of reconnecting to a stream that never terminates.
    var STALE_JOB_MS = 15 * 60 * 1000;
    React.useEffect(function () {
        var active = instance.state && instance.state.active_job;
        if (!(active && active.status === 'running' && active.job_id)) return;
        var startedMs = active.started_at ? Date.parse(active.started_at) : NaN;
        var age = isNaN(startedMs) ? Infinity : (Date.now() - startedMs);
        if (age > STALE_JOB_MS) { setJobError('The previous generation didn’t finish. Re-run any incomplete opportunity below.'); return; }
        setIsRunning(true);
        setProgress({ status: 'running', message: 'Reconnecting to the running job…' });
        attachStream(active.job_id);
    }, []);

    // Per-opp recovery: (re-)run a single opportunity and merge it into this
    // program run's generation. Explicit, opp-scoped — never re-fires the whole
    // program run.
    function regenerateOpp(oppId) {
        if (isRunning || busyOpp || isCompleted) return;
        setBusyOpp(oppId); setJobError(null);
        actions.startJob(instance.id, {
            job_type: 'program_audit_generate',
            run_id: instance.id,
            opportunity_id: instance.opportunity_id,
            program_id: instance.program_id,
            only_opportunity_id: oppId,
            window_start: runState.window_start || startDate,
            window_end: runState.window_end || endDate,
            pass_threshold: runState.pass_threshold != null ? runState.pass_threshold : passThreshold,
            deliver_unit_types: runState.deliver_unit_types || deliverUnitTypes,
            visit_statuses: runState.visit_statuses || visitStatuses
        }).then(function (resp) {
            if (!resp || !resp.success || !resp.task_id) {
                setBusyOpp(null); setJobError((resp && resp.error) || ('Failed to start re-run for opp #' + oppId)); return;
            }
            actions.streamJobProgress(
                resp.task_id,
                function () {}, applyItem, // the dispatched entry (with task_id) streams in
                function (results) {
                    setBusyOpp(null);
                    var gen = (results && results.generation) || {};
                    Object.keys(gen).forEach(function (k) { applyItem(gen[k]); });
                },
                function (err) { setBusyOpp(null); setJobError('Opp #' + oppId + ' re-run failed: ' + (err || '')); },
                function () { setBusyOpp(null); },
                instance.id
            );
        }).catch(function () { setBusyOpp(null); setJobError('Re-run failed to start for opp #' + oppId); });
    }

    function handleGenerate() {
        if (!startDate || !endDate || isRunning || isCompleted) return;
        setIsRunning(true); setJobError(null);
        setProgress({ status: 'starting', message: 'Submitting to the server…' });
        actions.startJob(instance.id, {
            job_type: 'program_audit_generate',
            run_id: instance.id,
            opportunity_id: instance.opportunity_id,
            program_id: instance.program_id,
            window_start: startDate,
            window_end: endDate,
            pass_threshold: passThreshold,
            deliver_unit_types: deliverUnitTypes,
            visit_statuses: visitStatuses,
        }).then(function (resp) {
            if (!resp || !resp.success || !resp.task_id) {
                setIsRunning(false); setJobError((resp && resp.error) || 'Failed to start generation'); return;
            }
            setProgress({ status: 'running', message: 'Starting…' });
            attachStream(resp.task_id);
        }).catch(function () {
            setIsRunning(false); setJobError('Generation job failed to start');
        });
    }

    function markComplete() {
        if (!view || !view.complete || isCompleted) return;
        view.complete({ confirm: 'Mark the program week complete? Every org must have finished all of its audits; the program week will be frozen as a snapshot.' });
    }

    // Program-level completion readiness: we track sessions_created per opp, but
    // per-opp completion isn't in state, so the authoritative gate is the server
    // build_snapshot (it raises with a helpful message if any audit is open).
    var completion = runState.per_opp_completion || {};
    var openTotal = 0, haveCompletionData = false;
    Object.keys(completion).forEach(function (k) {
        haveCompletionData = true; openTotal += (completion[k].open_audits || 0);
    });
    var readyToComplete = haveCompletionData ? openTotal === 0 : true;

    function fmtDate(iso) {
        if (!iso) return '—';
        try {
            var d = new Date(iso);
            var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            return months[d.getUTCMonth()] + ' ' + d.getUTCDate();
        } catch (e) { return iso; }
    }

    function pill(text, color) {
        var palette = {
            green: 'bg-green-100 text-green-800', yellow: 'bg-yellow-100 text-yellow-800',
            gray: 'bg-gray-100 text-gray-700', indigo: 'bg-indigo-100 text-indigo-800',
            red: 'bg-red-100 text-red-800'
        };
        return React.createElement('span', {
            className: 'inline-block px-2 py-0.5 rounded-full text-xs font-medium ' + (palette[color] || palette.gray)
        }, text);
    }

    var datePresets = [
        { id: 'last_week', label: 'Last Week' },
        { id: 'last_7_days', label: 'Last 7 Days' },
        { id: 'last_14_days', label: 'Last 14 Days' },
        { id: 'last_30_days', label: 'Last 30 Days' },
        { id: 'last_month', label: 'Last Month' },
        { id: 'custom', label: 'Custom' }
    ];

    // Expand a row to show the opp's per-FLW audit breakdown inline (the same
    // panel the opp-level run page shows). Lazy-fetch that opp run's sessions the
    // first time it's opened, via the shared LabsAudit.fetchSessions helper.
    function toggleExpand(oppId, oppRunId) {
        var key = String(oppId);
        var willOpen = !expandedOpps[key];
        setExpandedOpps(function (m) { var n = Object.assign({}, m); n[key] = willOpen; return n; });
        if (willOpen && oppRunId && oppSessions[key] === undefined && !oppSessionsLoading[key] && window.LabsAudit) {
            setOppSessionsLoading(function (m) { var n = Object.assign({}, m); n[key] = true; return n; });
            window.LabsAudit.fetchSessions(oppRunId, [oppId]).then(function (rows) {
                setOppSessions(function (m) { var n = Object.assign({}, m); n[key] = rows; return n; });
                setOppSessionsLoading(function (m) { var n = Object.assign({}, m); n[key] = false; return n; });
            }).catch(function () {
                setOppSessions(function (m) { var n = Object.assign({}, m); n[key] = []; return n; });
                setOppSessionsLoading(function (m) { var n = Object.assign({}, m); n[key] = false; return n; });
            });
        }
    }

    // ── Per-opp generation status rows ────────────────────────────────────────
    // One question per opp: is this week's audit ready? Show a readiness state +
    // the real audit count, and let a failed/empty (or not-yet-run) opp be
    // (re-)run individually for recovery. Each row also expands in place to the
    // opp's full per-FLW audit breakdown (the shared LabsAudit primitive).
    function statusRow(source) {
        var oppId = source.opportunity_id;
        var gen = mergedGen[String(oppId)];
        var comp = completion[String(oppId)];
        var prog = oppProgress[String(oppId)] || gen; // live poll of this opp's own job
        var runLink = (gen && gen.run_id != null)
            ? React.createElement('a', {
                href: '/labs/workflow/' + source.workflow_definition_id + '/run/?run_id=' + gen.run_id + '&opportunity_id=' + oppId,
                className: 'text-indigo-600 underline text-xs',
                target: '_blank'
            }, 'open run ↗')
            : null;

        // Real audit count: prefer the server completion rollup, else the count
        // the fire recorded. A generated run with zero audits = it didn't finish.
        var count = gen ? ((comp && comp.total_audits != null) ? comp.total_audits : (gen.session_count || 0)) : 0;
        // "running" arrives via the live stream (this opp's batch is executing);
        // busyOpp is a per-opp re-run in flight.
        var streamedRunning = !!gen && gen.status === 'running';
        var isBusy = busyOpp === oppId || streamedRunning;
        var isCancelled = !!gen && gen.status === 'cancelled';
        var isFailed = !!gen && !isBusy && !isCancelled && (gen.status === 'failed' || (gen.run_id != null && count === 0));
        var isComplete = !!comp && comp.open_audits === 0 && comp.total_audits > 0;

        var stateLabel, statePill;
        if (isBusy) {
            // Show this opp's own audit-creation message ("Creating audit 3/8…")
            // relayed on the program stream, else a neutral running label.
            stateLabel = (prog && prog.message) ? stepLabel(prog.message) : (streamedRunning ? 'Step 1 of 2 · Creating audits…' : 'Re-running…');
            statePill = pill('● running', 'indigo');
        }
        else if (!gen) { stateLabel = 'Not generated'; statePill = pill('pending', 'gray'); }
        else if (isCancelled) { stateLabel = 'Cancelled' + (count ? ' · ' + count + ' audit(s) so far' : ''); statePill = pill('● cancelled', 'gray'); }
        else if (isFailed) { stateLabel = 'Didn’t finish — no audits created'; statePill = pill('● failed', 'red'); }
        else if (isComplete) { stateLabel = count + ' audit(s) · all complete'; statePill = pill('✓ complete', 'green'); }
        else { stateLabel = count + ' audit(s)' + (comp ? ' · ' + (comp.open_audits || 0) + ' open' : ''); statePill = pill('● ready', 'indigo'); }

        // Recover individually when an opp didn't finish (failed / cancelled /
        // empty) or was never run. Never a whole-program re-fire.
        var canRerun = !isCompleted && !isRunning && !isBusy && (!gen || isFailed || isCancelled);
        var rerunBtn = canRerun
            ? React.createElement('button', {
                onClick: function () { regenerateOpp(oppId); },
                className: 'text-xs px-3 py-1 rounded border border-blue-300 text-blue-700 hover:bg-blue-50'
            }, gen ? 'Re-run' : 'Run')
            : null;

        // Expand-in-place: a chevron reveals this opp's per-FLW audit breakdown
        // below the row (only once there's a run to show).
        var canExpand = !!(gen && gen.run_id != null);
        var isExpanded = !!expandedOpps[String(oppId)];
        var chevron = canExpand
            ? React.createElement('button', {
                onClick: function () { toggleExpand(oppId, gen.run_id); },
                title: isExpanded ? 'Collapse audit results' : 'Show audit results',
                style: { border: 'none', background: 'transparent', cursor: 'pointer', color: '#6b7280', marginRight: 6, fontSize: 12, padding: 4, flexShrink: 0 }
            }, React.createElement('i', { className: 'fa-solid ' + (isExpanded ? 'fa-chevron-down' : 'fa-chevron-right') }))
            : React.createElement('span', { style: { display: 'inline-block', width: 22, flexShrink: 0 } });

        var row = React.createElement('div', {
            style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', border: '1px solid #e5e7eb', borderRadius: isExpanded ? '8px 8px 0 0' : 8, background: 'white' }
        },
            React.createElement('div', { style: { display: 'flex', alignItems: 'center', flex: 1, minWidth: 0, paddingRight: 12 } },
                chevron,
                React.createElement('div', { style: { flex: 1, minWidth: 0 } },
                    React.createElement('div', { style: { fontWeight: 600, color: '#111827', fontSize: 13 } }, 'Opp #' + oppId),
                    React.createElement('div', { style: { fontSize: 11, color: '#6b7280', marginTop: 2 } }, stateLabel),
                    // This opp's live audit-creation progress bar (same signal the
                    // per-opp workflow page shows).
                    (isBusy && prog && prog.total > 0)
                        ? React.createElement('div', { style: { marginTop: 6, maxWidth: 380 } },
                            React.createElement('div', { style: { height: 5, background: '#e5e7eb', borderRadius: 999 } },
                                React.createElement('div', { style: { height: 5, borderRadius: 999, background: '#2563eb', width: Math.round((prog.processed || 0) / prog.total * 100) + '%', transition: 'width .3s' } })),
                            React.createElement('div', { style: { fontSize: 10, color: '#9ca3af', marginTop: 3 } }, (prog.processed || 0) + ' / ' + prog.total))
                        : null
                )
            ),
            React.createElement('div', { style: { display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 } },
                statePill, rerunBtn, runLink
            )
        );

        // The inline breakdown — the SAME renderer the opp-level run page uses.
        var expandedPanel = (canExpand && isExpanded)
            ? React.createElement('div', { style: { border: '1px solid #e5e7eb', borderTop: 'none', borderRadius: '0 0 8px 8px', background: 'white', padding: 12 } },
                window.LabsAudit
                    ? window.LabsAudit.renderFlwBreakdown(React, {
                        sessions: oppSessions[String(oppId)] || [],
                        oppNames: {},
                        workflowRunId: gen.run_id,
                        loading: !!oppSessionsLoading[String(oppId)],
                        title: null
                    })
                    : React.createElement('div', { style: { fontSize: 12, color: '#6b7280' } }, 'Audit breakdown unavailable.'))
            : null;

        return React.createElement('div', { key: oppId, style: { marginBottom: 8 } }, row, expandedPanel);
    }

    return React.createElement('div', { style: { padding: 16, background: '#f7f8fb', minHeight: '100vh' } },
        // Header
        React.createElement('div', { style: { background: 'white', borderRadius: 10, border: '1px solid #e5e7eb', padding: 16, marginBottom: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
            React.createElement('div', null,
                React.createElement('div', { style: { fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#6b7280' } }, 'Program Audit Creator · ' + (definition.name || '')),
                React.createElement('div', { style: { fontSize: 18, fontWeight: 600, color: '#111827', marginTop: 2 } },
                    sources.length + ' opportunit' + (sources.length === 1 ? 'y' : 'ies') +
                    (runState.window_start ? ' · ' + fmtDate(runState.window_start) + ' – ' + fmtDate(runState.window_end) : ''))
            ),
            React.createElement('div', { style: { display: 'flex', gap: 10, alignItems: 'center' } },
                reportDefId
                    ? React.createElement('a', { href: '/labs/workflow/' + reportDefId + '/', className: 'text-indigo-600 underline text-sm', target: '_blank' }, 'Program report ↗')
                    : null,
                isCompleted ? pill('📌 Snapshot', 'indigo') : pill('● Live', 'gray')
            )
        ),
        // Completion banner
        isCompleted
            ? React.createElement('div', { style: { background: '#f3f4f6', borderLeft: '4px solid #9ca3af', padding: 12, borderRadius: 6, marginBottom: 14, fontSize: 13, color: '#374151' } },
                React.createElement('strong', null, 'This program week is completed.'),
                view.asOf ? ' Snapshot from ' + new Date(view.asOf).toLocaleString() + '.' : '')
            : null,
        // Window picker + generate — shown ONLY before this program run is fired.
        // A program run is fired once; after that, recovery is per-opp below.
        (isCompleted || hasFired) ? null : React.createElement('div', { style: { background: 'white', borderRadius: 10, border: '1px solid #e5e7eb', padding: 16, marginBottom: 14 } },
            React.createElement('div', { style: { fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 10 } }, 'Audit window'),
            React.createElement('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 } },
                datePresets.map(function (p) {
                    return React.createElement('button', {
                        key: p.id, onClick: function () { applyPreset(p.id); },
                        className: 'px-3 py-1.5 text-sm rounded-full border ' +
                            (datePreset === p.id ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400')
                    }, p.label);
                })
            ),
            React.createElement('div', { style: { display: 'flex', gap: 16, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 16 } },
                React.createElement('div', null,
                    React.createElement('label', { style: { display: 'block', fontSize: 11, color: '#6b7280', marginBottom: 4 } }, 'Start'),
                    React.createElement('input', { type: 'date', value: startDate, onChange: function (e) { setStartDate(e.target.value); setDatePreset('custom'); }, className: 'border border-gray-300 rounded px-3 py-2 text-sm' })
                ),
                React.createElement('div', null,
                    React.createElement('label', { style: { display: 'block', fontSize: 11, color: '#6b7280', marginBottom: 4 } }, 'End'),
                    React.createElement('input', { type: 'date', value: endDate, onChange: function (e) { setEndDate(e.target.value); setDatePreset('custom'); }, className: 'border border-gray-300 rounded px-3 py-2 text-sm' })
                )
            ),
            // Audit-quality filters (PR #884, ported from the Django creation wizard):
            // Pass Threshold slider, Deliver Unit Type + Visit Type checkbox lists.
            React.createElement('div', { style: { borderTop: '1px solid #e5e7eb', paddingTop: 14, marginBottom: 16 } },
                React.createElement('div', { style: { fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 10 } }, 'Filters'),
                React.createElement('div', { style: { marginBottom: 16, maxWidth: 420 } },
                    React.createElement('div', { style: { display: 'flex', justifyContent: 'space-between', marginBottom: 4 } },
                        React.createElement('label', { style: { fontSize: 12, color: '#374151' } }, 'Pass Threshold'),
                        React.createElement('span', { style: { fontSize: 13, fontWeight: 600, color: '#4f46e5' } }, passThreshold + '%')
                    ),
                    React.createElement('input', {
                        type: 'range', min: 75, max: 100, step: 1, value: passThreshold,
                        onChange: function (e) { setPassThreshold(parseInt(e.target.value, 10)); },
                        style: { width: '100%' }
                    }),
                    React.createElement('div', { style: { fontSize: 11, color: '#9ca3af', marginTop: 2 } },
                        'Minimum % of assessments that must pass for an audit to be marked "Pass". At 100%, a single failed assessment still fails the audit.')
                ),
                React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 } },
                    React.createElement('div', null,
                        React.createElement('label', { style: { display: 'block', fontSize: 12, color: '#374151', marginBottom: 6 } }, 'Deliver Unit Type'),
                        React.createElement('div', { style: { border: '1px solid #d1d5db', borderRadius: 6, padding: 10, maxHeight: 140, overflowY: 'auto' } },
                            deliverUnitTypesLoading
                                ? React.createElement('div', { style: { fontSize: 12, color: '#9ca3af' } }, 'Loading delivery unit types…')
                                : (availableDeliverUnitTypes.length === 0
                                    ? React.createElement('div', { style: { fontSize: 12, color: '#9ca3af' } }, 'No delivery unit types found.')
                                    : availableDeliverUnitTypes.map(function (duType) {
                                        return React.createElement('label', { key: duType, style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#374151', marginBottom: 4 } },
                                            React.createElement('input', {
                                                type: 'checkbox',
                                                checked: deliverUnitTypes.indexOf(duType) !== -1,
                                                onChange: function (e) {
                                                    setDeliverUnitTypes(function (prev) {
                                                        return e.target.checked ? prev.concat([duType]) : prev.filter(function (t) { return t !== duType; });
                                                    });
                                                }
                                            }),
                                            duType
                                        );
                                    }))
                        )
                    ),
                    React.createElement('div', null,
                        React.createElement('label', { style: { display: 'block', fontSize: 12, color: '#374151', marginBottom: 6 } }, 'Visit Type'),
                        React.createElement('div', { style: { border: '1px solid #d1d5db', borderRadius: 6, padding: 10 } },
                            VISIT_STATUS_OPTIONS.map(function (vs) {
                                return React.createElement('label', { key: vs.value, style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#374151', marginBottom: 4 } },
                                    React.createElement('input', {
                                        type: 'checkbox',
                                        checked: visitStatuses.indexOf(vs.value) !== -1,
                                        onChange: function (e) {
                                            setVisitStatuses(function (prev) {
                                                return e.target.checked ? prev.concat([vs.value]) : prev.filter(function (v) { return v !== vs.value; });
                                            });
                                        }
                                    }),
                                    vs.label
                                );
                            })
                        )
                    )
                ),
                React.createElement('div', { style: { fontSize: 11, color: '#9ca3af', marginTop: 8 } }, 'Leave a filter empty to include all.')
            ),
            React.createElement('button', {
                onClick: handleGenerate,
                disabled: !startDate || !endDate || isRunning || sources.length === 0,
                className: 'inline-flex items-center px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 font-medium'
            }, isRunning ? 'Generating…' : ('Generate audit runs for all ' + sources.length + ' opportunit' + (sources.length === 1 ? 'y' : 'ies'))),
            progress && isRunning
                ? React.createElement('div', { style: { marginTop: 14, background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 8, padding: 12, fontSize: 13, color: '#1e40af' } },
                    (progress.message || 'Working…') + (progress.total > 0 ? ' (' + (progress.processed || 0) + '/' + progress.total + ')' : ''))
                : null,
            jobError
                ? React.createElement('div', { style: { marginTop: 14, background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: 12, fontSize: 13, color: '#b91c1c' } }, jobError)
                : null
        ),
        // Fired summary — shown while/after the program run spawns its per-opp runs.
        (!isCompleted && hasFired) ? React.createElement('div', { style: { background: 'white', borderRadius: 10, border: '1px solid #e5e7eb', padding: 16, marginBottom: 14 } },
            React.createElement('div', { style: { fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 6 } }, 'Audit window'),
            React.createElement('div', { style: { fontSize: 14, color: '#111827' } },
                (runState.window_start || startDate ? fmtDate(runState.window_start || startDate) + ' – ' + fmtDate(runState.window_end || endDate) : 'Window set') +
                (anyOppRunning
                    ? ' · generating (' + oppsDone + '/' + sources.length + ' done)'
                    : ' · fired for ' + sources.length + ' opportunit' + (sources.length === 1 ? 'y' : 'ies'))),
            React.createElement('div', { style: { fontSize: 12, color: '#6b7280', marginTop: 6 } },
                anyOppRunning
                    ? 'Generating audits per opportunity — each row streams its own progress below.'
                    : 'This program run has been fired. To recover an opportunity that didn’t finish, use its Re-run button below.'),
            // Cancel the running program job.
            anyOppRunning
                ? React.createElement('div', { style: { marginTop: 12 } },
                    React.createElement('button', {
                        onClick: cancelRun,
                        className: 'text-xs px-3 py-1.5 rounded border border-red-300 text-red-700 hover:bg-red-50'
                    }, 'Cancel'))
                : null,
            jobError
                ? React.createElement('div', { style: { marginTop: 12, background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: 12, fontSize: 13, color: '#b91c1c' } }, jobError)
                : null
        ) : null,
        // Per-opp generation status
        React.createElement('div', { style: { background: 'white', borderRadius: 10, border: '1px solid #e5e7eb', padding: 16, marginBottom: 14 } },
            React.createElement('div', { style: { fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 10 } }, 'Per-opportunity generation'),
            sources.length === 0
                ? React.createElement('div', { style: { fontSize: 13, color: '#9ca3af' } }, 'No per-opp creator instances configured yet.')
                : sources.map(statusRow)
        ),
        // Completion CTA
        React.createElement('div', { style: { background: 'white', borderRadius: 10, border: '1px solid #e5e7eb', padding: 16 } },
            isCompleted
                ? React.createElement('div', { style: { fontSize: 13, color: '#065f46' } }, 'Program week frozen' + (view.asOf ? ' · ' + new Date(view.asOf).toLocaleString() : ''))
                : React.createElement('div', null,
                    React.createElement('button', {
                        onClick: markComplete,
                        disabled: !readyToComplete,
                        className: 'inline-flex items-center px-6 py-3 rounded-lg font-medium ' + (readyToComplete ? 'bg-green-600 text-white hover:bg-green-700' : 'bg-gray-300 text-gray-500 cursor-not-allowed')
                    }, 'Mark Program Week Complete'),
                    React.createElement('div', { style: { marginTop: 8, fontSize: 12, color: '#6b7280' } },
                        haveCompletionData
                            ? (readyToComplete ? 'All orgs have finished — ready to complete the program week.' : (openTotal + ' audit(s) still open across the program — every org must finish first.'))
                            : 'Every org must finish all of its audits before the program week can be completed.')
                )
        )
    );
}"""


PIPELINE_SCHEMA = None


TEMPLATE = {
    "key": "program_audit_creator",
    "name": "Program Audit Creator",
    "description": DEFINITION["description"],
    "icon": "fa-sitemap",
    "color": "indigo",
    "multi_opp": True,
    "supports_saved_runs": True,
    "supports_default_run": True,
    # NB: no `snapshot_inputs` — the Python build_snapshot hook governs
    # completion (resolve_snapshot_contract → source="template_hook").
    "snapshot_schema": {
        "version": 1,
        "keys": {
            "state.generation": "Per-opp fan-out record {opp_id: {run_id, sessions_created, created, order}}",
            "state.window_start": "Program week start (ISO)",
            "state.window_end": "Program week end (ISO)",
        },
    },
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
