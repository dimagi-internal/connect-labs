"""
Weekly KMC Audit with AI Review Workflow Template.

Streamlined workflow for creating weekly audit sessions with AI pre-validation.
Designed to run every Monday with sensible defaults:
- Date range: Previous week (Mon-Sun)
- Granularity: One audit per FLW
- Related fields: Weight image + reading pre-configured
- Title: Auto-generated from date range
"""

DEFINITION = {
    "name": "Weekly KMC Audit with AI Review",
    "description": "Create weekly KMC audit sessions per FLW with optional AI image validation",
    "version": 1,
    "templateType": "audit_with_ai_review",
    "statuses": [
        {"id": "ready", "label": "Ready to Run", "color": "gray"},
        {"id": "creating", "label": "Creating Audits", "color": "blue"},
        {"id": "ai_reviewing", "label": "Running AI Review", "color": "purple"},
        {"id": "completed", "label": "Completed", "color": "green"},
        {"id": "failed", "label": "Failed", "color": "red"},
    ],
    "config": {
        "showSummaryCards": True,
    },
    "pipeline_sources": [],
}

RENDER_CODE = """function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    // Audit type mode: 'date_range' or 'last_n_per_opp'
    const [auditMode, setAuditMode] = React.useState(instance.state?.audit_config?.audit_type || 'date_range');
    const [lastNCount, setLastNCount] = React.useState(instance.state?.audit_config?.count_per_opp || 10);

    // Date range state
    const [startDate, setStartDate] = React.useState(instance.state?.audit_config?.start_date || '');
    const [endDate, setEndDate] = React.useState(instance.state?.audit_config?.end_date || '');
    const [datePreset, setDatePreset] = React.useState('last_week');

    // AI Agent state
    const [availableAgents, setAvailableAgents] = React.useState([]);
    const [selectedAgent, setSelectedAgent] = React.useState(instance.state?.audit_config?.ai_agent_id || '');
    const [loadingAgents, setLoadingAgents] = React.useState(true);

    // Execution state
    const [isRunning, setIsRunning] = React.useState(false);
    const [isCancelling, setIsCancelling] = React.useState(false);
    const [progress, setProgress] = React.useState(null);
    const [taskId, setTaskId] = React.useState(null);
    const cleanupRef = React.useRef(null);

    // Linked audit sessions (persisted, fetched from API on mount)
    const [linkedSessions, setLinkedSessions] = React.useState([]);
    const [loadingSessions, setLoadingSessions] = React.useState(true);

    // Fetch linked sessions on mount
    React.useEffect(() => {
        if (!instance.id) {
            setLoadingSessions(false);
            return;
        }
        setLoadingSessions(true);
        fetch('/audit/api/workflow/' + instance.id + '/sessions/')
            .then(res => res.json())
            .then(data => {
                if (data.success && data.sessions) {
                    setLinkedSessions(data.sessions);
                }
                setLoadingSessions(false);
            })
            .catch(err => {
                console.error('Failed to load linked sessions:', err);
                setLoadingSessions(false);
            });
    }, [instance.id]);

    // Has sessions to display?
    const hasLinkedSessions = linkedSessions.length > 0;

    // Reconnect to running job on page load (if user refreshes during execution)
    React.useEffect(() => {
        try {
            const activeJob = instance.state?.active_job;
            if (!actions?.streamAuditProgress) return; // Safety check
            if (activeJob?.status === 'running' && activeJob?.job_id) {
                console.log('[AuditWorkflow] Reconnecting to running job:', activeJob.job_id);
                setIsRunning(true);
                setTaskId(activeJob.job_id);
                setProgress({
                    status: 'running',
                    stage_name: activeJob.stage_name || 'Processing',
                    processed: activeJob.processed || 0,
                    total: activeJob.total || 0,
                });

                // Reconnect to SSE stream
                const cleanup = actions.streamAuditProgress(
                    activeJob.job_id,
                    (progressData) => {
                        setProgress(progressData);
                    },
                    (finalResult) => {
                        setIsRunning(false);
                        setProgress({ status: 'completed', ...finalResult });
                        onUpdateState({
                            status: 'completed',
                            active_job: {
                                job_id: activeJob.job_id,
                                status: 'completed',
                                completed_at: new Date().toISOString(),
                            },
                        }).catch(err => console.warn('Failed to update state:', err));
                        // Refresh linked sessions
                        fetch('/audit/api/workflow/' + instance.id + '/sessions/')
                            .then(res => res.json())
                            .then(data => {
                                if (data.success && data.sessions) {
                                    setLinkedSessions(data.sessions);
                                }
                            })
                            .catch(err => console.error('Failed to refresh sessions:', err));
                    },
                    (error) => {
                        setIsRunning(false);
                        setProgress({ status: 'failed', error });
                        onUpdateState({
                            status: 'failed',
                            active_job: {
                                job_id: activeJob.job_id,
                                status: 'failed',
                                error: error,
                            },
                        }).catch(err => console.warn('Failed to update state:', err));
                    }
                );
                cleanupRef.current = cleanup;

                return () => {
                    if (cleanup) cleanup();
                };
            }
        } catch (err) {
            console.error('[AuditWorkflow] Error in reconnection:', err);
        }
    }, []); // Run once on mount

    // Get opportunity from URL or instance state
    const urlParams = new URLSearchParams(window.location.search);
    const opportunityId = parseInt(urlParams.get('opportunity_id')) || instance.state?.opportunity_id;
    const opportunityName = instance.state?.opportunity_name || 'Selected Opportunity';

    // Date preset calculator
    const calculateDateRange = (preset) => {
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        let start, end;

        switch (preset) {
            case 'last_week': {
                // Last Monday to Sunday (the full week before this week)
                const dayOfWeek = today.getDay(); // 0=Sun, 1=Mon, ...
                const daysToThisMonday = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
                const thisMonday = new Date(today);
                thisMonday.setDate(today.getDate() - daysToThisMonday);
                // Last Monday is 7 days before this Monday
                start = new Date(thisMonday);
                start.setDate(thisMonday.getDate() - 7);
                // Last Sunday is 6 days after last Monday
                end = new Date(start);
                end.setDate(start.getDate() + 6);
                break;
            }
            case 'last_7_days': {
                end = new Date(today);
                end.setDate(today.getDate() - 1); // Yesterday
                start = new Date(end);
                start.setDate(end.getDate() - 6); // 7 days total
                break;
            }
            case 'last_14_days': {
                end = new Date(today);
                end.setDate(today.getDate() - 1);
                start = new Date(end);
                start.setDate(end.getDate() - 13);
                break;
            }
            case 'last_30_days': {
                end = new Date(today);
                end.setDate(today.getDate() - 1);
                start = new Date(end);
                start.setDate(end.getDate() - 29);
                break;
            }
            case 'this_month': {
                start = new Date(today.getFullYear(), today.getMonth(), 1);
                end = new Date(today);
                end.setDate(today.getDate() - 1);
                break;
            }
            case 'last_month': {
                start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                end = new Date(today.getFullYear(), today.getMonth(), 0); // Last day of prev month
                break;
            }
            default:
                return null; // Custom - don't change
        }

        return {
            start: start.toISOString().split('T')[0],
            end: end.toISOString().split('T')[0]
        };
    };

    // Apply date preset
    const applyPreset = (preset) => {
        setDatePreset(preset);
        if (preset !== 'custom') {
            const range = calculateDateRange(preset);
            if (range) {
                setStartDate(range.start);
                setEndDate(range.end);
            }
        }
    };

    // Load AI agents on mount
    React.useEffect(() => {
        setLoadingAgents(true);
        fetch('/audit/api/ai-agents/')
            .then(res => res.json())
            .then(data => {
                setAvailableAgents(data.agents || []);
                // Auto-select scale_validation if available
                const scaleAgent = (data.agents || []).find(a => a.agent_id === 'scale_validation');
                if (scaleAgent && !instance.state?.audit_config?.ai_agent_id) {
                    setSelectedAgent('scale_validation');
                }
                setLoadingAgents(false);
            })
            .catch(err => {
                console.error('Failed to load AI agents:', err);
                setLoadingAgents(false);
            });
    }, []);

    // Set default dates to last week on mount
    React.useEffect(() => {
        if (!startDate && !endDate) {
            applyPreset('last_week');
        }
    }, []);

    // Cleanup on unmount
    React.useEffect(() => {
        return () => {
            if (cleanupRef.current) cleanupRef.current();
        };
    }, []);

    // Format date for display
    const formatDate = (dateStr) => {
        if (!dateStr) return '';
        const date = new Date(dateStr + 'T00:00:00');
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    };

    // Generate audit title based on mode
    const getAuditTitle = () => {
        if (auditMode === 'last_n_per_opp') {
            return 'Audit - Last ' + lastNCount + ' visits';
        }
        if (!startDate || !endDate) return 'Weekly Audit';
        return 'Audit for ' + formatDate(startDate) + ' - ' + formatDate(endDate);
    };

    // Filter options (checked by default to only include visits with image AND field value)
    const [filterByImage, setFilterByImage] = React.useState(
        instance.state?.audit_config?.filter_by_image !== false  // Default true
    );
    const [filterByField, setFilterByField] = React.useState(
        instance.state?.audit_config?.filter_by_field !== false  // Default true
    );

    // Pre-configured related fields for weight validation
    const relatedFields = [
        {
            imagePath: 'anthropometric/upload_weight_image',
            fieldPath: 'child_weight_visit',
            label: 'Weight Reading',
            filter_by_image: filterByImage,
            filter_by_field: filterByField,
        }
    ];

    // Start audit creation
    const handleCreateAudit = async () => {
        if (!opportunityId) {
            alert('No opportunity selected');
            return;
        }

        setIsRunning(true);
        setProgress({ status: 'starting', message: 'Initializing...' });

        // Save config to workflow state
        const auditConfig = {
            audit_type: auditMode,
            granularity: 'per_flw',
            start_date: auditMode === 'date_range' ? startDate : null,
            end_date: auditMode === 'date_range' ? endDate : null,
            count_per_opp: auditMode === 'last_n_per_opp' ? lastNCount : null,
            title_suffix: getAuditTitle(),
            ai_agent_id: selectedAgent,
            related_fields: relatedFields,
            filter_by_image: filterByImage,
            filter_by_field: filterByField,
        };

        await onUpdateState({
            audit_config: auditConfig,
            opportunity_id: opportunityId,
        });

        // Build request - use snake_case for Python backend
        const criteria = {
            audit_type: auditMode,
            granularity: 'per_flw',
            title: getAuditTitle(),
            start_date: auditMode === 'date_range' ? startDate : null,
            end_date: auditMode === 'date_range' ? endDate : null,
            count_per_opp: auditMode === 'last_n_per_opp' ? lastNCount : null,
            related_fields: relatedFields,
        };

        try {
            const result = await actions.createAudit({
                opportunities: [{ id: opportunityId, name: opportunityName }],
                criteria: criteria,
                workflow_run_id: instance.id,
                ai_agent_id: selectedAgent || undefined,
            });

            if (result.success && result.task_id) {
                setTaskId(result.task_id);

                // Persist active job to workflow state for list page SSE tracking (fire and forget)
                onUpdateState({
                    active_job: {
                        job_id: result.task_id,
                        status: 'running',
                        started_at: new Date().toISOString(),
                    },
                }).catch(err => console.warn('Failed to persist active_job:', err));

                // Stream progress
                const cleanup = actions.streamAuditProgress(
                    result.task_id,
                    (progressData) => {
                        // Update local UI state only (list page gets updates via its own SSE)
                        setProgress(progressData);
                    },
                    (finalResult) => {
                        setIsRunning(false);
                        setProgress({ status: 'completed', ...finalResult });

                        // Save status and mark job as completed
                        onUpdateState({
                            status: 'completed',
                            active_job: {
                                job_id: result.task_id,
                                status: 'completed',
                                completed_at: new Date().toISOString(),
                            },
                        }).catch(err => console.warn('Failed to update state:', err));

                        // Refresh linked sessions from API to show the table
                        fetch('/audit/api/workflow/' + instance.id + '/sessions/')
                            .then(res => res.json())
                            .then(data => {
                                if (data.success && data.sessions) {
                                    setLinkedSessions(data.sessions);
                                }
                            })
                            .catch(err => console.error('Failed to refresh sessions:', err));
                    },
                    (error) => {
                        setIsRunning(false);
                        setProgress({ status: 'failed', error });
                        // Mark job as failed
                        onUpdateState({
                            status: 'failed',
                            active_job: {
                                job_id: result.task_id,
                                status: 'failed',
                                error: error,
                            },
                        }).catch(err => console.warn('Failed to update state:', err));
                    }
                );
                cleanupRef.current = cleanup;
            } else {
                setIsRunning(false);
                setProgress({ status: 'failed', error: result.error || 'Failed to start audit creation' });
            }
        } catch (err) {
            setIsRunning(false);
            setProgress({ status: 'failed', error: err.message || 'Unknown error' });
        }
    };

    // Cancel audit creation
    const handleCancel = async () => {
        if (!taskId || isCancelling) return;

        setIsCancelling(true);

        // Stop the progress stream first
        if (cleanupRef.current) {
            cleanupRef.current();
            cleanupRef.current = null;
        }

        // Always stop the running state - user requested cancel
        setIsRunning(false);

        try {
            const result = await actions.cancelAudit(taskId);
            if (result.success) {
                setProgress({ status: 'cancelled', message: 'Audit creation cancelled' });
                // Clear any partial results and mark job as cancelled
                await onUpdateState({
                    audit_results: null,
                    status: 'cancelled',
                    active_job: {
                        job_id: taskId,
                        status: 'cancelled',
                        cancelled_at: new Date().toISOString(),
                    },
                });
            } else {
                // Cancel API failed but we've already stopped the UI
                setProgress({
                    status: 'cancelled',
                    message: 'Cancelled (cleanup may have failed: ' +
                        (result.error || 'unknown error') + ')'
                });
            }
        } catch (err) {
            // Even if cancel API throws, keep UI in cancelled state
            setProgress({
                status: 'cancelled',
                message: 'Cancelled (error during cleanup: ' +
                    (err.message || 'unknown error') + ')'
            });
        } finally {
            setIsCancelling(false);
        }
    };

    // Get selected agent info
    const selectedAgentInfo = availableAgents.find(a => a.agent_id === selectedAgent);

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <div className="flex justify-between items-start">
                    <div>
                        <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                        <p className="text-gray-600 mt-1">{definition.description}</p>
                    </div>
                    {opportunityId && (
                        <div className="text-sm text-gray-500 bg-gray-100 px-3 py-1 rounded">
                            {opportunityName}
                        </div>
                    )}
                </div>
            </div>

            {/* Loading sessions indicator */}
            {loadingSessions && (
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-6 text-center">
                    <i className="fa-solid fa-spinner fa-spin text-gray-400 text-xl mb-2"></i>
                    <p className="text-gray-500">Loading audit sessions...</p>
                </div>
            )}

            {/* Audit Sessions Table (shows when sessions exist) */}
            {!loadingSessions && hasLinkedSessions && (
                <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                    <div className="px-6 py-4 bg-green-50 border-b border-green-100">
                        <h2 className="text-lg font-semibold text-green-800 flex items-center gap-2">
                            <i className="fa-solid fa-check-circle"></i>
                            Audit Sessions Created
                        </h2>
                        <p className="text-sm text-green-600 mt-1">
                            {linkedSessions.length} session
                            {linkedSessions.length !== 1 ? 's' : ''} linked to this workflow run
                        </p>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="min-w-full divide-y divide-gray-200">
                            <thead className="bg-gray-50">
                                <tr>
                                    <th className={'px-4 py-3 text-left text-xs font-medium ' +
                                        'text-gray-500 uppercase tracking-wider'}>
                                        FLW
                                    </th>
                                    <th className={'px-4 py-3 text-center text-xs font-medium ' +
                                        'text-gray-500 uppercase tracking-wider'}>
                                        Visits
                                    </th>
                                    <th className={'px-4 py-3 text-center text-xs font-medium ' +
                                        'text-gray-500 uppercase tracking-wider'}>
                                        Status
                                    </th>
                                    <th className={'px-4 py-3 text-center text-xs font-medium ' +
                                        'text-gray-500 uppercase tracking-wider'}>
                                        <span className="text-green-600">Pass</span>
                                        {' / '}
                                        <span className="text-red-600">Fail</span>
                                    </th>
                                    <th className={'px-4 py-3 text-center text-xs font-medium ' +
                                        'text-purple-600 uppercase tracking-wider'}>
                                        AI Match
                                    </th>
                                    <th className={'px-4 py-3 text-center text-xs font-medium ' +
                                        'text-purple-600 uppercase tracking-wider'}>
                                        AI No Match
                                    </th>
                                    <th className={'px-4 py-3 text-right text-xs font-medium ' +
                                        'text-gray-500 uppercase tracking-wider'}>
                                        Actions
                                    </th>
                                </tr>
                            </thead>
                            <tbody className="bg-white divide-y divide-gray-200">
                                {linkedSessions.map(session => {
                                    const stats = session.assessment_stats || {};
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
                                                <span className={
                                                    'px-2 py-1 text-xs font-medium rounded ' +
                                                    (session.status === 'completed'
                                                        ? 'bg-green-100 text-green-700'
                                                        : 'bg-yellow-100 text-yellow-700')
                                                }>
                                                    {session.status === 'completed' ? 'Completed' : 'In Progress'}
                                                </span>
                                            </td>
                                            <td className="px-4 py-4 text-center">
                                                <div className="flex items-center justify-center gap-2 text-sm">
                                                    <span className="text-green-600 font-medium">
                                                        {stats.pass || 0}
                                                    </span>
                                                    <span className="text-gray-400">/</span>
                                                    <span className="text-red-600 font-medium">
                                                        {stats.fail || 0}
                                                    </span>
                                                    {stats.pending > 0 && (
                                                        <span className="text-gray-400 text-xs">
                                                            ({stats.pending} pending)
                                                        </span>
                                                    )}
                                                </div>
                                            </td>
                                            <td className="px-4 py-4 text-center">
                                                <span className="text-sm font-medium text-green-600">
                                                    {stats.ai_match || 0}
                                                </span>
                                            </td>
                                            <td className="px-4 py-4 text-center">
                                                <span className="text-sm font-medium text-red-600">
                                                    {stats.ai_no_match || 0}
                                                </span>
                                                {stats.ai_error > 0 && (
                                                    <span className="text-xs text-yellow-600 ml-1">
                                                        ({stats.ai_error} err)
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-4 py-4 text-right">
                                                <a
                                                    href={'/audit/' + session.id + '/bulk/' +
                                                        '?opportunity_id=' + session.opportunity_id}
                                                    className={'inline-flex items-center px-3 py-1.5 text-sm ' +
                                                        'bg-blue-50 text-blue-700 rounded hover:bg-blue-100 ' +
                                                        'border border-blue-200 transition-colors'}
                                                >
                                                    <i className="fa-solid fa-arrow-up-right-from-square mr-1.5"></i>
                                                    Review
                                                </a>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    {/* Aggregate Stats */}
                    {linkedSessions.length > 1 && (
                        <div className="px-6 py-4 bg-gray-50 border-t border-gray-200">
                            <div className="flex items-center justify-between">
                                <span className="text-sm font-medium text-gray-700">Totals:</span>
                                <div className="flex gap-6 text-sm">
                                    <span className="text-gray-600">
                                        <span className="font-medium">
                                            {linkedSessions.reduce((sum, s) => sum + (s.visit_count || 0), 0)}
                                        </span> visits
                                    </span>
                                    <span className="text-green-600">
                                        <i className="fa-solid fa-check mr-1"></i>
                                        {linkedSessions.reduce(
                                            (sum, s) => sum + (s.assessment_stats?.pass || 0), 0
                                        )} pass
                                    </span>
                                    <span className="text-red-600">
                                        <i className="fa-solid fa-xmark mr-1"></i>
                                        {linkedSessions.reduce(
                                            (sum, s) => sum + (s.assessment_stats?.fail || 0), 0
                                        )} fail
                                    </span>
                                    <span className="text-purple-600">
                                        <i className="fa-solid fa-robot mr-1"></i>
                                        {linkedSessions.reduce(
                                            (sum, s) => sum + (s.assessment_stats?.ai_match || 0), 0
                                        )} AI match /
                                        {' '}{linkedSessions.reduce(
                                            (sum, s) => sum + (s.assessment_stats?.ai_no_match || 0), 0
                                        )} no match
                                    </span>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Configuration (only show if no linked sessions yet) */}
            {!loadingSessions && !hasLinkedSessions && (
                <div className="bg-white rounded-lg shadow-sm p-6 space-y-6">
                    {/* Summary Card */}
                    <div className="bg-gradient-to-r from-blue-50 to-indigo-50 rounded-xl p-5 border border-blue-100">
                        <div className="flex items-start justify-between mb-4">
                            <div>
                                <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
                                    <span className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
                                        <i className="fa-solid fa-clipboard-check text-white text-sm"></i>
                                    </span>
                                    {getAuditTitle()}
                                </h2>
                                <p className="text-sm text-gray-500 mt-1 ml-10">
                                    {auditMode === 'date_range'
                                        ? 'Date range audit'
                                        : 'Last ' + lastNCount + ' visits'} - Weight validation
                                </p>
                            </div>
                            {selectedAgent && (
                                <span className={'inline-flex items-center gap-1.5 px-3 py-1 ' +
                                    'bg-green-100 text-green-700 rounded-full text-xs font-medium'}>
                                    <i className="fa-solid fa-robot"></i>
                                    AI Review Enabled
                                </span>
                            )}
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <div className="bg-white/60 rounded-lg p-3 border border-white">
                                <div className="flex items-center gap-2 text-gray-500 text-xs mb-1">
                                    <i className="fa-solid fa-users"></i>
                                    Audit Type
                                </div>
                                <div className="font-medium text-gray-900">One audit per FLW</div>
                            </div>
                            <div className="bg-white/60 rounded-lg p-3 border border-white">
                                <div className="flex items-center gap-2 text-gray-500 text-xs mb-2">
                                    <i className="fa-solid fa-link"></i>
                                    Related Fields
                                </div>
                                <div className="space-y-1">
                                    <div className="flex items-center gap-2 text-sm">
                                        <i className="fa-solid fa-image text-blue-500 w-4"></i>
                                        <span className="text-gray-600">Image:</span>
                                        <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">
                                            anthropometric/upload_weight_image
                                        </code>
                                    </div>
                                    <div className="flex items-center gap-2 text-sm">
                                        <i className="fa-solid fa-weight-scale text-green-500 w-4"></i>
                                        <span className="text-gray-600">Reading:</span>
                                        <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">
                                            child_weight_visit
                                        </code>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Audit Mode Selection */}
                    <div>
                        <h3 className="text-sm font-medium text-gray-700 mb-3">
                            <i className="fa-solid fa-sliders mr-2 text-gray-400"></i>
                            Visit Selection
                        </h3>
                        <div className="flex gap-2 mb-4">
                            <button
                                onClick={() => setAuditMode('date_range')}
                                className={
                                    'flex-1 px-4 py-3 text-sm rounded-lg border-2 transition-colors ' +
                                    (auditMode === 'date_range'
                                        ? 'bg-blue-50 text-blue-700 border-blue-500'
                                        : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300')
                                }
                            >
                                <i className="fa-solid fa-calendar mr-2"></i>
                                Date Range
                            </button>
                            <button
                                onClick={() => setAuditMode('last_n_per_opp')}
                                className={
                                    'flex-1 px-4 py-3 text-sm rounded-lg border-2 transition-colors ' +
                                    (auditMode === 'last_n_per_opp'
                                        ? 'bg-blue-50 text-blue-700 border-blue-500'
                                        : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300')
                                }
                            >
                                <i className="fa-solid fa-list-ol mr-2"></i>
                                Last N Visits
                            </button>
                        </div>

                        {/* Date Range Options (shown when date_range mode) */}
                        {auditMode === 'date_range' && (
                            <div className="p-4 bg-gray-50 rounded-lg border border-gray-200">
                                {/* Preset buttons */}
                                <div className="flex flex-wrap gap-2 mb-3">
                                    {[
                                        { id: 'last_week', label: 'Last Week' },
                                        { id: 'last_7_days', label: 'Last 7 Days' },
                                        { id: 'last_14_days', label: 'Last 14 Days' },
                                        { id: 'last_30_days', label: 'Last 30 Days' },
                                        { id: 'this_month', label: 'This Month' },
                                        { id: 'last_month', label: 'Last Month' },
                                        { id: 'custom', label: 'Custom' },
                                    ].map(preset => (
                                        <button
                                            key={preset.id}
                                            onClick={() => applyPreset(preset.id)}
                                            className={
                                                'px-3 py-1.5 text-sm rounded-full border transition-colors ' +
                                                (datePreset === preset.id
                                                    ? 'bg-blue-600 text-white border-blue-600'
                                                    : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400')
                                            }
                                        >
                                            {preset.label}
                                        </button>
                                    ))}
                                </div>
                                {/* Date inputs */}
                                <div className="flex gap-4 items-center">
                                    <div>
                                        <label className="block text-xs text-gray-500 mb-1">Start</label>
                                        <input
                                            type="date"
                                            value={startDate}
                                            onChange={e => { setStartDate(e.target.value); setDatePreset('custom'); }}
                                            className="border border-gray-300 rounded px-3 py-2 text-sm"
                                        />
                                    </div>
                                    <div>
                                        <label className="block text-xs text-gray-500 mb-1">End</label>
                                        <input
                                            type="date"
                                            value={endDate}
                                            onChange={e => { setEndDate(e.target.value); setDatePreset('custom'); }}
                                            className="border border-gray-300 rounded px-3 py-2 text-sm"
                                        />
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Last N Options (shown when last_n_per_opp mode) */}
                        {auditMode === 'last_n_per_opp' && (
                            <div className="p-4 bg-gray-50 rounded-lg border border-gray-200">
                                <div className="flex items-center gap-3">
                                    <label className="text-sm text-gray-700">
                                        Get the last
                                    </label>
                                    <input
                                        type="number"
                                        min="1"
                                        max="1000"
                                        value={lastNCount}
                                        onChange={e => setLastNCount(parseInt(e.target.value) || 10)}
                                        className="w-20 border border-gray-300 rounded px-3 py-2 text-sm text-center"
                                    />
                                    <label className="text-sm text-gray-700">
                                        visits per opportunity
                                    </label>
                                </div>
                                <p className="text-xs text-gray-500 mt-2">
                                    This will select the most recent {lastNCount} visits, regardless of date.
                                </p>
                            </div>
                        )}
                    </div>

                    {/* Filter Options */}
                    <div>
                        <h3 className="text-sm font-medium text-gray-700 mb-3">
                            <i className="fa-solid fa-filter mr-2 text-blue-500"></i>
                            Visit Filters
                        </h3>
                        <div className="p-4 bg-gray-50 rounded-lg border border-gray-200 space-y-3">
                            <label className="flex items-center gap-3 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={filterByImage}
                                    onChange={e => setFilterByImage(e.target.checked)}
                                    className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
                                />
                                <div>
                                    <div className="text-sm text-gray-900">
                                        Only include visits with weight image
                                    </div>
                                    <div className="text-xs text-gray-500">
                                        Excludes visits without an uploaded scale photo
                                    </div>
                                </div>
                            </label>
                            <label className="flex items-center gap-3 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={filterByField}
                                    onChange={e => setFilterByField(e.target.checked)}
                                    className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
                                />
                                <div>
                                    <div className="text-sm text-gray-900">
                                        Only include visits with weight reading
                                    </div>
                                    <div className="text-xs text-gray-500">
                                        Excludes visits without a recorded weight value
                                    </div>
                                </div>
                            </label>
                        </div>
                    </div>

                    {/* AI Agent Selection */}
                    <div>
                        <h3 className="text-sm font-medium text-gray-700 mb-2">
                            <i className="fa-solid fa-robot mr-2 text-purple-500"></i>
                            AI Review Agent
                        </h3>
                        {loadingAgents ? (
                            <div className="text-gray-500 text-sm">
                                <i className="fa-solid fa-spinner fa-spin mr-2"></i>Loading agents...
                            </div>
                        ) : (
                            <div className="space-y-2">
                                <select
                                    value={selectedAgent}
                                    onChange={e => setSelectedAgent(e.target.value)}
                                    className="border border-gray-300 rounded px-3 py-2 text-sm w-full md:w-auto"
                                >
                                    <option value="">None - Skip AI review</option>
                                    {availableAgents.map(agent => (
                                        <option key={agent.agent_id} value={agent.agent_id}>
                                            {agent.name}
                                        </option>
                                    ))}
                                </select>
                                {selectedAgentInfo && (
                                    <div className="text-sm text-purple-700 bg-purple-50 px-3 py-2 rounded">
                                        {selectedAgentInfo.description}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Create Button */}
                    <div className="pt-4 border-t border-gray-200">
                        <button
                            onClick={handleCreateAudit}
                            disabled={isRunning || !opportunityId || instance.status === 'completed'}
                            className={'inline-flex items-center px-6 py-3 bg-blue-600 ' +
                                'text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 font-medium'}
                        >
                            <i className="fa-solid fa-play mr-2"></i>
                            Create Weekly Audit{selectedAgent && ' with AI Review'}
                        </button>
                        {!opportunityId && (
                            <p className="mt-2 text-sm text-red-600">
                                No opportunity selected. Select an opportunity from the sidebar.
                            </p>
                        )}
                        {instance.status === 'completed' && (
                            <p className="mt-2 text-sm text-gray-500">
                                This run is completed. New audits cannot be created.
                            </p>
                        )}
                    </div>
                </div>
            )}

            {/* Progress (shown below config when running) */}
            {isRunning && progress && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-3">
                        <div className="flex items-center gap-3">
                            <i className="fa-solid fa-spinner fa-spin text-blue-600"></i>
                            <span className="font-medium text-blue-800">
                                {progress.stage_name || 'Processing...'}
                            </span>
                        </div>
                        <div className="flex items-center gap-3">
                            {progress.current_stage && progress.total_stages && (
                                <span className="text-sm text-blue-600">
                                    Stage {progress.current_stage}/{progress.total_stages}
                                </span>
                            )}
                            <button
                                onClick={handleCancel}
                                disabled={isCancelling}
                                className={'px-3 py-1 text-sm text-red-600 hover:text-red-800 ' +
                                    'hover:bg-red-100 rounded transition-colors disabled:opacity-50'}
                            >
                                <i className="fa-solid fa-times mr-1"></i>Cancel
                            </button>
                        </div>
                    </div>
                    {progress.total > 0 && (
                        <div className="w-full bg-blue-200 rounded-full h-2">
                            <div
                                className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                                style={{ width: (progress.processed / progress.total * 100) + '%' }}
                            ></div>
                        </div>
                    )}
                    <div className="mt-2 text-sm text-blue-700">{progress.message}</div>
                </div>
            )}

            {/* Error */}
            {progress?.status === 'failed' && !isRunning && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                    <div className="flex items-center gap-2 text-red-800">
                        <i className="fa-solid fa-circle-exclamation"></i>
                        <span className="font-medium">Error: {progress.error}</span>
                    </div>
                </div>
            )}

            {/* Cancelled */}
            {progress?.status === 'cancelled' && !isRunning && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                    <div className="flex items-center gap-2 text-amber-800">
                        <i className="fa-solid fa-ban"></i>
                        <span className="font-medium">Audit creation was cancelled</span>
                    </div>
                </div>
            )}
        </div>
    );
}"""

# Template export - this is what the registry imports
TEMPLATE = {
    "key": "audit_with_ai_review",
    "name": "Weekly KMC Audit with AI Review",
    "description": "Create weekly KMC audit sessions per FLW with AI image validation",
    "icon": "fa-calendar-check",
    "color": "purple",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": None,  # No pipeline needed - uses audit creation API
}
