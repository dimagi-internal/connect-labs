"""Muac Picture Audit — multi-opp, program-owned, interactive audit creator.

Re-implements the standalone /audit/create/ wizard (audit_creation_wizard.html)
as a workflow instance scoped to program 176's CHC PRE-RCT opportunities, with
one addition: Step 3 gets a date + day-of-week filter (see
connect_labs/audit/data_access.py AuditCriteria.days_of_week) alongside the
existing 4 audit-type options.

Faithfully replicates the reference wizard's behavior, including its known
per_opp-granularity no-op (per_opp behaves identically to combined — neither
run_audit_creation nor the sync/async create views special-case it) and the
single-first-opportunity image-extraction scoping in run_audit_creation for
combined/per_opp multi-opp sessions. This was an explicit choice (see PR
description) — not a bug introduced here.

Discovery (image types, field paths, deliver unit types, AI agents) and
preview all reuse the existing, already multi-opp-aware HTTP endpoints
(connect_labs/audit/views.py) via fetch() from the render code — no
duplicated backend logic. Creation itself goes through the workflow engine's
own job system (actions.startJob -> muac_picture_audit_create job handler ->
run_audit_creation), matching every other audit-creating workflow in this
codebase, rather than the wizard's own HTTP create endpoints.
"""

DEFINITION = {
    "name": "Muac Picture Audit",
    "description": "Create MUAC picture audits across program 176's opportunities, with full audit-creation "
    "controls (opportunity/granularity/criteria/FLW selection/field config) plus a date + day-of-week filter.",
    "version": 1,
    "templateType": "muac_picture_audit",
    "statuses": [
        {"id": "config", "label": "Configuring", "color": "gray"},
        {"id": "creating", "label": "Creating Audit", "color": "blue"},
        {"id": "created", "label": "Audit Created", "color": "green"},
        {"id": "failed", "label": "Failed", "color": "red"},
    ],
    "config": {
        "opp_names": {},  # { "<opp_id>": "Opp display name" } — seeded at creation time
    },
    "pipeline_sources": [],
}

RENDER_CODE = r"""function WorkflowUI({ definition, instance, actions, onUpdateState }) {

    function getCsrfToken() {
        return document.getElementById('workflow-root')?.dataset?.csrfToken
            || document.querySelector('[name=csrfmiddlewaretoken]')?.value
            || '';
    }

    // ── Fixed opportunity set (this workflow's whole multi_opp span) ──────────
    const oppNames = (definition.config && definition.config.opp_names) || {};
    const allOppIds = (instance.opportunity_ids && instance.opportunity_ids.length)
        ? instance.opportunity_ids
        : (instance.opportunity_id ? [instance.opportunity_id] : []);
    const oppLabel = (oid) => oppNames[String(oid)] || ('Opportunity ' + oid);

    // ── Step 1: Select Opportunities (subset of the fixed set for this run) ──
    const [selectedOppIds, setSelectedOppIds] = React.useState(allOppIds.slice());
    const toggleOpp = (oid) => setSelectedOppIds(prev =>
        prev.indexOf(oid) !== -1 ? prev.filter(x => x !== oid) : prev.concat([oid]));

    // ── Step 2: Granularity ────────────────────────────────────────────────
    // NOTE: per_opp intentionally behaves identically to combined here, exactly
    // matching the reference /audit/create/ wizard (neither run_audit_creation
    // nor the sync/async create views special-case "per_opp" — verified: zero
    // hits for it as a literal value anywhere in connect_labs/audit/*.py).
    const [granularity, setGranularity] = React.useState('combined');

    // ── Step 3: Configure Audit Criteria ──────────────────────────────────
    const [auditType, setAuditType] = React.useState('date_range');
    const [startDate, setStartDate] = React.useState('');
    const [endDate, setEndDate] = React.useState('');
    const ALL_WEEKDAYS = [1, 2, 3, 4, 5, 6, 7];
    const WEEKDAY_LABELS = { 1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday', 6: 'Saturday', 7: 'Sunday' };
    // Default "All" — every weekday selected, meaning no restriction.
    const [daysOfWeek, setDaysOfWeek] = React.useState(ALL_WEEKDAYS.slice());
    const allDaysSelected = daysOfWeek.length === ALL_WEEKDAYS.length;
    const toggleDay = (d) => setDaysOfWeek(prev =>
        prev.indexOf(d) !== -1 ? prev.filter(x => x !== d) : prev.concat([d]).sort());
    const toggleAllDays = () => setDaysOfWeek(allDaysSelected ? [] : ALL_WEEKDAYS.slice());

    const [countPerFlw, setCountPerFlw] = React.useState(100);
    const [allVisitsPerFlw, setAllVisitsPerFlw] = React.useState(false);
    const [countPerOpp, setCountPerOpp] = React.useState(100);
    const [countAcrossAll, setCountAcrossAll] = React.useState(100);
    const [samplePercentage, setSamplePercentage] = React.useState(100);

    const [deliverUnitTypes, setDeliverUnitTypes] = React.useState([]);
    const [availableDeliverUnitTypes, setAvailableDeliverUnitTypes] = React.useState([]);
    const deliverUnitTypesLoadedKeyRef = React.useRef('');

    const VISIT_STATUS_OPTIONS = [
        { value: 'pending', label: 'Pending' },
        { value: 'approved', label: 'Approved' },
        { value: 'rejected', label: 'Rejected' },
        { value: 'over_limit', label: 'Over Limit' },
        { value: 'duplicate', label: 'Duplicate' },
        { value: 'trial', label: 'Trial' },
    ];
    const [visitStatuses, setVisitStatuses] = React.useState([]);

    // Discovery: deliver unit types, unioned across selected opps, dedup-by-key.
    React.useEffect(() => {
        const key = selectedOppIds.slice().sort().join(',');
        if (!key || key === deliverUnitTypesLoadedKeyRef.current) return;
        deliverUnitTypesLoadedKeyRef.current = key;
        Promise.all(selectedOppIds.map(oid =>
            fetch('/audit/api/opportunity/' + oid + '/deliver-unit-types/').then(r => r.json()).catch(() => [])
        )).then(arrs => {
            const set = new Set();
            arrs.forEach(list => (list || []).forEach(t => set.add(t)));
            const all = Array.from(set).sort();
            setAvailableDeliverUnitTypes(all);
            setDeliverUnitTypes(prev => prev.filter(t => all.indexOf(t) !== -1));
        });
    }, [selectedOppIds.join(',')]);

    const isCriteriaValid = () => {
        if (auditType === 'date_range') return !!startDate && !!endDate;
        if (auditType === 'last_n_per_flw') return allVisitsPerFlw || Number(countPerFlw) > 0;
        if (auditType === 'last_n_per_opp') return Number(countPerOpp) > 0;
        if (auditType === 'last_n_across_all') return Number(countAcrossAll) > 0;
        return false;
    };

    const buildCriteriaForPreview = () => {
        const criteria = {
            audit_type: auditType,
            granularity: granularity,
            sample_percentage: Number(samplePercentage),
            countPerFlw: allVisitsPerFlw ? 99999 : Number(countPerFlw),
            countPerOpp: Number(countPerOpp),
            countAcrossAll: Number(countAcrossAll),
            deliver_unit_types: deliverUnitTypes,
            visit_statuses: visitStatuses,
        };
        if (auditType === 'date_range') {
            criteria.startDate = startDate;
            criteria.endDate = endDate;
            // "All" days selected = no restriction — send [] so the backend
            // filter is a no-op rather than (incorrectly) matching zero visits.
            criteria.days_of_week = allDaysSelected ? [] : daysOfWeek;
        }
        return criteria;
    };

    // ── Step 4: Preview — select FLWs to audit ────────────────────────────
    const [previewLoading, setPreviewLoading] = React.useState(false);
    const [previewError, setPreviewError] = React.useState(null);
    const [previewResults, setPreviewResults] = React.useState([]);
    const [precomputedVisitIds, setPrecomputedVisitIds] = React.useState([]);
    const [precomputedFlwVisitIds, setPrecomputedFlwVisitIds] = React.useState({});
    // FLW username -> the single opportunity that FLW's visits belong to. A program-owned
    // run spans multiple opportunities, and each FLW belongs to exactly one of them — this
    // lets audit creation scope each session to its FLW's real opportunity instead of
    // defaulting every session in the batch to the run's first selected opportunity.
    const [precomputedFlwOpportunityIds, setPrecomputedFlwOpportunityIds] = React.useState({});
    const [selectedFlwUserIds, setSelectedFlwUserIds] = React.useState([]);

    const getFlwId = (flw) => flw.connect_id || flw.username;
    const isFlwSelected = (flw) => selectedFlwUserIds.indexOf(getFlwId(flw)) !== -1;
    const toggleFlwSelection = (flw) => {
        const id = getFlwId(flw);
        setSelectedFlwUserIds(prev => prev.indexOf(id) !== -1 ? prev.filter(x => x !== id) : prev.concat([id]));
    };
    const areAllFlwsSelected = () => previewResults.length > 0 && previewResults.every(isFlwSelected);
    const toggleAllFlws = () => setSelectedFlwUserIds(areAllFlwsSelected() ? [] : previewResults.map(getFlwId));

    const updatePreview = () => {
        if (!isCriteriaValid() || selectedOppIds.length === 0) return;
        setPreviewLoading(true); setPreviewError(null);
        fetch('/audit/api/audit/preview/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ opportunities: selectedOppIds, criteria: buildCriteriaForPreview() }),
        }).then(r => r.json()).then(data => {
            setPreviewLoading(false);
            if (!data || !data.success) { setPreviewError((data && data.error) || 'Preview failed'); return; }
            const flws = (data.preview && data.preview.flws) || [];
            setPreviewResults(flws);
            setPrecomputedVisitIds((data.preview && data.preview.visit_ids) || []);
            const flwVisitIds = {};
            const flwOpportunityIds = {};
            flws.forEach(f => {
                flwVisitIds[f.username] = f.visit_ids || [];
                flwOpportunityIds[f.username] = f.opportunity_id;
            });
            setPrecomputedFlwVisitIds(flwVisitIds);
            setPrecomputedFlwOpportunityIds(flwOpportunityIds);
            setSelectedFlwUserIds(flws.map(getFlwId));
        }).catch(e => { setPreviewLoading(false); setPreviewError('Preview failed: ' + (e.message || e)); });
    };

    // ── Step 5: Audit Field Configuration ──────────────────────────────────
    const [availableImageTypes, setAvailableImageTypes] = React.useState([]);
    const imageTypesLoadedKeyRef = React.useRef('');
    const [selectedImagePaths, setSelectedImagePaths] = React.useState([]);
    const [imageReviewers, setImageReviewers] = React.useState({});
    const [availableFieldPaths, setAvailableFieldPaths] = React.useState([]);
    const fieldPathsLoadedKeyRef = React.useRef('');
    const [contextFields, setContextFields] = React.useState([]);
    const [availableAIAgents, setAvailableAIAgents] = React.useState([]);
    const aiAgentsLoadedRef = React.useRef(false);

    // This workflow only ever audits MUAC images — discovered image types are
    // filtered down to MUAC-matching fields (case-insensitive substring match
    // on label or path), and every match is auto-selected by default. Unlike
    // the reference wizard, an empty selection here does NOT mean "audit every
    // image" — see handleCreate's validation, which requires at least one.
    const isMuacImageType = (t) => {
        const hay = ((t.label || '') + ' ' + (t.path || '')).toLowerCase();
        return hay.indexOf('muac') !== -1;
    };

    React.useEffect(() => {
        const key = selectedOppIds.slice().sort().join(',');
        if (!key || key === imageTypesLoadedKeyRef.current) return;
        imageTypesLoadedKeyRef.current = key;
        Promise.all(selectedOppIds.map(oid =>
            fetch('/audit/api/opportunity/' + oid + '/image-questions/').then(r => r.json()).catch(() => [])
        )).then(arrs => {
            const seen = {}; const all = [];
            arrs.forEach(list => (list || []).forEach(t => { if (!seen[t.id]) { seen[t.id] = true; all.push(t); } }));
            const muacOnly = all.filter(isMuacImageType);
            setAvailableImageTypes(muacOnly);
            setSelectedImagePaths(muacOnly.map(t => t.path));
        });
    }, [selectedOppIds.join(',')]);

    React.useEffect(() => {
        const key = selectedOppIds.slice().sort().join(',');
        if (!key || key === fieldPathsLoadedKeyRef.current) return;
        fieldPathsLoadedKeyRef.current = key;
        Promise.all(selectedOppIds.map(oid =>
            fetch('/audit/api/opportunity/' + oid + '/field-questions/').then(r => r.json()).catch(() => [])
        )).then(arrs => {
            const seen = {}; const all = [];
            arrs.forEach(list => (list || []).forEach(t => { if (!seen[t.id]) { seen[t.id] = true; all.push(t); } }));
            setAvailableFieldPaths(all);
        });
    }, [selectedOppIds.join(',')]);

    React.useEffect(() => {
        if (aiAgentsLoadedRef.current) return;
        aiAgentsLoadedRef.current = true;
        fetch('/audit/api/ai-agents/').then(r => r.json()).then(data => setAvailableAIAgents((data && data.agents) || [])).catch(() => {});
    }, []);

    // Keep imageReviewers in lockstep with selectedImagePaths: add a slot for a
    // newly-checked path, drop the slot for an unchecked one.
    React.useEffect(() => {
        setImageReviewers(prev => {
            const next = {};
            selectedImagePaths.forEach(p => { next[p] = prev[p] || { agentId: '', config: {}, autoApplyActions: [] }; });
            return next;
        });
    }, [selectedImagePaths.join(',')]);

    const toggleImagePath = (path) => setSelectedImagePaths(prev =>
        prev.indexOf(path) !== -1 ? prev.filter(x => x !== path) : prev.concat([path]));

    const getAgentById = (id) => availableAIAgents.find(a => a.agent_id === id);
    const getAgentActions = (id) => {
        const agent = getAgentById(id);
        // result_actions comes back from the API as a dict keyed by action key
        // (e.g. {"fail_overzoomed": {ai_result, human_result, button_label}}),
        // not an array -- convert before anything iterates/.map()s over it.
        if (!agent || !agent.result_actions) return [];
        return Object.entries(agent.result_actions).map(([key, a]) => ({ key, ...a }));
    };
    const setReviewerAgent = (path, agentId) => setImageReviewers(prev => ({
        ...prev, [path]: { agentId: agentId, config: {}, autoApplyActions: [] },
    }));
    const setReviewerConfigField = (path, fieldKey, value) => setImageReviewers(prev => ({
        ...prev, [path]: { ...prev[path], config: { ...(prev[path] && prev[path].config), [fieldKey]: value } },
    }));
    const toggleReviewerAction = (path, actionKey) => setImageReviewers(prev => {
        const cur = (prev[path] && prev[path].autoApplyActions) || [];
        const next = cur.indexOf(actionKey) !== -1 ? cur.filter(x => x !== actionKey) : cur.concat([actionKey]);
        return { ...prev, [path]: { ...prev[path], autoApplyActions: next } };
    });

    const addContextField = () => setContextFields(prev => prev.concat([{ imagePath: '', fieldPath: '', label: '' }]));
    const updateContextField = (idx, key, value) => setContextFields(prev =>
        prev.map((cf, i) => i === idx ? { ...cf, [key]: value } : cf));
    const removeContextField = (idx) => setContextFields(prev => prev.filter((_, i) => i !== idx));

    const buildImageAudits = () => selectedImagePaths.map(p => {
        const r = imageReviewers[p] || {};
        const reviewers = r.agentId ? [{ agent_id: r.agentId, config: r.config || {}, auto_apply_actions: r.autoApplyActions || [] }] : [];
        return { image_path: p, reviewers: reviewers };
    });
    const buildContextFieldsPayload = () => contextFields
        .filter(cf => cf.imagePath && cf.fieldPath)
        .map(cf => ({ image_path: cf.imagePath, field_path: cf.fieldPath, label: cf.label || '' }));

    const reviewerValidationError = () => {
        for (const p of selectedImagePaths) {
            const r = imageReviewers[p];
            if (r && r.agentId) {
                const agent = getAgentById(r.agentId);
                const fields = (agent && agent.config_fields) || [];
                for (const f of fields) {
                    if (f.required && !(r.config && r.config[f.key])) {
                        return 'Please configure "' + (f.label || f.key) + '" for the reviewer on ' + p;
                    }
                }
            }
        }
        return null;
    };

    // ── Step 6: Metadata & Creation ────────────────────────────────────────
    const [auditTitle, setAuditTitle] = React.useState('');
    const [auditTag, setAuditTag] = React.useState('muac');
    const [auditPassThreshold, setAuditPassThreshold] = React.useState(100);
    const [excludePriorAudited, setExcludePriorAudited] = React.useState(false);
    const [isRunning, setIsRunning] = React.useState(false);
    const [progress, setProgress] = React.useState(null);
    const [jobError, setJobError] = React.useState(null);
    const cleanupRef = React.useRef(null);
    React.useEffect(() => () => { if (cleanupRef.current) cleanupRef.current(); }, []);

    const attachStream = (taskId) => {
        const cleanup = actions.streamJobProgress(
            taskId,
            (p) => setProgress(p),
            null,
            async (results) => {
                setIsRunning(false);
                setProgress({ status: 'completed', ...results });
                onUpdateState({
                    last_batch: {
                        sessions_created: (results && results.sessions_created) || 0,
                        title: auditTitle,
                        tag: auditTag,
                        opportunity_ids: selectedOppIds,
                        created_at: new Date().toISOString(),
                    },
                }).catch(() => {});
                await refreshSessions();
            },
            (err) => { setIsRunning(false); setJobError(err || 'Job failed'); setProgress(null); },
            () => { setIsRunning(false); setProgress({ status: 'cancelled' }); },
            instance.id
        );
        cleanupRef.current = cleanup;
    };

    // Reconnect to a still-running job after a page reload (same staleness
    // guard as weekly_dual_track_audit — a worker killed mid-batch leaves
    // active_job stuck at 'running' forever; Celery can't tell a dead task
    // from a queued one, so trust the timestamp instead of spinning forever).
    const STALE_JOB_MS = 15 * 60 * 1000;
    const [staleJob, setStaleJob] = React.useState(false);
    React.useEffect(() => {
        const active = instance.state && instance.state.active_job;
        if (!(active && active.status === 'running' && active.job_id)) return;
        const startedMs = active.started_at ? Date.parse(active.started_at) : NaN;
        const age = isNaN(startedMs) ? Infinity : (Date.now() - startedMs);
        if (age > STALE_JOB_MS) { setStaleJob(true); return; }
        setIsRunning(true);
        setProgress({ status: 'running', message: 'Reconnecting to the running job…' });
        attachStream(active.job_id);
    }, []);

    const handleCreate = async () => {
        if (!isCriteriaValid() || isRunning || selectedOppIds.length === 0 || selectedFlwUserIds.length === 0) return;
        if (selectedImagePaths.length === 0) {
            setJobError('Select at least one MUAC image type — this workflow only audits MUAC images.');
            return;
        }
        const revErr = reviewerValidationError();
        if (revErr) { setJobError(revErr); return; }
        setIsRunning(true); setJobError(null); setStaleJob(false);
        setProgress({ status: 'starting', message: 'Submitting to the server…' });

        const selectedUsernames = previewResults.filter(isFlwSelected).map(f => f.username);
        const flwVisitIds = {};
        const flwOpportunityIds = {};
        selectedUsernames.forEach(u => {
            if (precomputedFlwVisitIds[u]) flwVisitIds[u] = precomputedFlwVisitIds[u];
            if (precomputedFlwOpportunityIds[u] != null) flwOpportunityIds[u] = precomputedFlwOpportunityIds[u];
        });

        const criteria = {
            ...buildCriteriaForPreview(),
            title: auditTitle,
            tag: auditTag,
            pass_threshold: Number(auditPassThreshold),
            exclude_prior_audited: excludePriorAudited,
            selected_flw_user_ids: selectedFlwUserIds,
        };
        const opportunities = selectedOppIds.map(oid => ({ id: oid, name: oppLabel(oid) }));

        let resp;
        try {
            resp = await actions.startJob(instance.id, {
                job_type: 'muac_picture_audit_create',
                run_id: instance.id,
                opportunity_id: instance.opportunity_id,
                program_id: instance.program_id,
                opportunities: opportunities,
                criteria: criteria,
                visit_ids: precomputedVisitIds,
                flw_visit_ids: flwVisitIds,
                flw_opportunity_ids: flwOpportunityIds,
                image_audits: buildImageAudits(),
                context_fields: buildContextFieldsPayload(),
            });
        } catch (e) {
            setIsRunning(false); setJobError('Failed to start job: ' + (e.message || e)); return;
        }
        if (!resp || !resp.success || !resp.task_id) {
            setIsRunning(false); setJobError((resp && resp.error) || 'Failed to start job'); return;
        }
        setProgress({ status: 'running', message: 'Starting…' });
        attachStream(resp.task_id);
    };

    // ── Created sessions (view-only summary once a batch exists) ──────────
    const [sessions, setSessions] = React.useState([]);
    const [loadingSessions, setLoadingSessions] = React.useState(true);
    const refreshSessions = () => {
        if (!instance.id || !allOppIds.length) { setLoadingSessions(false); return Promise.resolve([]); }
        return Promise.all(allOppIds.map(opp =>
            fetch('/audit/api/workflow/' + instance.id + '/sessions/?opportunity_id=' + opp)
                .then(res => res.json())
                .then(data => (data.success && data.sessions) ? data.sessions : [])
                .catch(() => [])
        )).then(arrs => {
            const seen = {}; const all = [];
            arrs.forEach(list => list.forEach(s => { if (!seen[s.id]) { seen[s.id] = true; all.push(s); } }));
            setSessions(all); setLoadingSessions(false); return all;
        }).catch(() => { setLoadingSessions(false); return []; });
    };
    React.useEffect(() => { refreshSessions(); }, [instance.id]);

    const runState = instance.state || {};
    const lastBatch = runState.last_batch || null;
    const hasCreated = !!lastBatch;
    const viewOnly = hasCreated && !isRunning;

    // ── Small render helpers ───────────────────────────────────────────────
    const checkboxRow = (checked, onChange, label, key) => (
        <label key={key} className="flex items-center gap-2 text-sm text-gray-700 py-0.5">
            <input type="checkbox" checked={checked} onChange={onChange} className="w-4 h-4" />
            {label}
        </label>
    );

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                <p className="text-gray-600 mt-1">{definition.description}</p>
            </div>

            {viewOnly && (
                <div className="bg-white rounded-lg shadow-sm p-6">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                        <h3 className="text-sm font-medium text-gray-700">
                            <i className="fa-solid fa-lock mr-2 text-gray-400"></i>Audit created — view only
                        </h3>
                        <span className="text-xs px-2 py-1 rounded bg-blue-50 text-blue-700"><i className="fa-solid fa-circle-check mr-1"></i>Created</span>
                    </div>
                    <p className="text-sm text-gray-600 mt-2">
                        <span className="font-medium text-gray-900">{lastBatch.sessions_created}</span> audit session{lastBatch.sessions_created === 1 ? '' : 's'} created
                        {lastBatch.title ? (' — "' + lastBatch.title + '"') : ''} across {(lastBatch.opportunity_ids || []).length} opportunit{(lastBatch.opportunity_ids || []).length === 1 ? 'y' : 'ies'}.
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                        This run already created its audit(s) — start a new run from the workflow list to create more.
                    </p>
                </div>
            )}

            {!viewOnly && (
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-circle-1 mr-2 text-gray-400"></i>Step 1 — Select Opportunities
                </h3>
                <div className="space-y-1">
                    {allOppIds.map(oid => checkboxRow(
                        selectedOppIds.indexOf(oid) !== -1,
                        () => toggleOpp(oid),
                        oppLabel(oid) + '  #' + oid,
                        oid
                    ))}
                </div>
            </div>
            )}

            {!viewOnly && selectedOppIds.length > 0 && (
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-circle-2 mr-2 text-gray-400"></i>Step 2 — Select Audit Granularity
                </h3>
                <div className="space-y-2">
                    <label className="flex items-start gap-2">
                        <input type="radio" name="granularity" checked={granularity === 'combined'} onChange={() => setGranularity('combined')} className="mt-1" />
                        <span><span className="text-sm font-medium text-gray-800">One audit for all selected opportunities</span></span>
                    </label>
                    <label className="flex items-start gap-2">
                        <input type="radio" name="granularity" checked={granularity === 'per_opp'} onChange={() => setGranularity('per_opp')} className="mt-1" />
                        <span><span className="text-sm font-medium text-gray-800">One audit per opportunity</span></span>
                    </label>
                    <label className="flex items-start gap-2">
                        <input type="radio" name="granularity" checked={granularity === 'per_flw'} onChange={() => setGranularity('per_flw')} className="mt-1" />
                        <span><span className="text-sm font-medium text-gray-800">One audit per FLW</span></span>
                    </label>
                </div>
            </div>
            )}

            {!viewOnly && selectedOppIds.length > 0 && (
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-circle-3 mr-2 text-gray-400"></i>Step 3 — Configure Audit Criteria
                </h3>
                <div className="space-y-4">
                    <label className="flex items-start gap-2">
                        <input type="radio" name="audit-type" checked={auditType === 'date_range'} onChange={() => setAuditType('date_range')} className="mt-1" />
                        <span className="flex-1">
                            <span className="text-sm font-medium text-gray-800">Date Range</span>
                            {auditType === 'date_range' && (
                                <div className="mt-2 space-y-3">
                                    <div className="flex gap-4 items-center flex-wrap">
                                        <div>
                                            <label className="block text-xs text-gray-500 mb-1">Start Date</label>
                                            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
                                                className="border border-gray-300 rounded px-3 py-2 text-sm" />
                                        </div>
                                        <div>
                                            <label className="block text-xs text-gray-500 mb-1">End Date</label>
                                            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
                                                className="border border-gray-300 rounded px-3 py-2 text-sm" />
                                        </div>
                                    </div>
                                    <div>
                                        <label className="block text-xs text-gray-500 mb-2">
                                            Day of Week
                                            <span className="ml-1 text-gray-400">— visits in the date range above are further restricted to these weekdays.</span>
                                        </label>
                                        <div className="flex flex-wrap gap-3 items-center">
                                            <label className="flex items-center gap-1.5 text-sm text-gray-800 font-medium">
                                                <input type="checkbox" checked={allDaysSelected} onChange={toggleAllDays} className="w-4 h-4" />
                                                All
                                            </label>
                                            {ALL_WEEKDAYS.map(d => (
                                                <label key={d} className="flex items-center gap-1.5 text-sm text-gray-700">
                                                    <input type="checkbox" checked={daysOfWeek.indexOf(d) !== -1} onChange={() => toggleDay(d)} className="w-4 h-4" />
                                                    {WEEKDAY_LABELS[d]}
                                                </label>
                                            ))}
                                        </div>
                                        <p className="text-xs text-gray-400 mt-1">
                                            Example: start 2026-01-01, end 2026-01-31, day Friday — audits every Friday visit in January 2026.
                                        </p>
                                    </div>
                                </div>
                            )}
                        </span>
                    </label>

                    <label className="flex items-start gap-2">
                        <input type="radio" name="audit-type" checked={auditType === 'last_n_per_flw'} onChange={() => setAuditType('last_n_per_flw')} className="mt-1" />
                        <span className="flex-1">
                            <span className="text-sm font-medium text-gray-800">Last N Visits Per FLW</span>
                            {auditType === 'last_n_per_flw' && (
                                <div className="mt-2 flex items-center gap-3">
                                    <input type="number" min="1" max="1000" value={countPerFlw} disabled={allVisitsPerFlw}
                                        onChange={e => setCountPerFlw(e.target.value)}
                                        className="border border-gray-300 rounded px-3 py-2 text-sm w-24 disabled:bg-gray-100" />
                                    <label className="flex items-center gap-1.5 text-sm text-gray-700">
                                        <input type="checkbox" checked={allVisitsPerFlw}
                                            onChange={e => { setAllVisitsPerFlw(e.target.checked); setCountPerFlw(e.target.checked ? 99999 : 100); }}
                                            className="w-4 h-4" />
                                        All visits
                                    </label>
                                </div>
                            )}
                        </span>
                    </label>

                    <label className="flex items-start gap-2">
                        <input type="radio" name="audit-type" checked={auditType === 'last_n_per_opp'} onChange={() => setAuditType('last_n_per_opp')} className="mt-1" />
                        <span className="flex-1">
                            <span className="text-sm font-medium text-gray-800">Last N Visits Per Opportunity</span>
                            {auditType === 'last_n_per_opp' && (
                                <div className="mt-2">
                                    <input type="number" min="1" max="10000" value={countPerOpp}
                                        onChange={e => setCountPerOpp(e.target.value)}
                                        className="border border-gray-300 rounded px-3 py-2 text-sm w-24" />
                                </div>
                            )}
                        </span>
                    </label>

                    <label className="flex items-start gap-2">
                        <input type="radio" name="audit-type" checked={auditType === 'last_n_across_all'} onChange={() => setAuditType('last_n_across_all')} className="mt-1" />
                        <span className="flex-1">
                            <span className="text-sm font-medium text-gray-800">Last N Visits Across All Selected Opportunities</span>
                            {auditType === 'last_n_across_all' && (
                                <div className="mt-2">
                                    <input type="number" min="1" max="10000" value={countAcrossAll}
                                        onChange={e => setCountAcrossAll(e.target.value)}
                                        className="border border-gray-300 rounded px-3 py-2 text-sm w-24" />
                                </div>
                            )}
                        </span>
                    </label>

                    <div className="border-t border-gray-100 pt-4">
                        <label className="block text-xs text-gray-500 mb-2">Sample Percentage</label>
                        <div className="flex items-center gap-4">
                            <input type="range" min="1" max="100" step="1" value={samplePercentage}
                                onChange={e => setSamplePercentage(e.target.value)} className="flex-1" />
                            <span className="text-sm font-medium text-gray-800 w-12 text-right">{samplePercentage}%</span>
                        </div>
                        <div className="flex gap-2 mt-2">
                            {[25, 50, 75, 100].map(p => (
                                <button key={p} onClick={() => setSamplePercentage(p)}
                                    className="px-2 py-1 text-xs rounded border border-gray-300 hover:border-blue-400">{p}%</button>
                            ))}
                        </div>
                    </div>

                    <div className="border-t border-gray-100 pt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label className="block text-xs text-gray-500 mb-2">Deliver Unit Type</label>
                            {availableDeliverUnitTypes.length === 0
                                ? <p className="text-xs text-gray-400 italic">No deliver unit types discovered yet.</p>
                                : availableDeliverUnitTypes.map(t => checkboxRow(
                                    deliverUnitTypes.indexOf(t) !== -1,
                                    () => setDeliverUnitTypes(prev => prev.indexOf(t) !== -1 ? prev.filter(x => x !== t) : prev.concat([t])),
                                    t, t
                                ))}
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-2">Visit Type</label>
                            {VISIT_STATUS_OPTIONS.map(o => checkboxRow(
                                visitStatuses.indexOf(o.value) !== -1,
                                () => setVisitStatuses(prev => prev.indexOf(o.value) !== -1 ? prev.filter(x => x !== o.value) : prev.concat([o.value])),
                                o.label, o.value
                            ))}
                        </div>
                    </div>

                    <div className="border-t border-gray-100 pt-4">
                        <button onClick={updatePreview} disabled={!isCriteriaValid() || previewLoading}
                            className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 text-sm font-medium">
                            {previewLoading
                                ? <span><i className="fa-solid fa-spinner fa-spin mr-2"></i>Loading preview…</span>
                                : <span><i className="fa-solid fa-magnifying-glass mr-2"></i>Update Preview</span>}
                        </button>
                        {previewError && <p className="text-sm text-red-600 mt-2">{previewError}</p>}
                    </div>
                </div>
            </div>
            )}

            {!viewOnly && previewResults.length > 0 && (
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-circle-4 mr-2 text-gray-400"></i>Step 4 — Preview: Select FLWs to Audit
                </h3>
                <p className="text-sm text-gray-600 mb-3">
                    {previewResults.length} field worker{previewResults.length === 1 ? '' : 's'} · {previewResults.reduce((sum, f) => sum + (f.visit_count || 0), 0)} visits total
                </p>
                <table className="w-full text-sm">
                    <thead>
                        <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
                            <th className="py-2 pr-2"><input type="checkbox" checked={areAllFlwsSelected()} onChange={toggleAllFlws} className="w-4 h-4" /></th>
                            <th className="py-2 pr-2">FLW Name</th>
                            <th className="py-2 pr-2">Connect ID</th>
                            <th className="py-2 pr-2">Visits in Audit</th>
                            <th className="py-2 pr-2">Date Range</th>
                        </tr>
                    </thead>
                    <tbody>
                        {previewResults.map(flw => (
                            <tr key={getFlwId(flw)} className="border-b border-gray-100">
                                <td className="py-2 pr-2"><input type="checkbox" checked={isFlwSelected(flw)} onChange={() => toggleFlwSelection(flw)} className="w-4 h-4" /></td>
                                <td className="py-2 pr-2">{flw.name || flw.username}</td>
                                <td className="py-2 pr-2 font-mono text-xs">{flw.connect_id || flw.user_id}</td>
                                <td className="py-2 pr-2">{flw.visit_count}</td>
                                <td className="py-2 pr-2 text-xs text-gray-500">
                                    {flw.earliest_visit ? new Date(flw.earliest_visit).toLocaleDateString() : '—'} – {flw.latest_visit ? new Date(flw.latest_visit).toLocaleDateString() : '—'}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            )}

            {!viewOnly && previewResults.length > 0 && (
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-circle-5 mr-2 text-gray-400"></i>Step 5 — Audit Field Configuration
                </h3>
                <p className="text-xs text-gray-500 mb-3">
                    Only MUAC image types are shown and audited — this workflow never audits other image types.
                    All discovered MUAC fields are selected by default; uncheck any you want to exclude.
                </p>
                <div className="space-y-3">
                    {availableImageTypes.map(t => (
                        <div key={t.path} className="border border-gray-200 rounded-lg p-3">
                            <label className="flex items-center gap-2 text-sm text-gray-800 font-medium">
                                <input type="checkbox" checked={selectedImagePaths.indexOf(t.path) !== -1}
                                    onChange={() => toggleImagePath(t.path)} className="w-4 h-4" />
                                {t.label} <span className="text-xs text-gray-400 font-mono">{t.path}</span>
                            </label>
                            {selectedImagePaths.indexOf(t.path) !== -1 && (
                                <div className="mt-2 ml-6 space-y-2">
                                    <select value={(imageReviewers[t.path] && imageReviewers[t.path].agentId) || ''}
                                        onChange={e => setReviewerAgent(t.path, e.target.value)}
                                        className="border border-gray-300 rounded px-2 py-1 text-sm">
                                        <option value="">No AI reviewer</option>
                                        {availableAIAgents.map(a => <option key={a.agent_id} value={a.agent_id}>{a.name}</option>)}
                                    </select>
                                    {imageReviewers[t.path] && imageReviewers[t.path].agentId && (
                                        <div className="space-y-1">
                                            {(getAgentById(imageReviewers[t.path].agentId).config_fields || []).map(f => (
                                                <div key={f.key} className="flex items-center gap-2">
                                                    <label className="text-xs text-gray-500 w-32">{f.label || f.key}</label>
                                                    {f.type === 'form_field'
                                                        ? <select value={(imageReviewers[t.path].config || {})[f.key] || ''}
                                                            onChange={e => setReviewerConfigField(t.path, f.key, e.target.value)}
                                                            className="border border-gray-300 rounded px-2 py-1 text-xs flex-1">
                                                            <option value="">— select field —</option>
                                                            {availableFieldPaths.map(fp => <option key={fp.path} value={fp.path}>{fp.label}</option>)}
                                                        </select>
                                                        : <input type="text" value={(imageReviewers[t.path].config || {})[f.key] || ''}
                                                            onChange={e => setReviewerConfigField(t.path, f.key, e.target.value)}
                                                            className="border border-gray-300 rounded px-2 py-1 text-xs flex-1" />}
                                                </div>
                                            ))}
                                            <div className="flex flex-wrap gap-2 mt-1">
                                                {getAgentActions(imageReviewers[t.path].agentId).map(action => (
                                                    <label key={action.key} className="flex items-center gap-1 text-xs text-gray-600">
                                                        <input type="checkbox"
                                                            checked={(imageReviewers[t.path].autoApplyActions || []).indexOf(action.key) !== -1}
                                                            onChange={() => toggleReviewerAction(t.path, action.key)} className="w-3.5 h-3.5" />
                                                        Automatically apply {action.button_label || action.key}
                                                    </label>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    ))}
                    {availableImageTypes.length === 0 && <p className="text-xs text-gray-400 italic">No MUAC image types discovered yet for the selected opportunities.</p>}
                </div>

                <details className="mt-4">
                    <summary className="text-sm text-gray-600 cursor-pointer">Context Fields (optional)</summary>
                    <div className="mt-2 space-y-2">
                        {contextFields.map((cf, idx) => (
                            <div key={idx} className="flex items-center gap-2">
                                <select value={cf.imagePath} onChange={e => updateContextField(idx, 'imagePath', e.target.value)}
                                    className="border border-gray-300 rounded px-2 py-1 text-xs">
                                    <option value="">— image —</option>
                                    {availableImageTypes.map(t => <option key={t.path} value={t.path}>{t.label}</option>)}
                                </select>
                                <select value={cf.fieldPath} onChange={e => updateContextField(idx, 'fieldPath', e.target.value)}
                                    className="border border-gray-300 rounded px-2 py-1 text-xs">
                                    <option value="">— field —</option>
                                    {availableFieldPaths.map(fp => <option key={fp.path} value={fp.path}>{fp.label}</option>)}
                                </select>
                                <input type="text" placeholder="Label" value={cf.label} onChange={e => updateContextField(idx, 'label', e.target.value)}
                                    className="border border-gray-300 rounded px-2 py-1 text-xs flex-1" />
                                <button onClick={() => removeContextField(idx)} className="text-red-500 text-xs px-2">Remove</button>
                            </div>
                        ))}
                        <button onClick={addContextField} className="text-xs text-blue-600 hover:underline">+ Add context field</button>
                    </div>
                </details>
            </div>
            )}

            {!viewOnly && previewResults.length > 0 && (
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-circle-6 mr-2 text-gray-400"></i>Step 6 — Audit Metadata &amp; Creation
                </h3>
                <div className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">Title Suffix</label>
                            <input type="text" value={auditTitle} onChange={e => setAuditTitle(e.target.value)}
                                className="border border-gray-300 rounded px-3 py-2 text-sm w-full" />
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">Audit Tag</label>
                            <input type="text" value={auditTag} onChange={e => setAuditTag(e.target.value)}
                                className="border border-gray-300 rounded px-3 py-2 text-sm w-full" />
                        </div>
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Pass Threshold ({auditPassThreshold}%)</label>
                        <input type="range" min="75" max="100" step="1" value={auditPassThreshold}
                            onChange={e => setAuditPassThreshold(e.target.value)} className="w-64" />
                    </div>
                    <label className="flex items-center gap-2 text-sm text-gray-700">
                        <input type="checkbox" checked={excludePriorAudited} onChange={e => setExcludePriorAudited(e.target.checked)} className="w-4 h-4" />
                        Exclude images already audited in a completed session
                    </label>

                    <button onClick={handleCreate}
                        disabled={!isCriteriaValid() || isRunning || selectedOppIds.length === 0 || selectedFlwUserIds.length === 0 || selectedImagePaths.length === 0 || instance.status === 'completed'}
                        className="inline-flex items-center px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 font-medium">
                        {isRunning
                            ? <span><i className="fa-solid fa-spinner fa-spin mr-2"></i>Creating…</span>
                            : <span><i className="fa-solid fa-play mr-2"></i>Create Audit</span>}
                    </button>

                    {isRunning && progress && (
                        <div className="mt-2 bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800">
                            <div className="flex items-center font-medium">
                                <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                                {progress.message || progress.stage_name || 'Working…'}
                                {progress.total > 0 && <span className="ml-2 text-blue-600">({progress.processed || 0}/{progress.total})</span>}
                            </div>
                        </div>
                    )}
                    {jobError && (
                        <div className="mt-2 bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
                            <i className="fa-solid fa-circle-exclamation mr-2"></i>{jobError}
                        </div>
                    )}
                    {staleJob && !isRunning && (
                        <div className="mt-2 bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-800">
                            <i className="fa-solid fa-triangle-exclamation mr-2"></i>
                            The previous audit run didn't finish — click <strong>Create Audit</strong> to run it again.
                        </div>
                    )}
                </div>
            </div>
            )}

            {/* ── Audit results by field worker (shared primitive) ──────────── */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                {window.LabsAudit
                    ? window.LabsAudit.renderFlwBreakdown(React, {
                        sessions: sessions,
                        oppNames: oppNames,
                        workflowRunId: instance.id,
                        loading: loadingSessions,
                        emptyText: 'No sessions yet — configure and create an audit above.',
                      })
                    : <div className="text-sm text-gray-500">Loading…</div>}
            </div>
        </div>
    );
}"""

TEMPLATE = {
    "key": "muac_picture_audit",
    "name": "Muac Picture Audit",
    "description": DEFINITION["description"],
    "icon": "fa-images",
    "color": "purple",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": None,
}
