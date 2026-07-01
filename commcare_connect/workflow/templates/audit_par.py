"""Audit PAR — multi-opp + saved-runs program report over weekly dual-track audits.

Rolls up the weekly_dual_track_audit creator's runs into a week x opp grid of
audit results (MUAC census + sampled remainder), drillable to per-FLW audits.
See docs/superpowers/specs/2026-06-30-audit-program-report-design.md.
"""

import logging

from commcare_connect.audit.data_access import AuditDataAccess
from commcare_connect.workflow.data_access import WorkflowDataAccess

logger = logging.getLogger(__name__)

_TAGS = ("muac", "rest")


def _empty_tag_summary():
    return {"sessions": 0, "pass": 0, "fail": 0, "pending": 0, "ai_flagged": 0}


def summarize_run_sessions(sessions, opportunity_id):
    """Roll one creator run's audit sessions (for one opp) into tag summaries +
    per-FLW rows. See plan Task 4 Interfaces for the return shape.

    Each cell in flw_rows carries a ``session_id`` for deep-linking to
    /audit/<session_id>/bulk/.
    """
    by_tag = {t: _empty_tag_summary() for t in _TAGS}
    rows = {}

    for s in sessions:
        if s.opportunity_id != opportunity_id:
            continue
        tag = s.tag if s.tag in _TAGS else None
        if tag is None:
            continue
        stats = s.get_assessment_stats() or {}
        cell = {
            "pass": stats.get("pass", 0),
            "fail": stats.get("fail", 0),
            "pending": stats.get("pending", 0),
            "ai_flagged": stats.get("ai_no_match", 0),
            "status": s.status,
            "session_id": s.id,
        }
        agg = by_tag[tag]
        agg["sessions"] += 1
        agg["pass"] += cell["pass"]
        agg["fail"] += cell["fail"]
        agg["pending"] += cell["pending"]
        agg["ai_flagged"] += cell["ai_flagged"]

        flw_id = s.flw_username or "unknown"
        row = rows.setdefault(flw_id, {"flw_id": flw_id, "flw_name": flw_id, "muac": None, "rest": None})
        name = getattr(s, "flw_display_name", None)
        if name and name != flw_id:
            row["flw_name"] = name
        row[tag] = cell

    return {"by_tag": by_tag, "flw_rows": list(rows.values())}


def _in_window(run_ws, win_start, win_end):
    return bool(run_ws) and win_start <= run_ws <= win_end


def compute_audit_par_rollup(*, state, request=None, access_token=None, progress_callback=None):
    """Window-scoped rollup of the creator's weekly runs into per-opp week cells.

    Reads sessions per-opp with an opp-scoped AuditDataAccess (the labs API
    enforces opp scope on every request — a single DAO returns 0 for non-primary
    opps; same lesson as program_admin_report).
    """
    win_start = state.get("window_start")
    win_end = state.get("window_end")
    source = state.get("watched_source") or {}

    creator_def_id = source.get("creator_definition_id")
    opportunity_ids = source.get("opportunity_ids", [])
    if not creator_def_id:
        # No creator to watch — the report can't populate. (The window is
        # optional: when absent, every one of the creator's runs is included.)
        return {"watched_summary": [], "error": "missing_source"}

    def _progress(msg):
        if progress_callback:
            progress_callback(msg)

    # list_runs is opp-scoped: the labs API injects opportunity_id into the query,
    # and an UNSCOPED read returns only public records — workflow runs are not
    # public, so a no-opp WorkflowDataAccess finds nothing. Each creator run is
    # owned by one of the watched opps, so list under each and merge by id.
    runs_by_id = {}
    for opp_id in opportunity_ids:
        wda = WorkflowDataAccess(request=request, access_token=access_token, opportunity_id=opp_id)
        try:
            for run in wda.list_runs(creator_def_id):
                runs_by_id[run.id] = run
        finally:
            wda.close()
    runs = list(runs_by_id.values())

    # Keep the creator's runs, sorted by week. When a report window is set,
    # keep only runs whose batch window falls inside it; otherwise include all.
    has_window = bool(win_start and win_end)
    weeks = []
    for run in runs:
        run_state = (run.data or {}).get("state", {})
        rws = run_state.get("window_start")
        if not has_window or _in_window(rws, win_start, win_end):
            weeks.append((rws or "", run_state.get("window_end"), run))
    weeks.sort(key=lambda t: t[0])

    watched_summary = []
    for opp_id in opportunity_ids:
        _progress(f"Rolling up opportunity #{opp_id}…")
        ada = AuditDataAccess(request=request, access_token=access_token, opportunity_id=opp_id)
        try:
            opp_weeks = []
            for rws, rwe, run in weeks:
                sessions = ada.get_sessions_by_workflow_run(run.id)
                summary = summarize_run_sessions(sessions, opportunity_id=opp_id)
                opp_weeks.append(
                    {
                        "window_start": rws,
                        "window_end": rwe,
                        "run_id": run.id,
                        "by_tag": summary["by_tag"],
                        "flw_rows": summary["flw_rows"],
                    }
                )
        finally:
            ada.close()
        watched_summary.append({"opportunity_id": opp_id, "weeks": opp_weeks})

    return {
        "watched_summary": watched_summary,
        "window_start": win_start,
        "window_end": win_end,
        "watched_source": source,
    }


DEFINITION = {
    "name": "Audit Program Report",
    "description": "Weekly cross-opportunity rollup of MUAC-census + sampled-remainder audit results, drillable to FLW.",
    "version": 1,
    "templateType": "audit_par",
    "statuses": [],
    "config": {
        "watched_source": {"creator_definition_id": None, "opportunity_ids": []},
        "window_start": None,
        "window_end": None,
    },
    "pipeline_sources": [],
}


RENDER_CODE = r"""function WorkflowUI({ definition, instance, view, actions }) {
    // Live refresh overlays the server-computed rollup until the next page
    // load (the audit_par_rollup job handler persists it into run state, so
    // view.state has it from then on — and completion freezes it declaratively).
    var [rollupOverlay, setRollupOverlay] = React.useState(null);
    var baseState = (view && view.state) || {};
    var state = rollupOverlay ? Object.assign({}, baseState, rollupOverlay) : baseState;
    var summary = state.watched_summary || [];
    var displayStart = state.window_start || '';
    var displayEnd = state.window_end || '';

    var [selectedCell, setSelectedCell] = React.useState(null); // {opportunity_id, window_start}
    var [jobStatus, setJobStatus] = React.useState('idle'); // idle | running | error
    var [jobMessage, setJobMessage] = React.useState(null);

    function refreshRollup() {
        if (!actions || !actions.startJob || jobStatus === 'running') return;
        setJobStatus('running');
        setJobMessage('Starting rollup…');
        actions.startJob(instance.id, {
            job_type: 'audit_par_rollup',
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

    function markComplete() {
        if (!view || !view.complete || view.isCompleted) return;
        view.complete({ confirm: 'Mark this report complete? The week × opportunity grid will be frozen as a snapshot and can no longer be refreshed.' });
    }

    // Auto-run the rollup on open: a report should load itself, not sit behind a
    // manual "click refresh". Runs once when the report is live and has no data
    // yet; the server handler pulls the watched creator + window from the
    // workflow config. The Update button remains for pulling in newer runs.
    var didAutoRun = React.useRef(false);
    React.useEffect(function () {
        if (view && view.isCompleted) return;      // completed = frozen snapshot
        if (summary.length) return;                 // already have a rollup
        if (didAutoRun.current) return;
        didAutoRun.current = true;
        refreshRollup();
    }, []);

    function fmtDate(iso) {
        if (!iso) return '';
        // UTC components so date-only ISO strings render as the same calendar
        // day in every viewer's TZ.
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
            amber: 'bg-amber-100 text-amber-800',
            gray: 'bg-gray-100 text-gray-700'
        };
        return React.createElement('span', {
            className: 'inline-block px-2 py-0.5 rounded-full text-xs font-medium ' + (palette[color] || palette.gray)
        }, text);
    }

    // Stacked pass/fail/pending mini-bar for one tag summary
    // ({sessions, pass, fail, pending, ai_flagged}).
    function tagBar(label, t) {
        t = t || {};
        var pass = t.pass || 0, fail = t.fail || 0, pending = t.pending || 0;
        var total = pass + fail + pending;
        var seg = function(key, val, bg) {
            if (!val) return null;
            return React.createElement('div', {key: key, style: {background: bg, width: ((val / total) * 100) + '%'}});
        };
        var bar = total > 0
            ? React.createElement('div', {style: {display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', marginTop: 2, background: '#f3f4f6'}},
                seg('p', pass, '#16a34a'),
                seg('f', fail, '#dc2626'),
                seg('q', pending, '#d1d5db')
            )
            : React.createElement('div', {style: {height: 6, borderRadius: 3, marginTop: 2, background: '#f3f4f6'}});
        return React.createElement('div', {style: {fontSize: 10}},
            React.createElement('div', {style: {display: 'flex', justifyContent: 'space-between', alignItems: 'center'}},
                React.createElement('span', {style: {color: '#374151', fontWeight: 600}}, label),
                React.createElement('span', {style: {color: '#6b7280'}},
                    total > 0 ? (pass + '✓ · ' + fail + '✗' + (pending ? ' · ' + pending + '…' : '')) : '—')
            ),
            bar
        );
    }

    function weekObjFor(opp, col) {
        return (opp.weeks || []).filter(function(w) { return w.window_start === col; })[0] || null;
    }

    function weekCellCard(opp, week) {
        var isSelected = selectedCell &&
            selectedCell.opportunity_id === opp.opportunity_id &&
            selectedCell.window_start === week.window_start;
        var bt = week.by_tag || {};
        var muac = bt.muac || {};
        var rest = bt.rest || {};
        var aiFlags = muac.ai_flagged || 0;
        var border = isSelected ? '2px solid #4f46e5' : (aiFlags > 0 ? '1px solid #fcd34d' : '1px solid #e5e7eb');
        return React.createElement('div', {
            onClick: function() {
                setSelectedCell(isSelected ? null : {opportunity_id: opp.opportunity_id, window_start: week.window_start});
            },
            style: {
                background: isSelected ? '#eef2ff' : 'white',
                borderRadius: 10, border: border, padding: 10, cursor: 'pointer',
                display: 'flex', flexDirection: 'column', gap: 6, position: 'relative',
            },
        },
            isSelected
                ? React.createElement('div', {style: {position: 'absolute', top: -7, right: 8, background: '#4f46e5', color: 'white', fontSize: 9, padding: '1px 6px', borderRadius: 3, fontWeight: 600}}, 'SELECTED')
                : null,
            React.createElement('div', {style: {display: 'flex', justifyContent: 'space-between', alignItems: 'center'}},
                React.createElement('span', {style: {fontSize: 10, color: '#6b7280'}}, fmtDate(week.window_start)),
                aiFlags > 0 ? pill('⚑ ' + aiFlags + ' AI', 'amber') : null
            ),
            tagBar('MUAC', muac),
            tagBar('Rest', rest)
        );
    }

    function noRunCell(key) {
        return React.createElement('div', {
            key: key,
            style: {
                background: '#fafafa', borderRadius: 10, border: '1px dashed #e5e7eb',
                padding: 10, display: 'flex', alignItems: 'center', justifyContent: 'center',
                minHeight: 70, color: '#cbd5e1', fontSize: 18,
            }
        }, '—');
    }

    // One FLW tag cell in the detail panel: pass/fail (+ AI flag) with a direct
    // deep-link to that audit session's bulk page. Null/missing => dash, no link.
    function flwTagCell(cell, oppId, withAi) {
        if (!cell) return React.createElement('span', {className: 'text-gray-400 text-xs'}, '—');
        var bits = [];
        bits.push(React.createElement('span', {key: 'pf', style: {fontSize: 12, color: '#374151'}},
            (cell.pass || 0) + '✓ · ' + (cell.fail || 0) + '✗' + (cell.pending ? ' · ' + cell.pending + '…' : '')));
        if (withAi && (cell.ai_flagged || 0) > 0) {
            bits.push(React.createElement('span', {key: 'ai', style: {marginLeft: 6}}, pill('⚑ ' + cell.ai_flagged, 'amber')));
        }
        var link = cell.session_id != null
            ? React.createElement('a', {
                key: 'lnk',
                href: '/audit/' + cell.session_id + '/bulk/?opportunity_id=' + oppId,
                className: 'text-indigo-600 underline text-xs',
                style: {marginLeft: 8},
            }, 'open')
            : null;
        return React.createElement('div', {style: {display: 'flex', alignItems: 'center'}}, bits, link);
    }

    function flwRow(fr, oppId) {
        var flagged = (fr.muac && (fr.muac.ai_flagged || 0) > 0);
        return React.createElement('tr', {key: fr.flw_id, className: flagged ? 'bg-amber-50' : ''},
            React.createElement('td', {className: 'px-3 py-2 text-sm font-medium'}, fr.flw_name || fr.flw_id),
            React.createElement('td', {className: 'px-3 py-2 text-sm'}, flwTagCell(fr.muac, oppId, true)),
            React.createElement('td', {className: 'px-3 py-2 text-sm'}, flwTagCell(fr.rest, oppId, false))
        );
    }

    // Column union: sorted distinct window_start across all opp weeks.
    var seen = {};
    summary.forEach(function(opp) {
        (opp.weeks || []).forEach(function(w) { if (w.window_start) seen[w.window_start] = true; });
    });
    var weekColumns = Object.keys(seen).sort();

    var gridTemplate = '220px repeat(' + weekColumns.length + ', minmax(170px, 1fr))';

    return React.createElement('div', {style: {padding: 16, background: '#f7f8fb', minHeight: '100vh'}},
        // Top strip
        React.createElement('div', {style: {background: 'white', borderRadius: 10, border: '1px solid #e5e7eb', padding: 16, marginBottom: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'center'}},
            React.createElement('div', null,
                React.createElement('div', {style: {fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#6b7280'}}, 'Audit Program Report · ' + (definition.name || '')),
                React.createElement('div', {style: {fontSize: 18, fontWeight: 600, color: '#111827', marginTop: 2}},
                    (displayStart ? fmtDate(displayStart) + ' – ' + fmtDate(displayEnd) + ' · ' : '') + summary.length + ' opportunities · ' + weekColumns.length + ' weeks'
                )
            ),
            React.createElement('div', {style: {display: 'flex', gap: 10, alignItems: 'center'}},
                jobMessage ? React.createElement('span', {style: {fontSize: 12, color: jobStatus === 'error' ? '#b91c1c' : '#6b7280'}}, jobMessage) : null,
                view.isCompleted ? null : React.createElement('button', {
                    onClick: refreshRollup,
                    disabled: jobStatus === 'running',
                    style: {background: jobStatus === 'running' ? '#a5b4fc' : '#4f46e5', color: 'white', border: 0, padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 500, cursor: jobStatus === 'running' ? 'default' : 'pointer'}
                }, jobStatus === 'running' ? 'Loading…' : (summary.length ? '↻ Update' : '↻ Load report')),
                view.isCompleted ? null : React.createElement('button', {
                    onClick: markComplete,
                    style: {background: '#16a34a', color: 'white', border: 0, padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 500, cursor: 'pointer'}
                }, 'Mark Run Complete'),
                view.isCompleted ? pill('📌 Snapshot', 'indigo') : pill('● Live', 'gray')
            )
        ),
        // Completion banner
        view.isCompleted
            ? React.createElement('div', {style: {background: '#f3f4f6', borderLeft: '4px solid #9ca3af', padding: 12, borderRadius: 6, marginBottom: 14, fontSize: 13, color: '#374151'}},
                React.createElement('strong', null, 'This report is completed.'),
                view.asOf ? ' Snapshot from ' + new Date(view.asOf).toLocaleString() + '.' : '',
                ' The grid is read-only. To refresh, start a new run.')
            : null,
        // Empty state
        summary.length === 0
            ? React.createElement('div', {style: {background: 'white', borderRadius: 10, border: '1px solid #e5e7eb', padding: 32, textAlign: 'center', color: '#9ca3af'}},
                jobStatus === 'running'
                    ? 'Loading the watched creator\'s audit runs…'
                    : (jobStatus === 'error'
                        ? (jobMessage || 'Could not load the report.')
                        : 'No audit runs to show yet — create a batch on the watched creator and it will appear here (use ↻ Update to pull the latest).'))
            : null,
        // Column header row
        summary.length > 0
            ? React.createElement('div', {style: {display: 'grid', gridTemplateColumns: gridTemplate, gap: 10, marginBottom: 6, padding: '0 2px'}},
                React.createElement('div'),
                weekColumns.map(function(w, i) {
                    return React.createElement('div', {key: i, style: {fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#6b7280', textAlign: 'center'}},
                        'Wk ' + (i + 1) + ' · ' + fmtDate(w));
                })
            )
            : null,
        // Per-opp rows + inline detail panel below the row that owns selectedCell
        summary.map(function(opp) {
            var weekCells = weekColumns.map(function(col, i) {
                var week = weekObjFor(opp, col);
                return React.createElement('div', {key: i}, week ? weekCellCard(opp, week) : noRunCell(i));
            });

            var detail = null;
            if (selectedCell && selectedCell.opportunity_id === opp.opportunity_id) {
                var week = weekObjFor(opp, selectedCell.window_start);
                if (week) {
                    var flwRows = (week.flw_rows || []).slice().sort(function(a, b) {
                        var aHas = (a.muac && (a.muac.ai_flagged || 0) > 0) ? 0 : 1;
                        var bHas = (b.muac && (b.muac.ai_flagged || 0) > 0) ? 0 : 1;
                        return aHas - bHas;
                    });
                    detail = React.createElement('div', {style: {background: 'white', borderRadius: '0 0 12px 12px', border: '2px solid #4f46e5', borderTop: 'none', overflow: 'hidden'}},
                        React.createElement('div', {style: {padding: '14px 20px', background: '#eef2ff', borderBottom: '1px solid #c7d2fe', display: 'flex', justifyContent: 'space-between', alignItems: 'center'}},
                            React.createElement('div', null,
                                React.createElement('div', {style: {fontSize: 11, textTransform: 'uppercase', color: '#4338ca'}}, 'Week detail · Opp #' + opp.opportunity_id + ' · ' + fmtDate(week.window_start) + ' – ' + fmtDate(week.window_end)),
                                React.createElement('div', {style: {fontSize: 14, fontWeight: 600, color: '#111827', marginTop: 2}}, (week.flw_rows || []).length + ' FLW(s) · Run #' + week.run_id)
                            ),
                            React.createElement('button', {
                                onClick: function() { setSelectedCell(null); },
                                style: {background: 'transparent', border: 0, fontSize: 18, color: '#6b7280', cursor: 'pointer', lineHeight: 1}
                            }, '×')
                        ),
                        React.createElement('table', {style: {width: '100%', borderCollapse: 'collapse', fontSize: 12}},
                            React.createElement('thead', {style: {background: '#f9fafb'}},
                                React.createElement('tr', null,
                                    React.createElement('th', {style: {textAlign: 'left', padding: '10px 16px', fontSize: 11, textTransform: 'uppercase', color: '#6b7280', fontWeight: 500, width: 220}}, 'FLW'),
                                    React.createElement('th', {style: {textAlign: 'left', padding: '10px 16px', fontSize: 11, textTransform: 'uppercase', color: '#6b7280', fontWeight: 500}}, 'MUAC census'),
                                    React.createElement('th', {style: {textAlign: 'left', padding: '10px 16px', fontSize: 11, textTransform: 'uppercase', color: '#6b7280', fontWeight: 500}}, 'Sampled remainder')
                                )
                            ),
                            React.createElement('tbody', null,
                                flwRows.length === 0
                                    ? React.createElement('tr', null,
                                        React.createElement('td', {colSpan: 3, style: {padding: '24px', textAlign: 'center', color: '#9ca3af'}}, 'No FLW activity for this week'))
                                    : flwRows.map(function(fr) { return flwRow(fr, opp.opportunity_id); })
                            )
                        )
                    );
                }
            }

            return React.createElement('div', {key: opp.opportunity_id, style: {marginBottom: 14}},
                React.createElement('div', {style: {display: 'grid', gridTemplateColumns: gridTemplate, gap: 10, alignItems: 'stretch'}},
                    React.createElement('div', {style: {background: 'white', borderRadius: 10, border: '1px solid #e5e7eb', padding: 12, display: 'flex', flexDirection: 'column', justifyContent: 'center'}},
                        React.createElement('div', {style: {fontWeight: 600, color: '#111827', fontSize: 14}}, 'Opp #' + opp.opportunity_id),
                        React.createElement('div', {style: {fontSize: 11, color: '#6b7280', marginTop: 2}}, (opp.weeks || []).length + ' week(s) with runs')
                    ),
                    weekCells
                ),
                detail
            );
        })
    );
}"""


PIPELINE_SCHEMA = None


TEMPLATE = {
    "key": "audit_par",
    "name": "Audit Program Report",
    "description": DEFINITION["description"],
    "icon": "fa-clipboard-check",
    "color": "purple",
    "multi_opp": True,
    "supports_saved_runs": True,
    "snapshot_inputs": {
        "pipelines": [],
        "workers": False,
        "state_keys": ["watched_summary", "window_start", "window_end", "watched_source"],
    },
    "snapshot_schema": {
        "version": 1,
        "keys": {
            "state.watched_summary": "Per-opp week cells of audit results, computed live, frozen at completion",
            "state.window_start": "Report window start (ISO)",
            "state.window_end": "Report window end (ISO)",
            "state.watched_source": "{creator_definition_id, opportunity_ids}",
        },
    },
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
