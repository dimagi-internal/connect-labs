"""Photo Audit Report.

A read-only report over bulk-image-audit sessions. It scopes itself to the
caller's currently-selected labs context (the top-right context picker):

  * a PROGRAM selected  -> program-wide photo success rate (all the program's
    opportunities pooled) plus a per-opportunity breakdown, and
  * a single OPPORTUNITY selected -> just that opportunity.

Success rate = pass / (pass + fail + duplicate_fake): of the photos actually
reviewed during audit, the share that passed (duplicate/fake counts as a
failure; unreviewed "pending" photos are excluded from the denominator and
surfaced as their own KPI).

Filters are chosen from the values actually present (no typing): a multi-select
of auditors and a multi-select of audits (matched by audit-session id OR
workflow-run id), plus a date range over the audit period. Selections persist
on the instance's state.config.

Backend contract:
  * GET /audit/api/scope-context/                      -> current program/opp
  * GET /audit/api/program/<id>/sessions-summary/      -> program fan-out
  * GET /audit/api/opportunity/<id>/sessions-summary/  -> single opportunity
Each session dict carries assessment_stats, opportunity_id/opportunity_name,
auditor_username, workflow_run_id and start_date/end_date. See
connect_labs/audit/views.py and its tests.
"""

DEFINITION = {
    "name": "Photo Audit Report",
    "description": "Photo verification success rate by opportunity and program, from bulk image audits",
    "version": 1,
    "templateType": "photo_audit_report",
    "statuses": [
        {"id": "config", "label": "Configuring", "color": "gray"},
        {"id": "ready", "label": "Ready", "color": "green"},
    ],
    "config": {},
    "pipeline_sources": [],
}

RENDER_CODE = r"""function WorkflowUI({ definition, instance, onUpdateState }) {
    const cfg = (instance.state && instance.state.config) || {};

    // Migrate the earlier free-text config (auditor string, auditIds csv) into
    // the multi-select arrays, so an instance saved before this change still
    // loads with its filters applied.
    const initAuditors = () => {
        if (Array.isArray(cfg.selectedAuditors)) return cfg.selectedAuditors;
        return cfg.auditor ? [cfg.auditor] : [];
    };
    const initAudits = () => {
        if (Array.isArray(cfg.selectedAudits)) return cfg.selectedAudits;
        return (cfg.auditIds || '').split(',').map(s => s.trim()).filter(s => s !== '');
    };

    // ── Filter state (persisted to config) ───────────────────────────────────
    const [selectedAuditors, setSelectedAuditors] = React.useState(initAuditors);
    const [selectedAudits, setSelectedAudits] = React.useState(initAudits);
    const [startDate, setStartDate] = React.useState(cfg.startDate || '');
    const [endDate, setEndDate] = React.useState(cfg.endDate || '');
    const [saving, setSaving] = React.useState(false);
    // Which multi-select menu is open. Kept on the PARENT so re-renders (from
    // ticking a box) don't remount a child and slam the menu shut.
    const [openMenu, setOpenMenu] = React.useState(null);
    // In program mode, drill from "all opportunities" (the program total) into
    // one opportunity. Ignored in single-opportunity scope.
    const [selectedOpp, setSelectedOpp] = React.useState('all');

    // ── Scope (from the top-right context picker) ────────────────────────────
    const [scope, setScope] = React.useState(null);
    const [scopeError, setScopeError] = React.useState(null);
    const [data, setData] = React.useState({ loading: true, error: null, sessions: [] });

    React.useEffect(() => {
        fetch('/audit/api/scope-context/')
            .then(res => res.json())
            .then(d => {
                if (!d || d.success !== true) throw new Error('Could not read the selected context');
                setScope(d);
            })
            .catch(err => setScopeError(String(err.message || err)));
    }, []);

    // Fetch every session in scope (the filters are applied client-side so the
    // dropdowns can offer every auditor/audit that exists).
    const fetchData = () => {
        if (!scope) return;
        let url = null;
        if (scope.mode === 'program' && scope.program_id != null) {
            url = '/audit/api/program/' + scope.program_id + '/sessions-summary/';
        } else if (scope.mode === 'opportunity' && scope.opportunity_id != null) {
            url = '/audit/api/opportunity/' + scope.opportunity_id + '/sessions-summary/';
        }
        if (!url) { setData({ loading: false, error: null, sessions: [] }); return; }
        setData({ loading: true, error: null, sessions: [] });
        fetch(url)
            .then(res => res.json())
            .then(d => {
                if (!d || d.success !== true) throw new Error((d && d.error) || 'Request failed');
                setData({ loading: false, error: null, sessions: d.sessions || [] });
            })
            .catch(err => setData({ loading: false, error: String(err.message || err), sessions: [] }));
    };

    React.useEffect(() => { fetchData(); }, [scope && scope.mode, scope && scope.program_id, scope && scope.opportunity_id]);

    const saveConfig = async () => {
        setSaving(true);
        const clean = {
            selectedAuditors: selectedAuditors,
            selectedAudits: selectedAudits,
            startDate: startDate || '',
            endDate: endDate || '',
        };
        try {
            await onUpdateState({ config: clean, phase: 'ready' });
        } finally {
            setSaving(false);
        }
    };

    // ── Audit identity: the id an auditor would quote (its run if it has one,
    //    else its own session id) -- the same key the backend matches on. ─────
    const auditKey = (s) => String(s.workflow_run_id != null ? s.workflow_run_id : s.id);

    // ── Client-side filters ──────────────────────────────────────────────────
    const inDateRange = (s) => {
        if (!startDate && !endDate) return true;
        const sStart = s.start_date || null;
        const sEnd = s.end_date || s.start_date || null;
        if (!sStart && !sEnd) return true;                 // undated -> keep
        if (startDate && sEnd && sEnd < startDate) return false;
        if (endDate && sStart && sStart > endDate) return false;
        return true;
    };

    const dated = (data.sessions || []).filter(inDateRange);

    // Options come from the date-filtered set so they stay relevant to the window.
    const auditorOptions = (() => {
        const seen = {};
        dated.forEach(s => { if (s.auditor_username) seen[s.auditor_username] = true; });
        return Object.keys(seen).sort().map(a => ({ value: a, label: a }));
    })();
    const auditOptions = (() => {
        const seen = {};
        dated.forEach(s => {
            const k = auditKey(s);
            if (!seen[k]) {
                let label = 'Audit ' + k;
                const bits = [];
                if (s.opportunity_name) bits.push(s.opportunity_name);
                if (s.start_date) bits.push(s.start_date);
                if (bits.length) label += ' · ' + bits.join(' · ');
                seen[k] = { value: k, label: label, count: 0 };
            }
            seen[k].count += 1;
        });
        return Object.values(seen).sort((a, b) => (a.label > b.label ? 1 : -1));
    })();

    const passesFilters = (s) => {
        if (selectedAuditors.length && selectedAuditors.indexOf(s.auditor_username) < 0) return false;
        if (selectedAudits.length && selectedAudits.indexOf(auditKey(s)) < 0) return false;
        return true;
    };

    const allSessions = dated.filter(passesFilters);
    const isProgram = scope && scope.mode === 'program';

    const oppOptions = (() => {
        const seen = {};
        allSessions.forEach(s => { if (!seen[s.opportunity_id]) seen[s.opportunity_id] = s.opportunity_name || ('Opportunity ' + s.opportunity_id); });
        return Object.keys(seen).map(id => ({ id, name: seen[id] })).sort((a, b) => (a.name > b.name ? 1 : -1));
    })();
    const sessions = (isProgram && selectedOpp !== 'all')
        ? allSessions.filter(s => String(s.opportunity_id) === String(selectedOpp))
        : allSessions;

    // ── Aggregation ──────────────────────────────────────────────────────────
    const aggregate = (list) => {
        let p = 0, f = 0, d = 0, pend = 0;
        const auditors = {};
        (list || []).forEach(s => {
            const st = s.assessment_stats || {};
            p += st.pass || 0; f += st.fail || 0; d += st.duplicate_fake || 0; pend += st.pending || 0;
            if (s.auditor_username) auditors[s.auditor_username] = true;
        });
        const denom = p + f + d;
        return { pass: p, fail: f, dup: d, pending: pend, denom, rate: denom > 0 ? (p / denom * 100) : null, count: (list || []).length, auditors: Object.keys(auditors) };
    };

    const groupsByOpp = () => {
        const g = {};
        sessions.forEach(s => {
            const k = s.opportunity_id;
            if (!g[k]) g[k] = { id: k, name: s.opportunity_name || ('Opportunity ' + k), sessions: [] };
            g[k].sessions.push(s);
        });
        return Object.values(g).sort((a, b) => (a.name > b.name ? 1 : -1));
    };

    const fmtRate = (r) => (r === null || r === undefined) ? '—' : (Math.round(r * 100) / 100).toFixed(2) + '%';

    // ── Multi-select dropdown (parent-managed open state) ────────────────────
    const renderMultiSelect = (key, label, options, selected, setSelected) => {
        const isOpen = openMenu === key;
        const summary = selected.length === 0 ? 'All' : (selected.length + ' selected');
        const toggle = (v) => {
            if (selected.indexOf(v) >= 0) setSelected(selected.filter(x => x !== v));
            else setSelected(selected.concat([v]));
        };
        return (
            <div className="relative">
                <label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>
                <button type="button" onClick={() => setOpenMenu(isOpen ? null : key)}
                    className="w-56 flex justify-between items-center border border-gray-300 rounded px-2 py-1.5 text-sm bg-white">
                    <span className={selected.length ? 'text-gray-900' : 'text-gray-400'}>{summary}</span>
                    <i className="fa-solid fa-chevron-down text-xs text-gray-400"></i>
                </button>
                {isOpen && (
                    <div>
                        <div className="fixed inset-0 z-10" onClick={() => setOpenMenu(null)}></div>
                        <div className="absolute z-20 mt-1 w-72 max-h-64 overflow-auto bg-white border border-gray-200 rounded shadow-lg">
                            <div className="flex justify-between px-3 py-1.5 border-b border-gray-100 text-xs">
                                <button className="text-blue-600 hover:text-blue-800" onClick={() => setSelected(options.map(o => o.value))}>Select all</button>
                                <button className="text-gray-500 hover:text-gray-700" onClick={() => setSelected([])}>Clear</button>
                            </div>
                            {options.length === 0 && <div className="px-3 py-2 text-xs text-gray-400">No options</div>}
                            {options.map(o => (
                                <label key={o.value} className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-gray-50 cursor-pointer">
                                    <input type="checkbox" checked={selected.indexOf(o.value) >= 0} onChange={() => toggle(o.value)} />
                                    <span>{o.label}{o.count ? (' (' + o.count + ')') : ''}</span>
                                </label>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        );
    };

    // ── Scope banner ─────────────────────────────────────────────────────────
    const scopeLabel = () => {
        if (!scope) return '';
        if (scope.mode === 'program') return 'Program: ' + (scope.program_name || ('#' + scope.program_id)) + ' — all opportunities';
        if (scope.mode === 'opportunity') return 'Opportunity: ' + (scope.opportunity_name || ('#' + scope.opportunity_id));
        return '';
    };

    const groups = groupsByOpp();
    const overall = aggregate(sessions);
    const showTotalRow = isProgram && selectedOpp === 'all' && groups.length > 1;

    const KpiCard = ({ label, value, sub, highlight, amber }) => (
        <div className={'rounded-lg p-5 border ' + (highlight ? 'bg-blue-50 border-blue-200' : amber ? 'bg-amber-50 border-amber-200' : 'bg-white border-gray-200')}>
            <div className="text-sm font-medium text-gray-500 uppercase tracking-wider">{label}</div>
            <div className={'text-3xl font-bold mt-1 ' + (highlight ? 'text-blue-700' : amber ? 'text-amber-700' : 'text-gray-900')}>{value}</div>
            {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
        </div>
    );

    const AuditorCell = ({ auditors }) => {
        if (!auditors || auditors.length === 0) return <span className="text-gray-400">—</span>;
        if (auditors.length <= 2) return <span>{auditors.join(', ')}</span>;
        return <span title={auditors.join(', ')}>{auditors.slice(0, 2).join(', ')} +{auditors.length - 2}</span>;
    };

    const Row = ({ label, a, total }) => (
        <tr className={total ? 'bg-blue-50 font-semibold' : ''}>
            <td className={'px-4 py-3 ' + (total ? 'text-blue-800' : 'font-medium text-gray-800')}>{label}</td>
            <td className="px-4 py-3 text-gray-600"><AuditorCell auditors={a.auditors} /></td>
            <td className="px-4 py-3 text-right text-green-600">{a.pass}</td>
            <td className="px-4 py-3 text-right text-red-600">{a.fail}</td>
            <td className="px-4 py-3 text-right text-orange-600">{a.dup}</td>
            <td className="px-4 py-3 text-right">{a.denom}</td>
            <td className="px-4 py-3 text-right text-gray-400">{a.pending}</td>
            <td className="px-4 py-3 text-right text-gray-500">{a.count}</td>
            <td className={'px-4 py-3 text-right ' + (total ? 'text-blue-800' : 'font-semibold')}>{fmtRate(a.rate)}</td>
        </tr>
    );

    const filtersDirty = JSON.stringify({ selectedAuditors, selectedAudits, startDate, endDate }) !==
        JSON.stringify({ selectedAuditors: initAuditors(), selectedAudits: initAudits(), startDate: cfg.startDate || '', endDate: cfg.endDate || '' });

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm p-6">
                <div className="flex items-start justify-between">
                    <div>
                        <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                        <p className="text-gray-600 mt-1">{definition.description}</p>
                        {scope && scope.mode !== 'none' && (
                            <p className="text-sm text-gray-500 mt-2"><i className="fa-solid fa-location-crosshairs mr-1"></i>{scopeLabel()}</p>
                        )}
                    </div>
                    <button onClick={fetchData} disabled={data.loading}
                        className="text-sm text-gray-600 hover:text-gray-900 border border-gray-300 rounded px-3 py-1.5 disabled:opacity-50">
                        <i className={'fa-solid fa-rotate mr-1 ' + (data.loading ? 'fa-spin' : '')}></i>Refresh
                    </button>
                </div>

                {/* Filter bar */}
                {scope && scope.mode !== 'none' && (
                    <div className="mt-5 pt-4 border-t border-gray-100 flex flex-wrap items-end gap-4">
                        {renderMultiSelect('auditors', 'Auditors', auditorOptions, selectedAuditors, setSelectedAuditors)}
                        {renderMultiSelect('audits', 'Audits', auditOptions, selectedAudits, setSelectedAudits)}
                        <div>
                            <label className="block text-xs font-medium text-gray-600 mb-1">From</label>
                            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
                                className="border border-gray-300 rounded px-2 py-1.5 text-sm" />
                        </div>
                        <div>
                            <label className="block text-xs font-medium text-gray-600 mb-1">To</label>
                            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
                                className="border border-gray-300 rounded px-2 py-1.5 text-sm" />
                        </div>
                        <button onClick={saveConfig} disabled={saving || !filtersDirty}
                            className="text-sm border rounded px-3 py-1.5 disabled:opacity-40 border-blue-600 text-blue-700 hover:bg-blue-50">
                            {saving ? 'Saving…' : (filtersDirty ? 'Save filters' : 'Saved')}
                        </button>
                    </div>
                )}
            </div>

            {scopeError && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-5 text-sm text-red-700">Could not read the selected context: {scopeError}</div>
            )}

            {scope && scope.mode === 'none' && (
                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-5 text-sm text-yellow-800">
                    Pick a <strong>program</strong> or an <strong>opportunity</strong> from the context selector (top right) to see its photo audit results.
                </div>
            )}

            {isProgram && oppOptions.length > 1 && (
                <div className="flex items-center gap-2 text-sm">
                    <label className="text-gray-600">Show</label>
                    <select value={selectedOpp} onChange={e => setSelectedOpp(e.target.value)}
                        className="border border-gray-300 rounded px-2 py-1">
                        <option value="all">All opportunities (program total)</option>
                        {oppOptions.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
                    </select>
                </div>
            )}

            {scope && scope.mode !== 'none' && (
                <div>
                    {data.error && (
                        <div className="bg-red-50 border border-red-200 rounded-lg p-5 text-sm text-red-700 mb-4">Could not load audits: {data.error}</div>
                    )}
                    {data.loading && (
                        <div className="text-gray-400 text-sm mb-4"><i className="fa-solid fa-spinner fa-spin mr-2"></i>Loading audits…</div>
                    )}

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <KpiCard label="Photo verification success rate" value={fmtRate(overall.rate)}
                            sub={overall.denom > 0 ? (overall.pass + ' of ' + overall.denom + ' reviewed photos passed') : 'No reviewed photos'} highlight={true} />
                        <KpiCard label="Photos not yet reviewed" value={overall.pending}
                            sub={overall.pending > 0 ? 'Excluded from the success rate' : 'Everything sampled has been reviewed'} amber={overall.pending > 0} />
                        <KpiCard label="Audits included" value={overall.count}
                            sub={(isProgram ? groups.length + ' opportunit' + (groups.length === 1 ? 'y' : 'ies') : 'this opportunity')} />
                    </div>

                    <div className="mt-6 bg-white rounded-lg shadow-sm overflow-hidden">
                        <table className="min-w-full text-sm">
                            <thead className="bg-gray-50">
                                <tr className="text-left text-gray-500 uppercase tracking-wider text-xs">
                                    <th className="px-4 py-3">Opportunity</th>
                                    <th className="px-4 py-3">Auditor(s)</th>
                                    <th className="px-4 py-3 text-right">Pass</th>
                                    <th className="px-4 py-3 text-right">Fail</th>
                                    <th className="px-4 py-3 text-right">Dup/Fake</th>
                                    <th className="px-4 py-3 text-right">Reviewed</th>
                                    <th className="px-4 py-3 text-right">Not reviewed</th>
                                    <th className="px-4 py-3 text-right">Audits</th>
                                    <th className="px-4 py-3 text-right">Success rate</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-100">
                                {groups.length === 0 && !data.loading && (
                                    <tr><td colSpan="9" className="px-4 py-6 text-center text-gray-400">No audits match these filters.</td></tr>
                                )}
                                {groups.map(g => <Row key={g.id} label={g.name} a={aggregate(g.sessions)} />)}
                                {showTotalRow && <Row label="Program total" a={overall} total={true} />}
                            </tbody>
                        </table>
                    </div>

                    <p className="mt-3 text-xs text-gray-400">
                        Success rate = pass ÷ (pass + fail + duplicate/fake). "Not reviewed" (pending) photos are excluded from the rate.
                    </p>
                </div>
            )}
        </div>
    );
}"""

TEMPLATE = {
    "key": "photo_audit_report",
    "name": "Photo Audit Report",
    "description": "Photo verification success rate by opportunity and program, from bulk image audits",
    "icon": "fa-clipboard-check",
    "color": "green",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": None,
}
