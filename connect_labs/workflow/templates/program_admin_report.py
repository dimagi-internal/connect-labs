"""Program Admin Report Workflow Template.

Multi-opp + saved-runs template that gives a program admin a window-scoped
view of which network managers ran the SOP and what happened to FLWs that
the per-opp reports raised concerns about. Reads from any "watched"
workflow whose findings are recorded as Flag records (auto-applied by
that workflow's render code via view.ensureAutoFlags).

See docs/superpowers/specs/2026-05-25-program-admin-report-design.md.
"""

import logging
from datetime import datetime

from connect_labs.flags.data_access import FlagsDataAccess
from connect_labs.tasks.data_access import TaskDataAccess
from connect_labs.workflow.data_access import get_saved_runs_for_program_report  # noqa: F401

logger = logging.getLogger(__name__)


DEFINITION = {
    "name": "Program Admin Report",
    "description": "Cross-opportunity rollup of weekly SOP compliance + per-FLW flags/audits/tasks.",
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


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def compute_program_admin_rollup(
    *,
    state,
    request=None,
    access_token=None,
    progress_callback=None,
    **_,
):
    """Compute the window-scoped rollup of per-FLW flags + their associated
    audit/task records. See spec §5.3.

    This used to be the template's ``build_snapshot`` completion hook. It is
    now invoked *during* the run (via the ``program_admin_rollup`` job
    handler, or the synthetic demo seeder) and its result is written into run
    **state** — completion then captures that state declaratively via the
    template's ``snapshot_inputs`` manifest, like every other saved-runs
    template. Returns a state-shaped dict:
    ``{"watched_summary": [...], "window_start", "window_end",
    "watched_sources"}`` (plus ``"error"`` when the window is missing).

    `state` must contain ``window_start``, ``window_end``, ``watched_sources``.

    Auth: accepts either ``request`` (web view path — token is extracted from
    session) or ``access_token`` (MCP/CLI/Celery path — caller has the
    Connect OAuth token already). Exactly one must yield a token; the DAOs
    raise ``ValueError`` if neither is present.
    """

    def _progress(msg):
        if progress_callback:
            progress_callback(msg)

    window_start = _parse_iso(state.get("window_start"))
    window_end = _parse_iso(state.get("window_end"))
    watched_sources = state.get("watched_sources", [])

    if not window_start or not window_end:
        return {"watched_summary": [], "error": "missing_window"}

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
    from connect_labs.audit.data_access import AuditDataAccess

    watched_summary = []
    for src in sources:
        src_opp_id = src["opportunity_id"]
        _progress(f"Rolling up opportunity #{src_opp_id} ({len(src['runs'])} run(s))…")
        fda = FlagsDataAccess(request=request, access_token=access_token, opportunity_id=src_opp_id)
        tda = TaskDataAccess(request=request, access_token=access_token, opportunity_id=src_opp_id)
        ada = AuditDataAccess(request=request, access_token=access_token, opportunity_id=src_opp_id)

        try:
            run_summaries = []
            for run in src["runs"]:
                # Pull all three artifact sets for this run, then group by
                # flw_id. Audits and tasks are no longer linked through a
                # Decision record — each artifact carries workflow_run_id
                # directly. One FLW row per (flw_id) appears here if any
                # of {flags, audits, tasks} is non-empty for them.
                try:
                    run_flags = fda.get_flags_for_run(run.id)
                except Exception:
                    logger.warning("Failed to fetch flags for run %s", run.id, exc_info=True)
                    run_flags = []
                try:
                    run_audits = ada.get_sessions_by_workflow_run(run.id)
                except Exception:
                    logger.warning("Failed to fetch audits for run %s", run.id, exc_info=True)
                    run_audits = []
                try:
                    run_tasks = tda.get_tasks_for_run(run.id)
                except Exception:
                    logger.warning("Failed to fetch tasks for run %s", run.id, exc_info=True)
                    run_tasks = []

                by_flw: dict[str, dict] = {}

                def _row(flw_id: str) -> dict:
                    if flw_id not in by_flw:
                        by_flw[flw_id] = {
                            "flw_id": flw_id,
                            "flw_name": None,
                            "flags": [],
                            "audits": [],
                            "tasks": [],
                        }
                    return by_flw[flw_id]

                def _note_name(flw_id: str, name: str | None) -> None:
                    # Record the worker's human display name the first time we
                    # see a non-empty one that differs from the raw id, so the
                    # PAR drill row renders a real name instead of the username.
                    # Falls back to flw_id at render time when never set.
                    if not name or name == flw_id:
                        return
                    row = _row(flw_id)
                    if not row.get("flw_name"):
                        row["flw_name"] = name

                for f in run_flags:
                    if not f.flw_id:
                        continue
                    _row(f.flw_id)["flags"].append(
                        {
                            "id": f.id,
                            "flag_key": f.flag_key,
                            "flag_label": f.flag_label,
                            "evidence": f.evidence,
                            "source": f.source,
                            "flagged_at": f.flagged_at,
                        }
                    )

                for a in run_audits:
                    flw_id = a.username or a.data.get("flw_id")
                    if not flw_id:
                        continue
                    _note_name(flw_id, a.data.get("flw_name"))
                    img = a.data.get("image_results") or {}
                    _row(flw_id)["audits"].append(
                        {
                            "id": a.id,
                            "status": a.status,
                            "overall_result": a.overall_result,
                            "pass_count": img.get("pass", 0),
                            "fail_count": img.get("fail", 0),
                            "pending_count": img.get("pending", 0),
                        }
                    )

                for t in run_tasks:
                    flw_id = t.username or t.data.get("username")
                    if not flw_id:
                        continue
                    _note_name(flw_id, t.data.get("flw_name"))
                    _row(flw_id)["tasks"].append(
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

                run_summaries.append(
                    {
                        "id": run.id,
                        "completed_at": run.completed_at,
                        "flw_rows": list(by_flw.values()),
                    }
                )
        finally:
            fda.close()
            tda.close()
            ada.close()

        watched_summary.append(
            {
                "opportunity_id": src["opportunity_id"],
                "workflow_definition_id": src["workflow_definition_id"],
                "runs": run_summaries,
            }
        )
    # State-shaped: the job handler writes this into run state, and the
    # declarative snapshot manifest captures it from there at completion. We
    # echo the window + sources alongside so the render code never has to
    # reach into the definition config.
    return {
        "watched_summary": watched_summary,
        "window_start": state.get("window_start"),
        "window_end": state.get("window_end"),
        "watched_sources": watched_sources,
    }


RENDER_CODE = r"""function WorkflowUI({ definition, instance, view, actions }) {
    // Live refresh overlays the server-computed rollup until the next page
    // load (the job handler persists it into run state, so view.state has it
    // from then on — and conclude freezes it declaratively).
    var [rollupOverlay, setRollupOverlay] = React.useState(null);
    var baseState = (view && view.state) || {};
    var state = rollupOverlay ? Object.assign({}, baseState, rollupOverlay) : baseState;
    var summary = state.watched_summary || [];
    var expectedWeeks = state.expected_weeks || [];
    var displayStart = state.display_window_start || state.window_start || '';
    var displayEnd = state.display_window_end || state.window_end || '';

    var [selectedCell, setSelectedCell] = React.useState(null);
    var [jobStatus, setJobStatus] = React.useState('idle'); // idle | running | error
    var [jobMessage, setJobMessage] = React.useState(null);

    function refreshRollup() {
        if (!actions || !actions.startJob || jobStatus === 'running') return;
        setJobStatus('running');
        setJobMessage('Starting rollup…');
        actions.startJob(instance.id, {
            job_type: 'program_admin_rollup',
            run_id: instance.id,
            opportunity_id: instance.opportunity_id,
        }).then(function (resp) {
            if (!resp || !resp.success || !resp.task_id) {
                setJobStatus('error');
                setJobMessage((resp && resp.error) || 'Failed to start rollup job');
                return;
            }
            actions.streamJobProgress(
                resp.task_id,
                function (data) { if (data.message) setJobMessage(data.message); },
                null,
                function (results) {
                    if (results && results.watched_summary) {
                        setRollupOverlay({
                            watched_summary: results.watched_summary,
                            window_start: results.window_start || baseState.window_start,
                            window_end: results.window_end || baseState.window_end
                        });
                    }
                    setJobStatus('idle');
                    setJobMessage(null);
                },
                function (err) {
                    setJobStatus('error');
                    setJobMessage(err || 'Rollup failed');
                }
            );
        }).catch(function () {
            setJobStatus('error');
            setJobMessage('Rollup job failed to start');
        });
    }

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
        // 3 KPIs: % flagged FLWs, % audits closed, % tasks closed.
        // A "flagged FLW" is one where the per-opp report's
        // ensureAutoFlags raised at least one concern.
        var flwRows = run.flw_rows || [];
        var flaggedFlws = flwRows.filter(function(r) { return (r.flags || []).length > 0; }).length;
        var flwDenom = run.active_flws || flwRows.length;
        var flwDec = {num: flaggedFlws, denom: flwDenom};

        var auditTotal = 0, auditClosed = 0;
        var taskTotal = 0, taskClosed = 0;
        flwRows.forEach(function(r) {
            (r.audits || []).forEach(function(a) {
                auditTotal++;
                if (a.status === 'completed') auditClosed++;
            });
            (r.tasks || []).forEach(function(t) {
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
        // Aggregate over ONE run per expected week (dedup), mirroring the grid's
        // runForWeek. source.runs can carry >1 completed run for the same week
        // (a re-seed/refresh), and iterating it raw double-counts KPIs and prints
        // "12/4 runs". We roll up the SAME run the grid cell shows per week so the
        // window aggregate agrees with the cells above it.
        var missedSet = {};
        (source.missed_week_idxs || []).forEach(function(idx) { missedSet[idx] = true; });
        var weekCols = (typeof weekColumns !== 'undefined' && weekColumns.length)
            ? weekColumns
            : (expectedWeeks.length ? expectedWeeks : []);

        // Resolve the runs to aggregate: one per week column (deduped). When no
        // week columns are known, fall back to the raw run list.
        var aggRuns;
        if (weekCols.length) {
            aggRuns = [];
            weekCols.forEach(function(w, i) {
                if (missedSet[i]) return;        // declared-missed week: no run
                var run = runForWeek(source, w);  // the SAME run the grid shows
                if (run) aggRuns.push(run);
            });
        } else {
            aggRuns = source.runs || [];
        }

        var totalDec = 0, totalRoster = 0, totalAuditDone = 0, totalAudit = 0, totalTaskDone = 0, totalTask = 0;
        var outcomes = {satisfactory: 0, warned: 0, suspended: 0, none: 0, open: 0};
        aggRuns.forEach(function(r) {
            var k = computeRunKpis(r);
            totalDec += k.flwDec.num; totalRoster += k.flwDec.denom;
            totalAuditDone += k.audits.num; totalAudit += k.audits.denom;
            totalTaskDone += k.tasks.num; totalTask += k.tasks.denom;
            (r.flw_rows || []).forEach(function(fr) {
                (fr.tasks || []).forEach(function(t) {
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
        // Expected = the SOP threshold (one run per expected week). Actual = the
        // number of expected weeks that actually ran (deduped), never > expected.
        var runsExpected = weekCols.length || expectedWeeks.length || aggRuns.length;
        return {
            runsExpected: runsExpected,
            runsActual: aggRuns.length,
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

        // Completion status — "did the network manager finish what this run
        // started?" — surface independently of "was the run executed?".
        // A run is RESOLVED if every audit and task it spawned is closed;
        // it's IN PROGRESS if any are still open. Runs with no actions
        // taken are CLEAN — no work was needed.
        var openAudits = k.audits.denom - k.audits.num;
        var openTasks = k.tasks.denom - k.tasks.num;
        var openCount = openAudits + openTasks;
        var hasAnyActions = k.audits.denom > 0 || k.tasks.denom > 0;
        var statusPill;
        var statusBg = 'white';
        if (!hasAnyActions) {
            statusPill = pill('✓ Clean run', 'green');
        } else if (openCount === 0) {
            statusPill = pill('✓ All resolved', 'green');
        } else {
            statusPill = pill('⏳ ' + openCount + ' open', 'yellow');
            statusBg = '#fffbeb';
        }

        var border = isSelected
            ? '2px solid #4f46e5'
            : (openCount > 0 ? '1px solid #fcd34d' : '1px solid #e5e7eb');

        return React.createElement('div', {
            onClick: function() {
                setSelectedCell(isSelected ? null : {opportunity_id: source.opportunity_id, run_id: run.id});
            },
            style: {
                background: statusBg,
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
            React.createElement('div', {style: {marginTop: -2}}, statusPill),
            kpiBar('Flagged', k.flwDec.num, k.flwDec.denom),
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
        // Surface the SOP threshold (runs done / runs expected) on the pill so
        // "BELOW" / "SOP MET" is legible — the SOP is "run the weekly review every
        // expected week", and this is the ratio against it.
        var sopRatio = agg.runsActual + '/' + agg.runsExpected;
        var sopPill = below
            ? pill('⚠ BELOW SOP · ' + sopRatio, 'yellow')
            : pill('✓ SOP MET · ' + sopRatio, 'green');

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
                    React.createElement('div', {style: {color: '#6b7280'}}, 'Flagged')
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

    function flwRow(fr, source) {
        // Cross-opp links: every link out of the PAR detail panel needs to
        // carry the watched source's opportunity_id, otherwise labs scopes
        // the lookup to whatever opp the PAR run itself lives in (the
        // primary) and the audit/task/run page returns "not found" for any
        // non-primary watched opp.
        var oppScope = source && source.opportunity_id
            ? '?opportunity_id=' + source.opportunity_id
            : '';

        var flagCells = (fr.flags || []).map(function(f) {
            return React.createElement('span', {
                key: f.id || f.flag_key,
                className: 'inline-flex items-center gap-1 mr-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800',
                title: f.flag_label,
            }, f.flag_label || f.flag_key);
        });
        var flagCell = flagCells.length
            ? React.createElement('div', {className: 'flex flex-wrap gap-y-1'}, flagCells)
            : React.createElement('span', {className: 'text-gray-400 text-xs'}, '—');

        var auditCells = (fr.audits || []).map(function(a) {
            return React.createElement('a', {
                key: a.id,
                href: '/audit/' + a.id + '/' + oppScope,
                className: 'inline-block mr-2 text-indigo-600 underline text-xs',
                title: a.overall_result || a.status,
            }, 'Audit #' + a.id);
        });
        var auditCell = auditCells.length
            ? React.createElement('div', null, auditCells)
            : React.createElement('span', {className: 'text-gray-400 text-xs'}, '—');

        var taskCells = (fr.tasks || []).map(function(t) {
            var c = t.status === 'closed' ? 'green' : (t.status === 'review_needed' ? 'yellow' : 'gray');
            var actionLabel = t.official_action ? ' · ' + t.official_action : '';
            return React.createElement('div', {key: t.id, className: 'flex items-center gap-2'},
                pill(t.status + actionLabel, c),
                React.createElement('a', {
                    href: '/tasks/' + t.id + '/edit/' + oppScope,
                    className: 'text-indigo-600 underline text-xs'
                }, 'Task #' + t.id)
            );
        });
        var taskCell = taskCells.length ? React.createElement('div', null, taskCells) :
            React.createElement('span', {className: 'text-gray-400 text-xs'}, '—');

        return React.createElement('tr', {key: fr.flw_id, className: (fr.flags || []).length ? 'bg-amber-50' : ''},
            React.createElement('td', {className: 'px-3 py-2 text-sm font-medium'}, fr.flw_name || fr.flw_id),
            React.createElement('td', {className: 'px-3 py-2 text-sm'}, flagCell),
            React.createElement('td', {className: 'px-3 py-2 text-sm'}, auditCell),
            React.createElement('td', {className: 'px-3 py-2 text-sm'}, taskCell)
        );
    }

    function runCell(source, run) {
        var isSelected = selectedCell &&
            selectedCell.opportunity_id === source.opportunity_id &&
            selectedCell.run_id === run.id;
        var border = isSelected ? 'border-2 border-indigo-500' : 'border border-gray-200';
        var flwRows = run.flw_rows || [];
        var flaggedRows = flwRows.filter(function(r) { return (r.flags || []).length > 0; }).length;
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
                flwRows.length + ' FLWs · ' + flaggedRows + ' flagged')
        );
    }

    function noRunCell(key) {
        return React.createElement('div', {key: key, className: 'bg-red-50 border-2 border-red-200 rounded-lg p-3 flex items-center justify-center'},
            pill('⚠ NO RUN', 'red')
        );
    }

    function aggregateCell(source) {
        var runs = source.runs || [];
        var allRows = runs.reduce(function(acc, r) { return acc.concat(r.flw_rows || []); }, []);
        var flaggedCount = allRows.filter(function(r) { return (r.flags || []).length > 0; }).length;
        var openTasks = 0;
        var closedTasks = 0;
        allRows.forEach(function(r) {
            (r.tasks || []).forEach(function(t) {
                if (t.status === 'closed') closedTasks++;
                else openTasks++;
            });
        });
        return React.createElement('div', {className: 'bg-gray-50 border border-gray-200 rounded-lg p-3'},
            React.createElement('div', {className: 'text-xs text-gray-500 uppercase'}, runs.length + ' runs'),
            React.createElement('div', {className: 'text-xs mt-2'}, flaggedCount + ' flagged FLWs'),
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
            React.createElement('div', {style: {display: 'flex', gap: 10, alignItems: 'center'}},
                jobMessage ? React.createElement('span', {style: {fontSize: 12, color: jobStatus === 'error' ? '#b91c1c' : '#6b7280'}}, jobMessage) : null,
                view.isCompleted ? null : React.createElement('button', {
                    onClick: refreshRollup,
                    disabled: jobStatus === 'running',
                    style: {background: jobStatus === 'running' ? '#a5b4fc' : '#4f46e5', color: 'white', border: 0, padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 500, cursor: jobStatus === 'running' ? 'default' : 'pointer'}
                }, jobStatus === 'running' ? 'Refreshing…' : '↻ Refresh data'),
                view.isCompleted ? pill('📌 Snapshot', 'indigo') : pill('● Live', 'gray')
            )
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
                    // Show flagged FLWs first; then those with only actions; then any silent rows.
                    var flwRows = (run.flw_rows || []).slice().sort(function(a, b) {
                        var aHas = (a.flags || []).length > 0 ? 0 : 1;
                        var bHas = (b.flags || []).length > 0 ? 0 : 1;
                        return aHas - bHas;
                    });
                    detail = React.createElement('div', {style: {background: 'white', borderRadius: '0 0 12px 12px', border: '2px solid #4f46e5', borderTop: 'none', overflow: 'hidden'}},
                        React.createElement('div', {style: {padding: '14px 20px', background: '#eef2ff', borderBottom: '1px solid #c7d2fe', display: 'flex', justifyContent: 'space-between', alignItems: 'center'}},
                            React.createElement('div', null,
                                React.createElement('div', {style: {fontSize: 11, textTransform: 'uppercase', color: '#4338ca'}}, 'Run detail · ' + (source.label || ('Opp #' + source.opportunity_id)) + ' · ' + fmtDate(run.completed_at)),
                                React.createElement('div', {style: {fontSize: 14, fontWeight: 600, color: '#111827', marginTop: 2}}, 'NM ' + (source.network_manager || '') + ' · Run #' + run.id)
                            ),
                            React.createElement('div', {style: {display: 'flex', gap: 8, alignItems: 'center'}},
                                React.createElement('a', {
                                    href: '/labs/workflow/' + source.workflow_definition_id + '/run/?run_id=' + run.id + '&opportunity_id=' + source.opportunity_id,
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
                                    React.createElement('th', {style: {textAlign: 'left', padding: '10px 16px', fontSize: 11, textTransform: 'uppercase', color: '#6b7280', fontWeight: 500}}, 'Flags'),
                                    React.createElement('th', {style: {textAlign: 'left', padding: '10px 16px', fontSize: 11, textTransform: 'uppercase', color: '#6b7280', fontWeight: 500, width: 240}}, 'Audits'),
                                    React.createElement('th', {style: {textAlign: 'left', padding: '10px 16px', fontSize: 11, textTransform: 'uppercase', color: '#6b7280', fontWeight: 500, width: 280}}, 'Tasks')
                                )
                            ),
                            React.createElement('tbody', null,
                                flwRows.length === 0
                                    ? React.createElement('tr', null,
                                        React.createElement('td', {colSpan: 4, style: {padding: '24px', textAlign: 'center', color: '#9ca3af'}}, 'No FLW activity for this run'))
                                    : flwRows.map(function(fr) { return flwRow(fr, source); })
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
    # Declarative completion contract (no build_snapshot hook): the rollup is
    # computed into run state while the run is live (program_admin_rollup job
    # handler / demo seeder), and conclude captures these state keys verbatim.
    "snapshot_inputs": {
        "pipelines": [],
        "workers": False,
        "state_keys": [
            "watched_summary",
            "window_start",
            "window_end",
            "watched_sources",
            "weeks",
            "expected_weeks",
            "display_window_start",
            "display_window_end",
        ],
    },
    "snapshot_schema": {
        "version": 2,
        "keys": {
            "state.watched_summary": "Per-watched-source rollup computed while live, frozen at completion",
            "state.window_start": "Report window start (ISO)",
            "state.window_end": "Report window end (ISO)",
            "state.watched_sources": "Watched {opportunity_id, workflow_definition_id} pairs",
            "state.expected_weeks": "Optional expected week-start dates driving the grid columns",
            "state.display_window_start": "Optional display override for the window start",
            "state.display_window_end": "Optional display override for the window end",
        },
    },
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
