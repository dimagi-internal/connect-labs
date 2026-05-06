"""Program admin audit of the LLO's weekly review process.

Reads the saved runs of an ``llo_weekly_review`` instance and renders a
week-over-week compliance dashboard: did the LLO save a snapshot, did
they spawn coaching tasks for everyone they should have, did flagged
FLWs improve. Multi-opp capable so a regional admin can roll up several
opportunities into one audit.
"""

DEFINITION = {
    "name": "Program Admin LLO Audit",
    "description": ("Week-by-week meta view of how well the LLO is performing the " "operational weekly review."),
    "version": 1,
    "templateType": "program_admin_audit",
    "statuses": [
        {"id": "pending", "label": "Pending", "color": "gray"},
        {"id": "compliant", "label": "Compliant", "color": "green"},
        {"id": "gap", "label": "Process Gap", "color": "yellow"},
        {"id": "intervention_needed", "label": "Needs Intervention", "color": "red"},
    ],
    "config": {
        "showSummaryCards": True,
        # Set by ACE Phase 6 synthetic-workflow-seed:
        "watched_workflow_id": None,
    },
    "pipeline_sources": [],
}

RENDER_CODE = """function WorkflowUI({ definition, instance, links, actions, onUpdateState, view }) {
    // Scaffold render — ACE Phase 6 polish skill layers per-opp visuals on top.
    // Placeholder: `view.watchedSnapshots` is provided by the seeding step or the
    // polish skill via custom render-data wiring. Until that's hooked up, this
    // renders an empty state.
    const watchedId = definition.config && definition.config.watched_workflow_id;
    const snapshots = view.watchedSnapshots || [];

    if (!watchedId) {
        return <div>Set <code>watched_workflow_id</code> in this workflow's config.</div>;
    }

    return (
        <div className="program-admin-audit">
            <h1>{definition.name}</h1>
            <p>Watching workflow #{watchedId}</p>
            <table>
                <thead>
                    <tr>
                        <th>Snapshot</th>
                        <th>Captured</th>
                        <th>FLWs reviewed</th>
                        <th>Underperformers flagged</th>
                        <th>Coaching tasks spawned</th>
                        <th>Compliance</th>
                    </tr>
                </thead>
                <tbody>
                    {snapshots.map(s => {
                        const flagged = (s.metrics && s.metrics.flagged) || 0;
                        const spawned = (s.metrics && s.metrics.tasks_spawned) || 0;
                        const compliant = flagged === 0 || spawned >= flagged;
                        return (
                            <tr key={s.name}>
                                <td>{s.name}</td>
                                <td>{s.captured_at}</td>
                                <td>{(s.metrics && s.metrics.workers_reviewed) || 0}</td>
                                <td>{flagged}</td>
                                <td>{spawned}</td>
                                <td>{compliant ? "✓" : "gap"}</td>
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
    "key": "program_admin_audit",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-shield-halved",
    "color": "purple",
    "multi_opp": True,
    "supports_saved_runs": True,
    "snapshot_inputs": {
        "pipelines": [],
        "state_keys": ["audit_decisions"],
    },
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": None,  # this template reads the watched workflow's snapshots, not a pipeline
}
