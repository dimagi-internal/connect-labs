"""LLO weekly FLW performance review (synthetic-data demo scaffold).

A config-driven template the ACE Phase 6 synthetic generator instantiates.
The KPI list and coaching-task template are filled in via the workflow
definition's ``config`` dict; ACE's polish skill rewrites the JSX to feature
specific FLWs and seeded anomalies. Out of the box this template is
opp-agnostic and renders a generic per-FLW KPI table with a "spawn coaching
task" button and a chat-styled task drawer.
"""

PIPELINE_SCHEMA = {
    "name": "FLW KPI Aggregates",
    "description": ("Per-FLW aggregates of the KPIs declared in the workflow's " "kpi_config. One row per worker."),
    "version": 1,
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    # This is a weekly review: each saved run carries a period, and its
    # snapshot must reflect only that week's visits. `period_scoped` tells the
    # snapshot builder to re-aggregate this pipeline to the run's
    # `[period_start, period_end)` visit-date window instead of freezing the
    # all-time total — so Week 1 and Week 2 snapshots differ (ace#764). The
    # window is applied at read time from the existing cache; no recompute.
    "period_scoped": True,
    "fields": [
        # Real fields are injected by the seeding step using kpi_config —
        # the scaffold ships an empty list because field paths depend on the
        # opportunity's form schema.
    ],
}

DEFINITION = {
    "name": "LLO Weekly FLW Review",
    "description": (
        "Operational weekly view: each FLW's KPI scorecard, an "
        "underperforming-only filter, and a one-click coaching task spawn."
    ),
    "version": 1,
    "templateType": "llo_weekly_review",
    "statuses": [
        {"id": "pending", "label": "Pending Review", "color": "gray"},
        {"id": "ok", "label": "On Track", "color": "green"},
        {"id": "underperforming", "label": "Underperforming", "color": "yellow"},
        {"id": "task_created", "label": "Coaching Task Created", "color": "blue"},
    ],
    "config": {
        "showSummaryCards": True,
        "showFilters": True,
        # Filled in by ACE Phase 6 synthetic-workflow-seed:
        "kpi_config": [],  # list of KpiSpec dicts
        "coaching_task_template": {  # task-spawn template
            "subject_template": "Coaching feedback — week {week} for {flw_name}",
            "ocs_persona": "supportive_coach",
        },
    },
    "pipeline_sources": [],
}

RENDER_CODE = """function WorkflowUI({ definition, instance, links, actions, onUpdateState, view }) {
    const config = definition.config || {};
    const workers = view.workers || [];
    const kpis = config.kpi_config || [];
    const states = view.state.worker_states || {};
    const tasks = view.state.spawned_tasks || {};
    const isCompleted = view.isCompleted;
    const [showOnlyUnderperforming, setShowOnlyUnderperforming] = React.useState(false);

    // `actions.spawnCoachingTask` / `actions.openTaskDrawer` are not part of the
    // default ActionHandlers interface. A live instance wires them to real labs
    // Task records; in the seed they may be undefined, so the buttons below are
    // disabled rather than throwing on click.
    const rowsByUser = (view.pipelines.flw_kpis?.rows || []).reduce((acc, r) => {
        acc[r.username] = r;
        return acc;
    }, {});

    // A KPI is "under" when it is below its own threshold. Shared by the filter,
    // the per-cell highlight and the summary count so they can't disagree.
    const isUnder = (row, k) =>
        k.threshold_underperform != null &&
        row[k.kpi] !== undefined &&
        row[k.kpi] !== null &&
        row[k.kpi] < k.threshold_underperform;
    const underKpis = w => {
        const row = rowsByUser[w.username] || {};
        return kpis.filter(k => isUnder(row, k));
    };

    const filtered = workers.filter(w => !showOnlyUnderperforming || underKpis(w).length > 0);

    const stats = {
        total: workers.length,
        underperforming: workers.filter(w => underKpis(w).length > 0).length,
        tasked: workers.filter(w => tasks[w.username]).length,
        reviewed: workers.filter(w => states[w.username] && states[w.username] !== "pending").length,
    };

    const statusStyle = id => ({
        ok: "bg-green-100 text-green-800",
        underperforming: "bg-yellow-100 text-yellow-800",
        task_created: "bg-blue-100 text-blue-800",
    }[id] || "bg-gray-100 text-gray-700");
    const statusLabel = id =>
        ((definition.statuses || []).find(s => s.id === id) || {}).label || id;

    return (
        <div className="space-y-4">
            <div className="bg-white rounded-lg shadow-sm p-4">
                <div className="flex flex-wrap justify-between items-center gap-3">
                    <div>
                        <h1 className="text-xl font-semibold text-gray-900">{definition.name}</h1>
                        {view.asOf && (
                            <div className="text-sm text-gray-500">As of {view.asOf}</div>
                        )}
                    </div>
                    {isCompleted && (
                        <span className="px-2 py-1 rounded text-sm bg-gray-100 text-gray-700">
                            Completed — read only
                        </span>
                    )}
                </div>
            </div>

            {/* Summary Cards. `showSummaryCards` was declared in config and never
                read, so the flag did nothing and the page opened with no
                orientation at all (#1184). */}
            {config.showSummaryCards !== false && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div className="bg-white p-4 rounded-lg shadow-sm">
                        <div className="text-3xl font-bold text-gray-900">{stats.total}</div>
                        <div className="text-gray-600 text-sm">Workers</div>
                    </div>
                    <div className="bg-yellow-50 p-4 rounded-lg shadow-sm border border-yellow-200">
                        <div className="text-3xl font-bold text-yellow-700">{stats.underperforming}</div>
                        <div className="text-gray-600 text-sm">Underperforming</div>
                    </div>
                    <div className="bg-blue-50 p-4 rounded-lg shadow-sm border border-blue-200">
                        <div className="text-3xl font-bold text-blue-700">{stats.tasked}</div>
                        <div className="text-gray-600 text-sm">Coaching tasks</div>
                    </div>
                    <div className="bg-green-50 p-4 rounded-lg shadow-sm border border-green-200">
                        <div className="text-3xl font-bold text-green-700">{stats.reviewed}</div>
                        <div className="text-gray-600 text-sm">Reviewed</div>
                    </div>
                </div>
            )}

            {config.showFilters !== false && !isCompleted && (
                <div className="bg-white rounded-lg shadow-sm p-4">
                    <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                        <input
                            type="checkbox"
                            className="rounded border-gray-300"
                            checked={showOnlyUnderperforming}
                            onChange={e => setShowOnlyUnderperforming(e.target.checked)}
                        />
                        Show underperforming only
                    </label>
                </div>
            )}

            <div className="bg-white rounded-lg shadow-sm overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200 text-sm">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-4 py-2 text-left font-medium text-gray-600">FLW</th>
                            {kpis.map(k => (
                                <th key={k.kpi} className="px-4 py-2 text-right font-medium text-gray-600">
                                    {k.label || k.kpi}
                                    {k.threshold_underperform != null && (
                                        <span className="block text-xs font-normal text-gray-400">
                                            target ≥ {k.threshold_underperform}
                                        </span>
                                    )}
                                </th>
                            ))}
                            <th className="px-4 py-2 text-left font-medium text-gray-600">Status</th>
                            <th className="px-4 py-2 text-left font-medium text-gray-600">Action</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                        {filtered.length === 0 && (
                            <tr>
                                <td colSpan={kpis.length + 3} className="px-4 py-6 text-center text-gray-500">
                                    {workers.length === 0
                                        ? "No workers on this opportunity yet."
                                        : "No underperforming workers this week."}
                                </td>
                            </tr>
                        )}
                        {filtered.map(w => {
                            const row = rowsByUser[w.username] || {};
                            const status = states[w.username] || "pending";
                            const task = tasks[w.username];
                            return (
                                <tr key={w.username} className="hover:bg-gray-50">
                                    <td className="px-4 py-2 text-gray-900">{w.name || w.username}</td>
                                    {kpis.map(k => {
                                        const v = row[k.kpi];
                                        const under = isUnder(row, k);
                                        return (
                                            <td
                                                key={k.kpi}
                                                className={
                                                    "px-4 py-2 text-right tabular-nums " +
                                                    (under ? "bg-yellow-50 text-yellow-800 font-semibold" : "text-gray-700")
                                                }
                                                title={under ? "Below threshold " + k.threshold_underperform : undefined}
                                            >
                                                {v != null ? Number(v).toFixed(2) : "—"}
                                            </td>
                                        );
                                    })}
                                    <td className="px-4 py-2">
                                        <span className={"px-2 py-0.5 rounded text-xs " + statusStyle(status)}>
                                            {statusLabel(status)}
                                        </span>
                                    </td>
                                    <td className="px-4 py-2">
                                        {task ? (
                                            <button
                                                onClick={() => actions.openTaskDrawer && actions.openTaskDrawer(task.id)}
                                                disabled={!actions.openTaskDrawer}
                                                className="px-2 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                                            >
                                                View coaching chat
                                            </button>
                                        ) : !isCompleted ? (
                                            <button
                                                onClick={() => actions.spawnCoachingTask && actions.spawnCoachingTask(w.username)}
                                                disabled={!actions.spawnCoachingTask}
                                                className="px-2 py-1 text-xs rounded border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                                                title={actions.spawnCoachingTask ? undefined : "Task spawning is wired up per opportunity"}
                                            >
                                                Spawn coaching task
                                            </button>
                                        ) : (
                                            <span className="text-gray-400">—</span>
                                        )}
                                    </td>
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
    "key": "llo_weekly_review",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-chart-bar",
    "color": "blue",
    "multi_opp": False,
    "supports_saved_runs": True,
    # The render code reads ``view.pipelines.flw_kpis`` and the snapshot below
    # captures the ``flw_kpis`` alias, so the created pipeline source must use
    # that same alias (not the default ``"data"``) — see #464.
    "pipeline_alias": "flw_kpis",
    "snapshot_inputs": {
        "pipelines": ["flw_kpis"],
        "state_keys": ["worker_states", "spawned_tasks"],
    },
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
