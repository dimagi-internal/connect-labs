"""Weekly Dual-Track Image Audit — multi-opp, action-shaped creator.

Each weekly run creates, per FLW, two audits per opportunity — Track A and
Track B, user-named slots each pinned to their own set of image paths (see
_image_audits). Per-path AI classifiers (Hyperzoom, MUAC Mismatch, KMC Scale
Comparison) are user-selectable checkboxes in the "Opportunities & image
types" tile, gated server-side by _classifier_applies; a path with no
explicit saved selection falls back to _default_classifiers_for_path
(preserving the pre-checkbox automatic muac-substring behavior). Duplicate
Detection is a separate, non-per-path classifier wired through Visit
Clustering — see connect_labs.audit.visit_cluster_duplicate_detection.

The per-opp image paths and track config live on the workflow DEFINITION
(instance config); the batch window lives in run state. See
docs/superpowers/specs/2026-06-30-audit-program-report-design.md and
docs/superpowers/specs/2026-07-30-dual-track-audit-classifiers-design.md.
"""

from connect_labs.audit.data_access import AuditDataAccess

MUAC_OVERZOOM_REVIEWER = {
    "agent_id": "muac_overzoom",
    "auto_apply_actions": ["fail_overzoomed"],
}

# Manually-entered MUAC reading (cm) that accompanies the tape photo —
# confirmed against the real CommCare form JSON. soliciter_muac (a
# hidden DataBindOnly field elsewhere in the form) is just a calculated
# alias of this same value.
MUAC_READING_FIELD = "muac_group/muac_display_group_2/muac_colour_display/soliciter_muac_cm"

MUAC_MATCH_REVIEWER = {
    "agent_id": "muac_match",
    # "label" names the related-fields display for this comparison_field —
    # without it the box falls back to the raw field path (see
    # ai_review_config.build_review_config / AuditDataAccess's related_fields
    # rule builder), which is what the review UI's "MUAC Reading" box used to
    # show verbatim.
    "config": {"comparison_field": MUAC_READING_FIELD, "label": "MUAC Reading"},
    "auto_apply_actions": ["fail_unmatched"],
}

# Verbatim from audit_with_ai_review.py's (the "Weekly KMC Audit with AI
# Review" template) legacy relatedFields wiring — the scale_validation agent
# compares this reading field against a photo at this exact image path.
KMC_WEIGHT_IMAGE_PATH = "anthropometric/upload_weight_image"
KMC_WEIGHT_READING_FIELD = "child_weight_visit"

KMC_SCALE_REVIEWER = {
    "agent_id": "scale_validation",
    "config": {"comparison_field": KMC_WEIGHT_READING_FIELD},
    "auto_apply_actions": ["fail_unmatched"],
}

# Per-path classifier checkboxes (see the "Opportunities & image types" tile
# in RENDER_CODE) — each opportunity's DEFINITION.config.audit_batch.per_opp
# entry may carry a "classifiers" map: {"<path>": ["hyperzoom", ...]}.
CLASSIFIER_SPECS = {
    "hyperzoom": MUAC_OVERZOOM_REVIEWER,
    "muac_mismatch": MUAC_MATCH_REVIEWER,
    "kmc_scale": KMC_SCALE_REVIEWER,
}
CLASSIFIER_KEYS = frozenset(CLASSIFIER_SPECS)


def _classifier_applies(key, path):
    """Server-side gating — the actual enforcement point regardless of what
    the (advisory-only) frontend checkbox state sent. Hyperzoom/MUAC
    Mismatch require "muac" in the path name (case-insensitive); KMC Scale
    Comparison requires an EXACT (case-sensitive) match to the weight-image
    path used by the Weekly KMC Audit with AI Review template."""
    if key in ("hyperzoom", "muac_mismatch"):
        return "muac" in (path or "").lower()
    if key == "kmc_scale":
        return path == KMC_WEIGHT_IMAGE_PATH
    return False


def _default_classifiers_for_path(path):
    """Classifiers implied for a path with no explicit saved selection —
    preserves the pre-checkbox automatic behavior (every muac path got both
    MUAC reviewers, unconditionally) so a legacy/never-resaved config keeps
    working exactly as before. kmc_scale never defaults on (it's new — no
    legacy behavior to preserve). Mirrored in RENDER_CODE's own
    defaultClassifiersForPath; keep both in sync if this ever changes."""
    return ["hyperzoom", "muac_mismatch"] if "muac" in (path or "").lower() else []


def _reviewers_for_path(path, classifiers=None):
    """Reviewer specs for one image path, resolved from its saved classifier
    selection (DEFINITION.config.audit_batch.per_opp[<opp_id>].classifiers)
    — independent of which track (A/B) the path is pinned under. A path
    absent from `classifiers` (or `classifiers` entirely falsy/None) falls
    back to _default_classifiers_for_path, so legacy/never-resaved configs
    keep behaving exactly as before checkboxes existed. Every selected key
    is re-validated against _classifier_applies here regardless of what was
    saved — this is the actual enforcement point, not just the UI's greyed-
    out checkboxes."""
    keys = (classifiers or {}).get(path, _default_classifiers_for_path(path))
    return [CLASSIFIER_SPECS[k] for k in keys if k in CLASSIFIER_SPECS and _classifier_applies(k, path)]


def _image_audits(paths, classifiers=None):
    """One image_audits entry per pinned image path, each with its own
    per-path reviewer(s) (see _reviewers_for_path) — the PR #771 per-image-type
    model. See connect_labs/audit/ai_review_config.build_review_config."""
    return [{"image_path": p, "reviewers": _reviewers_for_path(p, classifiers)} for p in paths or []]


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
    pass_threshold=None,
    deliver_unit_types=None,
    visit_statuses=None,
    enable_time_gap=None,
    time_gap_minutes=None,
    enable_distance=None,
    distance_meters=None,
    enable_duplicate_detection=None,
):
    """Build the per-opp, per-track run_audit_creation kwargs for one weekly batch.

    Returns a flat list of kwargs dicts. A track is skipped when its per-opp
    image-path list is empty. JSON-coerced string keys are used to look up
    per_opp / opp_names, so callers may pass either int or str opp ids.

    ``pass_threshold``/``deliver_unit_types``/``visit_statuses`` (PR #884) are
    applied identically to every track's criteria when provided — they scope
    which visits are audited (deliver unit type, visit status) and how the
    resulting audit's overall_result is decided (pass threshold), same as the
    Django creation wizard. ``enable_time_gap``/``time_gap_minutes``/``enable_distance``/``distance_meters`` (visit clustering) are applied identically to every track's criteria when provided.
    ``AuditCriteria.from_dict`` (in
    ``connect_labs.audit.data_access``) already understands these keys, so no
    changes were needed to ``run_audit_creation`` itself.
    """
    calls = []
    for opp_id in opportunity_ids:
        key = str(opp_id)
        cfg = per_opp.get(key, {})
        name = opp_names.get(key, "")
        classifiers = cfg.get("classifiers")
        for track, paths in (
            (track_a, cfg.get("muac_image_paths")),
            (track_b, cfg.get("rest_image_paths")),
        ):
            image_audits = _image_audits(paths, classifiers)
            if not image_audits:
                continue
            criteria = {
                "audit_type": "date_range",
                "start_date": window_start,
                "end_date": window_end,
                "sample_percentage": track["sample_percentage"],
                "granularity": "per_flw",
                "tag": track["tag"],
                # related_fields is derived by run_audit_creation from image_audits.
            }
            if pass_threshold is not None:
                criteria["pass_threshold"] = pass_threshold
            if deliver_unit_types is not None:
                criteria["deliver_unit_types"] = deliver_unit_types
            if visit_statuses is not None:
                criteria["visit_statuses"] = visit_statuses
            if enable_time_gap is not None:
                criteria["enable_time_gap"] = enable_time_gap
            if time_gap_minutes is not None:
                criteria["time_gap_minutes"] = time_gap_minutes
            if enable_distance is not None:
                criteria["enable_distance"] = enable_distance
            if distance_meters is not None:
                criteria["distance_meters"] = distance_meters
            if enable_duplicate_detection is not None:
                criteria["enable_duplicate_detection"] = enable_duplicate_detection
            calls.append(
                {
                    "username": username,
                    "opportunities": [{"id": opp_id, "name": name}],
                    "criteria": criteria,
                    "workflow_run_id": workflow_run_id,
                    "image_audits": image_audits,
                    "context_fields": None,
                }
            )
    return calls


# =============================================================================
# Saved-runs completion gate (Task 2)
# =============================================================================


def _incomplete_audit_count(sessions):
    total = len(sessions)
    done = sum(1 for s in sessions if s.status == "completed")
    return total, total - done


def _audit_rollup_snapshot(sessions, opportunity_id):
    """Per-FLW rollup for the frozen snapshot. image_count for images; ai_no_match = AI-flagged."""
    rows = {}
    by_tag = {
        "muac": {"images": 0, "pass": 0, "fail": 0, "ai_flagged": 0},
        "rest": {"images": 0, "pass": 0, "fail": 0, "ai_flagged": 0},
    }
    for s in sessions:
        if s.opportunity_id != opportunity_id:  # defensive: sessions are opp-scoped already
            continue
        tag = s.tag if s.tag in by_tag else None
        if tag is None:
            continue
        st = s.get_assessment_stats() or {}
        cell = {
            "images": s.image_count or 0,
            "pass": st.get("pass", 0),
            "fail": st.get("fail", 0),
            "ai_flagged": st.get("ai_no_match", 0),
            "status": s.status,
            "session_id": s.id,
        }
        agg = by_tag[tag]
        agg["images"] += cell["images"]
        agg["pass"] += cell["pass"]
        agg["fail"] += cell["fail"]
        agg["ai_flagged"] += cell["ai_flagged"]
        fid = s.flw_username or "unknown"
        row = rows.setdefault(
            fid,
            {"flw_id": fid, "flw_name": getattr(s, "flw_display_name", fid) or fid, "muac": None, "rest": None},
        )
        row[tag] = cell
    return {"by_tag": by_tag, "flw_rows": list(rows.values())}


def build_snapshot(*, pipelines, state, opportunity_id, run_id=None, request=None, access_token=None, **_):
    """Saved-runs completion hook. GATE: raises until every audit session is completed."""
    ada = AuditDataAccess(request=request, access_token=access_token, opportunity_id=opportunity_id)
    try:
        sessions = ada.get_sessions_by_workflow_run(run_id) if run_id else []
    finally:
        ada.close()
    total, incomplete = _incomplete_audit_count(sessions)
    if incomplete > 0:
        raise ValueError(
            f"{incomplete} of {total} audits still open — complete every audit before marking this run complete."
        )
    return {
        "audit_summary": _audit_rollup_snapshot(sessions, opportunity_id),
        "completed_counts": {"total": total, "incomplete": incomplete},
        "window_start": state.get("window_start"),
        "window_end": state.get("window_end"),
    }


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
            # PR #771 per-image-type model, extended: classifier attachment is
            # checkbox-driven per path (see _reviewers_for_path /
            # _default_classifiers_for_path), not per-track and not automatic
            # substring matching. "name" is a purely cosmetic display label
            # the user can rename; it has no effect on which images get
            # AI-reviewed.
            "track_a": {"tag": "muac", "sample_percentage": 100, "name": "MUAC"},
            "track_b": {"tag": "rest", "sample_percentage": 10, "name": "Other"},
            "per_opp": {},  # { "<opp_id>": {"muac_image_paths": [...], "rest_image_paths": [...], "classifiers": {"<path>": ["hyperzoom", ...]}} }
            "opp_names": {},  # { "<opp_id>": "Opp display name" }
            "visit_clustering": {
                "enable_time_gap": False,
                "time_gap_minutes": 10,
                "enable_distance": False,
                "distance_meters": 10,
                "enable_duplicate_detection": False,
            },
        }
    },
    "pipeline_sources": [],
}

RENDER_CODE = r"""function WorkflowUI({ definition, instance, actions, onUpdateState, view }) {

    // ── Config from the DEFINITION (pinned at create time, read-only here) ────
    const batch = (definition.config && definition.config.audit_batch) || {};
    const perOpp = batch.per_opp || {};
    const oppNames = batch.opp_names || {};
    const trackA = batch.track_a || {};
    const trackB = batch.track_b || {};
    const oppIds = (instance.opportunity_ids && instance.opportunity_ids.length)
        ? instance.opportunity_ids
        : (instance.opportunity_id ? [instance.opportunity_id] : []);

    function getCsrfToken() {
        return document.getElementById('workflow-root')?.dataset?.csrfToken
            || document.querySelector('[name=csrfmiddlewaretoken]')?.value
            || '';
    }

    // ── Track names + per-opp image path selection (editable pinned config) ───
    // "name" is a cosmetic display label only — it has no bearing on which
    // images get AI-reviewed (see _reviewers_for_path: that's decided per-path,
    // by whether "muac" appears in the path itself).
    const [trackAName, setTrackAName] = React.useState(trackA.name || 'MUAC');
    const [trackBName, setTrackBName] = React.useState(trackB.name || 'Other');

    // Discovered image paths per opportunity (same discovery method as the
    // Bulk Image Audit template's image-questions fetch), and the user's
    // current checkbox selections per opp per track.
    const [imageQuestionsByOpp, setImageQuestionsByOpp] = React.useState({});
    const [selectedPathsByOpp, setSelectedPathsByOpp] = React.useState(() => {
        const init = {};
        oppIds.forEach(oid => {
            const key = String(oid);
            const cfg = perOpp[key] || {};
            init[key] = { trackA: cfg.muac_image_paths || [], trackB: cfg.rest_image_paths || [] };
        });
        return init;
    });

    // Per-path AI classifiers — see CLASSIFIER_SPECS / _classifier_applies /
    // _default_classifiers_for_path in weekly_dual_track_audit.py. appliesTo()
    // here is cosmetic (drives which checkboxes render greyed-out); the server
    // re-validates every selection regardless.
    const CLASSIFIERS = [
        { key: 'hyperzoom', label: 'Hyperzoom', appliesTo: p => /muac/i.test(p || '') },
        { key: 'muac_mismatch', label: 'MUAC Mismatch', appliesTo: p => /muac/i.test(p || '') },
        { key: 'kmc_scale', label: 'KMC Scale Comparison', appliesTo: p => p === 'anthropometric/upload_weight_image' },
    ];
    // Mirrors _default_classifiers_for_path — preserves the pre-checkbox
    // automatic behavior for any path that's never been explicitly saved.
    const defaultClassifiersForPath = (path) => (/muac/i.test(path || '') ? ['hyperzoom', 'muac_mismatch'] : []);

    const [classifiersByOpp, setClassifiersByOpp] = React.useState(() => {
        const init = {};
        oppIds.forEach(oid => {
            const key = String(oid);
            init[key] = (perOpp[key] || {}).classifiers || {};
        });
        return init;
    });
    const effectiveClassifiers = (oppKey, path) => {
        const forOpp = classifiersByOpp[oppKey] || {};
        return Object.prototype.hasOwnProperty.call(forOpp, path) ? forOpp[path] : defaultClassifiersForPath(path);
    };
    const toggleClassifier = (oppKey, path, clsKey) => {
        setClassifiersByOpp(prev => {
            const oppMap = { ...(prev[oppKey] || {}) };
            const current = effectiveClassifiers(oppKey, path);
            oppMap[path] = current.includes(clsKey) ? current.filter(k => k !== clsKey) : [...current, clsKey];
            return { ...prev, [oppKey]: oppMap };
        });
    };

    React.useEffect(() => {
        oppIds.forEach(oid => {
            const key = String(oid);
            if (imageQuestionsByOpp[key]) return; // already loaded or loading
            setImageQuestionsByOpp(prev => ({ ...prev, [key]: { loading: true, error: null, questions: [] } }));
            fetch('/audit/api/opportunity/' + oid + '/image-questions/')
                .then(async r => {
                    if (!r.ok) {
                        let msg = 'HTTP ' + r.status;
                        try { const errData = await r.json(); if (errData.error) msg = errData.error; } catch (_) {}
                        throw new Error(msg);
                    }
                    return r.json();
                })
                .then(data => {
                    setImageQuestionsByOpp(prev => ({ ...prev, [key]: { loading: false, error: null, questions: data } }));
                })
                .catch(err => {
                    setImageQuestionsByOpp(prev => ({
                        ...prev,
                        [key]: { loading: false, error: 'Failed to load image types: ' + err.message, questions: [] },
                    }));
                });
        });
    }, [oppIds.join(',')]);

    const setTrackPaths = (oppKey, trackKey, updater) => {
        setSelectedPathsByOpp(prev => {
            const cur = prev[oppKey] || { trackA: [], trackB: [] };
            return { ...prev, [oppKey]: { ...cur, [trackKey]: updater(cur[trackKey] || []) } };
        });
    };
    const togglePath = (oppKey, trackKey, path) => setTrackPaths(oppKey, trackKey, list =>
        list.includes(path) ? list.filter(p => p !== path) : [...list, path]);

    const [savingConfig, setSavingConfig] = React.useState(false);
    const [saveConfigError, setSaveConfigError] = React.useState(null);
    const [saveConfigSuccess, setSaveConfigSuccess] = React.useState(false);

    const handleSaveConfig = async () => {
        setSavingConfig(true); setSaveConfigError(null); setSaveConfigSuccess(false);
        const per_opp = {};
        oppIds.forEach(oid => {
            const key = String(oid);
            const sel = selectedPathsByOpp[key] || { trackA: [], trackB: [] };
            const selectedPaths = Array.from(new Set([...sel.trackA, ...sel.trackB]));
            const classifiers = {};
            selectedPaths.forEach(path => { classifiers[path] = effectiveClassifiers(key, path); });
            per_opp[key] = { muac_image_paths: sel.trackA, rest_image_paths: sel.trackB, classifiers };
        });
        try {
            const res = await fetch('/labs/workflow/api/' + instance.definition_id + '/audit-batch-config/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                body: JSON.stringify({ track_a_name: trackAName, track_b_name: trackBName, per_opp: per_opp }),
            });
            const data = await res.json();
            if (!res.ok || !data.success) throw new Error((data && data.error) || 'Failed to save');
            setSaveConfigSuccess(true);
            setTimeout(() => setSaveConfigSuccess(false), 2000);
        } catch (e) {
            setSaveConfigError('Failed to save: ' + (e.message || e));
        } finally {
            setSavingConfig(false);
        }
    };

    // ── Date-window picker (mirrors bulk_image_audit) ─────────────────────────
    // A completed run reads its frozen window from view.state; an in-progress
    // run reads live run state.
    const runState = (view && view.state) || instance.state || {};
    const [datePreset, setDatePreset] = React.useState(runState.date_preset || 'last_week');
    const [startDate, setStartDate] = React.useState(runState.window_start || '');
    const [endDate, setEndDate] = React.useState(runState.window_end || '');

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
    const [taskId, setTaskId] = React.useState(null);
    const [isCancelling, setIsCancelling] = React.useState(false);
    // A create job whose worker died mid-batch (e.g. a deploy cutover) never
    // writes a terminal status, so active_job stays 'running' forever. We detect
    // that on reconnect (see below) and surface it here instead of spinning.
    const [staleJob, setStaleJob] = React.useState(false);
    // Per-run sampling rates — default to the pinned config, adjustable before create.
    const [muacSample, setMuacSample] = React.useState(trackA.sample_percentage != null ? trackA.sample_percentage : 100);
    const [otherSample, setOtherSample] = React.useState(trackB.sample_percentage != null ? trackB.sample_percentage : 10);
    // Visit Clustering (optional 3rd filter) — the job handler persists whatever
    // was actually used onto run state (enable_time_gap, etc.), so a reopened
    // run shows ITS OWN params, not the pinned template default.
    const clustering = batch.visit_clustering || {};
    const [enableTimeGap, setEnableTimeGap] = React.useState(
        runState.enable_time_gap != null ? !!runState.enable_time_gap : !!clustering.enable_time_gap);
    const [timeGapMinutes, setTimeGapMinutes] = React.useState(
        runState.time_gap_minutes != null ? runState.time_gap_minutes
            : (clustering.time_gap_minutes != null ? clustering.time_gap_minutes : 10));
    const [enableDistance, setEnableDistance] = React.useState(
        runState.enable_distance != null ? !!runState.enable_distance : !!clustering.enable_distance);
    const [distanceMeters, setDistanceMeters] = React.useState(
        runState.distance_meters != null ? runState.distance_meters
            : (clustering.distance_meters != null ? clustering.distance_meters : 10));
    const [enableDuplicateDetection, setEnableDuplicateDetection] = React.useState(
        runState.enable_duplicate_detection != null ? !!runState.enable_duplicate_detection : !!clustering.enable_duplicate_detection);
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
            () => { setIsRunning(false); setIsCancelling(false); setProgress({ status: 'cancelled' }); },
            instance.id // run_id — lets the server unstick a reconnect to a dead job
        );
        cleanupRef.current = cleanup;
    };

    // ── Reconnect to a still-running job after a page reload ───────────────────
    // The batch runs server-side (a Celery job) — leaving the page never stops
    // it. If we come back while it's still working, re-attach the progress
    // stream instead of showing a stale idle state.
    //
    // Guard against ZOMBIE jobs: if the worker is killed mid-batch (a deploy
    // cutover, a crash) the job never writes a terminal status, so active_job
    // stays 'running' forever. Celery can't disambiguate a dead/expired task
    // from a queued one — both report PENDING — so attaching the progress stream
    // would "reconnect" eternally (the exact stuck-spinner symptom). Trust
    // active_job.started_at instead: a real batch finishes in minutes, so a
    // 'running' flag older than the staleness window is dead. Surface it and let
    // the user re-create rather than spin.
    const STALE_JOB_MS = 15 * 60 * 1000;
    React.useEffect(() => {
        const active = instance.state?.active_job;
        if (!(active && active.status === 'running' && active.job_id)) return;
        const startedMs = active.started_at ? Date.parse(active.started_at) : NaN;
        const age = isNaN(startedMs) ? Infinity : (Date.now() - startedMs);
        if (age > STALE_JOB_MS) {
            setStaleJob(true); // zombie — do not reconnect
            return;
        }
        setIsRunning(true);
        setTaskId(active.job_id);
        setProgress({ status: 'running', message: 'Reconnecting to the running job…' });
        attachStream(active.job_id);
    }, []); // once on mount

    // ── Create handler ────────────────────────────────────────────────────────
    // 1) persist the window to run STATE (the server handler reads window from
    //    state and the opp set + config from the DEFINITION), 2) start the job,
    //    3) stream progress, 4) reload the created sessions on completion.
    const handleCreate = async () => {
        if (!startDate || !endDate || isRunning || instance.status === 'completed') return;
        setIsRunning(true); setJobError(null); setStaleJob(false);
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
                program_id: instance.program_id,
                window_start: startDate,
                window_end: endDate,
                muac_sample_percentage: Number(muacSample),
                other_sample_percentage: Number(otherSample),
                enable_time_gap: enableTimeGap,
                time_gap_minutes: Number(timeGapMinutes),
                enable_distance: enableDistance,
                distance_meters: Number(distanceMeters),
                enable_duplicate_detection: enableDuplicateDetection,
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
        setTaskId(resp.task_id);
        setProgress({ status: 'running', message: 'Starting…' });
        attachStream(resp.task_id);
    };

    // ── Cancel handler ────────────────────────────────────────────────────────
    // Sessions/images already created and reviewed are left as-is — cancelling
    // only stops whatever work hasn't started yet (see run_audit_creation's
    // cooperative cancel_key and the job handler's between-call check).
    const handleCancel = async () => {
        if (!taskId || isCancelling) return;
        setIsCancelling(true);
        const result = await actions.cancelJob(taskId, instance.id);
        if (!result || !result.success) {
            setIsCancelling(false);
            setJobError((result && result.error) || 'Failed to stop — the job may still be running.');
            return;
        }
        setIsRunning(false);
        setIsCancelling(false);
        setProgress({ status: 'cancelled', message: 'Stopped — sessions created and images already reviewed are kept.' });
        if (cleanupRef.current) { cleanupRef.current(); cleanupRef.current = null; }
        await refreshSessions();
    };

    const datePresets = [
        { id: 'last_week', label: 'Last Week' },
        { id: 'last_7_days', label: 'Last 7 Days' },
        { id: 'last_14_days', label: 'Last 14 Days' },
        { id: 'last_30_days', label: 'Last 30 Days' },
        { id: 'last_month', label: 'Last Month' },
        { id: 'custom', label: 'Custom' },
    ];

    // ── Completion gate (all audit sessions must be completed) ────────────────
    var openCount = sessions.filter(function(s){ return s.status !== 'completed'; }).length;
    var allComplete = sessions.length > 0 && openCount === 0;
    var isCompleted = view && view.isCompleted;

    // ── View-only gate ────────────────────────────────────────────────────────
    // A run is fired ONCE: as soon as its audits exist, this workflow becomes a
    // read-only view of what was created (the program creator opens these runs to
    // inspect, not to re-fire). "Already created" = the handler persisted a
    // last_batch onto the run, or sessions loaded. Not view-only while a create
    // job is actively running (show its progress) or before anything was created
    // (show the creation UI so a never-run / recovered run can still fire).
    var lastBatch = (runState && runState.last_batch) || null;
    var createdCount = sessions.length || (lastBatch && lastBatch.sessions_created) || 0;
    var hasCreated = !!lastBatch || sessions.length > 0;
    var viewOnly = hasCreated && !isRunning;
    var winStart = (lastBatch && lastBatch.window_start) || runState.window_start || startDate;
    var winEnd = (lastBatch && lastBatch.window_end) || runState.window_end || endDate;
    var fmtWin = function (d) {
        if (!d) return '—';
        try { return new Date(d + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); }
        catch (e) { return d; }
    };

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

            {/* ── View-only summary (audits already created) ──────────────── */}
            {viewOnly && (
                <div className="bg-white rounded-lg shadow-sm p-6">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                        <h3 className="text-sm font-medium text-gray-700">
                            <i className="fa-solid fa-lock mr-2 text-gray-400"></i>Audits created — view only
                        </h3>
                        {isCompleted
                            ? <span className="text-xs px-2 py-1 rounded bg-gray-100 text-gray-600"><i className="fa-solid fa-lock mr-1"></i>Completed</span>
                            : <span className="text-xs px-2 py-1 rounded bg-blue-50 text-blue-700"><i className="fa-solid fa-circle-check mr-1"></i>Created</span>}
                    </div>
                    <p className="text-sm text-gray-600 mt-2">
                        Window <span className="font-medium text-gray-900">{fmtWin(winStart)} – {fmtWin(winEnd)}</span>
                        {' · '}<span className="font-medium text-gray-900">{createdCount}</span> audit session{createdCount === 1 ? '' : 's'} across {oppIds.length} opportunit{oppIds.length === 1 ? 'y' : 'ies'}.
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                        This run's audits have already been created — creation controls are hidden. Review the results below.
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                        Visit clustering: {(enableTimeGap || enableDistance)
                            ? [
                                enableTimeGap ? `within ${timeGapMinutes} min` : null,
                                enableDistance ? `within ${distanceMeters}m` : null,
                            ].filter(Boolean).join(' and ')
                            : 'not applied'}
                        {enableDuplicateDetection ? ' · Duplicate Detection enabled' : ''}.
                    </p>
                </div>
            )}

            {/* ── Date window ─────────────────────────────────────────────── */}
            {!viewOnly && (
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
            )}

            {/* ── Opportunities & image types (editable pinned config) ─────── */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-layer-group mr-2 text-gray-400"></i>
                    Opportunities &amp; image types ({oppIds.length})
                </h3>
                {viewOnly ? (
                    <React.Fragment>
                        <p className="text-xs text-gray-500 mb-4">
                            {trackAName} audited at {muacSample}%. {trackBName} audited at {otherSample}%.
                            Any path containing "muac" is AI-reviewed; every other path is human-reviewed.
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
                                                <div className="text-xs font-medium text-gray-600 mb-1">{trackAName} paths (Track A)</div>
                                                {pathPills(cfg.muac_image_paths, 'bg-purple-50 text-purple-700')}
                                            </div>
                                            <div>
                                                <div className="text-xs font-medium text-gray-600 mb-1">{trackBName} paths (Track B)</div>
                                                {pathPills(cfg.rest_image_paths, 'bg-gray-100 text-gray-700')}
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </React.Fragment>
                ) : (
                    <React.Fragment>
                        <p className="text-xs text-gray-500 mb-4">
                            Pick which image path(s) each track audits, per opportunity. Track A is
                            required — at least one path must be selected for every opportunity below.
                            Track B is optional; leave it empty to skip it for an opportunity. Each
                            selected path can independently opt into AI classifiers below — greyed-out
                            classifiers don't apply to that path's image type.
                        </p>
                        <div className="flex gap-6 items-end flex-wrap mb-4">
                            <div>
                                <label className="block text-xs text-gray-500 mb-1">Track A name</label>
                                <input type="text" value={trackAName}
                                    onChange={e => setTrackAName(e.target.value)}
                                    disabled={isRunning || instance.status === 'completed'}
                                    className="border border-gray-300 rounded px-3 py-2 text-sm w-40" />
                            </div>
                            <div>
                                <label className="block text-xs text-gray-500 mb-1">Track B name</label>
                                <input type="text" value={trackBName}
                                    onChange={e => setTrackBName(e.target.value)}
                                    disabled={isRunning || instance.status === 'completed'}
                                    className="border border-gray-300 rounded px-3 py-2 text-sm w-40" />
                            </div>
                        </div>
                        <div className="space-y-3">
                            {oppIds.map(oid => {
                                const key = String(oid);
                                const iq = imageQuestionsByOpp[key] || { loading: true, error: null, questions: [] };
                                const sel = selectedPathsByOpp[key] || { trackA: [], trackB: [] };
                                const renderColumn = (trackKey, label) => (
                                    <div key={trackKey}>
                                        <div className="flex items-center justify-between mb-1">
                                            <div className="text-xs font-medium text-gray-600">{label} paths</div>
                                            {iq.questions.length > 0 && (
                                                <div className="flex gap-2">
                                                    <button type="button"
                                                        disabled={isRunning || instance.status === 'completed'}
                                                        onClick={() => setTrackPaths(key, trackKey, () => iq.questions.map(q => q.path))}
                                                        className="text-xs text-blue-600 hover:underline">Select All</button>
                                                    <button type="button"
                                                        disabled={isRunning || instance.status === 'completed'}
                                                        onClick={() => setTrackPaths(key, trackKey, () => [])}
                                                        className="text-xs text-blue-600 hover:underline">Deselect All</button>
                                                </div>
                                            )}
                                        </div>
                                        {iq.loading && <div className="text-xs text-gray-400">Loading image types…</div>}
                                        {iq.error && <div className="text-xs text-red-500">{iq.error}</div>}
                                        {!iq.loading && !iq.error && iq.questions.length === 0 && (
                                            <div className="text-xs text-gray-400 italic">No image questions found.</div>
                                        )}
                                        {!iq.loading && !iq.error && iq.questions.length > 0 && (
                                            <div className="space-y-1">
                                                {iq.questions.map(q => (
                                                    <label key={q.id} className="flex items-center gap-2 text-xs text-gray-700">
                                                        <input type="checkbox"
                                                            checked={sel[trackKey].includes(q.path)}
                                                            onChange={() => togglePath(key, trackKey, q.path)}
                                                            disabled={isRunning || instance.status === 'completed'}
                                                            className="h-3.5 w-3.5" />
                                                        <span className="font-mono">{q.path}</span>
                                                    </label>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                );
                                return (
                                    <div key={key} className="border border-gray-200 rounded-lg p-4">
                                        <div className="text-sm font-semibold text-gray-900 mb-2">
                                            {oppNames[key] || ('Opportunity ' + key)}
                                            <span className="ml-2 text-xs text-gray-400 font-mono">#{key}</span>
                                        </div>
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                            {renderColumn('trackA', trackAName)}
                                            {renderColumn('trackB', trackBName)}
                                        </div>
                                        {(() => {
                                            const selectedPaths = Array.from(new Set([...(sel.trackA || []), ...(sel.trackB || [])]));
                                            if (!selectedPaths.length) return null;
                                            return (
                                                <div className="mt-3 pt-3 border-t border-gray-100">
                                                    <div className="text-xs font-medium text-gray-600 mb-2">AI Classifiers</div>
                                                    <div className="space-y-2">
                                                        {selectedPaths.map(path => {
                                                            const active = effectiveClassifiers(key, path);
                                                            return (
                                                                <div key={path} className="flex items-center flex-wrap gap-x-4 gap-y-1 text-xs">
                                                                    <span className="font-mono text-gray-700 w-full sm:w-auto">{path}</span>
                                                                    {CLASSIFIERS.map(c => {
                                                                        const applies = c.appliesTo(path);
                                                                        return (
                                                                            <label key={c.key}
                                                                                className={'flex items-center gap-1 ' + (applies ? 'text-gray-700' : 'text-gray-300')}>
                                                                                <input type="checkbox"
                                                                                    checked={applies && active.includes(c.key)}
                                                                                    disabled={!applies || isRunning || instance.status === 'completed'}
                                                                                    onChange={() => toggleClassifier(key, path, c.key)}
                                                                                    className="h-3.5 w-3.5" />
                                                                                {c.label}
                                                                            </label>
                                                                        );
                                                                    })}
                                                                </div>
                                                            );
                                                        })}
                                                    </div>
                                                </div>
                                            );
                                        })()}
                                        {sel.trackA.length === 0 && (
                                            <div className="mt-2 text-xs text-amber-600">
                                                <i className="fa-solid fa-triangle-exclamation mr-1"></i>
                                                {trackAName} requires at least one selected path for this opportunity.
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                        <div className="mt-4 flex items-center gap-3">
                            <button onClick={handleSaveConfig}
                                disabled={savingConfig || isRunning || instance.status === 'completed'
                                    || oppIds.some(oid => !(selectedPathsByOpp[String(oid)] || {}).trackA || (selectedPathsByOpp[String(oid)] || {}).trackA.length === 0)}
                                className="inline-flex items-center px-4 py-2 bg-gray-800 text-white rounded-lg hover:bg-gray-900 disabled:bg-gray-400 text-sm font-medium">
                                {savingConfig
                                    ? <span><i className="fa-solid fa-spinner fa-spin mr-2"></i>Saving…</span>
                                    : <span><i className="fa-solid fa-floppy-disk mr-2"></i>Save configuration</span>}
                            </button>
                            {saveConfigError && <span className="text-sm text-red-600">{saveConfigError}</span>}
                            {saveConfigSuccess && <span className="text-sm text-green-600"><i className="fa-solid fa-circle-check mr-1"></i>Saved</span>}
                        </div>
                    </React.Fragment>
                )}
            </div>

            {/* ── Sampling rates (per-run, default from config) ───────────── */}
            {!viewOnly && (
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-percent mr-2 text-gray-400"></i>Sampling rates
                </h3>
                <p className="text-xs text-gray-500 mb-3">
                    Share of each field worker's matching images to audit. Defaults to the pinned
                    configuration; adjust for this run.
                </p>
                <div className="flex gap-6 items-end flex-wrap">
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">{trackAName} (Track A)</label>
                        <div className="flex items-center gap-2">
                            <input type="number" min="1" max="100" value={muacSample}
                                onChange={e => setMuacSample(e.target.value)}
                                disabled={isRunning || instance.status === 'completed'}
                                className="border border-gray-300 rounded px-3 py-2 text-sm w-20" />
                            <span className="text-xs text-gray-400">% of {trackAName} images</span>
                        </div>
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">{trackBName} (Track B)</label>
                        <div className="flex items-center gap-2">
                            <input type="number" min="1" max="100" value={otherSample}
                                onChange={e => setOtherSample(e.target.value)}
                                disabled={isRunning || instance.status === 'completed'}
                                className="border border-gray-300 rounded px-3 py-2 text-sm w-20" />
                            <span className="text-xs text-gray-400">% of remaining images</span>
                        </div>
                    </div>
                </div>
            </div>
            )}

            {/* ── Visit Clustering (optional 3rd filter) ──────────────────────── */}
            {!viewOnly && (
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-layer-group mr-2 text-gray-400"></i>Visit Clustering
                </h3>
                <p className="text-xs text-gray-500 mb-3">
                    Optional — groups consecutive visits by the same field worker that are close in time
                    and/or location, for duplicate-detection review. Leave both unchecked to skip this entirely.
                </p>
                <div className="space-y-3">
                    <div className="flex items-center gap-3">
                        <input type="checkbox" checked={enableTimeGap}
                            onChange={e => setEnableTimeGap(e.target.checked)}
                            disabled={isRunning || instance.status === 'completed'}
                            className="w-4 h-4" />
                        <span className="text-sm text-gray-700">Group visits within</span>
                        <input type="number" min="1" value={timeGapMinutes}
                            onChange={e => setTimeGapMinutes(e.target.value)}
                            disabled={!enableTimeGap || isRunning || instance.status === 'completed'}
                            className="border border-gray-300 rounded px-2 py-1 text-sm w-20 disabled:bg-gray-100" />
                        <span className="text-sm text-gray-700">minutes of each other (by visit date)</span>
                    </div>
                    <div className="flex items-center gap-3">
                        <input type="checkbox" checked={enableDistance}
                            onChange={e => setEnableDistance(e.target.checked)}
                            disabled={isRunning || instance.status === 'completed'}
                            className="w-4 h-4" />
                        <span className="text-sm text-gray-700">Group visits within</span>
                        <input type="number" min="1" value={distanceMeters}
                            onChange={e => setDistanceMeters(e.target.value)}
                            disabled={!enableDistance || isRunning || instance.status === 'completed'}
                            className="border border-gray-300 rounded px-2 py-1 text-sm w-20 disabled:bg-gray-100" />
                        <span className="text-sm text-gray-700">meters of each other (by GPS location)</span>
                    </div>
                    <div className="flex items-start gap-3 pt-2 border-t border-gray-100">
                        <input type="checkbox" checked={enableDuplicateDetection}
                            onChange={e => setEnableDuplicateDetection(e.target.checked)}
                            disabled={(!enableTimeGap && !enableDistance) || isRunning || instance.status === 'completed'}
                            className="w-4 h-4 mt-0.5" />
                        <div>
                            <span className="text-sm text-gray-700">Send groupings to the Duplicate Detection API</span>
                            <p className="text-xs text-gray-500 mt-0.5">
                                Checks every image already in a grouping above, across whichever image paths
                                are selected for that track, against the Duplicate Detection service. Track A
                                and Track B are separate audits, so groupings never span across tracks -- this
                                setting applies independently to each track's own audit. Confirmed duplicates
                                are flagged in the AI summary and pre-tagged Duplicate/Fake in bulk assessment
                                (existing manual tags are never overwritten). Requires at least one of the
                                groupings above to be enabled.
                            </p>
                        </div>
                    </div>
                </div>
            </div>
            )}

            {/* ── Create button + progress ────────────────────────────────── */}
            {!viewOnly && (
            <div className="bg-white rounded-lg shadow-sm p-6">
                <button onClick={handleCreate}
                    disabled={!startDate || !endDate || isRunning || oppIds.length === 0 || instance.status === 'completed'
                        || (enableTimeGap && !(Number(timeGapMinutes) > 0))
                        || (enableDistance && !(Number(distanceMeters) > 0))}
                    title={instance.status === 'completed' ? 'Run is completed; cannot create new audits.' : ''}
                    className={'inline-flex items-center px-6 py-3 bg-blue-600 text-white rounded-lg ' +
                        'hover:bg-blue-700 disabled:bg-gray-400 font-medium'}>
                    {isRunning
                        ? <span><i className="fa-solid fa-spinner fa-spin mr-2"></i>Creating…</span>
                        : <span><i className="fa-solid fa-play mr-2"></i>Create audits</span>}
                </button>
                {isRunning && progress && (
                    <div className="mt-4 bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center font-medium">
                                <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                                {progress.message || progress.stage_name || 'Working…'}
                                {progress.total > 0 && (
                                    <span className="ml-2 text-blue-600">({progress.processed || 0}/{progress.total})</span>
                                )}
                            </div>
                            {taskId && (
                                <button onClick={handleCancel} disabled={isCancelling}
                                    className={'px-3 py-1 text-sm text-red-600 hover:text-red-800 ' +
                                        'hover:bg-red-100 rounded transition-colors disabled:opacity-50 whitespace-nowrap'}>
                                    <i className="fa-solid fa-times mr-1"></i>
                                    {isCancelling ? 'Stopping…' : 'Stop'}
                                </button>
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
                {staleJob && !isRunning && (
                    <div className="mt-4 bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-800">
                        <i className="fa-solid fa-triangle-exclamation mr-2"></i>
                        The previous audit run didn't finish — the server job stopped before completing, so no
                        audits were created. Click <strong>Create audits</strong> to run it again.
                    </div>
                )}
                {progress && progress.status === 'completed' && !isRunning && (
                    <div className="mt-4 bg-green-50 border border-green-200 rounded-lg p-4 text-sm text-green-800">
                        <i className="fa-solid fa-circle-check mr-2"></i>
                        Done — {sessions.length} audit session(s) created across {oppIds.length} opportunit{oppIds.length === 1 ? 'y' : 'ies'} (one MUAC + one sampled audit per field worker per opp).
                    </div>
                )}
                {progress && progress.status === 'cancelled' && !isRunning && (
                    <div className="mt-4 bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-800">
                        <i className="fa-solid fa-ban mr-2"></i>
                        {progress.message || 'Stopped.'} {sessions.length} audit session(s) exist so far across {oppIds.length} opportunit{oppIds.length === 1 ? 'y' : 'ies'} — click <strong>Create audits</strong> to review the rest, or open a session below.
                    </div>
                )}
            </div>
            )}

            {/* ── Audit results by field worker ───────────────────────────── */}
            {/* Rendered by the shared window.LabsAudit primitive so the opp run,
                the program creator's expandable rows, and the pages card all draw
                this panel identically. Edit the component in
                connect_labs/static/js/labs_audit_breakdown.js — not here. */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                {window.LabsAudit
                    ? window.LabsAudit.renderFlwBreakdown(React, {
                        sessions: sessions,
                        oppNames: oppNames,
                        workflowRunId: instance.id,
                        loading: loadingSessions,
                        emptyText: 'No sessions yet — set a window and create audits.',
                        trackALabel: trackAName,
                        trackBLabel: trackBName,
                      })
                    : <div className="text-sm text-gray-500">Loading…</div>}
            </div>

            {/* ── Completion ─────────────────────────────────────────────── */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                {isCompleted
                    ? <div className="text-sm text-green-800 bg-green-50 border border-green-200 rounded-lg p-4">
                        <i className="fa-solid fa-lock mr-2"></i>Run completed{view.asOf ? ' · ' + new Date(view.asOf).toLocaleString() : ''}. The results are frozen.
                      </div>
                    : <div>
                        <button
                            onClick={function(){ if (view && view.complete) view.complete({confirm: 'Mark this run complete? All ' + sessions.length + ' audits are done; the results will be frozen.'}); }}
                            disabled={!allComplete}
                            className={'inline-flex items-center px-6 py-3 rounded-lg font-medium ' + (allComplete ? 'bg-green-600 text-white hover:bg-green-700' : 'bg-gray-300 text-gray-500 cursor-not-allowed')}>
                            <i className="fa-solid fa-flag-checkered mr-2"></i>Mark Run Complete
                        </button>
                        {!allComplete
                            ? <div className="mt-2 text-xs text-gray-500">{openCount} of {sessions.length} audits still open — complete them all to finish the run.</div>
                            : <div className="mt-2 text-xs text-green-600">All audits complete — ready to mark the run complete.</div>}
                      </div>}
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

TEMPLATE["supports_saved_runs"] = True
TEMPLATE["snapshot_inputs"] = {
    "workers": False,
    "pipelines": [],
    "state_keys": ["window_start", "window_end", "last_batch"],
}


def run_default(*, definition, access_token, request=None, window=None, **_):
    """Default-run hook: create and fire this week's audit batch for the
    definition's opportunity, with no UI.

    ``window`` defaults to ``resolve_window("last_week", today)``; the per-track
    sampling rates come from the definition's ``config.audit_batch`` defaults
    (the same values the UI pre-fills). Always creates a fresh run and fires it
    (no reuse). Returns ``{"run_id", "sessions_created", "status"}``.
    """
    from datetime import date

    from connect_labs.workflow.audit_generation import resolve_window, run_this_week_batch

    if window is None:
        window_start, window_end = resolve_window("last_week", date.today())
    else:
        window_start, window_end = window

    batch = (definition.data.get("config") or {}).get("audit_batch") or {}
    track_a = batch.get("track_a") or {}
    track_b = batch.get("track_b") or {}
    sample_overrides = {
        "muac_sample_percentage": track_a.get("sample_percentage", 100),
        "other_sample_percentage": track_b.get("sample_percentage", 10),
    }

    return run_this_week_batch(
        definition,
        window_start,
        window_end,
        access_token=access_token,
        sample_overrides=sample_overrides,
    )


TEMPLATE["supports_default_run"] = True
