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
    *, definition, run_id, access_token, request=None, window=None, progress_callback=None, only_opportunity_id=None
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

    Returns ``{"per_opp": {opp_id: result}, "generation", "window_start",
    "window_end"}``.
    """
    from connect_labs.workflow.templates import run_default_for_definition

    window_start, window_end = window if window else (None, None)
    all_sources = _resolve_instances(definition)
    order_of = {s["opportunity_id"]: i for i, s in enumerate(all_sources)}

    # Load the program run's existing generation so a per-opp re-run MERGES into
    # it (rather than clobbering) and so a full fire is single-shot.
    pwda = _program_run_dao(definition, access_token)
    try:
        prun = pwda.get_run(run_id)
        existing = ((getattr(prun, "data", None) or {}).get("state", {}) or {}).get("generation") or {} if prun else {}
    finally:
        pwda.close()

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
        # Stream a per-opp status through the standard job progress channel so the
        # render's onItemResult flips that row live (pending → running → ready).
        if progress_callback:
            progress_callback(msg, processed=processed, total=total, item_result=item_result)

    per_opp = {}
    generation = dict(existing)
    for idx, source in enumerate(sources):
        opp_id = source["opportunity_id"]
        def_id = source["workflow_definition_id"]
        order = order_of.get(opp_id, idx)

        # Mark this opp running BEFORE its (synchronous) batch executes, so the row
        # shows "running" for the duration rather than sitting on "pending".
        # ``id`` keys the framework's per-item run-state write by opportunity.
        _emit(
            f"Generating audits for opportunity #{opp_id}…",
            idx,
            {"id": opp_id, "opportunity_id": opp_id, "status": "running", "order": order},
        )

        # Opp-scoped read of the per-opp creator definition (Global Constraint).
        wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opp_id)
        try:
            creator_def = wda.get_definition(def_id)
        finally:
            wda.close()
        if creator_def is None:
            _emit(
                f"Opportunity #{opp_id}: creator not found",
                idx + 1,
                {"id": opp_id, "opportunity_id": opp_id, "status": "failed", "order": order},
            )
            continue

        result = run_default_for_definition(creator_def, access_token=access_token, request=request, window=window)
        per_opp[opp_id] = result
        entry = {
            "opportunity_id": opp_id,
            "workflow_definition_id": def_id,
            "run_id": result.get("run_id"),
            "session_count": result.get("sessions_created", 0),
            "status": result.get("status", "ready"),
            "order": order,
        }
        generation[str(opp_id)] = entry

        # Persist the accumulating fan-out record onto the PROGRAM run, scoped
        # to the PROGRAM run's owner (program-scoped when program-owned, else
        # the creator's owning opp) so the run tracks it live.
        pwda = _program_run_dao(definition, access_token)
        try:
            pwda.update_run_state(
                run_id,
                {
                    "generation": dict(generation),
                    "window_start": window_start,
                    "window_end": window_end,
                },
            )
        finally:
            pwda.close()

        # Stream the finished per-opp row (with its real run_id + audit count).
        _emit(f"Opportunity #{opp_id}: {entry['status']}", idx + 1, dict(entry, id=opp_id))

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
    var [isRunning, setIsRunning] = React.useState(false);
    var [busyOpp, setBusyOpp] = React.useState(null);
    var [progress, setProgress] = React.useState(null);
    var [jobError, setJobError] = React.useState(null);
    // Live per-opp status streamed from the job via onItemResult (keyed by
    // opportunity_id) — merged over the persisted generation record so each row
    // updates in place (pending → running → ready/failed) with no page reload.
    var [liveItems, setLiveItems] = React.useState({});
    var cleanupRef = React.useRef(null);

    var isCompleted = view && view.isCompleted;

    var mergedGen = Object.assign({}, generation, liveItems);
    // A program run is fired ONCE: once it has spawned (or is spawning) its
    // per-opp runs, the Generate button is retired and recovery happens per-opp.
    var hasFired = Object.keys(mergedGen).length > 0 || isRunning;

    // Merge one streamed per-opp status update into the live map.
    function applyItem(item) {
        if (!item || item.opportunity_id == null) return;
        setLiveItems(function (m) {
            var n = Object.assign({}, m);
            var k = String(item.opportunity_id);
            n[k] = Object.assign({}, n[k], item);
            return n;
        });
    }

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

    function attachStream(taskId) {
        var cleanup = actions.streamJobProgress(
            taskId,
            function (p) { setProgress(p); },
            applyItem, // per-opp row updates stream in live
            function () {
                // Rows already reflect the final per-opp status via the streamed
                // item_results — no page reload needed.
                setIsRunning(false);
                setProgress({ status: 'completed' });
                onUpdateState({ active_job: { job_id: taskId, status: 'completed' } }).catch(function () {});
            },
            function (err) {
                setIsRunning(false); setJobError(err || 'Generation failed'); setProgress(null);
                onUpdateState({ active_job: { job_id: taskId, status: 'failed' } }).catch(function () {});
            },
            function () { setIsRunning(false); setProgress({ status: 'cancelled' }); },
            instance.id // run_id — lets the server unstick a reconnect to a dead job
        );
        cleanupRef.current = cleanup;
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
            window_end: runState.window_end || endDate
        }).then(function (resp) {
            if (!resp || !resp.success || !resp.task_id) {
                setBusyOpp(null); setJobError((resp && resp.error) || ('Failed to start re-run for opp #' + oppId)); return;
            }
            actions.streamJobProgress(
                resp.task_id,
                function () {}, applyItem, // the row updates live from the streamed status
                function () { setBusyOpp(null); },
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

    // ── Per-opp generation status rows ────────────────────────────────────────
    // One question per opp: is this week's audit ready? Show a readiness state +
    // the real audit count, and let a failed/empty (or not-yet-run) opp be
    // (re-)run individually for recovery.
    function statusRow(source) {
        var oppId = source.opportunity_id;
        var gen = mergedGen[String(oppId)];
        var comp = completion[String(oppId)];
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
        var isFailed = !!gen && !isBusy && (gen.status === 'failed' || (gen.run_id != null && count === 0));
        var isComplete = !!comp && comp.open_audits === 0 && comp.total_audits > 0;

        var stateLabel, statePill;
        if (isBusy) { stateLabel = streamedRunning ? 'Generating audits…' : 'Re-running…'; statePill = pill('● running', 'indigo'); }
        else if (!gen) { stateLabel = 'Not generated'; statePill = pill('pending', 'gray'); }
        else if (isFailed) { stateLabel = 'Didn’t finish — no audits created'; statePill = pill('● failed', 'red'); }
        else if (isComplete) { stateLabel = count + ' audit(s) · all complete'; statePill = pill('✓ complete', 'green'); }
        else { stateLabel = count + ' audit(s)' + (comp ? ' · ' + (comp.open_audits || 0) + ' open' : ''); statePill = pill('● ready', 'indigo'); }

        // Recover individually when an opp didn't produce audits (failed/empty)
        // or was never run (fan-out interrupted). Never a whole-program re-fire.
        var canRerun = !isCompleted && !isRunning && !isBusy && (!gen || isFailed);
        var rerunBtn = canRerun
            ? React.createElement('button', {
                onClick: function () { regenerateOpp(oppId); },
                className: 'text-xs px-3 py-1 rounded border border-blue-300 text-blue-700 hover:bg-blue-50'
            }, gen ? 'Re-run' : 'Run')
            : null;

        return React.createElement('div', {
            key: oppId,
            style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', border: '1px solid #e5e7eb', borderRadius: 8, marginBottom: 8, background: 'white' }
        },
            React.createElement('div', null,
                React.createElement('div', { style: { fontWeight: 600, color: '#111827', fontSize: 13 } }, 'Opp #' + oppId),
                React.createElement('div', { style: { fontSize: 11, color: '#6b7280', marginTop: 2 } }, stateLabel)
            ),
            React.createElement('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
                statePill, rerunBtn, runLink
            )
        );
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
            React.createElement('div', { style: { display: 'flex', gap: 16, alignItems: 'flex-end', flexWrap: 'wrap' } },
                React.createElement('div', null,
                    React.createElement('label', { style: { display: 'block', fontSize: 11, color: '#6b7280', marginBottom: 4 } }, 'Start'),
                    React.createElement('input', { type: 'date', value: startDate, onChange: function (e) { setStartDate(e.target.value); setDatePreset('custom'); }, className: 'border border-gray-300 rounded px-3 py-2 text-sm' })
                ),
                React.createElement('div', null,
                    React.createElement('label', { style: { display: 'block', fontSize: 11, color: '#6b7280', marginBottom: 4 } }, 'End'),
                    React.createElement('input', { type: 'date', value: endDate, onChange: function (e) { setEndDate(e.target.value); setDatePreset('custom'); }, className: 'border border-gray-300 rounded px-3 py-2 text-sm' })
                ),
                React.createElement('button', {
                    onClick: handleGenerate,
                    disabled: !startDate || !endDate || isRunning || sources.length === 0,
                    className: 'inline-flex items-center px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 font-medium'
                }, isRunning ? 'Generating…' : ('Generate audit runs for all ' + sources.length + ' opportunit' + (sources.length === 1 ? 'y' : 'ies')))
            ),
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
                (isRunning
                    ? ' · generating ' + (progress && progress.total ? '(' + (progress.processed || 0) + '/' + progress.total + ')' : '…')
                    : ' · fired for ' + sources.length + ' opportunit' + (sources.length === 1 ? 'y' : 'ies'))),
            React.createElement('div', { style: { fontSize: 12, color: '#6b7280', marginTop: 6 } },
                isRunning
                    ? 'Generating audits per opportunity — watch each row update below.'
                    : 'This program run has been fired. To recover an opportunity that didn’t finish, use its Re-run button below.'),
            (isRunning && progress && progress.total > 0)
                ? React.createElement('div', { style: { marginTop: 10, height: 6, background: '#e5e7eb', borderRadius: 999 } },
                    React.createElement('div', { style: { height: 6, borderRadius: 999, background: '#2563eb', width: Math.round((progress.processed || 0) / progress.total * 100) + '%', transition: 'width .3s' } }))
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
