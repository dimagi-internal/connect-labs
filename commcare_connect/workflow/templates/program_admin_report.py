"""Program Admin Report Workflow Template.

Multi-opp + saved-runs template that gives a program admin a window-scoped
view of which network managers ran the SOP and what happened to FLWs they
flagged. Reads from any "watched" workflow whose decisions are recorded
via the Phase 1 Decision contract (spec §3).

See docs/superpowers/specs/2026-05-25-program-admin-report-design.md.
"""

import logging
from datetime import datetime

from commcare_connect.decisions.data_access import DecisionsDataAccess
from commcare_connect.tasks.data_access import TaskDataAccess
from commcare_connect.workflow.data_access import WorkflowDataAccess, get_saved_runs_for_program_report

logger = logging.getLogger(__name__)


DEFINITION = {
    "name": "Program Admin Report",
    "description": "Cross-opportunity rollup of weekly SOP compliance + per-FLW decision/audit/task outcomes.",
    "version": 1,
    "templateType": "program_admin_report",
    "statuses": [],
    "config": {
        "watched_sources": [],
        "window_start": None,
        "window_end": None,
        "expected_run_dow": "monday",
    },
    "pipeline_sources": [],
}


def _build_request_for_hook():
    """Hook seam — the framework currently passes pipelines/state into hooks
    but not the full request. For now hooks run with the same access scope
    as the user who triggered completion; the LabsRecord API enforces ACL
    on the underlying writes. See spec §5.3 for the rationale.
    """
    return None


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def build_snapshot(
    *, pipelines, state, opportunity_id, workers, opportunity_ids, definition_id, request=None, access_token=None, **_
):
    """Freeze a window-scoped rollup of decisions + their live task/audit
    status at run completion time. See spec §5.3.

    `state` must contain ``window_start``, ``window_end``, ``watched_sources``.

    Auth: the hook accepts either ``request`` (web view path — token is
    extracted from session) or ``access_token`` (MCP/CLI path — caller has
    the Connect OAuth token already). Exactly one must yield a token; the
    DAOs raise ``ValueError`` if neither is present.
    """
    window_start = _parse_iso(state.get("window_start"))
    window_end = _parse_iso(state.get("window_end"))
    watched_sources = state.get("watched_sources", [])

    if not window_start or not window_end:
        return {"schema_version": 1, "watched_summary": [], "error": "missing_window"}

    wda = WorkflowDataAccess(request=request, access_token=access_token)
    sources = get_saved_runs_for_program_report(
        wda,
        watched_sources=watched_sources,
        window_start=window_start,
        window_end=window_end,
    )

    dda = DecisionsDataAccess(request=request, access_token=access_token, opportunity_id=opportunity_id)
    tda = TaskDataAccess(request=request, access_token=access_token, opportunity_id=opportunity_id)

    watched_summary = []
    for src in sources:
        run_summaries = []
        for run in src["runs"]:
            decisions = []
            for d in dda.get_decisions_for_run(run.id):
                task_outcomes = []
                for tid in d.task_ids:
                    try:
                        t = tda.get_task(tid)
                    except Exception:
                        logger.warning("Failed to fetch task %s for decision %s", tid, d.id)
                        continue
                    if t is None:
                        continue
                    task_outcomes.append(
                        {
                            "id": t.id,
                            "status": t.status,
                            "official_action": (t.resolution_details or {}).get("official_action"),
                            "closed_at": next(
                                (e.get("timestamp") for e in (t.events or []) if e.get("event_type") == "closed"),
                                None,
                            ),
                        }
                    )
                decisions.append(
                    {
                        "id": d.id,
                        "flw_id": d.flw_id,
                        "decision_type": d.decision_type,
                        "reason_key": d.reason_key,
                        "reason_label": d.reason_label,
                        "audit_session_ids": d.audit_session_ids,
                        "task_ids": d.task_ids,
                        "audit_outcomes": [],
                        "task_outcomes": task_outcomes,
                        "decided_at": d.decided_at,
                    }
                )
            run_summaries.append(
                {
                    "id": run.id,
                    "completed_at": run.completed_at,
                    "decisions": decisions,
                }
            )
        watched_summary.append(
            {
                "opportunity_id": src["opportunity_id"],
                "workflow_definition_id": src["workflow_definition_id"],
                "runs": run_summaries,
            }
        )
    return {"schema_version": 1, "watched_summary": watched_summary}


RENDER_CODE = r"""function WorkflowUI({ definition, instance, view }) {
    var summary = (view && view.state && view.state.watched_summary) || [];
    var config = (definition && definition.config) || {};
    var windowStart = config.window_start || '';
    var windowEnd = config.window_end || '';

    var [selectedCell, setSelectedCell] = React.useState(null);

    function fmtDate(iso) {
        if (!iso) return '';
        try { return new Date(iso).toLocaleDateString(); } catch(e) { return iso; }
    }

    function pill(text, color) {
        var palette = {
            green: 'bg-green-100 text-green-800',
            red: 'bg-red-100 text-red-800',
            yellow: 'bg-yellow-100 text-yellow-800',
            gray: 'bg-gray-100 text-gray-700'
        };
        return React.createElement('span', {
            className: 'inline-block px-2 py-0.5 rounded-full text-xs font-medium ' + (palette[color] || palette.gray)
        }, text);
    }

    function flwRow(decision) {
        var d = decision;
        var decisionCell = d.decision_type === 'no_issues'
            ? pill('✓ No issues', 'green')
            : pill('⚠ ' + (d.reason_label || d.reason_key || 'Action'), 'red');
        var auditCell = d.audit_session_ids && d.audit_session_ids.length
            ? React.createElement('a', {
                href: '/audit/' + d.audit_session_ids[0] + '/',
                className: 'text-indigo-600 underline text-xs'
            }, 'Audit #' + d.audit_session_ids[0])
            : React.createElement('span', {className: 'text-gray-400 text-xs'}, '—');
        var taskCells = (d.task_outcomes || []).map(function(t) {
            var c = t.status === 'closed' ? 'green' : (t.status === 'review_needed' ? 'yellow' : 'gray');
            var actionLabel = t.official_action ? ' · ' + t.official_action : '';
            return React.createElement('div', {key: t.id, className: 'flex items-center gap-2'},
                pill(t.status + actionLabel, c),
                React.createElement('a', {
                    href: '/tasks/' + t.id + '/edit/',
                    className: 'text-indigo-600 underline text-xs'
                }, 'Task #' + t.id)
            );
        });
        var taskCell = taskCells.length ? React.createElement('div', null, taskCells) :
            React.createElement('span', {className: 'text-gray-400 text-xs'}, '—');

        return React.createElement('tr', {key: d.id, className: d.decision_type === 'action_taken' ? 'bg-amber-50' : ''},
            React.createElement('td', {className: 'px-3 py-2 text-sm font-medium'}, d.flw_id),
            React.createElement('td', {className: 'px-3 py-2 text-sm'}, decisionCell),
            React.createElement('td', {className: 'px-3 py-2 text-sm'}, auditCell),
            React.createElement('td', {className: 'px-3 py-2 text-sm'}, taskCell)
        );
    }

    function runCell(source, run) {
        var isSelected = selectedCell &&
            selectedCell.opportunity_id === source.opportunity_id &&
            selectedCell.run_id === run.id;
        var border = isSelected ? 'border-2 border-indigo-500' : 'border border-gray-200';
        var totalDecisions = (run.decisions || []).length;
        var actionDecisions = (run.decisions || []).filter(function(d) { return d.decision_type === 'action_taken'; }).length;
        return React.createElement('div', {
            key: run.id,
            onClick: function() {
                setSelectedCell(isSelected ? null : {opportunity_id: source.opportunity_id, run_id: run.id});
            },
            className: 'bg-white rounded-lg p-3 cursor-pointer ' + border
        },
            React.createElement('div', {className: 'flex items-center justify-between mb-2'},
                pill('✓ RAN', 'green'),
                React.createElement('span', {className: 'text-xs text-gray-500'}, fmtDate(run.completed_at))
            ),
            React.createElement('div', {className: 'text-xs text-gray-600'},
                totalDecisions + ' decisions · ' + actionDecisions + ' action')
        );
    }

    function noRunCell(key) {
        return React.createElement('div', {key: key, className: 'bg-red-50 border-2 border-red-200 rounded-lg p-3 flex items-center justify-center'},
            pill('⚠ NO RUN', 'red')
        );
    }

    function aggregateCell(source) {
        var runs = source.runs || [];
        var allDecisions = runs.reduce(function(acc, r) { return acc.concat(r.decisions || []); }, []);
        var actionCount = allDecisions.filter(function(d) { return d.decision_type === 'action_taken'; }).length;
        var openTasks = 0;
        var closedTasks = 0;
        allDecisions.forEach(function(d) {
            (d.task_outcomes || []).forEach(function(t) {
                if (t.status === 'closed') closedTasks++;
                else openTasks++;
            });
        });
        return React.createElement('div', {className: 'bg-gray-50 border border-gray-200 rounded-lg p-3'},
            React.createElement('div', {className: 'text-xs text-gray-500 uppercase'}, runs.length + ' runs'),
            React.createElement('div', {className: 'text-xs mt-2'}, actionCount + ' action decisions'),
            React.createElement('div', {className: 'text-xs'}, closedTasks + ' closed · ' + openTasks + ' open')
        );
    }

    return React.createElement('div', {className: 'space-y-4 p-6'},
        React.createElement('div', {className: 'bg-white rounded-lg p-4 border border-gray-200'},
            React.createElement('h1', {className: 'text-xl font-bold'}, definition.name),
            React.createElement('p', {className: 'text-sm text-gray-600'},
                'Window: ' + fmtDate(windowStart) + ' — ' + fmtDate(windowEnd) + ' · ' +
                summary.length + ' opportunities watched')
        ),
        summary.map(function(source) {
            var runsByCompletedAt = (source.runs || []).slice().sort(function(a, b) {
                return (a.completed_at || '').localeCompare(b.completed_at || '');
            });
            return React.createElement('div', {key: source.opportunity_id, className: 'space-y-2'},
                React.createElement('div', {className: 'grid grid-cols-[200px_1fr_180px] gap-3 items-stretch'},
                    React.createElement('div', {className: 'bg-white rounded-lg p-3 border border-gray-200 flex flex-col justify-center'},
                        React.createElement('div', {className: 'text-sm font-semibold'}, 'Opp #' + source.opportunity_id),
                        React.createElement('div', {className: 'text-xs text-gray-500'}, runsByCompletedAt.length + ' completed runs')
                    ),
                    React.createElement('div', {className: 'grid grid-cols-1 md:grid-cols-3 gap-3'},
                        runsByCompletedAt.length === 0
                            ? noRunCell('norun')
                            : runsByCompletedAt.map(function(r) { return runCell(source, r); })
                    ),
                    aggregateCell(source)
                ),
                (selectedCell && selectedCell.opportunity_id === source.opportunity_id)
                    ? (function() {
                        var run = runsByCompletedAt.filter(function(r) { return r.id === selectedCell.run_id; })[0];
                        if (!run) return null;
                        var decisions = (run.decisions || []).slice().sort(function(a, b) {
                            return (a.decision_type === 'action_taken' ? 0 : 1) - (b.decision_type === 'action_taken' ? 0 : 1);
                        });
                        return React.createElement('div', {className: 'bg-white rounded-lg border-2 border-indigo-200 p-4 mt-2'},
                            React.createElement('div', {className: 'flex items-center justify-between mb-3'},
                                React.createElement('div', null,
                                    React.createElement('div', {className: 'text-sm text-gray-500'}, 'Run detail — ' + fmtDate(run.completed_at)),
                                    React.createElement('div', {className: 'text-base font-semibold'}, 'Opp #' + source.opportunity_id + ' · Run #' + run.id)
                                ),
                                React.createElement('a', {
                                    href: '/labs/workflow/' + source.workflow_definition_id + '/run/?run_id=' + run.id,
                                    className: 'inline-flex items-center px-3 py-1.5 bg-indigo-600 text-white rounded-md text-sm font-medium'
                                }, '↗ Open the run')
                            ),
                            React.createElement('table', {className: 'w-full text-sm'},
                                React.createElement('thead', null,
                                    React.createElement('tr', {className: 'bg-gray-50'},
                                        React.createElement('th', {className: 'px-3 py-2 text-left text-xs uppercase text-gray-500'}, 'FLW'),
                                        React.createElement('th', {className: 'px-3 py-2 text-left text-xs uppercase text-gray-500'}, 'Decision'),
                                        React.createElement('th', {className: 'px-3 py-2 text-left text-xs uppercase text-gray-500'}, 'Audits'),
                                        React.createElement('th', {className: 'px-3 py-2 text-left text-xs uppercase text-gray-500'}, 'Tasks')
                                    )
                                ),
                                React.createElement('tbody', null,
                                    decisions.length === 0
                                        ? React.createElement('tr', null,
                                            React.createElement('td', {colSpan: 4, className: 'px-3 py-4 text-center text-gray-500'}, 'No decisions recorded'))
                                        : decisions.map(flwRow)
                                )
                            )
                        );
                    })()
                    : null
            );
        })
    );
}"""


PIPELINE_SCHEMA = None


TEMPLATE = {
    "key": "program_admin_report",
    "name": "Program Admin Report",
    "description": DEFINITION["description"],
    "icon": "fa-shield-halved",
    "color": "purple",
    "multi_opp": True,
    "supports_saved_runs": True,
    "snapshot_inputs": {
        "pipelines": None,
        "workers": False,
        "state_keys": ["watched_summary"],
    },
    "snapshot_schema": {
        "version": 1,
        "keys": {
            "state.watched_summary": "Per-watched-source rollup frozen at run completion",
        },
    },
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
