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
from commcare_connect.workflow.data_access import get_saved_runs_for_program_report  # noqa: F401

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

    # Don't pass a shared WDA here — the reader constructs a per-source
    # scoped WDA internally so list_runs hits the LabsRecord API with the
    # right opp scope (the labs API silently returns 0 records when called
    # without a scope unless the records are public).
    sources = get_saved_runs_for_program_report(
        watched_sources=watched_sources,
        window_start=window_start,
        window_end=window_end,
        request=request,
        access_token=access_token,
    )

    # Per-source DAOs scoped to the watched opp. A single primary-opp DAO
    # returns 0 records when queried for a non-primary opp because the labs
    # API enforces opp-scope on every request. See spec §5.3.
    from commcare_connect.audit.data_access import AuditDataAccess

    watched_summary = []
    for src in sources:
        src_opp_id = src["opportunity_id"]
        dda = DecisionsDataAccess(request=request, access_token=access_token, opportunity_id=src_opp_id)
        tda = TaskDataAccess(request=request, access_token=access_token, opportunity_id=src_opp_id)
        ada = AuditDataAccess(request=request, access_token=access_token, opportunity_id=src_opp_id)

        try:
            # Preload all audits for this opp once; lookups per decision are
            # then dict reads. The labs API has no batch-get-by-id so the
            # alternative is N+1 round-trips.
            try:
                all_audits = ada.get_audit_sessions()
                audit_by_id = {a.id: a for a in all_audits}
            except Exception:
                logger.warning("Failed to preload audits for opp %s", src_opp_id, exc_info=True)
                audit_by_id = {}

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
                    audit_outcomes = []
                    for aid in d.audit_session_ids:
                        a = audit_by_id.get(aid)
                        if a is None:
                            continue
                        img = a.data.get("image_results") or {}
                        audit_outcomes.append(
                            {
                                "id": a.id,
                                "status": a.status,
                                "overall_result": a.overall_result,
                                "pass_count": img.get("pass", 0),
                                "fail_count": img.get("fail", 0),
                                "pending_count": img.get("pending", 0),
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
                            "audit_outcomes": audit_outcomes,
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
        finally:
            dda.close()
            tda.close()
            ada.close()

        watched_summary.append(
            {
                "opportunity_id": src["opportunity_id"],
                "workflow_definition_id": src["workflow_definition_id"],
                "runs": run_summaries,
            }
        )
    # Wrap in `state` so useRunView's `view.state = snapshot.state` path
    # finds the rollup. We preserve the window + sources alongside so the
    # render code never has to reach into the definition config.
    return {
        "schema_version": 1,
        "state": {
            "watched_summary": watched_summary,
            "window_start": state.get("window_start"),
            "window_end": state.get("window_end"),
            "watched_sources": watched_sources,
        },
    }


RENDER_CODE = r"""function WorkflowUI({ definition, instance, view }) {
    var state = (view && view.state) || {};
    var summary = state.watched_summary || [];
    var expectedWeeks = state.expected_weeks || [];
    var displayStart = state.display_window_start || state.window_start || '';
    var displayEnd = state.display_window_end || state.window_end || '';

    var [selectedCell, setSelectedCell] = React.useState(null);

    function fmtDate(iso) {
        if (!iso) return '';
        // Use UTC components so date-only ISO strings render as the same
        // calendar day in every viewer's TZ (otherwise "2025-11-03" parses
        // as midnight UTC → previous evening in negative-offset zones).
        try {
            var d = new Date(iso);
            var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            return months[d.getUTCMonth()] + ' ' + d.getUTCDate();
        } catch(e) { return iso; }
    }

    function pill(text, color) {
        var palette = {
            green: 'bg-green-100 text-green-800',
            red: 'bg-red-100 text-red-800',
            yellow: 'bg-yellow-100 text-yellow-800',
            indigo: 'bg-indigo-100 text-indigo-800',
            gray: 'bg-gray-100 text-gray-700'
        };
        return React.createElement('span', {
            className: 'inline-block px-2 py-0.5 rounded-full text-xs font-medium ' + (palette[color] || palette.gray)
        }, text);
    }

    function kpiBar(label, num, denom) {
        var pct = denom > 0 ? Math.round((num / denom) * 100) : null;
        var color = 'gray';
        if (pct === null) color = 'gray';
        else if (pct >= 90) color = 'green';
        else if (pct >= 60) color = 'yellow';
        else color = 'red';
        var palette = {
            green: {bg: '#dcfce7', fg: '#16a34a', text: '#166534'},
            yellow: {bg: '#fef3c7', fg: '#f59e0b', text: '#92400e'},
            red: {bg: '#fee2e2', fg: '#dc2626', text: '#991b1b'},
            gray: {bg: '#f3f4f6', fg: '#9ca3af', text: '#6b7280'},
        };
        var p = palette[color];
        return React.createElement('div', {key: label, className: 'text-[11px]'},
            React.createElement('div', {className: 'flex justify-between'},
                React.createElement('span', {style: {color: '#374151'}}, label),
                React.createElement('span', {style: {color: p.text, fontWeight: 600}},
                    pct === null ? '—' : (pct + '%') + (denom > 0 ? (' (' + num + '/' + denom + ')') : '')
                )
            ),
            React.createElement('div', {style: {background: p.bg, height: 3, borderRadius: 2, marginTop: 2, overflow: 'hidden'}},
                React.createElement('div', {style: {background: p.fg, width: (pct === null ? 0 : pct) + '%', height: '100%'}})
            )
        );
    }

    function computeRunKpis(run) {
        // 3 KPIs: % FLWs with decision, % audits closed, % tasks closed
        var decisions = run.decisions || [];
        var activeFlws = decisions.length;  // every decision = one FLW reviewed this run
        var flwDenom = run.active_flws || activeFlws;
        var flwDec = {num: activeFlws, denom: flwDenom};

        var auditTotal = 0, auditClosed = 0;
        var taskTotal = 0, taskClosed = 0;
        decisions.forEach(function(d) {
            (d.audit_outcomes || []).forEach(function(a) {
                auditTotal++;
                if (a.status === 'completed') auditClosed++;
            });
            (d.task_outcomes || []).forEach(function(t) {
                taskTotal++;
                if (t.status === 'closed') taskClosed++;
            });
        });
        return {
            flwDec: flwDec,
            audits: {num: auditClosed, denom: auditTotal},
            tasks: {num: taskClosed, denom: taskTotal},
        };
    }

    function computeAggregate(source) {
        var runs = source.runs || [];
        var totalDec = 0, totalRoster = 0, totalAuditDone = 0, totalAudit = 0, totalTaskDone = 0, totalTask = 0;
        var outcomes = {satisfactory: 0, warned: 0, suspended: 0, none: 0, open: 0};
        runs.forEach(function(r) {
            var k = computeRunKpis(r);
            totalDec += k.flwDec.num; totalRoster += k.flwDec.denom;
            totalAuditDone += k.audits.num; totalAudit += k.audits.denom;
            totalTaskDone += k.tasks.num; totalTask += k.tasks.denom;
            (r.decisions || []).forEach(function(d) {
                (d.task_outcomes || []).forEach(function(t) {
                    if (t.status === 'closed') {
                        var oa = t.official_action || 'none';
                        if (outcomes[oa] === undefined) outcomes[oa] = 0;
                        outcomes[oa]++;
                    } else {
                        outcomes.open++;
                    }
                });
            });
        });
        return {
            runsExpected: expectedWeeks.length || runs.length,
            runsActual: runs.length,
            flwDec: {num: totalDec, denom: totalRoster},
            audits: {num: totalAuditDone, denom: totalAudit},
            tasks: {num: totalTaskDone, denom: totalTask},
            outcomes: outcomes,
        };
    }

    function isSameWeek(completedIso, mondayIso) {
        if (!completedIso || !mondayIso) return false;
        // Compare ISO date of completedAt to monday and 6 days after
        var c = (completedIso || '').slice(0, 10);
        var m = mondayIso;
        var end = new Date(m); end.setUTCDate(end.getUTCDate() + 6);
        var endIso = end.toISOString().slice(0, 10);
        return c >= m && c <= endIso;
    }

    function runForWeek(source, mondayIso) {
        return (source.runs || []).filter(function(r) { return isSameWeek(r.completed_at, mondayIso); })[0];
    }

    function runCellCard(source, run, mondayIso) {
        var isSelected = selectedCell &&
            selectedCell.opportunity_id === source.opportunity_id &&
            selectedCell.run_id === run.id;
        var k = computeRunKpis(run);
        var border = isSelected
            ? '2px solid #4f46e5'
            : '1px solid #e5e7eb';
        return React.createElement('div', {
            onClick: function() {
                setSelectedCell(isSelected ? null : {opportunity_id: source.opportunity_id, run_id: run.id});
            },
            style: {
                background: 'white',
                borderRadius: 10,
                border: border,
                padding: 10,
                cursor: 'pointer',
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                position: 'relative',
            },
        },
            isSelected
                ? React.createElement('div', {style: {position: 'absolute', top: -7, right: 8, background: '#4f46e5', color: 'white', fontSize: 9, padding: '1px 6px', borderRadius: 3, fontWeight: 600}}, 'SELECTED')
                : null,
            React.createElement('div', {style: {display: 'flex', justifyContent: 'space-between', alignItems: 'center'}},
                pill('✓ RAN', 'green'),
                React.createElement('span', {style: {fontSize: 10, color: '#6b7280'}}, fmtDate(run.completed_at))
            ),
            kpiBar('FLW dec.', k.flwDec.num, k.flwDec.denom),
            kpiBar('Audits', k.audits.num, k.audits.denom),
            kpiBar('Tasks', k.tasks.num, k.tasks.denom)
        );
    }

    function noRunCard() {
        return React.createElement('div', {
            style: {
                background: '#fef2f2', borderRadius: 10, border: '2px solid #fecaca',
                padding: 10, display: 'flex', flexDirection: 'column',
                alignItems: 'center', justifyContent: 'center', textAlign: 'center', minHeight: 100,
            }
        },
            pill('⚠ NO RUN', 'red'),
            React.createElement('div', {style: {fontSize: 11, color: '#991b1b', marginTop: 8}}, 'SOP missed')
        );
    }

    function aggregateCard(source) {
        var agg = computeAggregate(source);
        var below = agg.runsActual < agg.runsExpected ||
                    (agg.audits.denom > 0 && agg.audits.num < agg.audits.denom) ||
                    (agg.tasks.denom > 0 && agg.tasks.num < agg.tasks.denom);
        var bg = below ? '#fffbeb' : '#f9fafb';
        var border = below ? '2px solid #f59e0b' : '1px solid #e5e7eb';
        var sopPill = below ? pill('⚠ BELOW', 'yellow') : pill('✓ SOP MET', 'green');

        var outcomes = agg.outcomes;
        var totalClosed = (outcomes.satisfactory||0) + (outcomes.warned||0) + (outcomes.suspended||0) + (outcomes.none||0);
        var totalAll = totalClosed + (outcomes.open||0);

        var outcomeBar = totalAll > 0
            ? React.createElement('div', {style: {display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', marginTop: 4}},
                (outcomes.satisfactory||0) > 0 ? React.createElement('div', {key:'s', style: {background: '#16a34a', width: ((outcomes.satisfactory/totalAll)*100)+'%'}}) : null,
                (outcomes.warned||0) > 0 ? React.createElement('div', {key:'w', style: {background: '#f59e0b', width: ((outcomes.warned/totalAll)*100)+'%'}}) : null,
                (outcomes.suspended||0) > 0 ? React.createElement('div', {key:'x', style: {background: '#dc2626', width: ((outcomes.suspended/totalAll)*100)+'%'}}) : null,
                (outcomes.open||0) > 0 ? React.createElement('div', {key:'o', style: {background: '#e5e7eb', width: ((outcomes.open/totalAll)*100)+'%'}}) : null
            )
            : null;

        return React.createElement('div', {style: {background: bg, borderRadius: 10, border: border, padding: 10, display: 'flex', flexDirection: 'column'}},
            React.createElement('div', {style: {display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6}},
                React.createElement('span', {style: {fontSize: 10, color: '#6b7280', textTransform: 'uppercase'}}, agg.runsActual + '/' + agg.runsExpected + ' runs'),
                sopPill
            ),
            React.createElement('div', {style: {display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, textAlign: 'center', fontSize: 10}},
                React.createElement('div', null,
                    React.createElement('div', {style: {fontWeight: 600, color: '#111827', fontSize: 13}}, (agg.flwDec.denom > 0 ? Math.round(agg.flwDec.num / agg.flwDec.denom * 100) : 0) + '%'),
                    React.createElement('div', {style: {color: '#6b7280'}}, 'FLW dec')
                ),
                React.createElement('div', null,
                    React.createElement('div', {style: {fontWeight: 600, color: agg.audits.denom > 0 && agg.audits.num < agg.audits.denom ? '#dc2626' : '#111827', fontSize: 13}}, (agg.audits.denom > 0 ? Math.round(agg.audits.num / agg.audits.denom * 100) : 0) + '%'),
                    React.createElement('div', {style: {color: '#6b7280'}}, 'Audits')
                ),
                React.createElement('div', null,
                    React.createElement('div', {style: {fontWeight: 600, color: agg.tasks.denom > 0 && agg.tasks.num < agg.tasks.denom ? '#dc2626' : '#111827', fontSize: 13}}, (agg.tasks.denom > 0 ? Math.round(agg.tasks.num / agg.tasks.denom * 100) : 0) + '%'),
                    React.createElement('div', {style: {color: '#6b7280'}}, 'Tasks')
                )
            ),
            outcomeBar
                ? React.createElement('div', {style: {marginTop: 6, paddingTop: 6, borderTop: '1px solid ' + (below ? '#fed7aa' : '#e5e7eb')}},
                    React.createElement('div', {style: {fontSize: 10, color: '#6b7280'}}, 'Outcomes (' + totalClosed + ' closed' + (outcomes.open ? ', ' + outcomes.open + ' open' : '') + ')'),
                    outcomeBar
                )
                : null
        );
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

    // weekColumns: derived from expected_weeks if available; otherwise from sorted distinct completed_at weeks
    var weekColumns = expectedWeeks.slice();
    if (weekColumns.length === 0) {
        var seen = {};
        summary.forEach(function(s) {
            (s.runs || []).forEach(function(r) {
                if (r.completed_at) {
                    var d = new Date(r.completed_at);
                    var dow = d.getUTCDay();
                    var monday = new Date(d);
                    monday.setUTCDate(d.getUTCDate() - ((dow + 6) % 7));
                    var iso = monday.toISOString().slice(0, 10);
                    seen[iso] = true;
                }
            });
        });
        weekColumns = Object.keys(seen).sort();
    }

    var gridTemplate = '220px repeat(' + weekColumns.length + ', minmax(180px, 1fr)) 200px';

    return React.createElement('div', {style: {padding: 16, background: '#f7f8fb', minHeight: '100vh'}},
        // Top strip
        React.createElement('div', {style: {background: 'white', borderRadius: 10, border: '1px solid #e5e7eb', padding: 16, marginBottom: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'center'}},
            React.createElement('div', null,
                React.createElement('div', {style: {fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#6b7280'}}, 'Program Admin Report · ' + (definition.name || '')),
                React.createElement('div', {style: {fontSize: 18, fontWeight: 600, color: '#111827', marginTop: 2}},
                    fmtDate(displayStart) + ' – ' + fmtDate(displayEnd) + ' · ' + summary.length + ' opportunities watched'
                )
            ),
            view.isCompleted ? pill('📌 Snapshot', 'indigo') : pill('● Live', 'gray')
        ),
        // Column header row
        React.createElement('div', {style: {display: 'grid', gridTemplateColumns: gridTemplate, gap: 10, marginBottom: 6, padding: '0 2px'}},
            React.createElement('div'),
            weekColumns.map(function(w, i) {
                return React.createElement('div', {key: i, style: {fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#6b7280', textAlign: 'center'}},
                    'Wk ' + (i + 1) + ' · ' + fmtDate(w));
            }),
            React.createElement('div', {style: {fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#6b7280', textAlign: 'center', background: '#f3f4f6', padding: '4px 8px', borderRadius: 4}}, 'Window aggregate')
        ),

        // Per-opp rows + inline detail panel below the row that owns selectedCell
        summary.map(function(source) {
            var oppMissedSet = {};
            (source.missed_week_idxs || []).forEach(function(idx) { oppMissedSet[idx] = true; });

            var weekCells = weekColumns.map(function(w, i) {
                if (oppMissedSet[i]) return React.createElement('div', {key: i}, noRunCard());
                var run = runForWeek(source, w);
                return React.createElement('div', {key: i}, run ? runCellCard(source, run, w) : noRunCard());
            });

            var detail = null;
            if (selectedCell && selectedCell.opportunity_id === source.opportunity_id) {
                var run = (source.runs || []).filter(function(r) { return r.id === selectedCell.run_id; })[0];
                if (run) {
                    var decisions = (run.decisions || []).slice().sort(function(a, b) {
                        return (a.decision_type === 'action_taken' ? 0 : 1) - (b.decision_type === 'action_taken' ? 0 : 1);
                    });
                    detail = React.createElement('div', {style: {background: 'white', borderRadius: '0 0 12px 12px', border: '2px solid #4f46e5', borderTop: 'none', overflow: 'hidden'}},
                        React.createElement('div', {style: {padding: '14px 20px', background: '#eef2ff', borderBottom: '1px solid #c7d2fe', display: 'flex', justifyContent: 'space-between', alignItems: 'center'}},
                            React.createElement('div', null,
                                React.createElement('div', {style: {fontSize: 11, textTransform: 'uppercase', color: '#4338ca'}}, 'Run detail · ' + (source.label || ('Opp #' + source.opportunity_id)) + ' · ' + fmtDate(run.completed_at)),
                                React.createElement('div', {style: {fontSize: 14, fontWeight: 600, color: '#111827', marginTop: 2}}, 'NM ' + (source.network_manager || '') + ' · Run #' + run.id)
                            ),
                            React.createElement('div', {style: {display: 'flex', gap: 8, alignItems: 'center'}},
                                React.createElement('a', {
                                    href: '/labs/workflow/' + source.workflow_definition_id + '/run/?run_id=' + run.id,
                                    style: {display: 'inline-flex', alignItems: 'center', gap: 6, background: '#4f46e5', color: 'white', padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 500, textDecoration: 'none'}
                                }, '↗ Open the run'),
                                React.createElement('button', {
                                    onClick: function() { setSelectedCell(null); },
                                    style: {background: 'transparent', border: 0, fontSize: 18, color: '#6b7280', cursor: 'pointer', lineHeight: 1}
                                }, '×')
                            )
                        ),
                        React.createElement('table', {style: {width: '100%', borderCollapse: 'collapse', fontSize: 12}},
                            React.createElement('thead', {style: {background: '#f9fafb'}},
                                React.createElement('tr', null,
                                    React.createElement('th', {style: {textAlign: 'left', padding: '10px 16px', fontSize: 11, textTransform: 'uppercase', color: '#6b7280', fontWeight: 500, width: 170}}, 'FLW'),
                                    React.createElement('th', {style: {textAlign: 'left', padding: '10px 16px', fontSize: 11, textTransform: 'uppercase', color: '#6b7280', fontWeight: 500}}, 'Decision'),
                                    React.createElement('th', {style: {textAlign: 'left', padding: '10px 16px', fontSize: 11, textTransform: 'uppercase', color: '#6b7280', fontWeight: 500, width: 240}}, 'Audits'),
                                    React.createElement('th', {style: {textAlign: 'left', padding: '10px 16px', fontSize: 11, textTransform: 'uppercase', color: '#6b7280', fontWeight: 500, width: 280}}, 'Tasks')
                                )
                            ),
                            React.createElement('tbody', null,
                                decisions.length === 0
                                    ? React.createElement('tr', null,
                                        React.createElement('td', {colSpan: 4, style: {padding: '24px', textAlign: 'center', color: '#9ca3af'}}, 'No decisions recorded'))
                                    : decisions.map(flwRow)
                            )
                        )
                    );
                }
            }

            return React.createElement('div', {key: source.opportunity_id, style: {marginBottom: 14}},
                React.createElement('div', {style: {display: 'grid', gridTemplateColumns: gridTemplate, gap: 10, alignItems: 'stretch'}},
                    React.createElement('div', {style: {background: 'white', borderRadius: 10, border: '1px solid #e5e7eb', padding: 12, display: 'flex', flexDirection: 'column', justifyContent: 'center'}},
                        React.createElement('div', {style: {fontWeight: 600, color: '#111827', fontSize: 14}}, source.label || ('Opp #' + source.opportunity_id)),
                        React.createElement('div', {style: {fontSize: 11, color: '#6b7280', marginTop: 2}}, 'opp #' + source.opportunity_id + (source.flw_count ? ' · ' + source.flw_count + ' FLWs' : '')),
                        source.network_manager ? React.createElement('div', {style: {fontSize: 12, color: '#111827', marginTop: 6}}, 'NM: ', React.createElement('strong', null, source.network_manager)) : null
                    ),
                    weekCells,
                    aggregateCard(source)
                ),
                detail
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
