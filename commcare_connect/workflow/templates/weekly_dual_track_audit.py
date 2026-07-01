"""Weekly Dual-Track Image Audit — multi-opp, action-shaped creator.

Each weekly run creates, per FLW, two audits per opportunity:
  - Track A ("muac"): census of the pinned MUAC image type(s), 100%, with the
    muac_overzoom AI agent auto-tagging fails.
  - Track B ("rest"): the remaining pinned image types, sampled (default 10%),
    human-reviewed.

The per-opp image paths and track config live on the workflow DEFINITION
(instance config); the batch window lives in run state. See
docs/superpowers/specs/2026-06-30-audit-program-report-design.md.
"""


def _image_audits(paths, reviewer):
    """One image_audits entry per pinned image path. The track's reviewer (or no
    reviewer) is attached to each — the PR #771 per-image-type model. See
    commcare_connect/audit/ai_review_config.build_review_config."""
    reviewers = [reviewer] if reviewer else []
    return [{"image_path": p, "reviewers": list(reviewers)} for p in (paths or [])]


def build_track_audit_calls(
    *,
    opportunity_ids,
    opp_names,
    per_opp,
    track_a,
    track_b,
    window_start,
    window_end,
    username,
    workflow_run_id,
):
    """Build the per-opp, per-track run_audit_creation kwargs for one weekly batch.

    Returns a flat list of kwargs dicts. A track is skipped when its per-opp
    image-path list is empty. JSON-coerced string keys are used to look up
    per_opp / opp_names, so callers may pass either int or str opp ids.
    """
    calls = []
    for opp_id in opportunity_ids:
        key = str(opp_id)
        cfg = per_opp.get(key, {})
        name = opp_names.get(key, "")
        for track, paths in (
            (track_a, cfg.get("muac_image_paths")),
            (track_b, cfg.get("rest_image_paths")),
        ):
            image_audits = _image_audits(paths, track.get("reviewer"))
            if not image_audits:
                continue
            calls.append(
                {
                    "username": username,
                    "opportunities": [{"id": opp_id, "name": name}],
                    "criteria": {
                        "audit_type": "date_range",
                        "start_date": window_start,
                        "end_date": window_end,
                        "sample_percentage": track["sample_percentage"],
                        "granularity": "per_flw",
                        "tag": track["tag"],
                        # related_fields is derived by run_audit_creation from image_audits.
                    },
                    "workflow_run_id": workflow_run_id,
                    "image_audits": image_audits,
                    "context_fields": None,
                }
            )
    return calls


DEFINITION = {
    "name": "Weekly Dual-Track Image Audit",
    "description": "Per FLW, per week: a MUAC-census+AI audit and a sampled-remainder audit, across all selected opportunities.",
    "version": 1,
    "templateType": "weekly_dual_track_audit",
    "statuses": [
        {"id": "config", "label": "Configuring", "color": "gray"},
        {"id": "creating", "label": "Creating Audits", "color": "blue"},
        {"id": "created", "label": "Audits Created", "color": "green"},
        {"id": "failed", "label": "Failed", "color": "red"},
    ],
    "config": {
        "audit_batch": {
            # PR #771 per-image-type model: each track's reviewer rides into image_audits.
            "track_a": {
                "tag": "muac",
                "sample_percentage": 100,
                "reviewer": {
                    "agent_id": "muac_overzoom",
                    "auto_apply_actions": ["fail_overzoomed"],
                },
            },
            "track_b": {"tag": "rest", "sample_percentage": 10, "reviewer": None},
            "per_opp": {},  # { "<opp_id>": {"muac_image_paths": [...], "rest_image_paths": [...]} }
            "opp_names": {},  # { "<opp_id>": "Opp display name" }
        }
    },
    "pipeline_sources": [],
}

RENDER_CODE = r"""function WorkflowUI({ definition, instance, actions, onUpdateState }) {

    // ── Config from the DEFINITION (pinned at create time, read-only here) ────
    const batch = (definition.config && definition.config.audit_batch) || {};
    const perOpp = batch.per_opp || {};
    const oppNames = batch.opp_names || {};
    const trackA = batch.track_a || {};
    const trackB = batch.track_b || {};
    const oppIds = (instance.opportunity_ids && instance.opportunity_ids.length)
        ? instance.opportunity_ids
        : (instance.opportunity_id ? [instance.opportunity_id] : []);

    // ── Date-window picker (mirrors bulk_image_audit) ─────────────────────────
    const [datePreset, setDatePreset] = React.useState(instance.state?.date_preset || 'last_week');
    const [startDate, setStartDate] = React.useState(instance.state?.window_start || '');
    const [endDate, setEndDate] = React.useState(instance.state?.window_end || '');

    const calculateDateRange = (preset) => {
        const today = new Date(); today.setHours(0,0,0,0);
        let start, end;
        switch (preset) {
            case 'last_week': {
                const dow = today.getDay();
                const thisSun = new Date(today); thisSun.setDate(today.getDate() - dow);
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
    };

    const applyPreset = (preset) => {
        setDatePreset(preset);
        if (preset !== 'custom') {
            const range = calculateDateRange(preset);
            if (range) { setStartDate(range.start); setEndDate(range.end); }
        }
    };

    // Default the window to "last week" on first mount.
    React.useEffect(() => { if (!startDate && !endDate) applyPreset('last_week'); }, []);

    // ── Job execution state ───────────────────────────────────────────────────
    const [isRunning, setIsRunning] = React.useState(false);
    const [progress, setProgress] = React.useState(null);
    const [jobError, setJobError] = React.useState(null);
    const cleanupRef = React.useRef(null);
    React.useEffect(() => () => { if (cleanupRef.current) cleanupRef.current(); }, []);

    // ── Created sessions ──────────────────────────────────────────────────────
    const [sessions, setSessions] = React.useState([]);
    const [loadingSessions, setLoadingSessions] = React.useState(true);
    const refreshSessions = () => {
        if (!instance.id || !oppIds.length) { setLoadingSessions(false); return Promise.resolve([]); }
        // The sessions endpoint is scoped to ONE opportunity per request (the
        // labs API enforces opp scope), so fetch each opp in the run's set and
        // merge — otherwise only the primary opp's sessions would show even
        // though the batch created audits for every selected opportunity.
        return Promise.all(oppIds.map(opp =>
            fetch('/audit/api/workflow/' + instance.id + '/sessions/?opportunity_id=' + opp)
                .then(res => res.json())
                .then(data => (data.success && data.sessions) ? data.sessions : [])
                .catch(() => [])
        )).then(arrs => {
            const seen = {};
            const all = [];
            arrs.forEach(list => list.forEach(s => { if (!seen[s.id]) { seen[s.id] = true; all.push(s); } }));
            setSessions(all); setLoadingSessions(false); return all;
        }).catch(() => { setLoadingSessions(false); return []; });
    };
    React.useEffect(() => { refreshSessions(); }, [instance.id]);

    // Attach the SSE progress stream for a running job. Shared by the create
    // handler and the on-reload reconnect below.
    const attachStream = (taskId) => {
        const cleanup = actions.streamJobProgress(
            taskId,
            (p) => setProgress(p),
            null,
            async (results) => {
                setIsRunning(false);
                setProgress({ status: 'completed', ...results });
                onUpdateState({ active_job: { job_id: taskId, status: 'completed' } }).catch(() => {});
                await refreshSessions();
            },
            (err) => {
                setIsRunning(false); setJobError(err || 'Job failed'); setProgress(null);
                onUpdateState({ active_job: { job_id: taskId, status: 'failed' } }).catch(() => {});
            },
            () => { setIsRunning(false); setProgress({ status: 'cancelled' }); }
        );
        cleanupRef.current = cleanup;
    };

    // ── Reconnect to a still-running job after a page reload ───────────────────
    // The batch runs server-side (a Celery job) — leaving the page never stops
    // it. If we come back while it's still working, re-attach the progress
    // stream instead of showing a stale idle state.
    React.useEffect(() => {
        const active = instance.state?.active_job;
        if (active && active.status === 'running' && active.job_id) {
            setIsRunning(true);
            setProgress({ status: 'running', message: 'Reconnecting to the running job…' });
            attachStream(active.job_id);
        }
    }, []); // once on mount

    // ── Create handler ────────────────────────────────────────────────────────
    // 1) persist the window to run STATE (the server handler reads window from
    //    state and the opp set + config from the DEFINITION), 2) start the job,
    //    3) stream progress, 4) reload the created sessions on completion.
    const handleCreate = async () => {
        if (!startDate || !endDate || isRunning || instance.status === 'completed') return;
        setIsRunning(true); setJobError(null);
        setProgress({ status: 'starting', message: 'Submitting to the server…' });

        // No run-state write from the render: the window travels in the job
        // payload below and the server job persists it onto the run. A
        // session-scoped state write here can 404 when the opp picker has
        // drifted off the run's owning opp, surfacing a misleading
        // "Failed to update state" even though creation succeeds.

        let resp;
        try {
            resp = await actions.startJob(instance.id, {
                job_type: 'weekly_dual_track_audit_create',
                run_id: instance.id,
                opportunity_id: instance.opportunity_id,
                window_start: startDate,
                window_end: endDate,
            });
        } catch (e) {
            setIsRunning(false); setJobError('Failed to start job: ' + (e.message || e)); return;
        }
        if (!resp || !resp.success || !resp.task_id) {
            setIsRunning(false); setJobError((resp && resp.error) || 'Failed to start job'); return;
        }

        // The server job records active_job (with progress) on the run itself,
        // so a page reload reconnects — no separate state write needed here
        // (a redundant one races the server's write and can flake a 404).
        setProgress({ status: 'running', message: 'Starting…' });
        attachStream(resp.task_id);
    };

    // ── Group created sessions by opportunity_id then tag ─────────────────────
    // Group by opp → FLW → { muac, rest } so each field worker's two audits sit
    // together and their status/results are visible at a glance.
    const groupByOppFlw = () => {
        const byOpp = {};
        sessions.forEach(s => {
            const oid = s.opportunity_id != null ? String(s.opportunity_id) : 'unknown';
            const flw = s.flw_username || 'unknown';
            if (!byOpp[oid]) byOpp[oid] = { flws: {}, order: [] };
            if (!byOpp[oid].flws[flw]) {
                byOpp[oid].flws[flw] = { name: s.flw_display_name || s.flw_username || flw, muac: null, rest: null };
                byOpp[oid].order.push(flw);
            }
            if (s.tag === 'muac') byOpp[oid].flws[flw].muac = s;
            else if (s.tag === 'rest') byOpp[oid].flws[flw].rest = s;
        });
        return byOpp;
    };
    const grouped = groupByOppFlw();
    const statsOf = (s) => (s && s.assessment_stats) || {};
    const oppSummary = (oppData) => {
        var out = { flws: 0, muacTotal: 0, muacReviewed: 0, muacFlagged: 0, restTotal: 0, restReviewed: 0 };
        oppData.order.forEach(function (flw) {
            var r = oppData.flws[flw]; out.flws++;
            var m = statsOf(r.muac); out.muacTotal += (m.total || 0); out.muacReviewed += ((m.pass || 0) + (m.fail || 0)); out.muacFlagged += (m.ai_no_match || 0);
            var e = statsOf(r.rest); out.restTotal += (e.total || 0); out.restReviewed += ((e.pass || 0) + (e.fail || 0));
        });
        return out;
    };
    // One compact audit line: status + image count + pass/fail/pending + (MUAC) AI-flags.
    const auditLine = (label, s) => {
        if (!s) return React.createElement('div', { className: 'text-xs text-gray-400 pl-2' }, label + ': not created');
        var a = statsOf(s);
        var done = s.status === 'completed';
        return React.createElement('a', {
            href: bulkUrl(s),
            className: 'flex items-center gap-3 px-3 py-1.5 rounded bg-gray-50 hover:bg-blue-50 border border-gray-200 text-xs'
        },
            React.createElement('span', { className: 'font-semibold text-gray-700 w-12' }, label),
            React.createElement('span', {
                className: 'px-1.5 py-0.5 rounded ' + (done ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700')
            }, done ? 'Completed' : 'In progress'),
            React.createElement('span', { className: 'text-gray-500 w-16' }, (a.total || 0) + ' images'),
            React.createElement('span', { className: 'flex-1' },
                React.createElement('span', { className: 'text-green-600 font-medium' }, (a.pass || 0) + ' pass'),
                ' · ',
                React.createElement('span', { className: 'text-red-600 font-medium' }, (a.fail || 0) + ' fail'),
                ' · ',
                React.createElement('span', { className: 'text-gray-500' }, (a.pending || 0) + ' pending')
            ),
            label === 'MUAC'
                ? React.createElement('span', { className: (a.ai_no_match || 0) > 0 ? 'text-amber-600 font-medium' : 'text-gray-400' },
                    (a.ai_no_match || 0) + ' AI-flagged')
                : React.createElement('span', { className: 'text-gray-300' }, 'no AI'),
            React.createElement('i', { className: 'fa-solid fa-arrow-up-right-from-square text-blue-500' })
        );
    };

    const bulkUrl = (s) => {
        const params = new URLSearchParams();
        if (s.opportunity_id != null) params.set('opportunity_id', s.opportunity_id);
        if (instance.id) params.set('workflow_run_id', instance.id);
        return '/audit/' + s.id + '/bulk/?' + params.toString();
    };

    const datePresets = [
        { id: 'last_week', label: 'Last Week' },
        { id: 'last_7_days', label: 'Last 7 Days' },
        { id: 'last_14_days', label: 'Last 14 Days' },
        { id: 'last_30_days', label: 'Last 30 Days' },
        { id: 'last_month', label: 'Last Month' },
        { id: 'custom', label: 'Custom' },
    ];

    const pathPills = (paths, color) => (
        (paths && paths.length)
            ? paths.map(p => (
                <span key={p} className={'inline-block px-2 py-0.5 mr-1 mb-1 rounded text-xs font-mono ' + color}>{p}</span>
            ))
            : <span className="text-xs text-gray-400 italic">none pinned</span>
    );

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                <p className="text-gray-600 mt-1">{definition.description}</p>
            </div>

            {/* ── Date window ─────────────────────────────────────────────── */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-calendar-week mr-2 text-gray-400"></i>Audit window
                </h3>
                <div className="flex flex-wrap gap-2 mb-3">
                    {datePresets.map(p => (
                        <button key={p.id} onClick={() => applyPreset(p.id)}
                            className={'px-3 py-1.5 text-sm rounded-full border transition-colors ' +
                                (datePreset === p.id
                                    ? 'bg-blue-600 text-white border-blue-600'
                                    : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400')}>
                            {p.label}
                        </button>
                    ))}
                </div>
                <div className="flex gap-4 items-center">
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Start</label>
                        <input type="date" value={startDate}
                            onChange={e => { setStartDate(e.target.value); setDatePreset('custom'); }}
                            className="border border-gray-300 rounded px-3 py-2 text-sm" />
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">End</label>
                        <input type="date" value={endDate}
                            onChange={e => { setEndDate(e.target.value); setDatePreset('custom'); }}
                            className="border border-gray-300 rounded px-3 py-2 text-sm" />
                    </div>
                </div>
            </div>

            {/* ── Per-opp config preview (read-only) ──────────────────────── */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-layer-group mr-2 text-gray-400"></i>
                    Opportunities &amp; pinned image types ({oppIds.length})
                </h3>
                <p className="text-xs text-gray-500 mb-4">
                    Track A audits the MUAC image type(s) at {trackA.sample_percentage}% with the
                    {' '}{(trackA.reviewer && trackA.reviewer.agent_id) || 'no'} AI reviewer.
                    Track B audits the remaining image type(s) at {trackB.sample_percentage}%
                    {trackB.reviewer ? '' : ', human-reviewed'}.
                </p>
                <div className="space-y-3">
                    {oppIds.map(oid => {
                        const key = String(oid);
                        const cfg = perOpp[key] || {};
                        return (
                            <div key={key} className="border border-gray-200 rounded-lg p-4">
                                <div className="text-sm font-semibold text-gray-900 mb-2">
                                    {oppNames[key] || ('Opportunity ' + key)}
                                    <span className="ml-2 text-xs text-gray-400 font-mono">#{key}</span>
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    <div>
                                        <div className="text-xs font-medium text-gray-600 mb-1">MUAC paths (Track A)</div>
                                        {pathPills(cfg.muac_image_paths, 'bg-purple-50 text-purple-700')}
                                    </div>
                                    <div>
                                        <div className="text-xs font-medium text-gray-600 mb-1">Rest paths (Track B)</div>
                                        {pathPills(cfg.rest_image_paths, 'bg-gray-100 text-gray-700')}
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* ── Create button + progress ────────────────────────────────── */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <button onClick={handleCreate}
                    disabled={!startDate || !endDate || isRunning || oppIds.length === 0 || instance.status === 'completed'}
                    title={instance.status === 'completed' ? 'Run is completed; cannot create new audits.' : ''}
                    className={'inline-flex items-center px-6 py-3 bg-blue-600 text-white rounded-lg ' +
                        'hover:bg-blue-700 disabled:bg-gray-400 font-medium'}>
                    {isRunning
                        ? <span><i className="fa-solid fa-spinner fa-spin mr-2"></i>Creating…</span>
                        : <span><i className="fa-solid fa-play mr-2"></i>Create audits</span>}
                </button>
                {isRunning && progress && (
                    <div className="mt-4 bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800">
                        <div className="flex items-center font-medium">
                            <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                            {progress.message || progress.stage_name || 'Working…'}
                            {progress.total > 0 && (
                                <span className="ml-2 text-blue-600">({progress.processed || 0}/{progress.total})</span>
                            )}
                        </div>
                        {progress.total > 0 && (
                            <div className="mt-2 w-full bg-blue-200 rounded-full h-2">
                                <div className="bg-blue-600 h-2 rounded-full transition-all"
                                    style={{ width: (progress.processed / progress.total * 100) + '%' }}></div>
                            </div>
                        )}
                        <div className="mt-3 text-xs text-blue-600">
                            <i className="fa-solid fa-circle-info mr-1"></i>
                            This runs on the server — creating per-FLW audits and running the MUAC AI across
                            every selected opportunity takes a while. You can safely leave this page; the work
                            keeps running and you can return to this run to see the results.
                        </div>
                    </div>
                )}
                {jobError && (
                    <div className="mt-4 bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
                        <i className="fa-solid fa-circle-exclamation mr-2"></i>{jobError}
                    </div>
                )}
                {progress && progress.status === 'completed' && !isRunning && (
                    <div className="mt-4 bg-green-50 border border-green-200 rounded-lg p-4 text-sm text-green-800">
                        <i className="fa-solid fa-circle-check mr-2"></i>
                        Done — {progress.sessions_created != null ? progress.sessions_created : '?'} session(s)
                        created across {progress.successful != null ? progress.successful : '?'} audit(s).
                    </div>
                )}
            </div>

            {/* ── Audit results by field worker ───────────────────────────── */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-user-check mr-2 text-gray-400"></i>Audit results by field worker
                </h3>
                {loadingSessions
                    ? <div className="text-sm text-gray-500"><i className="fa-solid fa-spinner fa-spin mr-2"></i>Loading…</div>
                    : sessions.length === 0
                        ? <div className="text-sm text-gray-500">No sessions yet — set a window and create audits.</div>
                        : (
                            <div className="space-y-4">
                                {Object.keys(grouped).map(oid => {
                                    var oppData = grouped[oid];
                                    var sum = oppSummary(oppData);
                                    return (
                                        <div key={oid} className="border border-gray-200 rounded-lg overflow-hidden">
                                            <div className="bg-gray-50 px-4 py-3 border-b border-gray-200">
                                                <div className="text-sm font-semibold text-gray-900">
                                                    {oppNames[oid] || ('Opportunity ' + oid)}
                                                    <span className="ml-2 text-xs text-gray-400 font-mono">#{oid}</span>
                                                </div>
                                                <div className="text-xs text-gray-500 mt-1">
                                                    {sum.flws} field worker{sum.flws === 1 ? '' : 's'}
                                                    {' · MUAC '}{sum.muacReviewed}/{sum.muacTotal}{' reviewed, '}
                                                    <span className={sum.muacFlagged > 0 ? 'text-amber-600 font-medium' : ''}>{sum.muacFlagged} AI-flagged</span>
                                                    {' · Rest '}{sum.restReviewed}/{sum.restTotal}{' reviewed'}
                                                </div>
                                            </div>
                                            <div className="divide-y divide-gray-100">
                                                {oppData.order.map(flw => {
                                                    var r = oppData.flws[flw];
                                                    return (
                                                        <div key={flw} className="px-4 py-3">
                                                            <div className="text-sm font-medium text-gray-800 mb-1.5">{r.name}</div>
                                                            <div className="space-y-1">
                                                                {auditLine('MUAC', r.muac)}
                                                                {auditLine('Rest', r.rest)}
                                                            </div>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
            </div>
        </div>
    );
}"""

TEMPLATE = {
    "key": "weekly_dual_track_audit",
    "name": "Weekly Dual-Track Image Audit",
    "description": DEFINITION["description"],
    "icon": "fa-layer-group",
    "color": "blue",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": None,
}
