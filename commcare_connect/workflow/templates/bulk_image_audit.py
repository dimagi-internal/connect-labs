"""
Bulk Image Audit Workflow Template.

Single-opportunity image review with per-FLW pass/fail summary.
Image types are discovered dynamically from Connect blob data.
"""

DEFINITION = {
    "name": "Bulk Image Audit",
    "description": "Review photos for an opportunity with per-FLW pass/fail tracking",
    "version": 1,
    "templateType": "bulk_image_audit",
    "statuses": [
        {"id": "config", "label": "Configuring", "color": "gray"},
        {"id": "creating", "label": "Creating Review", "color": "blue"},
        {"id": "in_progress", "label": "In Review", "color": "yellow"},
        {"id": "completed", "label": "Completed", "color": "green"},
        {"id": "failed", "label": "Failed", "color": "red"},
    ],
    "config": {
        "showSummaryCards": True,
    },
    "pipeline_sources": [],
}

RENDER_CODE = """function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {

    // ── Phase (drives which section renders) ────────────────────────────────
    const [phase, setPhase] = React.useState(instance.state?.phase || 'config');

    // ── CSRF helper ─────────────────────────────────────────────────────────
    function getCsrfToken() {
        return document.getElementById('workflow-root')?.dataset?.csrfToken
            || document.querySelector('[name=csrfmiddlewaretoken]')?.value
            || '';
    }

    // ── Config state ────────────────────────────────────────────────────────
    const [selectedOpps, setSelectedOpps] = React.useState(
        instance.state?.config?.selected_opps || []
    );
    const [auditMode, setAuditMode] = React.useState(
        instance.state?.config?.audit_mode || 'date_range'
    );
    const [startDate, setStartDate] = React.useState(
        instance.state?.config?.start_date || ''
    );
    const [endDate, setEndDate] = React.useState(
        instance.state?.config?.end_date || ''
    );
    const [datePreset, setDatePreset] = React.useState(
        instance.state?.config?.date_preset || 'last_week'
    );
    const [lastNCount, setLastNCount] = React.useState(
        instance.state?.config?.count_per_opp || 10
    );
    const [samplePct, setSamplePct] = React.useState(
        instance.state?.config?.sample_percentage ?? 100
    );
    const [threshold, setThreshold] = React.useState(
        instance.state?.config?.threshold ?? 80
    );

    // ── Dynamic image type state ─────────────────────────────────────────────
    const [imageQuestions, setImageQuestions] = React.useState(
        instance.state?.config?.image_questions || []
    );
    const [imageQuestionsLoading, setImageQuestionsLoading] = React.useState(false);
    const [imageQuestionsError, setImageQuestionsError] = React.useState(null);
    const [selectedImageTypeIds, setSelectedImageTypeIds] = React.useState(
        instance.state?.config?.selected_image_type_ids || []
    );

    // ── Date helpers ─────────────────────────────────────────────────────────
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
            case 'this_month':
                start = new Date(today.getFullYear(), today.getMonth(), 1);
                end = new Date(today); end.setDate(today.getDate() - 1); break;
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

    // Set default date range on mount
    React.useEffect(() => {
        if (!startDate && !endDate) applyPreset('last_week');
    }, []);

    // Auto-populate opportunity from context on mount (if not already set from saved state)
    React.useEffect(() => {
        if (selectedOpps.length === 0 && instance.opportunity_id && instance.opportunity_name) {
            setSelectedOpps([{ id: instance.opportunity_id, name: instance.opportunity_name }]);
        }
    }, []);

    // Fetch image question types from Connect data on mount
    React.useEffect(() => {
        const oppId = instance.opportunity_id;
        if (!oppId) return;
        // If already loaded from saved state, skip
        if (imageQuestions.length > 0) return;
        setImageQuestionsLoading(true);
        setImageQuestionsError(null);
        fetch('/audit/api/opportunity/' + oppId + '/image-questions/')
            .then(async r => {
                if (!r.ok) {
                    // Try to get a descriptive error from the JSON response body
                    let msg = 'HTTP ' + r.status;
                    try {
                        const errData = await r.json();
                        if (errData.error) msg = errData.error;
                    } catch (_) {}
                    throw new Error(msg);
                }
                return r.json();
            })
            .then(data => {
                setImageQuestions(data);
                setImageQuestionsLoading(false);
            })
            .catch(err => {
                setImageQuestionsError('Failed to load image types: ' + err.message);
                setImageQuestionsLoading(false);
            });
    }, []);

    // ── Execution state ──────────────────────────────────────────────────────
    const [isRunning, setIsRunning] = React.useState(false);
    const [isCancelling, setIsCancelling] = React.useState(false);
    const [progress, setProgress] = React.useState(null);
    const [taskId, setTaskId] = React.useState(null);
    const cleanupRef = React.useRef(null);
    const cancelledRef = React.useRef(false); // Set when user cancels before taskId is known

    // Cleanup SSE on unmount
    React.useEffect(() => {
        return () => { if (cleanupRef.current) cleanupRef.current(); };
    }, []);

    // ── Linked sessions state ─────────────────────────────────────────────────
    const [linkedSessions, setLinkedSessions] = React.useState([]);
    const [loadingSessions, setLoadingSessions] = React.useState(true);

    // Fetch sessions on mount
    React.useEffect(() => {
        if (!instance.id) { setLoadingSessions(false); return; }
        fetch('/audit/api/workflow/' + instance.id + '/sessions/')
            .then(res => res.json())
            .then(data => {
                if (data.success && data.sessions) setLinkedSessions(data.sessions);
                setLoadingSessions(false);
            })
            .catch(() => setLoadingSessions(false));
    }, [instance.id]);

    const refreshSessions = () => {
        if (!instance.id) return Promise.resolve([]);
        return fetch('/audit/api/workflow/' + instance.id + '/sessions/')
            .then(res => res.json())
            .then(data => {
                const sessions = data.sessions || [];
                if (data.success) setLinkedSessions(sessions);
                return sessions;
            })
            .catch(() => []);
    };

    // Reconnect to running job on page load (if user refreshed mid-creation)
    React.useEffect(() => {
        try {
            const activeJob = instance.state?.active_job;
            if (!actions?.streamAuditProgress) return;
            if (activeJob?.status === 'running' && activeJob?.job_id) {
                setIsRunning(true);
                setTaskId(activeJob.job_id);
                setProgress({
                    status: 'running',
                    stage_name: activeJob.stage_name || 'Processing',
                    processed: activeJob.processed || 0,
                    total: activeJob.total || 0,
                });
                const cleanup = actions.streamAuditProgress(
                    activeJob.job_id,
                    (p) => setProgress(p),
                    async (final) => {
                        setIsRunning(false);
                        setProgress({ status: 'completed', ...final });
                        const sessions = await refreshSessions();
                        const config = instance.state?.config || {};
                        try {
                            await onUpdateState({
                                phase: 'reviewing',
                                status: 'in_progress',
                                flw_count: sessions.length,
                                images_reviewed: sessions.reduce((sum, s) => sum + (s.image_count || s.assessment_stats?.total || 0), 0),
                                tasks_created: 0,
                                period_start: config.start_date || null,
                                period_end: config.end_date || null,
                                active_job: {
                                    job_id: activeJob.job_id,
                                    status: 'completed',
                                    completed_at: new Date().toISOString(),
                                },
                            });
                        } catch (e) { /* non-fatal */ }
                        if (sessions.length > 0) {
                            const s = sessions[0];
                            const params = new URLSearchParams();
                            if (s.opportunity_id) params.set('opportunity_id', s.opportunity_id);
                            params.set('threshold', config.threshold || threshold);
                            if (instance.id) params.set('workflow_run_id', instance.id);
                            window.location.href = '/audit/' + s.id + '/bulk/?' + params.toString();
                        } else {
                            setPhase('reviewing');
                        }
                    },
                    (err) => {
                        setIsRunning(false);
                        setProgress({ status: 'failed', error: err });
                        onUpdateState({
                            active_job: { job_id: activeJob.job_id, status: 'failed', error: err },
                        }).catch(() => {});
                    }
                );
                cleanupRef.current = cleanup;
                return () => { if (cleanup) cleanup(); };
            }
        } catch (err) {
            console.error('[BulkImageAudit] Reconnect error:', err);
        }
    }, []); // Run once on mount

    // ── Create handler ───────────────────────────────────────────────────────
    const handleCreate = async () => {
        cancelledRef.current = false; // Reset cancellation flag for new creation attempt
        if (selectedOpps.length === 0) return;
        if (selectedImageTypeIds.length === 0) return;

        const selectedTypes = imageQuestions.filter(q => selectedImageTypeIds.includes(q.id));

        const config = {
            selected_opps: selectedOpps,
            image_questions: imageQuestions,
            selected_image_type_ids: selectedImageTypeIds,
            image_types: selectedTypes.map(t => ({ id: t.id, label: t.label })),
            audit_mode: auditMode,
            start_date: auditMode === 'date_range' ? startDate : null,
            end_date: auditMode === 'date_range' ? endDate : null,
            count_per_opp: auditMode === 'last_n_per_opp' ? lastNCount : null,
            sample_percentage: samplePct,
            threshold: threshold,
            date_preset: datePreset,
        };

        setIsRunning(true);
        setProgress({ status: 'starting', stage_name: 'Initializing', message: 'Submitting to task queue...' });
        setPhase('creating');

        await onUpdateState({
            phase: 'creating',
            config,
            sample_percentage: samplePct,
            pass_threshold: threshold,
            // Store the NM's username so it can be shown in the Audit of Audits
            // admin report under "Run By". instance.username is the top-level
            // username from the LabsRecord API response (the authenticated creator).
            run_by: instance.username || null,
        });

        const criteria = {
            audit_type: auditMode,
            start_date: auditMode === 'date_range' ? startDate : null,
            end_date: auditMode === 'date_range' ? endDate : null,
            count_per_opp: auditMode === 'last_n_per_opp' ? lastNCount : null,
            sample_percentage: samplePct,
            related_fields: selectedTypes.map(t => ({
                image_path: t.path,
                filter_by_image: true,
            })),
        };

        try {
            const result = await actions.createAudit({
                opportunities: selectedOpps,
                criteria,
                workflow_run_id: instance.id,
            });

            if (result.success && result.task_id) {
                setTaskId(result.task_id);

                // If user cancelled while we were waiting for task creation, cancel it now
                if (cancelledRef.current) {
                    cancelledRef.current = false;
                    await actions.cancelAudit(result.task_id).catch(() => {});
                    onUpdateState({
                        phase: 'config',
                        active_job: { job_id: result.task_id, status: 'cancelled' },
                    }).catch(() => {});
                    return;
                }

                onUpdateState({
                    active_job: {
                        job_id: result.task_id,
                        status: 'running',
                        started_at: new Date().toISOString(),
                    },
                }).catch(() => {});

                const cleanup = actions.streamAuditProgress(
                    result.task_id,
                    (p) => setProgress(p),
                    async (final) => {
                        setIsRunning(false);
                        setProgress({ status: 'completed', ...final });
                        const sessions = await refreshSessions();
                        try {
                            await onUpdateState({
                                phase: 'reviewing',
                                status: 'in_progress',
                                flw_count: sessions.length,
                                images_reviewed: sessions.reduce((sum, s) => sum + (s.image_count || s.assessment_stats?.total || 0), 0),
                                tasks_created: 0,
                                period_start: auditMode === 'date_range' ? startDate : null,
                                period_end: auditMode === 'date_range' ? endDate : null,
                                active_job: {
                                    job_id: result.task_id,
                                    status: 'completed',
                                    completed_at: new Date().toISOString(),
                                },
                            });
                        } catch (e) { /* non-fatal */ }
                        // Navigate directly to the bulk assessment page
                        if (sessions.length > 0) {
                            const s = sessions[0];
                            const params = new URLSearchParams();
                            if (s.opportunity_id) params.set('opportunity_id', s.opportunity_id);
                            params.set('threshold', threshold);
                            if (instance.id) params.set('workflow_run_id', instance.id);
                            window.location.href = '/audit/' + s.id + '/bulk/?' + params.toString();
                        } else {
                            setPhase('reviewing');
                        }
                    },
                    (err) => {
                        setIsRunning(false);
                        setProgress({ status: 'failed', error: err });
                        setPhase('config');
                        onUpdateState({
                            phase: 'config',
                            active_job: { job_id: result.task_id, status: 'failed', error: err },
                        }).catch(() => {});
                    }
                );
                cleanupRef.current = cleanup;
            } else {
                setIsRunning(false);
                setProgress({ status: 'failed', error: result.error || 'Failed to start' });
                setPhase('config');
                onUpdateState({ phase: 'config' }).catch(() => {});
            }
        } catch (err) {
            setIsRunning(false);
            setProgress({ status: 'failed', error: err.message });
            setPhase('config');
            onUpdateState({ phase: 'config' }).catch(() => {});
        }
    };

    // ── Cancel handler ───────────────────────────────────────────────────────
    const handleCancel = async () => {
        if (isCancelling) return;
        setIsCancelling(true);
        if (cleanupRef.current) { cleanupRef.current(); cleanupRef.current = null; }
        setIsRunning(false);

        if (!taskId) {
            // Task hasn't been created yet ("Submitting to task queue..." phase).
            // Set a flag so handleCreate cancels it as soon as it gets a taskId.
            cancelledRef.current = true;
            setProgress({ status: 'cancelled', message: 'Audit creation cancelled' });
            setPhase('config');
            try {
                await onUpdateState({ phase: 'config' });
            } finally {
                setIsCancelling(false);
            }
            return;
        }

        try {
            await actions.cancelAudit(taskId);
        } catch (e) { /* ignore */ }
        setProgress({ status: 'cancelled', message: 'Audit creation cancelled' });
        setPhase('config');
        try {
            await onUpdateState({ phase: 'config' });
        } finally {
            setIsCancelling(false);
        }
    };

    // ── Compute average % passed across sessions ──────────────────────────────
    const computeAvgPassed = (sessions) => {
        if (!sessions || sessions.length === 0) return null;
        const pcts = sessions.map(s => {
            const stats = s.assessment_stats || {};
            const total = (stats.pass || 0) + (stats.fail || 0);
            return total > 0 ? Math.round((stats.pass || 0) / total * 100) : null;
        }).filter(pct => pct !== null);
        if (pcts.length === 0) return null;
        return Math.round(pcts.reduce((a, b) => a + b, 0) / pcts.length);
    };

    // ── Complete handler (mark workflow done) ─────────────────────────────────
    const [isCompleting, setIsCompleting] = React.useState(false);
    const [completeError, setCompleteError] = React.useState(null);

    const handleComplete = async () => {
        const avgPassed = computeAvgPassed(linkedSessions);
        setIsCompleting(true);
        setCompleteError(null);
        try {
            await onUpdateState({
                phase: 'completed',
                status: 'completed',
                avg_passed: avgPassed,
                images_reviewed: linkedSessions.reduce((sum, s) => sum + (s.image_count || s.assessment_stats?.total || 0), 0),
                tasks_created: typeof instance.state?.tasks_created === 'number' ? instance.state.tasks_created : 0,
                completion: { completed_at: new Date().toISOString() },
            });
            const oppId = instance.opportunity_id || '';
            window.location.href = '/labs/workflow/' + (oppId ? '?opportunity_id=' + oppId : '');
        } catch (err) {
            setCompleteError(err.message || 'Failed to complete');
            setIsCompleting(false);
        }
    };

    // ── Inner component: Sessions Table ──────────────────────────────────────
    const SessionsTable = ({ sessions, readOnly }) => {
        const allCompleted = sessions.length > 0 && sessions.every(s => s.status === 'completed');
        const avgPct = computeAvgPassed(sessions);

        return (
            <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                <div className={'px-6 py-4 border-b ' +
                    (readOnly ? 'bg-green-50 border-green-100' : 'bg-blue-50 border-blue-100')}>
                    <div className="flex items-center justify-between">
                        <div>
                            <h2 className={'text-lg font-semibold flex items-center gap-2 ' +
                                (readOnly ? 'text-green-800' : 'text-blue-800')}>
                                <i className={'fa-solid ' + (readOnly ? 'fa-check-circle' : 'fa-images')}></i>
                                {readOnly ? 'Completed Review' : 'Audit Sessions'}
                            </h2>
                            <p className={'text-sm mt-1 ' + (readOnly ? 'text-green-600' : 'text-blue-600')}>
                                {sessions.length} session{sessions.length !== 1 ? 's' : ''} &bull;{' '}
                                {sessions.filter(s => s.status === 'completed').length} completed
                                {avgPct !== null && (
                                    <span className={'ml-2 font-medium ' +
                                        (avgPct >= threshold ? 'text-green-700' : 'text-red-700')}>
                                        &bull; Avg {avgPct}% passed
                                    </span>
                                )}
                            </p>
                        </div>
                        {!readOnly && allCompleted && (
                            <div className="flex items-center gap-3">
                                {completeError && (
                                    <span className="text-sm text-red-600">{completeError}</span>
                                )}
                                <button onClick={handleComplete} disabled={isCompleting}
                                    className={'inline-flex items-center px-4 py-2 bg-green-600 text-white ' +
                                        'rounded-lg hover:bg-green-700 disabled:bg-gray-400 font-medium text-sm'}>
                                    {isCompleting
                                        ? <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                                        : <i className="fa-solid fa-flag-checkered mr-2"></i>}
                                    Mark Complete
                                </button>
                            </div>
                        )}
                    </div>
                </div>
                <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className={'px-4 py-3 text-left text-xs font-medium ' +
                                    'text-gray-500 uppercase tracking-wider'}>FLW</th>
                                <th className={'px-4 py-3 text-center text-xs font-medium ' +
                                    'text-gray-500 uppercase tracking-wider'}>Visits</th>
                                <th className={'px-4 py-3 text-center text-xs font-medium ' +
                                    'text-gray-500 uppercase tracking-wider'}>Status</th>
                                <th className={'px-4 py-3 text-center text-xs font-medium ' +
                                    'text-gray-500 uppercase tracking-wider'}>
                                    <span className="text-green-600">Pass</span>
                                    {' / '}
                                    <span className="text-red-600">Fail</span>
                                    {' / '}
                                    <span className="text-gray-500">Pending</span>
                                </th>
                                <th className={'px-4 py-3 text-center text-xs font-medium ' +
                                    'text-gray-500 uppercase tracking-wider'}>% Passed</th>
                                <th className={'px-4 py-3 text-right text-xs font-medium ' +
                                    'text-gray-500 uppercase tracking-wider'}>Actions</th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                            {sessions.map(session => {
                                const stats = session.assessment_stats || {};
                                const total = (stats.pass || 0) + (stats.fail || 0);
                                const pct = total > 0 ? Math.round((stats.pass || 0) / total * 100) : null;
                                return (
                                    <tr key={session.id} className="hover:bg-gray-50">
                                        <td className="px-4 py-4">
                                            <div className="text-sm font-medium text-gray-900">
                                                {session.flw_display_name || session.flw_username || 'Unknown'}
                                            </div>
                                            {session.flw_display_name !== session.flw_username
                                                && session.flw_username && (
                                                <div className="text-xs text-gray-400 mt-0.5 font-mono">
                                                    {session.flw_username}
                                                </div>
                                            )}
                                        </td>
                                        <td className="px-4 py-4 text-center">
                                            <span className="text-sm font-medium text-blue-600">
                                                {session.visit_count || 0}
                                            </span>
                                        </td>
                                        <td className="px-4 py-4 text-center">
                                            <span className={'px-2 py-1 text-xs font-medium rounded ' +
                                                (session.status === 'completed'
                                                    ? 'bg-green-100 text-green-700'
                                                    : 'bg-yellow-100 text-yellow-700')}>
                                                {session.status === 'completed' ? 'Completed' : 'In Progress'}
                                            </span>
                                        </td>
                                        <td className="px-4 py-4 text-center">
                                            <div className="flex items-center justify-center gap-1.5 text-sm">
                                                <span className="text-green-600 font-medium">{stats.pass || 0}</span>
                                                <span className="text-gray-300">/</span>
                                                <span className="text-red-600 font-medium">{stats.fail || 0}</span>
                                                <span className="text-gray-300">/</span>
                                                <span className="text-gray-500">{stats.pending || 0}</span>
                                            </div>
                                        </td>
                                        <td className="px-4 py-4 text-center">
                                            {pct !== null
                                                ? <span className={'text-sm font-medium ' +
                                                    (pct >= threshold ? 'text-green-700' : 'text-red-700')}>
                                                    {pct}%
                                                  </span>
                                                : <span className="text-gray-400 text-sm">&mdash;</span>}
                                        </td>
                                        <td className="px-4 py-4 text-right">
                                            <a
                                                href={'/audit/' + session.id + '/bulk/' +
                                                    '?opportunity_id=' + session.opportunity_id}
                                                className={'inline-flex items-center px-3 py-1.5 text-sm ' +
                                                    'bg-blue-50 text-blue-700 rounded hover:bg-blue-100 ' +
                                                    'border border-blue-200 transition-colors'}
                                            >
                                                <i className={'fa-solid mr-1.5 ' +
                                                    (session.status === 'completed'
                                                        ? 'fa-eye'
                                                        : 'fa-arrow-up-right-from-square')}></i>
                                                {session.status === 'completed' ? 'View' : 'Review'}
                                            </a>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>

                {/* Aggregate footer */}
                {sessions.length > 1 && (
                    <div className="px-6 py-4 bg-gray-50 border-t border-gray-200">
                        <div className="flex items-center justify-between">
                            <span className="text-sm font-medium text-gray-700">Totals:</span>
                            <div className="flex gap-6 text-sm">
                                <span className="text-gray-600">
                                    <span className="font-medium">
                                        {sessions.reduce((sum, s) => sum + (s.visit_count || 0), 0)}
                                    </span> visits
                                </span>
                                <span className="text-green-600">
                                    <i className="fa-solid fa-check mr-1"></i>
                                    {sessions.reduce(
                                        (sum, s) => sum + (s.assessment_stats?.pass || 0), 0
                                    )} pass
                                </span>
                                <span className="text-red-600">
                                    <i className="fa-solid fa-xmark mr-1"></i>
                                    {sessions.reduce(
                                        (sum, s) => sum + (s.assessment_stats?.fail || 0), 0
                                    )} fail
                                </span>
                                {avgPct !== null && (
                                    <span className={'font-medium ' +
                                        (avgPct >= threshold ? 'text-green-700' : 'text-red-700')}>
                                        Avg {avgPct}% passed
                                    </span>
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </div>
        );
    };

    // ── Inner component: Visit Selection ────────────────────────────────────
    const VisitSelectionSection = () => (
        <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">
                <i className="fa-solid fa-sliders mr-2 text-gray-400"></i>
                Visit Selection
            </h3>
            <div className="flex gap-2 mb-4">
                <button onClick={() => setAuditMode('date_range')}
                    className={
                        'flex-1 px-4 py-3 text-sm rounded-lg border-2 transition-colors ' +
                        (auditMode === 'date_range'
                            ? 'bg-blue-50 text-blue-700 border-blue-500'
                            : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300')
                    }>
                    <i className="fa-solid fa-calendar mr-2"></i>Date Range
                </button>
                <button onClick={() => setAuditMode('last_n_per_opp')}
                    className={
                        'flex-1 px-4 py-3 text-sm rounded-lg border-2 transition-colors ' +
                        (auditMode === 'last_n_per_opp'
                            ? 'bg-blue-50 text-blue-700 border-blue-500'
                            : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300')
                    }>
                    <i className="fa-solid fa-list-ol mr-2"></i>Last N Visits
                </button>
            </div>
            {auditMode === 'date_range' && (
                <div className="p-4 bg-gray-50 rounded-lg border border-gray-200">
                    <div className="flex flex-wrap gap-2 mb-3">
                        {[
                            { id: 'last_week', label: 'Last Week' },
                            { id: 'last_7_days', label: 'Last 7 Days' },
                            { id: 'last_14_days', label: 'Last 14 Days' },
                            { id: 'last_30_days', label: 'Last 30 Days' },
                            { id: 'this_month', label: 'This Month' },
                            { id: 'last_month', label: 'Last Month' },
                            { id: 'custom', label: 'Custom' },
                        ].map(p => (
                            <button key={p.id} onClick={() => applyPreset(p.id)}
                                className={
                                    'px-3 py-1.5 text-sm rounded-full border transition-colors ' +
                                    (datePreset === p.id
                                        ? 'bg-blue-600 text-white border-blue-600'
                                        : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400')
                                }>
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
            {auditMode === 'last_n_per_opp' && (
                <div className="p-4 bg-gray-50 rounded-lg border border-gray-200">
                    <div className="flex items-center gap-3">
                        <label className="text-sm text-gray-700">Get the last</label>
                        <input type="number" min="1" max="1000" value={lastNCount}
                            onChange={e => setLastNCount(parseInt(e.target.value) || 10)}
                            className="w-20 border border-gray-300 rounded px-3 py-2 text-sm text-center" />
                        <label className="text-sm text-gray-700">visits per opportunity</label>
                    </div>
                </div>
            )}
        </div>
    );

    // ── Inner component: Config ──────────────────────────────────────────────
    const ConfigPhase = () => (
        <div className="bg-white rounded-lg shadow-sm p-6 space-y-6">

            {/* Image types (dynamic, from Connect data) */}
            <div>
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-image mr-2 text-gray-400"></i>
                    Image Types
                </h3>
                {imageQuestionsLoading && (
                    <div className="flex items-center gap-2 text-sm text-blue-600 p-3 bg-blue-50 rounded-lg">
                        <i className="fa-solid fa-spinner fa-spin"></i>
                        <span>Loading image types...</span>
                    </div>
                )}
                {imageQuestionsError && (
                    <div className="text-sm text-red-600 p-3 bg-red-50 rounded-lg border border-red-200">
                        <i className="fa-solid fa-circle-exclamation mr-2"></i>
                        {imageQuestionsError}
                    </div>
                )}
                {!imageQuestionsLoading && !imageQuestionsError && imageQuestions.length > 0 && (
                    <div>
                        <div className="flex gap-2 mb-2">
                            <button
                                onClick={() => setSelectedImageTypeIds(imageQuestions.map(q => q.id))}
                                className="text-xs text-blue-600 hover:underline">
                                Select All
                            </button>
                            <span className="text-gray-300">|</span>
                            <button
                                onClick={() => setSelectedImageTypeIds([])}
                                className="text-xs text-blue-600 hover:underline">
                                Deselect All
                            </button>
                        </div>
                        <div className="grid grid-cols-1 gap-2">
                            {imageQuestions.map(q => {
                                const checked = selectedImageTypeIds.includes(q.id);
                                return (
                                    <label key={q.id}
                                        className={'flex items-start gap-3 p-3 rounded-lg border-2 cursor-pointer ' +
                                            'transition-colors ' +
                                            (checked
                                                ? 'bg-blue-50 border-blue-400'
                                                : 'bg-white border-gray-200 hover:border-gray-300')}>
                                        <input
                                            type="checkbox"
                                            checked={checked}
                                            onChange={() => {
                                                setSelectedImageTypeIds(prev =>
                                                    prev.includes(q.id)
                                                        ? prev.filter(id => id !== q.id)
                                                        : [...prev, q.id]
                                                );
                                            }}
                                            className="mt-0.5 h-4 w-4 text-blue-600 rounded"
                                        />
                                        <div className="min-w-0">
                                            <div className="text-sm font-medium text-gray-900 font-mono">{q.id}</div>
                                            {q.form_name && (
                                                <div className="text-xs text-gray-500 mt-0.5">{q.form_name}</div>
                                            )}
                                        </div>
                                    </label>
                                );
                            })}
                        </div>
                    </div>
                )}
                {!imageQuestionsLoading && !imageQuestionsError && imageQuestions.length === 0 && (
                    <div className="text-sm text-gray-500 p-3 bg-gray-50 rounded-lg">
                        No image questions found in this opportunity's app.
                    </div>
                )}
            </div>

            {/* Visit Selection */}
            {VisitSelectionSection()}

            {/* Sampling */}
            <div>
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-percent mr-2 text-gray-400"></i>
                    Sampling
                </h3>
                <div className="p-4 bg-gray-50 rounded-lg border border-gray-200 flex items-center gap-3">
                    <label className="text-sm text-gray-700">Sample</label>
                    <input type="number" min="1" max="100" value={samplePct}
                        onChange={e => setSamplePct(parseInt(e.target.value) || 100)}
                        className="w-20 border border-gray-300 rounded px-3 py-2 text-sm text-center" />
                    <label className="text-sm text-gray-700">% of matching visits</label>
                </div>
            </div>

            {/* Passing threshold */}
            <div>
                <h3 className="text-sm font-medium text-gray-700 mb-3">
                    <i className="fa-solid fa-gauge mr-2 text-gray-400"></i>
                    Passing Threshold
                </h3>
                <div className="p-4 bg-gray-50 rounded-lg border border-gray-200 flex items-center gap-3">
                    <label className="text-sm text-gray-700">Mark FLW as passing if</label>
                    <input type="number" min="1" max="100" value={threshold}
                        onChange={e => setThreshold(parseInt(e.target.value) || 80)}
                        className="w-20 border border-gray-300 rounded px-3 py-2 text-sm text-center" />
                    <label className="text-sm text-gray-700">% or more of their photos pass</label>
                </div>
            </div>

            {/* Submit */}
            <div className="pt-4 border-t border-gray-200">
                <button
                    onClick={handleCreate}
                    disabled={selectedOpps.length === 0 || selectedImageTypeIds.length === 0}
                    className={'inline-flex items-center px-6 py-3 bg-blue-600 text-white ' +
                        'rounded-lg hover:bg-blue-700 disabled:bg-gray-400 font-medium'}
                >
                    <i className="fa-solid fa-play mr-2"></i>
                    Create Review
                </button>
                {selectedImageTypeIds.length === 0 && !imageQuestionsLoading &&
                    imageQuestions.length > 0 && !imageQuestionsError && (
                    <p className="mt-2 text-sm text-red-600">
                        Select at least one image type to continue.
                    </p>
                )}
            </div>
        </div>
    );

    // ── Inner component: Creating ────────────────────────────────────────────
    const CreatingPhase = () => (
        <div className="space-y-4">
            {isRunning && progress && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-3">
                        <div className="flex items-center gap-3">
                            <i className="fa-solid fa-spinner fa-spin text-blue-600"></i>
                            <span className="font-medium text-blue-800">
                                Creating Review Session...
                            </span>
                            {progress.stage_name && progress.stage_name !== 'Initializing' && (
                                <span className="text-xs text-blue-600 ml-2">({progress.stage_name})</span>
                            )}
                        </div>
                        <div className="flex items-center gap-3">
                            {progress.current_stage && progress.total_stages && (
                                <span className="text-sm text-blue-600">
                                    Stage {progress.current_stage}/{progress.total_stages}
                                </span>
                            )}
                            <button onClick={handleCancel} disabled={isCancelling}
                                className={'px-3 py-1 text-sm text-red-600 hover:text-red-800 ' +
                                    'hover:bg-red-100 rounded transition-colors disabled:opacity-50'}>
                                <i className="fa-solid fa-times mr-1"></i>Cancel
                            </button>
                        </div>
                    </div>
                    {progress.total > 0 && (
                        <div className="w-full bg-blue-200 rounded-full h-2">
                            <div className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                                style={{ width: (progress.processed / progress.total * 100) + '%' }}>
                            </div>
                        </div>
                    )}
                    <div className="mt-2 text-sm text-blue-700">{progress.message}</div>
                </div>
            )}
            {progress?.status === 'failed' && !isRunning && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                    <div className="flex items-center gap-2 text-red-800">
                        <i className="fa-solid fa-circle-exclamation"></i>
                        <span className="font-medium">Error: {progress.error}</span>
                    </div>
                    <button onClick={() => setPhase('config')}
                        className="mt-3 text-sm text-blue-600 hover:underline">
                        &larr; Back to configuration
                    </button>
                </div>
            )}
            {progress?.status === 'cancelled' && !isRunning && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                    <div className="flex items-center gap-2 text-amber-800">
                        <i className="fa-solid fa-ban"></i>
                        <span className="font-medium">Audit creation was cancelled</span>
                    </div>
                    <button onClick={() => setPhase('config')}
                        className="mt-3 text-sm text-blue-600 hover:underline">
                        &larr; Back to configuration
                    </button>
                </div>
            )}
        </div>
    );

    // ── Inner component: Review ──────────────────────────────────────────────
    const ReviewPhase = () => {
        React.useEffect(() => {
            if (!loadingSessions && linkedSessions.length > 0) {
                const s = linkedSessions[0];
                const params = new URLSearchParams();
                if (s.opportunity_id) params.set('opportunity_id', s.opportunity_id);
                params.set('threshold', threshold);
                if (instance.id) params.set('workflow_run_id', instance.id);
                window.location.href = '/audit/' + s.id + '/bulk/?' + params.toString();
            }
        }, [loadingSessions, linkedSessions]);

        if (loadingSessions) return (
            <div className="bg-white rounded-lg shadow-sm p-12 text-center">
                <i className="fa-solid fa-spinner fa-spin text-gray-400 text-3xl mb-3"></i>
                <p className="text-gray-500 mt-3">Loading sessions...</p>
            </div>
        );
        if (linkedSessions.length === 0) return (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-6">
                <p className="text-amber-700">No sessions found for this workflow run.</p>
                <button onClick={() => setPhase('config')}
                    className="mt-3 text-sm text-blue-600 hover:underline">
                    &larr; Back to configuration
                </button>
            </div>
        );
        return (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-6">
                <h2 className="text-lg font-semibold text-blue-800 flex items-center gap-2 mb-2">
                    <i className="fa-solid fa-spinner fa-spin text-blue-600"></i>
                    Redirecting to review...
                </h2>
            </div>
        );
    };

    // ── Inner component: Completed Phase ────────────────────────────────────
    const CompletedPhase = () => {
        const completion = instance.state?.completion || {};
        const avgPassed = instance.state?.avg_passed;
        const completedAt = completion.completed_at
            ? new Date(completion.completed_at).toLocaleDateString('en-US',
                { month: 'short', day: 'numeric', year: 'numeric' })
            : '';

        // Auto-redirect to the single session's bulk assessment view
        React.useEffect(() => {
            if (!loadingSessions && linkedSessions.length === 1) {
                const s = linkedSessions[0];
                window.location.href = '/audit/' + s.id + '/bulk/?opportunity_id=' + s.opportunity_id;
            }
        }, [loadingSessions, linkedSessions.length]);

        // While loading or waiting for redirect, show spinner
        if (loadingSessions || linkedSessions.length === 1) {
            return (
                <div className="bg-white rounded-lg shadow-sm p-8 text-center">
                    <i className="fa-solid fa-spinner fa-spin text-gray-400 text-2xl"></i>
                    <p className="text-gray-500 mt-2 text-sm">Loading…</p>
                </div>
            );
        }

        return (
            <div className="space-y-6">
                {/* Completion banner */}
                <div className="bg-green-50 border border-green-200 rounded-lg p-6">
                    <div className="flex items-start justify-between">
                        <div>
                            <h2 className="text-lg font-semibold text-green-800 flex items-center gap-2">
                                <i className="fa-solid fa-circle-check text-green-600"></i>
                                Image Review Completed
                            </h2>
                            {completedAt && (
                                <p className="text-sm text-green-600 mt-1">Completed on {completedAt}</p>
                            )}
                        </div>
                        {avgPassed !== null && avgPassed !== undefined && (
                            <span className={
                                'inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium ' +
                                (avgPassed >= threshold
                                    ? 'bg-green-100 text-green-800'
                                    : 'bg-red-100 text-red-800')}>
                                <i className={'fa-solid ' +
                                    (avgPassed >= threshold ? 'fa-circle-check' : 'fa-circle-xmark')}></i>
                                Avg {avgPassed}% Passed
                            </span>
                        )}
                    </div>
                </div>

                {/* Sessions table (read-only) for multi-session runs */}
                <SessionsTable sessions={linkedSessions} readOnly={true} />
            </div>
        );
    };

    // ── Phase router ────────────────────────────────────────────────────────
    return (
        <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                <p className="text-gray-600 mt-1">{definition.description}</p>
            </div>
            {phase === 'config' && <ConfigPhase />}
            {phase === 'creating' && <CreatingPhase />}
            {phase === 'reviewing' && <ReviewPhase />}
            {phase === 'completed' && <CompletedPhase />}
        </div>
    );
}"""

TEMPLATE = {
    "key": "bulk_image_audit",
    "name": "Bulk Image Audit",
    "description": "Review photos for an opportunity with per-FLW pass/fail tracking",
    "icon": "fa-images",
    "color": "blue",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": None,
}
