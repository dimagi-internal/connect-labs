"""
Performance Review Workflow Template.

Review worker performance and mark as confirmed, needs audit, or create tasks.
"""

PIPELINE_SCHEMA = {
    "name": "Worker Performance Data",
    "description": "Extract performance metrics from form submissions for each worker",
    "version": 1,
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [
        {
            "name": "visit_count",
            "path": "form.meta.instanceID",
            "aggregation": "count",
            "description": "Total form submissions",
        },
        {
            "name": "last_visit_date",
            "path": "form.meta.timeEnd",
            "aggregation": "last",
            "description": "Date of most recent submission",
        },
        {
            "name": "first_visit_date",
            "path": "form.meta.timeEnd",
            "aggregation": "first",
            "description": "Date of first submission",
        },
        {
            "name": "app_version",
            "path": "form.meta.appVersion",
            "aggregation": "last",
            "description": "Application version used",
        },
    ],
    "histograms": [],
    "filters": {},
}

DEFINITION = {
    "name": "Weekly Performance Review",
    "description": "Review each worker's performance and mark as confirmed, needs audit, or create a task",
    "version": 1,
    "templateType": "performance_review",
    "statuses": [
        {"id": "pending", "label": "Pending Review", "color": "gray"},
        {"id": "confirmed", "label": "Confirmed Good", "color": "green"},
        {"id": "needs_audit", "label": "Needs Audit", "color": "yellow"},
        {"id": "task_created", "label": "Task Created", "color": "blue"},
    ],
    "config": {
        "showSummaryCards": True,
        "showFilters": True,
    },
    "pipeline_sources": [],  # Will be populated when pipeline is created
    "card": {
        "card_type": "summary",
        "title": "Weekly Performance Review",
        "metrics": [{"label": "Cadence", "value": "Weekly"}],
    },
}

RENDER_CODE = """function WorkflowUI({ definition, instance, links, actions, onUpdateState, view }) {
    const [sortBy, setSortBy] = React.useState('name');
    const [filterStatus, setFilterStatus] = React.useState('all');

    // Read run data via the view helper — works the same whether the run is
    // in_progress (live data) or completed (snapshot data). Render code never
    // reaches into instance.snapshot or live props directly. See
    // WORKFLOW_REFERENCE.md §"Saved-runs templates".
    const workers = view.workers;
    const workerStates = view.state.worker_states || {};
    const isCompleted = view.isCompleted;

    const statuses = definition.statuses || [];
    const config = definition.config || {};

    // Calculate stats
    const stats = React.useMemo(() => {
        const counts = {};
        statuses.forEach(s => { counts[s.id] = 0; });
        workers.forEach(w => {
            const status = workerStates[w.username]?.status || 'pending';
            counts[status] = (counts[status] || 0) + 1;
        });
        return {
            total: workers.length,
            reviewed: workers.length - (counts['pending'] || 0),
            counts
        };
    }, [workers, workerStates, statuses]);

    // Filter workers
    const displayWorkers = React.useMemo(() => {
        let filtered = workers;
        if (filterStatus !== 'all') {
            filtered = workers.filter(w =>
                (workerStates[w.username]?.status || 'pending') === filterStatus
            );
        }
        return [...filtered].sort((a, b) => {
            if (sortBy === 'name') return (a.name || a.username).localeCompare(b.name || b.username);
            if (sortBy === 'visits') return b.visit_count - a.visit_count;
            return 0;
        });
    }, [workers, workerStates, filterStatus, sortBy]);

    const handleStatusChange = async (username, newStatus) => {
        if (isCompleted) return;  // Defensive: BE rejects with 409 anyway.
        await onUpdateState({
            worker_states: {
                ...workerStates,
                [username]: { ...workerStates[username], status: newStatus }
            }
        });
    };

    const handleComplete = async () => {
        const remaining = stats.total - stats.reviewed;
        const msg = remaining > 0
            ? "Mark this run complete? " + remaining + " worker(s) still pending — you won't be able to edit decisions after."
            : "Mark this run complete? You won't be able to edit decisions after.";
        await view.complete({ confirm: msg });
    };

    const getStatusColor = (statusId) => {
        const colorMap = {
            gray: 'bg-gray-100 text-gray-800',
            green: 'bg-green-100 text-green-800',
            yellow: 'bg-yellow-100 text-yellow-800',
            blue: 'bg-blue-100 text-blue-800',
            red: 'bg-red-100 text-red-800',
            purple: 'bg-purple-100 text-purple-800',
            orange: 'bg-orange-100 text-orange-800',
            pink: 'bg-pink-100 text-pink-800'
        };
        const status = statuses.find(s => s.id === statusId);
        return colorMap[status?.color] || colorMap.gray;
    };

    return (
        <div className="space-y-6">
            {/* Completion banner — shown only when viewing a completed run.
                Captured-at-completion-time data is the source; FE is read-only. */}
            {isCompleted && (
                <div className="bg-gray-100 border-l-4 border-gray-400 p-4 rounded">
                    <div className="text-sm text-gray-700">
                        <strong>This run is completed.</strong>
                        {view.asOf ? " Snapshot from " + new Date(view.asOf).toLocaleString() + "." : ""}
                        {" "}Decisions are read-only. To redo this work, start a new run.
                    </div>
                </div>
            )}

            {/* Header */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <div className="flex justify-between items-start">
                    <div>
                        <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                        <p className="text-gray-600 mt-1">{definition.description}</p>
                    </div>
                    <div className="flex flex-col items-end gap-2">
                        <div className="text-sm text-gray-500">
                            {instance.state?.period_start} - {instance.state?.period_end}
                        </div>
                        {!isCompleted && (
                            <button
                                onClick={handleComplete}
                                className="inline-flex items-center px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white text-sm font-medium rounded"
                            >
                                Mark Run Complete
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {/* Summary Cards */}
            {config.showSummaryCards !== false && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div className="bg-white p-4 rounded-lg shadow-sm">
                        <div className="text-3xl font-bold text-gray-900">{stats.total}</div>
                        <div className="text-gray-600">Total Workers</div>
                    </div>
                    <div className="bg-green-50 p-4 rounded-lg shadow-sm border border-green-200">
                        <div className="text-3xl font-bold text-green-700">{stats.reviewed}</div>
                        <div className="text-gray-600">Reviewed</div>
                    </div>
                    {statuses.slice(0, 2).map(status => (
                        <div key={status.id} className={"p-4 rounded-lg shadow-sm " + getStatusColor(status.id)}>
                            <div className="text-2xl font-bold">{stats.counts[status.id] || 0}</div>
                            <div className="text-sm">{status.label}</div>
                        </div>
                    ))}
                </div>
            )}

            {/* Filters */}
            {config.showFilters !== false && (
                <div className="bg-white rounded-lg shadow-sm p-4">
                    <div className="flex flex-wrap gap-4 items-center">
                        <select
                            value={filterStatus}
                            onChange={e => setFilterStatus(e.target.value)}
                            className="border border-gray-300 rounded-md px-3 py-2 text-sm"
                        >
                            <option value="all">All Statuses</option>
                            {statuses.map(s => (
                                <option key={s.id} value={s.id}>{s.label}</option>
                            ))}
                        </select>
                        <select
                            value={sortBy}
                            onChange={e => setSortBy(e.target.value)}
                            className="border border-gray-300 rounded-md px-3 py-2 text-sm"
                        >
                            <option value="name">Sort by Name</option>
                            <option value="visits">Sort by Visits</option>
                        </select>
                        <div className="ml-auto text-sm text-gray-500">
                            Showing {displayWorkers.length} of {workers.length} workers
                        </div>
                    </div>
                </div>
            )}

            {/* Worker Table */}
            <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                Worker
                            </th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                Opp
                            </th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                Visits
                            </th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                Last Active
                            </th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                Status
                            </th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                Actions
                            </th>
                        </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                        {displayWorkers.map(worker => {
                            const currentStatus = workerStates[worker.username]?.status || 'pending';
                            return (
                                <tr key={worker.username} className="hover:bg-gray-50">
                                    <td className="px-6 py-4 whitespace-nowrap">
                                        <div className="font-medium text-gray-900">
                                            {worker.name || worker.username}
                                        </div>
                                        <div className="text-sm text-gray-500">{worker.username}</div>
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                                        {worker.opportunity_id}
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                        {worker.visit_count || 0}
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                                        {worker.last_active || 'Never'}
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap">
                                        <select
                                            value={currentStatus}
                                            disabled={isCompleted}
                                            onChange={e => handleStatusChange(worker.username, e.target.value)}
                                            className="border rounded px-2 py-1 text-sm disabled:bg-gray-100 disabled:text-gray-500"
                                        >
                                            {statuses.map(s => (
                                                <option key={s.id} value={s.id}>{s.label}</option>
                                            ))}
                                        </select>
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                                        <div className="flex gap-2">
                                            <a
                                                href={links.auditUrl({ username: worker.username, count: 5 })}
                                                className="text-blue-600 hover:text-blue-800"
                                            >
                                                Audit
                                            </a>
                                            <a
                                                href={links.taskUrl({ username: worker.username })}
                                                className="text-blue-600 hover:text-blue-800"
                                            >
                                                Task
                                            </a>
                                        </div>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                {displayWorkers.length === 0 && (
                    <div className="px-6 py-12 text-center text-gray-500">
                        No workers match the current filter.
                    </div>
                )}
            </div>
        </div>
    );
}"""


# Snapshot contract for performance_review.
#
# Captured verbatim via the framework's default hook (see
# templates/__init__.py:_default_snapshot_from_inputs). The render code reads
# `view.workers` and `view.state.worker_states`, and computes summary counts
# at render time via React.useMemo — there's nothing to precompute, so we
# don't need a build_snapshot hook.
#
# - workers: FLW list at completion (so reopening shows the same workers
#   even if the live FLW list has since changed).
# - state.worker_states: per-FLW review decisions.
# - pipelines: empty — the template's pipeline_schema exists for future use,
#   but the render doesn't read pipeline rows, so we don't bloat the snapshot.
SNAPSHOT_INPUTS = {
    "pipelines": [],
    "workers": True,
    "state_keys": ["worker_states"],
}

SNAPSHOT_SCHEMA = {
    "version": 1,
    "keys": {
        "workers": "FLW list at completion (with opportunity_id tags for multi-opp)",
        "state.worker_states": "Per-FLW review decisions (keyed by username)",
        "opportunity_ids": "Opportunities the run covered",
    },
}


# Template export - this is what the registry imports
TEMPLATE = {
    "key": "performance_review",
    "name": "Weekly Performance Review",
    "description": "Review worker performance and mark as confirmed, needs audit, or create tasks",
    "icon": "fa-clipboard-check",
    "color": "green",
    "multi_opp": True,
    # Run-shaped: opts in to the in_progress | completed lifecycle. Reference
    # implementation for the saved-runs framework — see WORKFLOW_REFERENCE.md
    # §"Saved-runs templates".
    "supports_saved_runs": True,
    # Declarative snapshot — captures workers + state.worker_states verbatim;
    # render code recomputes summary cards from that. Reference adopter for
    # the framework's default hook path. See WORKFLOW_REFERENCE.md §9.
    "snapshot_inputs": SNAPSHOT_INPUTS,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
