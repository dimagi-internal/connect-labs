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
    // Scaffold render — ACE Phase 6 polish skill layers per-opp visuals on top.
    const workers = view.workers || [];
    const kpis = (definition.config && definition.config.kpi_config) || [];
    const states = view.state.worker_states || {};
    const tasks = view.state.spawned_tasks || {};
    const isCompleted = view.isCompleted;
    const [showOnlyUnderperforming, setShowOnlyUnderperforming] = React.useState(false);

    // Placeholders: `actions.spawnCoachingTask` and `actions.openTaskDrawer` are
    // not part of the default ActionHandlers interface — the ACE Phase 6
    // `synthetic-workflow-polish` skill replaces this render code with versions
    // that wire those up to real labs Task records and the chat-styled drawer.
    // Until then, clicking the buttons is a no-op (the handler is undefined).
    const rowsByUser = (view.pipelines.flw_kpis?.rows || []).reduce((acc, r) => {
        acc[r.username] = r;
        return acc;
    }, {});

    const filtered = workers.filter(w => {
        if (!showOnlyUnderperforming) return true;
        const row = rowsByUser[w.username] || {};
        return kpis.some(k => row[k.kpi] !== undefined && row[k.kpi] < k.threshold_underperform);
    });

    return (
        <div className="llo-weekly-review">
            <header>
                <h1>{definition.name}</h1>
                {!isCompleted && (
                    <label>
                        <input
                            type="checkbox"
                            checked={showOnlyUnderperforming}
                            onChange={e => setShowOnlyUnderperforming(e.target.checked)}
                        />
                        Show underperforming only
                    </label>
                )}
            </header>
            <table>
                <thead>
                    <tr>
                        <th>FLW</th>
                        {kpis.map(k => <th key={k.kpi}>{k.kpi}</th>)}
                        <th>Status</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {filtered.map(w => {
                        const row = rowsByUser[w.username] || {};
                        const status = states[w.username] || "pending";
                        const task = tasks[w.username];
                        return (
                            <tr key={w.username}>
                                <td>{w.name || w.username}</td>
                                {kpis.map(k => <td key={k.kpi}>{row[k.kpi] != null ? row[k.kpi].toFixed(2) : "-"}</td>)}
                                <td>{status}</td>
                                <td>
                                    {task ? (
                                        <button onClick={() => actions.openTaskDrawer(task.id)}>
                                            View coaching chat
                                        </button>
                                    ) : !isCompleted ? (
                                        <button onClick={() => actions.spawnCoachingTask(w.username)}>
                                            Spawn coaching task
                                        </button>
                                    ) : (
                                        <span>—</span>
                                    )}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
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
    "snapshot_inputs": {
        "pipelines": ["flw_kpis"],
        "state_keys": ["worker_states", "spawned_tasks"],
    },
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
