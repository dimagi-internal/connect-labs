function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    // ---- data (bound to the SAM follow-up pipelines: children + visits) ----
    const visitsAll = (pipelines && pipelines.visits && pipelines.visits.rows) || [];
    const childrenRows = (pipelines && pipelines.children && pipelines.children.rows) || [];

    // MUAC recovery bands (cm): <11.5 SAM · 11.5–12.5 MAM · >=12.5 recovered
    const band = (m) => m == null ? { key: 'none', label: '—', color: '#9ca3af', bg: '#f3f4f6' }
        : m < 11.5 ? { key: 'sam', label: 'SAM', color: '#dc2626', bg: '#fee2e2' }
        : m < 12.5 ? { key: 'mam', label: 'MAM', color: '#d97706', bg: '#fef3c7' }
        : { key: 'rec', label: 'Recovered', color: '#059669', bg: '#d1fae5' };

    const num = (v) => (v == null || v === '') ? null : (isNaN(parseFloat(v)) ? null : parseFloat(v));

    const byChild = React.useMemo(() => {
        const g = {};
        visitsAll.forEach((v) => {
            const k = v.entity_id;
            if (!k) return;
            (g[k] = g[k] || []).push(v);
        });
        Object.keys(g).forEach((k) => g[k].sort((a, b) =>
            new Date(a.visit_date || 0) - new Date(b.visit_date || 0)));
        return g;
    }, [visitsAll]);

    // Rank by a CLEAN climb, not just net gain: a child whose MUAC bounces
    // 14.5 -> 13 -> 12 -> 15.4 has a big delta but reads as noise, not recovery.
    // Penalising each week-over-week drop puts the monotonic climbers first.
    const childIds = React.useMemo(() => {
        const ids = Object.keys(byChild).filter((k) => (byChild[k] || []).length >= 4);
        const score = (k) => {
            const ms = byChild[k].map((v) => num(v.muac_cm)).filter((m) => m != null);
            if (ms.length < 4) return -99;
            let drops = 0;
            for (let i = 1; i < ms.length; i++) if (ms[i] < ms[i - 1]) drops += (ms[i - 1] - ms[i]);
            const gain = ms[ms.length - 1] - ms[0];
            const startsLow = ms[0] < 12.5 ? 0.5 : 0;   // a story needs somewhere to climb from
            return gain - 1.5 * drops + startsLow;
        };
        return ids.sort((x, y) => score(y) - score(x));
    }, [byChild]);

    const [selectedId, setSelectedId] = React.useState(null);
    const [openPhoto, setOpenPhoto] = React.useState(null);
    const chartRef = React.useRef(null);
    const chartInst = React.useRef(null);
    const mapRef = React.useRef(null);
    const mapInst = React.useRef(null);

    const activeId = selectedId || childIds[0] || null;
    const visits = activeId ? (byChild[activeId] || []) : [];
    const meta = childrenRows.find((c) => c.entity_id === activeId) || {};
    const first = visits[0] || {};
    const last = visits[visits.length - 1] || {};
    const firstM = num(first.muac_cm), lastM = num(last.muac_cm);
    const gain = (firstM != null && lastM != null) ? (lastM - firstM) : null;
    const name = first.entity_name || meta.entity_name || activeId || '—';
    const age = first.childs_age_in_month || meta.childs_age_in_month;
    const phone = first.household_phone || meta.household_phone;
    const curBand = band(lastM);
    // One screening photo per visit. If the visit data ever carries a real image
    // reference we use it; otherwise we fall back to the synthetic MUAC corpus the
    // rest of this demo's audits draw from, picked deterministically per visit so a
    // given visit always shows the same photo.
    const oppId = (typeof instance === 'object' && instance && instance.opportunity_id) || 10036;
    const CORPUS = ['001', '002', '003', '004', '005', '006', '007', '008'];
    const photoOf = (v, i) => v.muac_photo_url || v.image_url || v.photo_url ||
        ('/audit/image/' + oppId + '/synth-muac-good-' + CORPUS[i % CORPUS.length] + '/');
    const anyGps = visits.some((v) => v.gps);

    // Esc closes the expanded photo. Click-outside alone left users (and the
    // walkthrough recorder) with no reliable way out of the overlay.
    React.useEffect(() => {
        if (!openPhoto) return;
        const onKey = (e) => { if (e.key === 'Escape') setOpenPhoto(null); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [openPhoto]);

    // ---- MUAC progression chart, with the recovery bands drawn behind it ----
    React.useEffect(() => {
        if (!chartRef.current || !window.Chart || visits.length === 0) return;
        if (chartInst.current) { chartInst.current.destroy(); chartInst.current = null; }
        const pts = visits.map((v) => ({ x: v.visit_date, y: num(v.muac_cm) })).filter((p) => p.y != null);
        if (!pts.length) return;
        chartInst.current = new window.Chart(chartRef.current.getContext('2d'), {
            type: 'line',
            data: {
                datasets: [{
                    label: 'MUAC (cm)', data: pts, borderColor: '#2563eb', borderWidth: 3,
                    pointRadius: 5, pointBackgroundColor: pts.map((p) => band(p.y).color),
                    tension: 0.3, fill: false,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { type: 'time', time: { unit: 'week' }, grid: { display: false } },
                    // Scale to the data — a fixed 14 ceiling silently clipped a 15.4cm visit.
                    y: {
                        min: Math.min(10.5, Math.floor(Math.min.apply(null, pts.map((p) => p.y)) * 2) / 2 - 0.5),
                        max: Math.max(13, Math.ceil(Math.max.apply(null, pts.map((p) => p.y)) * 2) / 2 + 0.5),
                        title: { display: true, text: 'MUAC (cm)' }, ticks: { stepSize: 0.5 },
                    },
                },
            },
            plugins: [{
                id: 'muacBands',
                beforeDraw: (chart) => {
                    const { ctx, chartArea: a, scales: { y } } = chart;
                    if (!a) return;
                    // Bands follow the axis, so the "recovered" zone reaches the top
                    // whatever the child's peak MUAC turns out to be.
                    [[y.min, 11.5, 'rgba(220,38,38,0.08)'], [11.5, 12.5, 'rgba(217,119,6,0.08)'],
                     [12.5, y.max, 'rgba(5,150,105,0.10)']].forEach(([lo, hi, col]) => {
                        const y1 = y.getPixelForValue(hi), y2 = y.getPixelForValue(lo);
                        ctx.fillStyle = col; ctx.fillRect(a.left, y1, a.right - a.left, y2 - y1);
                    });
                },
            }],
        });
        return () => { if (chartInst.current) { chartInst.current.destroy(); chartInst.current = null; } };
    }, [activeId, visits.length]);

    // ---- Visit-location map (only when the visits actually carry GPS) ----
    React.useEffect(() => {
        if (!mapRef.current || !window.L || typeof window.L.map !== 'function') return;
        const coords = visits.map((v, i) => {
            if (!v.gps) return null;
            const p = String(v.gps).split(/[ ,]+/).map(parseFloat);
            return (p.length >= 2 && !isNaN(p[0]) && !isNaN(p[1])) ? { lat: p[0], lng: p[1], i, v } : null;
        }).filter(Boolean);
        if (mapInst.current) { mapInst.current.remove(); mapInst.current = null; }
        if (!coords.length) return;
        const map = window.L.map(mapRef.current).setView([coords[0].lat, coords[0].lng], 13);
        window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18 }).addTo(map);
        coords.forEach((c) => window.L.circleMarker([c.lat, c.lng], {
            radius: 7, color: band(num(c.v.muac_cm)).color,
            fillColor: band(num(c.v.muac_cm)).color, fillOpacity: 0.85,
        }).bindPopup('Visit ' + (c.i + 1) + ' · MUAC ' + c.v.muac_cm + 'cm').addTo(map));
        const line = window.L.polyline(coords.map((c) => [c.lat, c.lng]), { color: '#2563eb', weight: 2, dashArray: '4' }).addTo(map);
        map.fitBounds(line.getBounds().pad(0.3));
        mapInst.current = map;
        return () => { if (mapInst.current) { mapInst.current.remove(); mapInst.current = null; } };
    }, [activeId, visits.length]);

    if (!childIds.length) {
        return <div className="p-8 text-center text-sm text-gray-500">No SAM follow-up visits found.</div>;
    }

    return (
        <div className="max-w-7xl mx-auto space-y-4">
            {/* header + client picker */}
            <div className="bg-white rounded-lg shadow-sm p-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-xl font-bold text-gray-900">{definition.name || 'Single Client Weight Trend'}</h1>
                    <p className="text-sm text-gray-500">One child's MUAC recovery across their follow-up visits</p>
                </div>
                <select value={activeId || ''} onChange={(e) => setSelectedId(e.target.value)}
                    className="border border-gray-300 rounded-md px-3 py-2 text-sm min-w-[240px]">
                    {childIds.slice(0, 60).map((id) => {
                        const vs = byChild[id];
                        const lb = band(num(vs[vs.length - 1].muac_cm));
                        return <option key={id} value={id}>{(vs[0] && vs[0].entity_name) || id} — {lb.label}</option>;
                    })}
                </select>
            </div>

            {/* Explicit grid styles: this harness's Tailwind build ships the utility
                classes but NOT grid-cols-12 / col-span-*, so those silently stacked. */}
            <div style={{ display: 'grid', gridTemplateColumns: '310px minmax(0, 1fr) 280px', gap: '16px', alignItems: 'start' }}>
                {/* LEFT RAIL — the visits */}
                <div className="bg-white rounded-lg shadow-sm p-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
                        MUAC photo per visit ({visits.length})
                    </div>
                    <div className="space-y-1.5 max-h-[34rem] overflow-y-auto pr-1">
                        {visits.map((v, i) => {
                            const b = band(num(v.muac_cm));
                            const src = photoOf(v, i);
                            return (
                                <button key={v.id || i} onClick={() => setOpenPhoto({ src, i, v })}
                                    className="w-full flex items-center gap-2.5 rounded-md border px-2 py-1.5 text-left hover:shadow-sm"
                                    style={{ borderColor: b.bg, background: b.bg + '55' }}>
                                    <img src={src} alt={'Visit ' + (i + 1) + ' MUAC photo'}
                                        className="w-16 h-16 rounded object-cover border-2 shrink-0"
                                        style={{ borderColor: b.color }} />
                                    <div className="flex-1 min-w-0">
                                        <div className="text-sm font-medium text-gray-800">Visit {i + 1}</div>
                                        <div className="text-xs text-gray-500">{(v.visit_date || '').slice(0, 10)}</div>
                                    </div>
                                    <div className="text-right shrink-0">
                                        <div className="text-sm font-semibold" style={{ color: b.color }}>
                                            {v.muac_cm != null ? v.muac_cm : '—'}<span className="text-xs font-normal"> cm</span>
                                        </div>
                                        <div className="text-[10px] font-medium" style={{ color: b.color }}>{b.label}</div>
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                </div>

                {/* CENTER — the weight/MUAC trend, made prominent */}
                <div className="bg-white rounded-lg shadow-sm p-4">
                    <div className="flex items-center justify-between gap-3 mb-2 flex-nowrap">
                        <h2 className="text-xl font-bold text-gray-900">MUAC progression</h2>
                        <span className="text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap shrink-0"
                            style={{ background: curBand.bg, color: curBand.color }}>
                            now {lastM != null ? lastM : '—'}cm · {curBand.label}
                        </span>
                    </div>
                    <div style={{ height: '430px' }}><canvas ref={chartRef}></canvas></div>
                    <div className="mt-2 flex gap-4 text-xs text-gray-500">
                        <span><span className="inline-block w-3 h-3 align-middle rounded-sm" style={{ background: 'rgba(220,38,38,0.25)' }}></span> SAM &lt;11.5</span>
                        <span><span className="inline-block w-3 h-3 align-middle rounded-sm" style={{ background: 'rgba(217,119,6,0.25)' }}></span> MAM 11.5–12.5</span>
                        <span><span className="inline-block w-3 h-3 align-middle rounded-sm" style={{ background: 'rgba(5,150,105,0.3)' }}></span> Recovered ≥12.5</span>
                    </div>
                </div>

                {/* RIGHT RAIL — client / case */}
                <div className="bg-white rounded-lg shadow-sm p-4 space-y-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Client / Case</div>
                    <div>
                        <div className="text-base font-semibold text-gray-900">{name}</div>
                        <div className="text-[11px] text-gray-500 font-mono break-all">{activeId}</div>
                    </div>
                    <dl className="text-sm space-y-1.5">
                        <div className="flex justify-between"><dt className="text-gray-500">Age</dt><dd className="text-gray-800">{age ? age + ' mo' : '—'}</dd></div>
                        <div className="flex justify-between"><dt className="text-gray-500">Phone</dt><dd className="text-gray-800">{phone || '—'}</dd></div>
                        <div className="flex justify-between"><dt className="text-gray-500">FLW</dt><dd className="text-gray-800">{first.username || '—'}</dd></div>
                        <div className="flex justify-between"><dt className="text-gray-500">Visits</dt><dd className="text-gray-800">{visits.length}</dd></div>
                        <div className="flex justify-between"><dt className="text-gray-500">First MUAC</dt><dd className="text-gray-800">{firstM != null ? firstM + ' cm' : '—'}</dd></div>
                        <div className="flex justify-between"><dt className="text-gray-500">Current MUAC</dt><dd className="font-semibold" style={{ color: curBand.color }}>{lastM != null ? lastM + ' cm' : '—'}</dd></div>
                        <div className="flex justify-between"><dt className="text-gray-500">MUAC gain</dt>
                            <dd className="font-semibold" style={{ color: (gain != null && gain >= 0) ? '#059669' : '#dc2626' }}>
                                {gain != null ? (gain >= 0 ? '+' : '') + gain.toFixed(1) + ' cm' : '—'}
                            </dd></div>
                        <div className="flex justify-between"><dt className="text-gray-500">Status</dt>
                            <dd><span className="text-xs px-2 py-0.5 rounded-full font-medium"
                                style={{ background: curBand.bg, color: curBand.color }}>{curBand.label}</span></dd></div>
                    </dl>
                    {anyGps && (
                        <div className="pt-1">
                            <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">Visit locations</div>
                            <div ref={mapRef} style={{ height: '170px', borderRadius: '8px', overflow: 'hidden', background: '#eef2f7' }} />
                        </div>
                    )}
                </div>
            </div>

            {openPhoto && (
                <div onClick={() => setOpenPhoto(null)} className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-6">
                    <div className="bg-white rounded-lg p-3 max-w-lg relative" onClick={(e) => e.stopPropagation()}>
                        <button aria-label="Close photo" onClick={() => setOpenPhoto(null)}
                            className="absolute -top-3 -right-3 w-8 h-8 rounded-full bg-white shadow-md border border-gray-300 text-gray-600 text-lg leading-none flex items-center justify-center hover:bg-gray-50">
                            ×
                        </button>
                        <img src={openPhoto.src} className="w-full rounded" />
                        <div className="mt-2 text-sm text-gray-700">
                            Visit {openPhoto.i + 1} · {(openPhoto.v.visit_date || '').slice(0, 10)} · MUAC {openPhoto.v.muac_cm}cm
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
