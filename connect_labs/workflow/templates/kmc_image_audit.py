"""
KMC Image Audit — cross-LLO scale-photo audit with per-opportunity AI routing.

Why this template exists
------------------------
KMC's existing options each fall short in a specific way:

* ``bulk_image_audit`` (wf 5113) works, but its AI agent list is hardcoded, it has no
  sampling controls, and one agent applies to the whole run — which is wrong for KMC
  because scale hardware differs by LLO.
* ``weekly_dual_track_audit`` has the better operating model (census one image type,
  sample the rest, run on a schedule) but its template lives outside this repo and its
  classifier registry has no analog-dial entry, so it cannot serve EHA/BERI/NAMA.

The deciding constraint is hardware: PIPN uses digital scales, EHA/BERI/NAMA use analog
dials, and *within program 46 both appear side by side*. So the agent has to be chosen
per opportunity, not per run. That is this template's core idea, and the reason it fans
out one single-opportunity audit per selected opp rather than one audit for all of them.

Design notes worth knowing before editing
-----------------------------------------
* **Per-opp fan-out is deliberate.** Setting ``opportunity_ids`` and relying on the
  backend's multi-opp routing produced zero sessions for every non-home opportunity when
  it was tried on wf 5113. Firing one single-opp ``createAudit`` per opportunity — the
  pattern the KMC dashboard already uses in production — works. Keep it.
* **Sessions are stored scoped per opportunity**, so the results fetch loops the opp set
  and merges. Fetching ``/sessions/`` without ``?opportunity_id=`` only ever returns the
  home opp's sessions, which reads as "0 sessions" for everything else.
* **Image extraction differs between this repo and the deployed backend.** The copy of
  ``_filter_visits_by_related_fields`` in this repo filters *visits* and returns every image
  on a matched visit — running it locally against opp 1487 produced four question types and
  161 of 308 images (52%) with no reading. The DEPLOYED backend also narrows to the
  requested image path: live run 13250 extracted 858 images across 6 opportunities, all of
  them ``anthropometric/upload_weight_image`` and all 858 carrying a reading. The repo is
  behind. The "images without a reading" warning is kept as a guard — it simply does not
  fire on the deployed path — and must not be removed on the assumption that it is dead.
* **There is no per-image-type sampling and no FLW cap.** ``AuditCriteria`` accepts only
  ``sample_percentage`` for a date-range audit (``count_per_flw`` applies solely to
  ``last_n_per_flw``, and ``max_flws`` does not exist — passing it is silently ignored).
  An earlier draft of this template exposed both; they were removed rather than shipped
  as controls that quietly do nothing.
"""

import logging

logger = logging.getLogger(__name__)

# Scale hardware per LLO drives which AI agent scores the photo. Sources: per-project
# procurement (EHA/BERI Salter 235 dials, NAMA KINLee dials, PIPN digital); GHI-KE and
# Kikapu are UNCONFIRMED and provisionally treated as digital — the UI flags them as
# unverified so nobody reads a green verdict there as settled.
DIGITAL = "digital"
DIAL = "dial"

OPP_META = {
    523: {"llo": "NAMA", "country": "Uganda", "version": "V0/V1", "scale": DIAL, "program": 46},
    524: {"llo": "PIPN", "country": "Uganda", "version": "V0/V1", "scale": DIGITAL, "program": 46},
    675: {
        "llo": "GHI-KE",
        "country": "Kenya",
        "version": "V0/V1",
        "scale": DIGITAL,
        "program": 68,
        "unverified": True,
    },
    874: {"llo": "PIPN", "country": "Uganda", "version": "V2", "scale": DIGITAL, "program": 46},
    938: {"llo": "NAMA", "country": "Uganda", "version": "V2", "scale": DIAL, "program": 46},
    1234: {"llo": "GHI-KE", "country": "Kenya", "version": "V2", "scale": DIGITAL, "program": 68, "unverified": True},
    1236: {"llo": "EHA", "country": "Nigeria", "version": "V2+", "scale": DIAL, "program": 114},
    1487: {
        "llo": "PIPN",
        "country": "Uganda",
        "version": "V3",
        "scale": DIGITAL,
        "program": 46,
        # Two live opportunities are both "PIPN V3" and the bare version cannot tell them
        # apart in a picker. See 2166.
        "display_name": "PIPN (V3 Apr-26)",
    },
    1488: {"llo": "NAMA", "country": "Uganda", "version": "V3", "scale": DIAL, "program": 46},
    1739: {"llo": "Kikapu", "country": "Kenya", "version": "V3", "scale": DIGITAL, "program": 68, "unverified": True},
    1790: {"llo": "BERI", "country": "Nigeria", "version": "V3", "scale": DIAL, "program": 114},
    2166: {
        "llo": "PIPN",
        "country": "Uganda",
        "version": "V3 GW",
        "scale": DIGITAL,
        "program": 46,
        "display_name": "PIPN (V3 GW Aug-26)",
        "confirmed_on": "2026-08-25",
        "confirmed_note": (
            "GiveWell scale-up; SD confirmed digital. Deliver app verified byte-equivalent "
            "to 1487 on the audited weight path and form name."
        ),
    },
}

# The follow-up weight photo and the typed reading it is checked against. Verified against
# raw form JSON for all 11 opportunities. Registration photos live at
# child_details/upload_weight_image instead and are NOT audited here — that path carries
# ~29% of all KMC weight images and is a deliberate, flagged omission, not an oversight.
WEIGHT_IMAGE_PATH = "anthropometric/upload_weight_image"
WEIGHT_FIELD_PATH = "child_weight_visit"

# The label a reviewer sees on the typed reading shown beside a weight photo, and the
# field the scale classifier compares that photo against.
READING_LABEL = "Scale Weight Reading"

# ── The photo types this workflow can audit ───────────────────────────────────
# Declared ONCE. This list is what the dashboard's picker offers, what the schedule
# dialog offers, what run_default turns into audit rules, and what decides whether a
# type counts toward the machine-checked pass rate. Adding a type here adds it to all
# four; there is no second list anywhere to keep in step with this one.
#
# "scoreable" means an AI classifier exists for that photo. Only the weight photo has
# one. Equipment and KMC-wrap photos are review-only and always have been -- the older
# bulk-image workflow never scored them either, because its agents only ever applied to
# weight and MUAC paths. Marking them here is what keeps them OUT of the pass-rate
# denominator (they would otherwise read as a quality drop) and out of the classifier's
# work queue entirely, so they cost no gateway budget.
#
# Paths verified on 2026-08-25 against the deliver app definitions of 1487 (PIPN V3),
# 1790 (BERI V3) and 1236 (EHA). 1487 and 1790 carry all three. EHA carries ONLY the
# weight photo: searching its whole app for "wrap" or "equipment" returns nothing, so
# those questions are absent from the app rather than merely unused. The dashboard asks
# each opportunity what it actually has (the live image-questions endpoint) instead of
# trusting this note, so an opportunity that lacks a type is shown as lacking it.
#
# Only the FOLLOW-UP visit photos are listed. The registration form carries its own
# weight photo at child_details/upload_weight_image and its own wrap photo, and neither
# is audited here -- the same deliberate, flagged omission the weight audit already
# made, kept unchanged so adding photo types does not quietly widen the audit's scope.
IMAGE_TYPES = [
    {
        "key": "weight",
        "label": "Weight photo",
        "path": WEIGHT_IMAGE_PATH,
        "field_path": WEIGHT_FIELD_PATH,
        "field_label": READING_LABEL,
        "scoreable": True,
        "help": "Checked by the scale classifier against the typed reading.",
    },
    {
        "key": "equipment",
        "label": "Equipment photo",
        "path": "danger_signs_checklist/equipment_image_capture_checklist/equipments_image_capture",
        "field_path": "",
        "field_label": "",
        "scoreable": False,
        "help": "Taken before danger-sign screening. No classifier exists - human review only.",
    },
    {
        "key": "wrap",
        "label": "KMC wrap photo",
        "path": "commodities_delivered/kmc_wrap_provided_image",
        "field_path": "",
        "field_label": "",
        "scoreable": False,
        "help": "The wrap handed over at the visit. No classifier exists - human review only.",
    },
]

# {path: label}, the shape a multi_str schedule option's choices_from_config expects.
IMAGE_TYPE_NAMES = {t["path"]: t["label"] for t in IMAGE_TYPES}

# What an audit covers when nothing says otherwise: the weight photo alone, which is
# exactly what this workflow audited before it could audit anything else.
DEFAULT_IMAGE_PATHS = [WEIGHT_IMAGE_PATH]

# The ONLY form that carries WEIGHT_IMAGE_PATH, and therefore the only form worth selecting
# from when capping photos per worker.
#
# This constant exists because visit SELECTION cannot filter by image. related_fields /
# filter_by_image is applied at image EXTRACTION time; the selection layer
# (filter_visits_for_audit) accepts no such parameter and silently drops the key. Capping raw
# selected visits therefore caps visits of every type: on opp 1487, 80 of 200 sampled visits
# are registration forms carrying no photo at this path at all (40%), so a cap of 20 could
# yield anywhere from 20 photos down to zero depending on submission order.
#
# deliver_unit_types IS honoured by the selection backend — it matches form.@name and is
# pushed into SQL — which makes it the cheap, window-size-independent way to make the cap
# mean photos rather than visits.
#
# Verified against the deliver app definition of every scheduled opportunity — 1236 (EHA),
# 1488 (NAMA), 1739 (Kikapu), 1790 (BERI) — plus observed form data for 1487 (PIPN): the name
# is identical in all five, and in each app the audited path appears in this form and nowhere
# else. The registration forms do declare an upload_weight_image, but at
# child_details/upload_weight_image — a different path, not audited here.
PHOTO_FORM_NAMES = ["Record Visit Details"]

# Stands in for "no cap" when a dry run needs a selection purely to count what an armed
# run would produce. Far above any real per-worker photo count (the busiest worker on the
# largest opportunity has ~380 across four months), so it never truncates in practice.
UNCAPPED = 1_000_000

AGENT_FOR_SCALE = {DIGITAL: "scale_validation", DIAL: "scale_dial_read"}

# Display labels for the opportunities, keyed by id as a STRING because this lands in
# JSON config. Matches the shape the schedule dialog reads via choices_from_config, and
# the labels the live workflow already used ("NAMA (V0/V1)").
OPP_NAMES = {
    str(opp_id): meta.get("display_name") or f"{meta['llo']} ({meta['version']})" for opp_id, meta in OPP_META.items()
}

DEFINITION = {
    "name": "KMC Image Audit",
    "description": (
        "Scale-photo audit across KMC's LLOs with the AI agent chosen per opportunity by scale "
        "hardware — digital LLOs scored by Scale [Digital], analog-dial LLOs by Scale [Dial]. "
        "Audits the follow-up weight photo against the typed reading, per FLW, across every "
        "selected opportunity in one run."
    ),
    "version": 1,
    "templateType": "kmc_image_audit",
    "statuses": [
        {"id": "config", "label": "Configuring", "color": "gray"},
        {"id": "creating", "label": "Creating Audits", "color": "blue"},
        {"id": "created", "label": "Audits Created", "color": "green"},
        {"id": "failed", "label": "Failed", "color": "red"},
    ],
    "config": {
        "multi_opp": True,
        "showSummaryCards": True,
        "opp_meta": OPP_META,
        # Derived from OPP_META rather than typed out, so the two cannot disagree. The
        # live workflow 13234 has had this key by hand for a while; the TEMPLATE did not,
        # which meant a workflow newly created from it offered no opportunities in the
        # schedule dialog and so could never have a schedule saved at all.
        "opp_names": OPP_NAMES,
        "weight_image_path": WEIGHT_IMAGE_PATH,
        "weight_field_path": WEIGHT_FIELD_PATH,
        # Overridable so a form rename upstream can be absorbed with a definition patch
        # instead of a release. See PHOTO_FORM_NAMES for why the cap needs it.
        "photo_form_names": PHOTO_FORM_NAMES,
        # The photo types this workflow can audit, and their {path: label} map for the
        # schedule dialog's choices. Both derived from IMAGE_TYPES so a type added there
        # reaches the dashboard picker and the schedule dialog together. On config rather
        # than baked into the code so a path renamed upstream can be absorbed with a
        # definition patch instead of a release.
        "image_types": IMAGE_TYPES,
        "image_type_names": IMAGE_TYPE_NAMES,
        "agent_for_scale": AGENT_FOR_SCALE,
    },
    "pipeline_sources": [],
}

RENDER_CODE = """function WorkflowUI({ definition, instance, actions, onUpdateState, view }) {
    // ── Pinned config ─────────────────────────────────────────────────────────
    const cfg = definition.config || {};
    const OPP_META = cfg.opp_meta || {};
    const WEIGHT_IMAGE_PATH = cfg.weight_image_path || 'anthropometric/upload_weight_image';
    const WEIGHT_FIELD_PATH = cfg.weight_field_path || 'child_weight_visit';
    // The only form carrying WEIGHT_IMAGE_PATH — verified against the deliver app of every
    // scheduled opportunity (1236/1488/1739/1790) plus observed data for 1487; the name is
    // identical in all five. Registration forms declare an upload_weight_image too, but at
    // child_details/upload_weight_image, which is a different path and is not audited.
    // Used to make the per-worker cap count photos rather than visits (see previewFlwVisits).
    const PHOTO_FORM_NAMES = cfg.photo_form_names || ['Record Visit Details'];
    // -- Photo types ----------------------------------------------------------
    // Declared ONCE on config (from IMAGE_TYPES in the template) and read here, by the
    // schedule dialog and by the headless runner, so all three offer the same set and a
    // type added in one place appears in all three.
    //
    // "scoreable" means a classifier exists for that photo, and only the weight photo has
    // one. Equipment and KMC-wrap photos are collected for a person to look at, which is
    // why they are held apart from the machine pass rate below rather than counted as
    // photos the AI failed to reach.
    const IMAGE_TYPES = (cfg.image_types && cfg.image_types.length) ? cfg.image_types : [{
        key: 'weight', label: 'Weight photo', path: WEIGHT_IMAGE_PATH,
        field_path: WEIGHT_FIELD_PATH, field_label: 'Scale Weight Reading', scoreable: true,
    }];
    const typeByPath = {};
    IMAGE_TYPES.forEach(t => { if (t && t.path) typeByPath[t.path] = t; });
    const SCOREABLE_PATHS = IMAGE_TYPES.filter(t => t && t.scoreable && t.path).map(t => t.path);
    const isScoreablePath = (p) => SCOREABLE_PATHS.indexOf(p) >= 0;
    // Falls back to the last path segment - exactly how the audit app labels a question it
    // was not told about - so an undeclared photo type still reads as something.
    const typeLabel = (p) => (typeByPath[p] && typeByPath[p].label)
        || String(p || '').split('/').pop() || 'Unknown';
    // A session's photos split by whether a classifier could ever have scored them.
    //
    // by_image_type comes from the sessions endpoint (AuditSessionRecord.to_summary_dict).
    // It is keyed by the IMAGE path - the same vocabulary as the picker, the audit rules
    // and the per-photo rows - which the assessment-keyed breakdown is NOT: an assessment
    // is filed under the comparison FIELD it was checked against, so a weight photo lands
    // under child_weight_visit there. Do not swap one for the other.
    //
    // When it is absent (a session created before it existed, or a server that predates
    // it) everything falls back to the whole image count, which is exactly the behaviour
    // this dashboard had when it only ever audited weight photos.
    const splitPhotos = (s) => {
        const byType = s && s.by_image_type;
        const imgs = (s && s.image_count) || 0;
        if (!byType || typeof byType !== 'object') {
            return { scoreable: imgs, humanOnly: 0, counts: null };
        }
        let scoreable = 0, humanOnly = 0;
        const counts = {};
        Object.keys(byType).forEach(path => {
            const n = (byType[path] && byType[path].total) || 0;
            counts[path] = n;
            if (isScoreablePath(path)) scoreable += n; else humanOnly += n;
        });
        return { scoreable: scoreable, humanOnly: humanOnly, counts: counts };
    };
    // "W 8 - E 3 - K 2" rather than a column per type: the point is to see at a glance
    // what a worker's photos are, and three sparse columns cost more width than they buy.
    const typeInitial = (path) => {
        const t = typeByPath[path];
        const word = (t && t.label) || String(path || '').split('/').pop() || '?';
        return word.replace(/^KMC /, '').charAt(0).toUpperCase();
    };
    const AGENT_FOR_SCALE = cfg.agent_for_scale || { digital: 'scale_validation', dial: 'scale_dial_read' };
    // Sessions created by a scale-hardware probe carry this tag so they can be told apart from real
    // audit sessions and excluded from every figure the run reports.
    const SCALE_CHECK_TAG = 'kmc_scale_check';
    const SCALE_CHECK_PHOTOS = 8;

    const allOppIds = Object.keys(OPP_META).map(s => parseInt(s, 10)).sort((a, b) => a - b);
    const meta = (id) => OPP_META[String(id)] || {};
    const oppLabel = (id) => {
        const m = meta(id);
        return m.llo ? (m.llo + ' ' + (m.version || '') + ' · ' + (m.country || '')) : ('Opportunity ' + id);
    };
    const scaleOf = (id) => meta(id).scale || 'digital';
    const agentFor = (id) => AGENT_FOR_SCALE[scaleOf(id)] || 'scale_validation';

    // ── Selection + window ────────────────────────────────────────────────────
    const runState = (view && view.state) || instance.state || {};
    const [selected, setSelected] = React.useState(runState.selected_opps || []);
    const [datePreset, setDatePreset] = React.useState(runState.date_preset || 'last_week');
    const [startDate, setStartDate] = React.useState(runState.window_start || '');
    const [endDate, setEndDate] = React.useState(runState.window_end || '');
    // Volume control. sample_percentage is the ONLY lever this backend honours for a
    // date_range audit: count_per_flw applies solely to last_n_per_flw, and there is no
    // max_flws in AuditCriteria at all (passing one is silently ignored). Sampling is per
    // FLW, so every worker keeps representation rather than the busiest ones crowding out
    // the rest -- see filter_visits_for_audit.
    const [samplePct, setSamplePct] = React.useState(runState.sample_percentage != null ? runState.sample_percentage : 100);
    // Which photo types to audit. Defaults to the weight photo alone, which is what this
    // workflow audited before it could audit anything else, so an existing run reopens
    // unchanged.
    const [imageTypes, setImageTypes] = React.useState(
        (runState.image_paths && runState.image_paths.length) ? runState.image_paths : [WEIGHT_IMAGE_PATH]);
    // What photo types an opportunity ACTUALLY has, asked of the opportunity itself
    // rather than assumed from a list written down here. The image-questions endpoint
    // samples recent visits and reports the image questions it finds, so this is the app's
    // own answer and it stays right when an app changes.
    //
    // Fetched on demand and cached per opportunity, never automatically: that endpoint
    // streams visit data to sample it, so firing it for every opportunity on every
    // selection change would be an expensive request storm for a line of reassurance.
    const [typeAvail, setTypeAvail] = React.useState({});
    const loadTypeAvail = (oppId) => {
        if (typeAvail[oppId]) return;
        setTypeAvail(prev => Object.assign({}, prev, { [oppId]: { loading: true } }));
        fetch('/audit/api/opportunity/' + oppId + '/image-questions/')
            .then(r => r.json())
            .then(d => {
                // {id, label, path} per question; id and path are both the image path.
                const paths = Array.isArray(d) ? d.map(q => q && (q.path || q.id)).filter(Boolean) : [];
                setTypeAvail(prev => Object.assign({}, prev, { [oppId]: { loading: false, paths: paths } }));
            })
            .catch(e => setTypeAvail(prev => Object.assign({}, prev,
                { [oppId]: { loading: false, error: String((e && e.message) || e) } })));
    };
    const checkTypeAvail = () => (selected || []).forEach(loadTypeAvail);
    const availAnswered = (selected || []).filter(id => typeAvail[id] && typeAvail[id].paths);
    const availLoading = (selected || []).some(id => typeAvail[id] && typeAvail[id].loading);
    // Selected opportunities whose app has no such question. Only ever computed from
    // opportunities that actually answered, so "none missing" cannot be an artefact of
    // nobody having asked.
    const missingFor = (path) => availAnswered.filter(id => typeAvail[id].paths.indexOf(path) < 0);
    const toggleImageType = (path) => setImageTypes(prev => {
        const next = prev.indexOf(path) >= 0 ? prev.filter(p => p !== path) : prev.concat([path]);
        // Never leave nothing selected: an audit with no photo type selects no visits at
        // all and reports an empty window, which reads exactly like a quiet day.
        if (!next.length) return [WEIGHT_IMAGE_PATH];
        // Keep declaration order so the rules, the columns and the chips all agree.
        return IMAGE_TYPES.filter(t => t && t.path && next.indexOf(t.path) >= 0).map(t => t.path);
    });
    // Hard cap on photos per field worker. sample_percentage is proportional, so a worker with 200
    // visits still contributes ~8x one with 25 — the busiest crowd out the rest and the volume of a
    // run is unpredictable. A flat cap fixes both: 100 workers x 20 = 2,000 photos, whatever the
    // activity. Implemented via the preview endpoint (see previewFlwVisits) because AuditCriteria
    // cannot express "last N per worker WITHIN a date window" — last_n_per_flw ignores the window.
    const [maxPerFlw, setMaxPerFlw] = React.useState(runState.max_per_flw != null ? runState.max_per_flw : '');
    // Per-opp agent override. Defaults come from scale hardware; an override is kept per
    // opportunity so a mixed-hardware program (46 has both) stays correct.
    const [agentOverride, setAgentOverride] = React.useState(runState.agent_override || {});
    const effectiveAgent = (id) => agentOverride[String(id)] || agentFor(id);

    const calcRange = (preset) => {
        const today = new Date(); today.setHours(0, 0, 0, 0);
        let start, end;
        if (preset === 'last_week') {
            const dow = today.getDay();
            const thisSun = new Date(today); thisSun.setDate(today.getDate() - dow);
            end = new Date(thisSun); end.setDate(thisSun.getDate() - 1);
            start = new Date(thisSun); start.setDate(thisSun.getDate() - 7);
        } else if (preset === 'last_7_days' || preset === 'last_14_days' || preset === 'last_30_days') {
            const n = preset === 'last_7_days' ? 7 : (preset === 'last_14_days' ? 14 : 30);
            end = new Date(today); end.setDate(today.getDate() - 1);
            start = new Date(end); start.setDate(end.getDate() - (n - 1));
        } else { return null; }
        const iso = (d) => d.toISOString().split('T')[0];
        return { start: iso(start), end: iso(end) };
    };
    const applyPreset = (p) => {
        setDatePreset(p);
        if (p !== 'custom') { const r = calcRange(p); if (r) { setStartDate(r.start); setEndDate(r.end); } }
    };
    React.useEffect(() => { if (!startDate && !endDate) applyPreset('last_week'); }, []);

    // ── Agent registry (live, so a newly deployed agent appears without a code change) ──
    const [agents, setAgents] = React.useState([]);
    const [agentsError, setAgentsError] = React.useState(null);
    React.useEffect(() => {
        fetch('/audit/api/ai-agents/')
            .then(r => r.json())
            .then(d => setAgents((d && d.agents) || []))
            .catch(e => setAgentsError(String(e && e.message ? e.message : e)));
    }, []);
    const agentName = (id) => {
        const a = agents.find(x => x.agent_id === id);
        return a ? a.name : id;
    };
    const agentKnown = (id) => !agents.length || agents.some(x => x.agent_id === id);

    // ── Sessions, merged across every opportunity in the run ───────────────────
    // Sessions are stored scoped per opportunity, so a single unscoped fetch returns only
    // the home opp's — which is exactly how a working run comes to report "0 sessions".
    const [rawSessions, setRawSessions] = React.useState([]);
    const [loadingSessions, setLoadingSessions] = React.useState(true);
    // Scale-check probes create real audit sessions, but they are diagnostics rather than part of
    // the audit: a handful of photos deliberately scored by the agent we believe is WRONG, to see
    // whether it can read the scale at all. Keep them out of every roll-up — otherwise a probe
    // surfaces as a field worker with a catastrophic score and drags the run's figures with it.
    const sessions = React.useMemo(
        () => rawSessions.filter(s => s.tag !== SCALE_CHECK_TAG), [rawSessions]);
    const refreshSessions = () => {
        const ids = (selected.length ? selected : allOppIds);
        if (!instance.id || !ids.length) { setLoadingSessions(false); return Promise.resolve([]); }
        return Promise.all(ids.map(oid =>
            fetch('/audit/api/workflow/' + instance.id + '/sessions/?opportunity_id=' + oid)
                .then(r => r.json())
                .then(d => ((d && d.success && d.sessions)
                    // Keep which request produced it, for debugging, but never let it override
                    // the session own opportunity_id.
                    ? d.sessions.map(s => Object.assign({ _requested_opp: oid }, s))
                    : []))
                .catch(() => [])
        )).then(arrs => {
            const seen = {}; const all = [];
            arrs.forEach(list => list.forEach(s => { if (!seen[s.id]) { seen[s.id] = true; all.push(s); } }));
            setRawSessions(all); setLoadingSessions(false); return all;
        }).catch(() => { setLoadingSessions(false); return []; });
    };
    React.useEffect(() => { refreshSessions(); }, [instance.id]);

    // ── Run execution ─────────────────────────────────────────────────────────
    const [isRunning, setIsRunning] = React.useState(false);
    const [progress, setProgress] = React.useState(null);
    const [runError, setRunError] = React.useState(null);
    // One screen, one job. Showing the trigger controls next to the results of a finished run
    // invites re-firing by accident and makes it unclear whether what is on screen is a plan or an
    // outcome. Phase is derived rather than stored, so a page reload lands in the right place;
    // 'newRun' is the only manual override, for deliberately configuring another run.
    const [newRun, setNewRun] = React.useState(false);

    // ── Scheduling ────────────────────────────────────────────────────────────
    // There is no server-side scheduler a workflow template can call. Every path that creates an
    // audit needs the OAuth access token that only exists in a logged-in browser session —
    // start_job_api reads it from request.session['labs_oauth'], and run_workflow_job takes it as an
    // argument — so a Celery-beat job would have no credentials of its own. What we can do is arm
    // the run from this tab and fire it at a chosen time, which is enough to move the classifier
    // work into the quiet hours. That is the actual goal; it just costs an open tab, and the UI says
    // so plainly rather than implying a server-side guarantee we do not have.
    const [scheduleMode, setScheduleMode] = React.useState(runState.schedule_mode || 'now');
    const [scheduleTime, setScheduleTime] = React.useState(runState.schedule_time || '22:00');
    const [armedFor, setArmedFor] = React.useState(null);
    const [nowTs, setNowTs] = React.useState(Date.now());
    // Next local-clock occurrence of HH:MM — today if it is still ahead of us, otherwise tomorrow.
    const nextOccurrence = (hhmm) => {
        const bits = String(hhmm == null ? '' : hhmm).split(':');
        // Reject empty parts explicitly. Number('') is 0, so ':' would otherwise pass as a perfectly
        // valid midnight and arm a run at a time nobody chose.
        if (bits.length !== 2 || !bits[0].length || !bits[1].length) return null;
        const h = Number(bits[0]), m = Number(bits[1]);
        if (!isFinite(h) || !isFinite(m) || h !== Math.floor(h) || m !== Math.floor(m)) return null;
        if (!(h >= 0 && h <= 23 && m >= 0 && m <= 59)) return null;
        const d = new Date();
        d.setHours(h, m, 0, 0);
        if (d.getTime() <= Date.now()) d.setDate(d.getDate() + 1);
        return d.getTime();
    };
    const untilLabel = (ms) => {
        const s = Math.max(0, Math.round(ms / 1000));
        const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
        return h ? (h + 'h ' + m + 'm') : (m + 'm');
    };
    const phase = isRunning ? 'running'
        : (armedFor ? 'scheduled' : ((sessions.length && !newRun) ? 'results' : 'config'));

    // Per-session image detail, loaded on demand. The sessions list carries only counts, so the
    // reason a photo failed (the classifier's own message) has to come from the bulk-data endpoint.
    const [expanded, setExpanded] = React.useState(null);
    const [detail, setDetail] = React.useState({});
    const loadDetail = (sessionId) => {
        if (expanded === sessionId) { setExpanded(null); return; }
        setExpanded(sessionId);
        if (detail[sessionId]) return;
        setDetail(prev => Object.assign({}, prev, { [sessionId]: { loading: true } }));
        fetch('/audit/api/' + sessionId + '/bulk-data/')
            .then(r => r.json())
            .then(d => {
                const rows = (d && (d.assessments || (d.data && d.data.assessments))) || [];
                setDetail(prev => Object.assign({}, prev, { [sessionId]: { loading: false, rows: rows } }));
            })
            .catch(e => setDetail(prev => Object.assign({}, prev,
                { [sessionId]: { loading: false, error: String(e && e.message ? e.message : e) } })));
    };
    const cleanupsRef = React.useRef([]);
    React.useEffect(() => () => { cleanupsRef.current.forEach(c => { try { if (c) c(); } catch (e) {} }); }, []);

    // Poll while a run is in flight so the live panel shows OUR counts. The server's progress
    // string reports "(N passed, M failed)" and omits errors entirely — on run 13575 it read
    // "110/140 images (1 passed, 0 failed)" while 109 of those 110 had errored. Read literally,
    // that line says a healthy run is in progress.
    React.useEffect(() => {
        if (!isRunning) return undefined;
        const t = setInterval(() => { refreshSessions(); }, 20000);
        return () => clearInterval(t);
    }, [isRunning]);

    const toggleOpp = (id) => setSelected(prev =>
        prev.indexOf(id) >= 0 ? prev.filter(x => x !== id) : prev.concat([id]).sort((a, b) => a - b));
    const selectAll = () => setSelected(allOppIds.slice());
    const selectNone = () => setSelected([]);
    const selectScale = (kind) => setSelected(allOppIds.filter(id => scaleOf(id) === kind));

    const buildCriteria = (oppId) => {
        // One filter_by_image rule per chosen photo type. Several such rules are OR-ed by
        // the selection layer: a visit is kept if it carries ANY of the chosen types, and
        // only images of those types are kept - which is what lets one audit cover weight
        // plus equipment plus wrap without also dragging in the immunization card and
        // house photos those same visits carry.
        const relatedFields = imageTypes.map(p => ({
            image_path: p,
            field_path: (typeByPath[p] && typeByPath[p].field_path) || '',
            label: (typeByPath[p] && typeByPath[p].field_label) || '',
            filter_by_image: true,
        }));
        return {
            audit_type: 'date_range',
            granularity: 'per_flw',
            start_date: startDate,
            end_date: endDate,
            sample_percentage: Number(samplePct) > 0 ? Number(samplePct) : 100,
            related_fields: relatedFields,
            title: 'KMC Image Audit · ' + oppLabel(oppId) + ' · ' + startDate + ' → ' + endDate,
            // Stamped onto every session the backend creates, so these are findable later
            // without inferring intent from the title string.
            tag: 'kmc_weight_photo',
        };
    };

    // Ask the server which visits an opportunity has in the window, then decide ourselves which of
    // them to audit. The preview returns each worker with their visit ids, so capping per worker is
    // a client-side trim — and passing the resulting ids back as flw_visit_ids also lets the
    // creation task skip its own visit-fetch stage entirely.
    const csrfToken = () => (
        (document.getElementById('workflow-root') && document.getElementById('workflow-root').dataset
            && document.getElementById('workflow-root').dataset.csrfToken)
        || (document.querySelector('[name=csrfmiddlewaretoken]') && document.querySelector('[name=csrfmiddlewaretoken]').value)
        || ''
    );
    const previewFlwVisits = async (oppId) => {
        const res = await fetch('/audit/api/audit/preview/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
            body: JSON.stringify({
                opportunities: [oppId],
                criteria: {
                    audit_type: 'date_range',
                    startDate: startDate,
                    endDate: endDate,
                    // Narrow to the ONE form that carries WEIGHT_IMAGE_PATH, or the cap slices
                    // blind: a worker whose photo visits fall outside the first N silently
                    // yields fewer photos than the cap, or none. On opp 1487, 40% of visits are
                    // registration forms with no photo at that path at all.
                    //
                    // It must be deliver_unit_types, NOT related_fields. The preview endpoint
                    // drops related_fields when it normalises criteria, and the selection layer
                    // (filter_visits_for_audit) has no such parameter in the first place --
                    // filter_by_image is applied later, at image extraction. deliver_unit_types
                    // matches form.@name and IS pushed into SQL, so it actually filters here.
                    deliver_unit_types: PHOTO_FORM_NAMES,
                    sample_percentage: Number(samplePct) > 0 ? Number(samplePct) : 100,
                },
            }),
        });
        const data = await res.json();
        if (!res.ok || !data || !data.success || !data.preview) {
            throw new Error((data && data.error) || ('preview failed (HTTP ' + res.status + ')'));
        }
        return data.preview.flws || [];
    };

    const handleCreate = async () => {
        if (!selected.length || !startDate || !endDate || isRunning) return;
        setIsRunning(true); setRunError(null);
        setProgress({ status: 'starting', message: 'Submitting ' + selected.length + ' audit(s)…' });

        await onUpdateState({
            selected_opps: selected, window_start: startDate, window_end: endDate,
            date_preset: datePreset, sample_percentage: Number(samplePct),
            max_per_flw: maxPerFlw === '' ? null : Number(maxPerFlw),
            agent_override: agentOverride,
            // Without this a run that audited three photo types is indistinguishable in
            // the history from a weight-only one, and its lower machine-reviewed share
            // looks like a classifier failure rather than the extra photos it was.
            image_paths: imageTypes,
        });

        let done = 0, failed = 0;
        const total = selected.length;
        const cleanups = [];
        const onOne = async () => {
            done += 1;
            setProgress({ status: 'running', message: 'Finished ' + done + ' of ' + total + ' opportunities', processed: done, total: total });
            if (done >= total) {
                await refreshSessions();
                setIsRunning(false);
                setProgress({ status: 'completed', message: 'Created audits for ' + (total - failed) + ' of ' + total + ' opportunities' });
            }
        };

        try {
            for (let k = 0; k < selected.length; k++) {
                const oid = selected[k];
                // One single-opportunity audit per opp. Multi-opp routing is deliberately
                // not used — it produced zero sessions for non-home opportunities.
                const criteria = buildCriteria(oid);
                let flwVisitIds = null;
                const cap = maxPerFlw === '' ? 0 : Number(maxPerFlw);
                if (cap > 0) {
                    try {
                        setProgress({ status: 'running', message: 'Selecting photos for ' + oppLabel(oid) + '…', processed: done, total: total });
                        const flws = await previewFlwVisits(oid);
                        flwVisitIds = {};
                        const chosen = [];
                        flws.forEach(f => {
                            const ids = (f.visit_ids || []).slice(0, cap);
                            if (ids.length) { flwVisitIds[f.username] = ids; chosen.push(f.username); }
                        });
                        criteria.selected_flw_user_ids = chosen;
                    } catch (e) {
                        // A preview failure must not silently become an uncapped run — that is how a
                        // 2,000-photo batch turns into 20,000. Skip this opportunity and say so.
                        failed += 1;
                        setRunError('Could not select photos for ' + oppLabel(oid) + ' (' + (e.message || e) + ') — skipped rather than running it uncapped.');
                        await onOne();
                        continue;
                    }
                }
                // One photo type, and it is the scoreable one: the single-agent call this
                // workflow has always made, left exactly as it was. It is also the only
                // shape that names the classifier on the run summary - the per-image-type
                // path reports "per-image-type" instead.
                //
                // Anything else MUST go down the per-image-type path, because a single
                // ai_agent_id is applied to every image in the session: the scale
                // classifier would be handed equipment and wrap photos and would report
                // each one as a mismatch - false flags at full gateway cost. Giving only
                // the scoreable type a reviewer makes the review task skip the others,
                // which is what leaves them for a person.
                const weightOnly = imageTypes.length === 1 && isScoreablePath(imageTypes[0]);
                const reviewArgs = weightOnly
                    ? { ai_agent_id: effectiveAgent(oid) }
                    : { image_audits: imageTypes.map(p => ({
                        image_path: p,
                        reviewers: isScoreablePath(p) ? [{
                            agent_id: effectiveAgent(oid),
                            config: (typeByPath[p] && typeByPath[p].field_path) ? {
                                comparison_field: typeByPath[p].field_path,
                                label: (typeByPath[p] && typeByPath[p].field_label) || '',
                            } : {},
                        }] : [],
                    })) };
                const res = await actions.createAudit(Object.assign({
                    opportunities: [{ id: oid, name: oppLabel(oid) }],
                    criteria: criteria,
                    flw_visit_ids: flwVisitIds || undefined,
                    workflow_run_id: instance.id,
                }, reviewArgs));
                if (!(res && res.success && res.task_id)) { failed += 1; await onOne(); continue; }
                setProgress({ status: 'running', message: 'Auditing ' + oppLabel(oid) + ' (' + (k + 1) + ' of ' + total + ')…', processed: done, total: total });

                // Wait for THIS opportunity before starting the next. Queuing them all at once
                // multiplies into (audits × ~10 images in flight) concurrent classifier calls,
                // and the scale models collapse above ~20: platform-side measurement put 5
                // parallel audits at 77 usable classifications/hour against 644 for one at a
                // time. Past the gateway's capacity every extra parallel audit is negative work
                // — it holds a 60s slot and returns nothing. Our own runs agree: three audits
                // fired within six minutes on 13 Aug failed 92–98%, while the same workflow run
                // one opportunity at a time on 14 Aug failed 0–20%.
                await new Promise((resolve) => {
                    let settled = false;
                    const finish = () => { if (!settled) { settled = true; resolve(); } };
                    // Safety net: if the progress stream drops we would otherwise wait forever
                    // and never start the remaining opportunities.
                    const guard = setTimeout(finish, 45 * 60 * 1000);
                    const cleanup = actions.streamAuditProgress(
                        res.task_id,
                        (p) => setProgress({
                            status: 'running',
                            message: (p && p.message) || ('Auditing ' + oppLabel(oid)),
                            processed: done, total: total,
                        }),
                        async () => { clearTimeout(guard); await onOne(); finish(); },
                        async () => { clearTimeout(guard); failed += 1; await onOne(); finish(); }
                    );
                    cleanups.push(cleanup);
                });
            }
            cleanupsRef.current = cleanups;
        } catch (err) {
            setIsRunning(false);
            setRunError(String(err && err.message ? err.message : err));
            setProgress({ status: 'failed', error: String(err && err.message ? err.message : err) });
        }
    };

    // Fire the armed run when its time arrives. handleCreate is held in a ref because the timer is
    // installed once per armed window and must call the CURRENT one, not the closure captured when
    // the schedule was set — otherwise a config change made while waiting would be silently ignored.
    const handleCreateRef = React.useRef(handleCreate);
    handleCreateRef.current = handleCreate;
    React.useEffect(() => {
        if (!armedFor || isRunning) return undefined;
        const tick = setInterval(() => {
            const now = Date.now();
            setNowTs(now);
            if (now >= armedFor) { setArmedFor(null); handleCreateRef.current(); }
        }, 15000);
        return () => clearInterval(tick);
    }, [armedFor, isRunning]);

    // A daily schedule re-arms for the next day once the run it triggered has finished. Watching the
    // running -> idle edge rather than re-arming inside handleCreate means the repeat also survives a
    // run the user kicked off by hand, and cannot double-arm while one is still in flight.
    const wasRunning = React.useRef(false);
    React.useEffect(() => {
        if (wasRunning.current && !isRunning && scheduleMode === 'daily' && !armedFor) {
            setArmedFor(nextOccurrence(scheduleTime));
        }
        wasRunning.current = isRunning;
    }, [isRunning, scheduleMode, scheduleTime, armedFor]);

    // ── Roll the session records up for display ───────────────────────────────
    // IMPORTANT: /audit/api/workflow/<id>/sessions/ returns to_summary_dict() — id, title, status,
    // opportunity_id, flw_username/flw_display_name, visit_count and assessment_stats. It does NOT
    // return visit_images or visit_results. An earlier version of this rollup walked those two and
    // consequently rendered 0 for every metric except the FLW count, on a run that had 858 images
    // and 845 verdicts sitting in the records. Read assessment_stats; do not reach for the raw
    // session data here.
    //
    // Substitutions, both verified against live run 13250:
    //   visit_count == image count            858 == 858 (this audit takes one weight photo per visit)
    //   visit_count - assessment_stats.total  == the images the AI never reached (13, exact)
    const rollup = React.useMemo(() => {
        const byOpp = {}; const byLlo = {};
        const T = { flws: 0, images: 0, scoreableImages: 0, humanOnlyImages: 0, assessed: 0,
            match: 0, noMatch: 0, error: 0, aiPending: 0,
            humanPass: 0, humanFail: 0, humanPending: 0, notReviewed: 0, unstarted: 0 };
        sessions.forEach(s => {
            const oid = s.opportunity_id || s._requested_opp;
            const m = meta(oid);
            const o = byOpp[oid] || (byOpp[oid] = { opp: oid, llo: m.llo || '?', scale: m.scale || '?',
                sessions: 0, images: 0, scoreableImages: 0, humanOnlyImages: 0, assessed: 0,
                match: 0, noMatch: 0, error: 0, aiPending: 0,
                humanPass: 0, humanFail: 0, humanPending: 0, notReviewed: 0, unstarted: 0 });
            const st = s.assessment_stats || {};
            const imgs = s.image_count || 0;
            const assessed = st.total || 0;
            // The gap is measured against the photos a classifier could score, NOT every
            // photo. Measured against every photo, an audit that also collected equipment
            // and wrap photos reports each of them as one the AI "never reached" - a red
            // count that reads as a classifier failure when it is work waiting for a
            // person, and the reason this split exists at all.
            const split = splitPhotos(s);
            const gap = Math.max(0, split.scoreable - assessed);
            o.sessions += 1; T.flws += 1;
            o.images += imgs; T.images += imgs;
            o.scoreableImages += split.scoreable; T.scoreableImages += split.scoreable;
            o.humanOnlyImages += split.humanOnly; T.humanOnlyImages += split.humanOnly;
            o.assessed += assessed; T.assessed += assessed;
            o.match += st.ai_match || 0; T.match += st.ai_match || 0;
            o.noMatch += st.ai_no_match || 0; T.noMatch += st.ai_no_match || 0;
            o.error += st.ai_error || 0; T.error += st.ai_error || 0;
            o.aiPending += st.ai_pending || 0; T.aiPending += st.ai_pending || 0;
            o.humanPass += st.pass || 0; T.humanPass += st.pass || 0;
            o.humanFail += st.fail || 0; T.humanFail += st.fail || 0;
            o.humanPending += st.pending || 0; T.humanPending += st.pending || 0;
            o.notReviewed += gap; T.notReviewed += gap;
            // "Nothing was scored here" is about the photos that COULD be scored: a
            // session of wrap photos alone has no classifier work to start, so counting it
            // as an unstarted audit would be a false alarm on every such run.
            if (split.scoreable > 0 && assessed === 0) { o.unstarted += 1; T.unstarted += 1; }
        });
        Object.keys(byOpp).forEach(k => {
            const o = byOpp[k];
            const l = byLlo[o.llo] || (byLlo[o.llo] = { llo: o.llo, scale: o.scale, opps: 0, sessions: 0,
                images: 0, scoreableImages: 0, humanOnlyImages: 0, assessed: 0, match: 0, noMatch: 0,
                error: 0, notReviewed: 0, unstarted: 0 });
            l.opps += 1; l.sessions += o.sessions; l.images += o.images; l.assessed += o.assessed;
            l.scoreableImages += o.scoreableImages; l.humanOnlyImages += o.humanOnlyImages;
            l.match += o.match; l.noMatch += o.noMatch; l.error += o.error;
            l.notReviewed += o.notReviewed; l.unstarted += o.unstarted;
        });
        const scored = T.match + T.noMatch;
        const attempts = scored + T.error;
        return Object.assign({}, T, {
            byOpp: Object.keys(byOpp).map(k => byOpp[k]).sort((a, b) => a.llo.localeCompare(b.llo) || a.opp - b.opp),
            byLlo: Object.keys(byLlo).map(k => byLlo[k]).sort((a, b) => a.llo.localeCompare(b.llo)),
            scored: scored, reviewed: attempts,
            errorPct: attempts > 0 ? Math.round(100 * T.error / attempts) : 0,
            noMatchPct: scored > 0 ? Math.round(100 * T.noMatch / scored) : 0,
        });
    }, [sessions]);

    // ── One row per field worker, the unit people actually act on ─────────────
    // Human review status is deliberately separate from the AI verdict: a photo the AI matched is
    // still "pending" until a person signs it off, which is what "unless reviewed by a user the
    // status is pending" means. Conflating the two would let an unreviewed run look complete.
    // Opportunities that were selected for the run but produced no audit sessions at all. Without
    // these the results table silently drops them: a run over five opportunities that yields two
    // looks identical to a run over two. There is then no way to tell "no photos in this window"
    // from "audit creation failed here" without querying the backend by hand, which is exactly the
    // position this run left us in.
    const emptyOpps = React.useMemo(() => {
        const seen = {};
        rollup.byOpp.forEach(o => { seen[o.opp] = true; });
        return (selected || []).filter(id => !seen[id]);
    }, [rollup, selected]);

    const flwRows = React.useMemo(() => {
        return sessions.map(s => {
            const st = s.assessment_stats || {};
            const oid = s.opportunity_id || s._requested_opp;
            const photos = s.image_count || 0;
            const assessed = st.total || 0;
            const split = splitPhotos(s);
            const pass = st.pass || 0;
            const fail = st.fail || 0;
            const pending = st.pending || 0;
            const humanDone = pass + fail;
            return {
                id: s.id, opp: oid, llo: (meta(oid).llo || '?'),
                flw: s.flw_display_name || s.flw_username || ('Session ' + s.id),
                username: s.flw_username || '',
                photos: photos, assessed: assessed,
                scoreablePhotos: split.scoreable, humanOnlyPhotos: split.humanOnly,
                typeCounts: split.counts,
                match: st.ai_match || 0, noMatch: st.ai_no_match || 0, error: st.ai_error || 0,
                pass: pass, fail: fail, pending: pending, humanDone: humanDone,
                // Against the scoreable photos only - see the rollup for why.
                neverReviewed: Math.max(0, split.scoreable - assessed),
                pctPassed: humanDone > 0 ? Math.round(100 * pass / humanDone) : null,
                reviewStatus: humanDone === 0 ? 'Pending' : (pending > 0 ? 'In review' : 'Reviewed'),
                sessionStatus: s.status || 'open',
            };
        }).sort((a, b) => (b.neverReviewed - a.neverReviewed) || (b.noMatch - a.noMatch)
            || (b.error - a.error) || String(a.flw).localeCompare(String(b.flw)));
    }, [sessions]);

    // Filtering the worker table. A run across eleven opportunities produces hundreds of rows and
    // the thing people actually do is "show me one LLO" or "find this person", so both are offered.
    // visibleRows — not flwRows — feeds the table AND its header count: a filtered table above an
    // unfiltered total is the exact chip-vs-rows mismatch that made an earlier dashboard untrustworthy.
    // Per-photo filter for the expanded detail. The platform's review page filters on the HUMAN
    // result only, so with every photo still pending there is no way to isolate what the AI
    // actually said — which is the question people are trying to answer. Filter on both.
    // Default to ALL photos, not just the flagged ones. Defaulting to 'flagged' meant a
    // worker whose photos all matched expanded to an empty list - indistinguishable from
    // 'nothing was reviewed'. The verdict on a passed photo is evidence too, and it is the
    // answer to 'did the AI actually look at this?'. The chips below still narrow to
    // Needs-a-look in one click.
    const [photoAi, setPhotoAi] = React.useState('');
    const [photoHuman, setPhotoHuman] = React.useState('');
    // Which photo TYPE to show. Empty means all of them. This is the filter a reviewer
    // wants when an audit covered more than one type: "just show me the wrap photos".
    // It reads x.question_id, which the bulk-data rows already carry - it is the image's
    // own path, the same key the picker and the audit rules use.
    const [photoType, setPhotoType] = React.useState('');
    const [lloFilter, setLloFilter] = React.useState('');
    const [flwSearch, setFlwSearch] = React.useState('');
    const lloOptions = React.useMemo(() => {
        const seen = [];
        flwRows.forEach(r => { if (seen.indexOf(r.llo) < 0) seen.push(r.llo); });
        return seen.sort();
    }, [flwRows]);
    const visibleRows = React.useMemo(() => {
        const q = flwSearch.trim().toLowerCase();
        return flwRows.filter(r =>
            (!lloFilter || r.llo === lloFilter)
            && (!q || String(r.flw).toLowerCase().indexOf(q) >= 0
                || String(r.username).toLowerCase().indexOf(q) >= 0));
    }, [flwRows, lloFilter, flwSearch]);

    // The workflow list's "FLWs" column renders run.selected_count, which reads state.flw_count
    // (workflow/data_access.py:134). Nothing ever wrote it, so every run in the history showed a
    // dash. Write it once the session list has settled rather than on every poll.
    // Also write a readable LLO label. The run-history table has no idea which LLOs a run covered —
    // every row just reads "Run #14192" — and it cannot work it out for itself: the opportunity
    // names live in this workflow's config, and a Django template cannot look up a dict by a
    // variable key without a custom filter. Writing the label here keeps the platform-side change
    // to a single column rendering a single string.
    const flwCountRef = React.useRef(null);
    React.useEffect(() => {
        if (isRunning || loadingSessions || !sessions.length) return;
        const llos = [];
        rollup.byOpp.forEach(o => { if (llos.indexOf(o.llo) < 0) llos.push(o.llo); });
        const label = llos.join(' · ');
        const stamp = sessions.length + '|' + label;
        if (flwCountRef.current === stamp) return;
        flwCountRef.current = stamp;
        onUpdateState({ flw_count: sessions.length, llo_summary: label });
    }, [sessions, rollup, isRunning, loadingSessions]);

    // Runs stayed "In Progress" in the history for ever because nothing called the complete
    // endpoint. Completion is a human judgement here — the AI pass finishing is not the same as the
    // audit being done — so it is a button, not an automatic consequence of the run ending.
    const [runCompleted, setRunCompleted] = React.useState(
        !!(instance && (instance.status === 'completed' || (instance.data || {}).status === 'completed')));
    const [completing, setCompleting] = React.useState(false);
    const markComplete = async () => {
        if (completing || runCompleted) return;
        setCompleting(true);
        try {
            const res = await actions.completeRun(instance.id, { overall_result: 'completed' });
            if (res && res.error) { setRunError('Could not mark complete: ' + res.error); }
            else { setRunCompleted(true); }
        } catch (e) {
            setRunError('Could not mark complete: ' + (e && e.message ? e.message : e));
        }
        setCompleting(false);
    };

    // ── Scale hardware check ──────────────────────────────────────────────────
    // Three opportunities ship with unverified: true and fall back to the digital agent. If that
    // guess is wrong every verdict they produce is worthless — and it fails silently, because a
    // wrong-hardware agent returns confident no-matches rather than errors. The probe runs the
    // agent we are NOT using over a few photos: a wrong-hardware agent scores near zero (the digital
    // agent on NAMA's dial photos previously returned 0 pass / 18 fail), so a real match rate from
    // the other agent is the tell. Only the non-default agent is run — half the classifier calls,
    // and less exposed to the timeouts that are currently costing digital two calls in three.
    // Classify the probe. Errors are excluded from the denominator on purpose: a timeout is the
    // service failing, not evidence about the hardware, and counting it as a non-match would let a
    // bad afternoon on the classifier silently "prove" the wrong answer. Too few real verdicts and
    // the honest result is 'inconclusive' rather than a guess dressed up as a finding.
    const scaleVerdict = (match, noMatch, errored, testedName, testedKind) => {
        const scored = match + noMatch;
        if (scored < 3) {
            return { verdict: 'inconclusive',
                detail: 'Only ' + scored + ' of ' + (scored + errored) + ' calls returned a verdict — the rest '
                    + 'failed. That is a service problem, not an answer about the hardware. Try again later.' };
        }
        if (match / scored >= 0.5) {
            return { verdict: testedKind,
                detail: testedName + ' read ' + match + ' of ' + scored + ' photos correctly. It can read this '
                    + 'scale, so this opportunity looks like ' + testedKind + ' — not what it is set to now.' };
        }
        if (match === 0) {
            return { verdict: 'keep',
                detail: 'Confirmed. ' + testedName + ' could not read any of the ' + scored + ' photos it '
                    + 'scored — which is exactly what we expect if the current setting is correct. Nothing '
                    + 'to change.' };
        }
        return { verdict: 'unclear',
            detail: testedName + ' read only ' + match + ' of ' + scored + '. Not a clean answer — widen the '
                + 'window for a bigger sample, or open a photo and look.' };
    };
    const [scaleMode, setScaleMode] = React.useState({});   // oppId -> 'dial' | 'digital' | 'test'
    const [scaleTest, setScaleTest] = React.useState({});   // oppId -> probe state/result
    const setMode = (id, mode) => {
        setScaleMode(prev => Object.assign({}, prev, { [String(id)]: mode }));
        if (mode === 'dial' || mode === 'digital') {
            setAgentOverride(prev => Object.assign({}, prev, { [String(id)]: AGENT_FOR_SCALE[mode] }));
        }
    };
    const modeOf = (id) => scaleMode[String(id)]
        || (effectiveAgent(id) === AGENT_FOR_SCALE.dial ? 'dial' : 'digital');

    const runScaleCheck = async (oid) => {
        const cur = effectiveAgent(oid);
        const other = cur === AGENT_FOR_SCALE.dial ? AGENT_FOR_SCALE.digital : AGENT_FOR_SCALE.dial;
        const otherKind = other === AGENT_FOR_SCALE.dial ? 'dial' : 'digital';
        if (!startDate || !endDate) {
            setScaleTest(p => Object.assign({}, p, { [oid]: { error: 'Set the window first.' } }));
            return;
        }
        setScaleTest(p => Object.assign({}, p, { [oid]: { running: true, tested: other, kind: otherKind } }));
        try {
            const flws = await previewFlwVisits(oid);
            const picked = {}; let count = 0;
            flws.forEach(f => {
                if (count >= SCALE_CHECK_PHOTOS) return;
                const ids = (f.visit_ids || []).slice(0, SCALE_CHECK_PHOTOS - count);
                if (ids.length) { picked[f.username] = ids; count += ids.length; }
            });
            if (!count) {
                setScaleTest(p => Object.assign({}, p, { [oid]: {
                    error: 'No weight photos for this opportunity between ' + startDate + ' and ' + endDate
                        + '. Widen the window and try again.' } }));
                return;
            }
            const criteria = buildCriteria(oid);
            criteria.title = 'Scale hardware check · ' + oppLabel(oid);
            criteria.tag = SCALE_CHECK_TAG;
            criteria.selected_flw_user_ids = Object.keys(picked);
            const res = await actions.createAudit({
                opportunities: [{ id: oid, name: oppLabel(oid) }],
                criteria: criteria,
                flw_visit_ids: picked,
                workflow_run_id: instance.id,
                ai_agent_id: other,
            });
            if (!(res && res.success && res.task_id)) throw new Error('could not start the check');
            setScaleTest(p => Object.assign({}, p, { [oid]: {
                running: true, tested: other, kind: otherKind, photos: count,
                message: 'Scoring ' + count + ' photos with ' + agentName(other) + '…' } }));
            await new Promise((resolve) => {
                let settled = false;
                const finish = () => { if (!settled) { settled = true; resolve(); } };
                const guard = setTimeout(finish, 15 * 60 * 1000);
                cleanupsRef.current.push(actions.streamAuditProgress(res.task_id,
                    () => {},
                    () => { clearTimeout(guard); finish(); },
                    () => { clearTimeout(guard); finish(); }));
            });
            const all = await refreshSessions();
            let match = 0, noMatch = 0, errored = 0, seen = 0;
            let probeSession = null;
            (all || []).forEach(s => {
                if (s.tag !== SCALE_CHECK_TAG) return;
                if ((s.opportunity_id || s._requested_opp) !== oid) return;
                if (probeSession == null) probeSession = s.id;
                const st = s.assessment_stats || {};
                seen += s.image_count || 0;
                match += st.ai_match || 0; noMatch += st.ai_no_match || 0; errored += st.ai_error || 0;
            });
            const v = scaleVerdict(match, noMatch, errored, agentName(other), otherKind);
            const verdict = v.verdict, detail = v.detail;
            setScaleTest(p => Object.assign({}, p, { [oid]: {
                tested: other, kind: otherKind, session: probeSession,
                photos: seen, scored: match + noMatch + errored,
                notReviewed: Math.max(0, seen - (match + noMatch + errored)),
                match: match, noMatch: noMatch,
                errored: errored, verdict: verdict, detail: detail } }));
        } catch (e) {
            setScaleTest(p => Object.assign({}, p, { [oid]: {
                error: String(e && e.message ? e.message : e) } }));
        }
    };

    const downloadCsv = () => {
        const head = ['FLW', 'Username', 'LLO', 'Opportunity', 'Opp ID', 'Scale', 'Agent', 'Photos',
            'AI reviewed', 'AI match', 'AI no match', 'AI errored', 'Never reviewed',
            'Human pass', 'Human fail', 'Human pending', '% passed', 'Review status'];
        const esc = (v) => {
            const s = (v == null ? '' : String(v));
            return /[",\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
        };
        // Exports what is on screen, filters included. An export that silently disagrees with the
        // table above it is worse than no export — you cannot tell which one is wrong.
        const lines = [head.join(',')].concat(visibleRows.map(r => [
            r.flw, r.username, r.llo, oppLabel(r.opp), r.opp, scaleOf(r.opp), agentName(effectiveAgent(r.opp)),
            r.photos, r.assessed, r.match, r.noMatch, r.error, r.neverReviewed,
            r.pass, r.fail, r.pending, (r.pctPassed == null ? '' : r.pctPassed), r.reviewStatus,
        ].map(esc).join(',')));
        // Leading BOM so Excel opens the file as UTF-8 rather than mangling non-ASCII worker names.
        const blob = new Blob(['﻿' + lines.join('\\r\\n')], { type: 'text/csv;charset=utf-8;' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'kmc-image-audit-run' + instance.id + '.csv';
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
    };

    // ── Warnings: things a reader would otherwise mis-conclude ────────────────
    const warnings = [];
    // Leads the list on purpose. An unfinished run is worse than a failed one: it looks like a
    // result. There is no resume — the AI pass walks the session list once, and if the worker
    // stops (deploy, restart) the remaining sessions are simply never reviewed and nothing
    // retries them. Re-running is the only fix, and it starts from zero.
    if (rollup.notReviewed > 0) {
        warnings.push({
            level: 'red',
            text: 'INCOMPLETE RUN — ' + rollup.notReviewed + ' of ' + rollup.images + ' images were never reviewed'
                + (rollup.unstarted ? (', including ' + rollup.unstarted + ' FLW audit' + (rollup.unstarted === 1 ? '' : 's') + ' with no review at all') : '')
                + '. The AI pass stopped before finishing; it does not resume. Re-run to complete.',
        });
    }
    if (rollup.reviewed > 0 && rollup.errorPct >= 20) {
        warnings.push({
            level: 'red',
            text: rollup.errorPct + '% of AI calls errored (' + rollup.error + ' of ' + rollup.reviewed + '). '
                + 'Errored images were NOT reviewed — treat this run as incomplete, not as a clean result.',
        });
    }
    if (rollup.aiPending > 0) {
        warnings.push({
            level: 'amber',
            text: rollup.aiPending + ' assessment' + (rollup.aiPending === 1 ? '' : 's') + ' exist with no AI verdict — '
                + 'the image was extracted and queued but the agent returned nothing for it.',
        });
    }
    const unverifiedSel = selected.filter(id => meta(id).unverified);
    if (unverifiedSel.length) {
        warnings.push({
            level: 'amber',
            text: 'Scale hardware is UNCONFIRMED for ' + unverifiedSel.map(oppLabel).join(', ')
                + '. They default to the digital agent — verify before trusting their verdicts.',
        });
    }
    const mismatched = selected.filter(id => effectiveAgent(id) !== agentFor(id));
    if (mismatched.length) {
        warnings.push({
            level: 'amber',
            text: 'Agent overridden against scale hardware for ' + mismatched.map(oppLabel).join(', ')
                + '. A digital agent reading an analog dial false-fails almost everything.',
        });
    }
    const unknownAgents = selected.map(effectiveAgent).filter(a => !agentKnown(a));
    if (unknownAgents.length) {
        warnings.push({
            level: 'red',
            text: 'Agent(s) not present in this environment: ' + Array.from(new Set(unknownAgents)).join(', ')
                + '. Those audits will produce no verdicts.',
        });
    }

    // ── Small presentational helpers ──────────────────────────────────────────
    const Card = ({ label, value, sub, tone }) => (
        <div className={'bg-white rounded-lg shadow-sm p-4 border-l-4 ' + (tone || 'border-gray-200')}>
            <div className="text-2xl font-bold text-gray-900">{value}</div>
            <div className="text-xs font-medium text-gray-600 mt-0.5">{label}</div>
            {sub ? <div className="text-xs text-gray-400 mt-0.5">{sub}</div> : null}
        </div>
    );
    const ScalePill = ({ kind, unverified }) => (
        <span className={'inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold '
            + (kind === 'dial' ? 'bg-purple-100 text-purple-800' : 'bg-sky-100 text-sky-800')}
            title={kind === 'dial' ? 'Analog dial scale — scored by Scale [Dial]' : 'Digital LCD scale — scored by Scale [Digital]'}>
            {kind === 'dial' ? 'Dial' : 'Digital'}{unverified ? ' ?' : ''}
        </span>
    );
    const num = (n) => (n == null ? '—' : String(n));
    const pct = (a, b) => (b > 0 ? Math.round(100 * a / b) + '%' : '—');

    const byCountry = {};
    allOppIds.forEach(id => {
        const c = meta(id).country || 'Other';
        (byCountry[c] || (byCountry[c] = [])).push(id);
    });

    const hasCreated = sessions.length > 0;

    // In config the coverage list is what you have picked; afterwards it is what the run actually
    // produced, with FLW and photo counts filled in. Same component, two truths, never mixed.
    const coverage = (phase === 'config')
        ? selected.map(id => ({ opp: id, llo: meta(id).llo || ('#' + id), flws: null, photos: null }))
        : rollup.byOpp.map(o => ({ opp: o.opp, llo: o.llo, flws: o.sessions, photos: o.images }))
            // Selected but produced nothing: shown here for the same reason the table below
            // shows them. A strip listing four LLOs above a table listing five is a header
            // contradicting its own body.
            .concat(emptyOpps.map(id => ({ opp: id, llo: meta(id).llo || ('#' + id), flws: 0, photos: 0 })));
    const winLabel = (startDate && endDate) ? (startDate + ' → ' + endDate) : 'no window set';

    return (
        <div className="space-y-5 pb-10">
            {/* Header */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                <p className="text-gray-600 mt-1 text-sm">{definition.description}</p>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <span className="px-2 py-1 rounded bg-gray-100 text-gray-700">
                        Audited photo · <span className="font-mono">{WEIGHT_IMAGE_PATH}</span>
                    </span>
                    <span className="px-2 py-1 rounded bg-gray-100 text-gray-700">
                        Reading · <span className="font-mono">{WEIGHT_FIELD_PATH}</span>
                    </span>
                    <span className="px-2 py-1 rounded bg-amber-50 text-amber-800"
                        title="Registration weight photos sit at child_details/upload_weight_image and are not covered by this audit.">
                        Registration photos not audited
                    </span>
                </div>

                {/* Which LLOs and opportunities this run covers — stated on every phase, so it is
                    never ambiguous what a screenful of numbers is about. */}
                <div className="mt-4 pt-4 border-t border-gray-100">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                        <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                            {phase === 'config' ? 'Selected for this run' : 'This run covers'}
                        </span>
                        <span className="text-xs text-gray-500">
                            {winLabel}
                            {Number(samplePct) < 100 ? ' · ' + samplePct + '% sample' : ' · census'}
                            {maxPerFlw !== '' ? ' · max ' + maxPerFlw + '/worker' : ''}
                        </span>
                    </div>
                    {coverage.length ? (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                            {coverage.map(c => (
                                <span key={c.opp} className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg border border-gray-200 bg-gray-50 text-xs">
                                    <span className="font-semibold text-gray-900">{c.llo}</span>
                                    <span className="text-gray-500">{meta(c.opp).version}</span>
                                    <span className="text-gray-400">{meta(c.opp).country}</span>
                                    <span className="text-gray-400 font-mono">#{c.opp}</span>
                                    <ScalePill kind={scaleOf(c.opp)} unverified={meta(c.opp).unverified} />
                                    {c.flws != null ? <span className="text-gray-500">{c.flws} FLW{c.flws === 1 ? '' : 's'}</span> : null}
                                    {c.photos != null ? <span className="text-gray-500">{c.photos} photos</span> : null}
                                </span>
                            ))}
                        </div>
                    ) : (
                        <div className="mt-2 text-xs text-gray-400">No opportunities selected yet.</div>
                    )}
                </div>
            </div>

            {/* Where we are — three steps, only one active at a time. */}
            <div className="bg-white rounded-lg shadow-sm px-4 py-3">
                <div className="flex items-center gap-2 flex-wrap text-sm">
                    {[['config', '1. Configure'], ['running', '2. Processing'], ['results', '3. Results']].map((st, i) => {
                        const active = phase === st[0];
                        const passedStep = ((phase === 'running' || phase === 'scheduled') && i === 0)
                            || (phase === 'results' && i < 2);
                        // A scheduled run has finished configuring but has not started, so step 2 is
                        // waiting rather than active — showing nothing active at all would read as broken.
                        const waiting = phase === 'scheduled' && st[0] === 'running';
                        return (
                            <span key={st[0]} className="flex items-center gap-2">
                                {i > 0 ? <i className="fa-solid fa-chevron-right text-gray-300 text-xs"></i> : null}
                                <span className={'px-3 py-1 rounded-full font-medium '
                                    + (active ? 'bg-blue-600 text-white'
                                        : waiting ? 'bg-amber-50 text-amber-800 border border-amber-200'
                                            : passedStep ? 'bg-green-50 text-green-700 border border-green-200'
                                                : 'bg-gray-100 text-gray-400')}>
                                    {passedStep ? <i className="fa-solid fa-check mr-1"></i> : null}
                                    {waiting ? <i className="fa-solid fa-clock mr-1"></i> : null}{st[1]}
                                </span>
                            </span>
                        );
                    })}
                    {phase === 'results' ? (
                        <span className="ml-auto flex items-center gap-2">
                            {runCompleted ? (
                                <span className="px-3 py-1.5 text-sm rounded-lg bg-green-50 text-green-800 border border-green-200 font-medium">
                                    <i className="fa-solid fa-check mr-1.5"></i>Run marked complete
                                </span>
                            ) : (
                                <button onClick={markComplete} disabled={completing}
                                    title="Closes this run in the workflow history. Do it once you have reviewed what you intend to review — the AI pass finishing is not the same as the audit being done."
                                    className="px-3 py-1.5 text-sm rounded-lg border border-green-300 text-green-800 hover:bg-green-50 disabled:opacity-50">
                                    <i className={'mr-1.5 fa-solid ' + (completing ? 'fa-spinner fa-spin' : 'fa-flag-checkered')}></i>
                                    {completing ? 'Marking…' : 'Mark run complete'}
                                </button>
                            )}
                            <button onClick={() => { setNewRun(true); setProgress(null); setRunError(null); }}
                                className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 hover:border-blue-400 hover:text-blue-700">
                                <i className="fa-solid fa-rotate-right mr-1.5"></i>Configure another run
                            </button>
                        </span>
                    ) : null}
                    {phase === 'config' && sessions.length ? (
                        <button onClick={() => setNewRun(false)}
                            className="ml-auto px-3 py-1.5 text-sm rounded-lg border border-gray-300 hover:border-blue-400 hover:text-blue-700">
                            <i className="fa-solid fa-list-check mr-1.5"></i>Back to results
                        </button>
                    ) : null}
                </div>
            </div>

            {/* Warnings */}
            {warnings.length ? (
                <div className="space-y-2">
                    {warnings.map((w, i) => (
                        <div key={i} className={'rounded-lg px-4 py-3 text-sm border '
                            + (w.level === 'red' ? 'bg-red-50 border-red-200 text-red-800' : 'bg-amber-50 border-amber-200 text-amber-800')}>
                            <i className={'mr-2 fa-solid ' + (w.level === 'red' ? 'fa-circle-exclamation' : 'fa-triangle-exclamation')}></i>
                            {w.text}
                        </div>
                    ))}
                </div>
            ) : null}

            {/* Summary */}
            {(phase !== 'config' && hasCreated) ? (
                <div>
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                        <Card label="FLW audits" value={num(rollup.flws)} sub={rollup.byOpp.length + ' opportunit' + (rollup.byOpp.length === 1 ? 'y' : 'ies')} tone="border-blue-400" />
                        <Card label="Photos" value={num(rollup.images)}
                            sub={rollup.humanOnlyImages
                                ? (num(rollup.scoreableImages) + ' weight \\u00b7 ' + num(rollup.humanOnlyImages) + ' for human review')
                                : 'one per audited visit'}
                            tone="border-gray-300" />
                        <Card label="AI scored" value={num(rollup.reviewed)}
                            sub={rollup.aiPending
                                ? (rollup.aiPending + ' awaiting a verdict')
                                : rollup.notReviewed
                                    ? (rollup.notReviewed + ' never reviewed')
                                    : (pct(rollup.reviewed, rollup.scoreableImages) + ' of weight photos')}
                            tone={rollup.notReviewed ? 'border-red-500' : 'border-indigo-400'} />
                        <Card label="Match" value={num(rollup.match)} sub={pct(rollup.match, rollup.scored) + ' of scored'} tone="border-green-500" />
                        <Card label="No match" value={num(rollup.noMatch)} sub={rollup.noMatchPct + '% of scored'} tone="border-amber-500" />
                        <Card label="Errored" value={num(rollup.error)} sub={rollup.errorPct + '% of attempts'} tone="border-red-500" />
                    </div>
                    <p className="text-xs text-gray-500 mt-2">
                        "Scored" = match + no-match, i.e. images the AI actually judged. Errors are not verdicts —
                        an errored image is unreviewed and still needs a human.
                        {rollup.humanOnlyImages ? (
                            <span> Match, no-match and errored are counted over <strong>weight photos only</strong>
                                {' '}(excludes the {num(rollup.humanOnlyImages)} equipment and KMC-wrap photos, which
                                {' '}no classifier can read and which are waiting on a person).</span>
                        ) : null}
                    </p>
                </div>
            ) : null}

            {/* Per-LLO rollup */}
            {(phase !== 'config' && hasCreated) ? (
                <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                    <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between flex-wrap gap-2">
                        <span className="text-sm font-semibold text-gray-800">Results by LLO</span>
                        <span className="text-xs text-gray-400">scale hardware decides which agent scored the photo</span>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="min-w-full text-sm">
                            <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                                <tr>
                                    {['LLO', 'Scale', 'Opps', 'FLWs', 'Photos', 'Reviewed', 'Match', 'No match', 'Errored', 'Never reviewed', 'Error %'].map((h, i) => (
                                        <th key={h} className={'px-3 py-2 font-medium whitespace-nowrap ' + (i < 2 ? 'text-left' : 'text-right')}>{h}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {rollup.byLlo.map(l => {
                                    const attempts = l.match + l.noMatch + l.error;
                                    const ep = attempts > 0 ? Math.round(100 * l.error / attempts) : 0;
                                    return (
                                        <tr key={l.llo} className="border-t border-gray-100">
                                            <td className="px-3 py-2 font-semibold text-gray-900 whitespace-nowrap">{l.llo}</td>
                                            <td className="px-3 py-2"><ScalePill kind={l.scale} /></td>
                                            <td className="px-3 py-2 text-right">{l.opps}</td>
                                            <td className="px-3 py-2 text-right">{l.sessions}</td>
                                            <td className="px-3 py-2 text-right">{l.images}</td>
                                            <td className="px-3 py-2 text-right">{l.assessed}</td>
                                            <td className="px-3 py-2 text-right text-green-700 font-semibold">{l.match}</td>
                                            <td className="px-3 py-2 text-right text-amber-700 font-semibold">{l.noMatch}</td>
                                            <td className="px-3 py-2 text-right text-red-700 font-semibold">{l.error}</td>
                                            <td className={'px-3 py-2 text-right font-semibold ' + (l.notReviewed ? 'text-red-700' : 'text-gray-300')}>{l.notReviewed || '—'}</td>
                                            <td className={'px-3 py-2 text-right font-semibold ' + (ep >= 20 ? 'text-red-700' : 'text-gray-600')}>{ep}%</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            ) : null}

            {/* Configuration — config phase only. Leaving these controls on screen beside a
                finished run's results is what made the page confusing and invited accidental
                re-fires. */}
            {phase === 'config' ? (
            <div className="bg-white rounded-lg shadow-sm p-6 space-y-5">
                <div>
                    <div className="flex items-center justify-between flex-wrap gap-2">
                        <h3 className="text-sm font-semibold text-gray-800">
                            <i className="fa-solid fa-building-user mr-2 text-gray-400"></i>Opportunities
                            <span className="ml-2 text-xs font-normal text-gray-500">{selected.length} of {allOppIds.length} selected</span>
                        </h3>
                        <div className="flex flex-wrap gap-1.5 text-xs">
                            <button onClick={selectAll} className="px-2 py-1 rounded border border-gray-300 hover:border-blue-400">All</button>
                            <button onClick={selectNone} className="px-2 py-1 rounded border border-gray-300 hover:border-blue-400">None</button>
                            <button onClick={() => selectScale('digital')} className="px-2 py-1 rounded border border-sky-300 text-sky-700 hover:bg-sky-50">Digital only</button>
                            <button onClick={() => selectScale('dial')} className="px-2 py-1 rounded border border-purple-300 text-purple-700 hover:bg-purple-50">Dial only</button>
                        </div>
                    </div>
                    <div className="mt-3 space-y-4">
                        {Object.keys(byCountry).sort().map(country => (
                            <div key={country}>
                                <div className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-1.5">{country}</div>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                    {byCountry[country].map(id => {
                                        const m = meta(id);
                                        const on = selected.indexOf(id) >= 0;
                                        return (
                                            <label key={id} className={'flex items-center gap-2 rounded-lg border px-3 py-2 cursor-pointer '
                                                + (on ? 'border-blue-400 bg-blue-50' : 'border-gray-200 hover:border-gray-300')}>
                                                <input type="checkbox" checked={on} onChange={() => toggleOpp(id)} />
                                                <span className="text-sm font-medium text-gray-900">{m.llo}</span>
                                                <span className="text-xs text-gray-500">{m.version}</span>
                                                <span className="text-xs text-gray-400 font-mono">#{id}</span>
                                                <span className="ml-auto flex items-center gap-1.5">
                                                    <ScalePill kind={m.scale} unverified={m.unverified} />
                                                </span>
                                            </label>
                                        );
                                    })}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Agent routing */}
                {selected.length ? (
                    <div className="border-t border-gray-100 pt-4">
                        <h3 className="text-sm font-semibold text-gray-800 mb-2">
                            <i className="fa-solid fa-robot mr-2 text-gray-400"></i>AI agent per opportunity
                            <span className="ml-2 text-xs font-normal text-gray-500">defaults follow scale hardware</span>
                        </h3>
                        {agentsError ? (
                            <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2 mb-2">
                                Could not load the agent registry ({agentsError}). Defaults still apply.
                            </div>
                        ) : null}
                        <div className="overflow-x-auto">
                            <table className="min-w-full text-sm">
                                <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                                    <tr>
                                        <th className="px-3 py-2 text-left font-medium">Opportunity</th>
                                        <th className="px-3 py-2 text-left font-medium">Scale</th>
                                        <th className="px-3 py-2 text-left font-medium">Agent</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {selected.map(id => {
                                        const t = scaleTest[id] || {};
                                        return [
                                        <tr key={id} className="border-t border-gray-100">
                                            <td className="px-3 py-2 whitespace-nowrap">{oppLabel(id)}</td>
                                            <td className="px-3 py-2">
                                                <select value={modeOf(id)} onChange={(e) => setMode(id, e.target.value)}
                                                    className={'border rounded-lg px-2 py-1 text-sm '
                                                        + (modeOf(id) === 'test' ? 'border-amber-400 text-amber-800' : 'border-gray-300')}>
                                                    <option value="dial">Dial (analog)</option>
                                                    <option value="digital">Digital</option>
                                                    <option value="test">Test &amp; confirm</option>
                                                </select>
                                                {meta(id).unverified ? (
                                                    <div className="text-xs text-amber-700 mt-1">
                                                        <i className="fa-solid fa-circle-question mr-1"></i>unconfirmed hardware
                                                    </div>
                                                ) : null}
                                            </td>
                                            <td className="px-3 py-2">
                                                <select
                                                    value={effectiveAgent(id)}
                                                    onChange={(e) => setAgentOverride(prev => Object.assign({}, prev, { [String(id)]: e.target.value }))}
                                                    className={'border rounded-lg px-2 py-1 text-sm '
                                                        + (effectiveAgent(id) === agentFor(id) ? 'border-gray-300' : 'border-amber-400 text-amber-800')}>
                                                    {(agents.length
                                                        ? agents.map(a => a.agent_id)
                                                        : [AGENT_FOR_SCALE.digital, AGENT_FOR_SCALE.dial]
                                                    ).map(aid => (
                                                        <option key={aid} value={aid}>{agentName(aid)}</option>
                                                    ))}
                                                </select>
                                            </td>
                                        </tr>,
                                        modeOf(id) === 'test' ? (
                                            <tr key={id + '-check'} className="bg-amber-50/50 border-t border-amber-100">
                                                <td colSpan={3} className="px-3 py-3">
                                                    <div className="text-xs text-gray-700 mb-2">
                                                        Runs <span className="font-medium">{agentName(
                                                            effectiveAgent(id) === AGENT_FOR_SCALE.dial
                                                                ? AGENT_FOR_SCALE.digital : AGENT_FOR_SCALE.dial)}</span> —
                                                        the agent this opportunity is <span className="font-medium">not</span> using —
                                                        over {SCALE_CHECK_PHOTOS} photos. If it can read the scale, the hardware is
                                                        set wrong. Creates a small audit tagged as a check; it is kept out of every
                                                        figure this run reports.
                                                    </div>
                                                    <button onClick={() => runScaleCheck(id)} disabled={!!t.running}
                                                        className="px-3 py-1.5 text-sm rounded-lg bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50">
                                                        <i className={'mr-1.5 fa-solid ' + (t.running ? 'fa-spinner fa-spin' : 'fa-flask')}></i>
                                                        {t.running ? (t.message || 'Checking…') : 'Run hardware check'}
                                                    </button>
                                                    {t.error ? (
                                                        <div className="text-xs text-red-700 mt-2">{t.error}</div>
                                                    ) : null}
                                                    {/* 'keep' is a PASS, not a failure — the other agent being
                                                        unable to read the scale is what confirms the setting. It was
                                                        previously styled the same as a problem and read like one.
                                                        Green, with a tick, and the headline says confirmed. */}
                                                    {t.verdict ? (
                                                        <div className={'mt-2 text-xs rounded p-2 border '
                                                            + (t.verdict === 'keep'
                                                                ? 'bg-green-50 border-green-300 text-green-900'
                                                                : t.verdict === 'dial' || t.verdict === 'digital'
                                                                    ? 'bg-amber-50 border-amber-400 text-amber-900'
                                                                    : 'bg-gray-50 border-gray-200 text-gray-700')}>
                                                            <div className="font-semibold mb-0.5">
                                                                {t.verdict === 'keep' ? (
                                                                    <span><i className="fa-solid fa-circle-check mr-1"></i>
                                                                        Confirmed — this opportunity is set correctly</span>
                                                                ) : t.verdict === 'dial' || t.verdict === 'digital' ? (
                                                                    <span><i className="fa-solid fa-triangle-exclamation mr-1"></i>
                                                                        Change needed — this looks like {t.verdict.toUpperCase()}</span>
                                                                ) : (
                                                                    <span><i className="fa-solid fa-circle-question mr-1"></i>
                                                                        {t.verdict === 'unclear' ? 'Unclear — no confident answer' : 'Inconclusive — too few verdicts'}</span>
                                                                )}
                                                            </div>
                                                            <div>{t.detail}</div>
                                                            {/* Reconciles: selected = scored + not reviewed. The counts
                                                                below are of SCORED photos only, so stating the selected
                                                                total on its own read as a mismatch (8 photos, 5 counts). */}
                                                            <div className="text-gray-600 mt-1">
                                                                Tested with {agentName(t.tested)} — {t.photos} photo(s) selected,
                                                                {' '}{t.scored} scored{t.notReviewed ? ' (' + t.notReviewed + ' not reviewed)' : ''}:
                                                                {' '}{t.match} match · {t.noMatch} no match · {t.errored} errored
                                                            </div>
                                                            {t.session ? (
                                                                <div className="mt-2">
                                                                    <a href={'/audit/' + t.session + '/bulk/?opportunity_id=' + id}
                                                                        target="_blank" rel="noopener noreferrer"
                                                                        className="font-medium text-blue-700 hover:underline">
                                                                        <i className="fa-solid fa-images mr-1"></i>
                                                                        View the {t.scored} photo(s) this check judged →
                                                                    </a>
                                                                    <div className="text-gray-600 mt-1">
                                                                        Look at them yourself before trusting the verdict. A dial scale has a
                                                                        round clock face and a needle; a digital one has an LCD number.
                                                                    </div>
                                                                </div>
                                                            ) : null}
                                                            {meta(id).unverified ? (
                                                                <div className="mt-2 text-gray-700">
                                                                    <i className="fa-solid fa-lock mr-1"></i>
                                                                    This opportunity stays marked <span className="font-medium">unconfirmed</span>
                                                                    {' '}until its hardware is recorded in the workflow config — which this page
                                                                    cannot write (there is no browser API for it). Once you have looked at the
                                                                    photos, ask Claude: <span className="font-mono">"opportunity {id} is
                                                                    {' '}{t.verdict === 'dial' || t.verdict === 'digital' ? t.verdict : scaleOf(id)}, confirm it"</span>.
                                                                </div>
                                                            ) : null}
                                                            {(t.verdict === 'dial' || t.verdict === 'digital') ? (
                                                                <div className="mt-2">
                                                                    <button onClick={() => setMode(id, t.kind)}
                                                                        className="px-2 py-1 rounded bg-amber-600 text-white hover:bg-amber-700">
                                                                        Use {t.kind} for this run
                                                                    </button>
                                                                    <span className="ml-2 text-gray-600">
                                                                        To make it permanent, tell Claude: "set opportunity {id} to {t.kind}".
                                                                        The page cannot write workflow config — there is no API for it.
                                                                    </span>
                                                                </div>
                                                            ) : null}
                                                        </div>
                                                    ) : null}
                                                </td>
                                            </tr>
                                        ) : null,
                                        ];
                                    })}
                                </tbody>
                            </table>
                        </div>
                    </div>
                ) : null}

                {/* Window + scope */}
                <div className="border-t border-gray-100 pt-4">
                    <h3 className="text-sm font-semibold text-gray-800 mb-2">
                        <i className="fa-solid fa-calendar-week mr-2 text-gray-400"></i>Window and scope
                    </h3>
                    <div className="flex flex-wrap gap-2 mb-3">
                        {[['last_week', 'Last week'], ['last_7_days', 'Last 7 days'], ['last_14_days', 'Last 14 days'], ['last_30_days', 'Last 30 days'], ['custom', 'Custom']].map(p => (
                            <button key={p[0]} onClick={() => applyPreset(p[0])}
                                className={'px-3 py-1.5 text-sm rounded-full border '
                                    + (datePreset === p[0] ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400')}>
                                {p[1]}
                            </button>
                        ))}
                    </div>
                    <div className="flex flex-wrap items-end gap-4">
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">Start</label>
                            <input type="date" value={startDate} max={endDate || undefined}
                                onChange={(e) => { setDatePreset('custom'); setStartDate(e.target.value); }}
                                className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm" />
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">End</label>
                            <input type="date" value={endDate} min={startDate || undefined}
                                onChange={(e) => { setDatePreset('custom'); setEndDate(e.target.value); }}
                                className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm" />
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-1"
                                title="Percentage of each FLW's qualifying visits to audit. Sampling is applied per FLW, so every worker keeps representation instead of the busiest crowding out the rest. 100 = census.">
                                Sample %
                            </label>
                            <div className="flex items-center gap-2">
                                <input type="number" min="1" max="100" value={samplePct}
                                    onChange={(e) => setSamplePct(Math.max(1, Math.min(100, Number(e.target.value) || 1)))}
                                    className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm w-20" />
                                <span className="text-xs text-gray-400">{Number(samplePct) >= 100 ? 'census' : 'per FLW'}</span>
                            </div>
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-1"
                                title="A hard ceiling on VISITS per field worker. Sample % is proportional, so a worker with 200 visits contributes far more than one with 25. A cap makes every worker count the same and makes the size of a run predictable. Each capped visit yields one photo of each selected type.">
                                Max visits per worker
                            </label>
                            <div className="flex items-center gap-2">
                                <input type="number" min="1" value={maxPerFlw} placeholder="no cap"
                                    onChange={(e) => setMaxPerFlw(e.target.value === '' ? '' : Math.max(1, Number(e.target.value)))}
                                    className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm w-24" />
                                {maxPerFlw !== '' ? (
                                    <span className="text-xs text-gray-400">
                                        ≈ {selected.length * 25 * Number(maxPerFlw) * Math.max(1, imageTypes.length)} photos max
                                    </span>
                                ) : <span className="text-xs text-gray-400">unbounded</span>}
                            </div>
                        </div>
                    </div>
                    <div className="mt-4 pt-3 border-t border-gray-100">
                        <label className="block text-xs text-gray-500 mb-1.5"
                            title="Which photos from each audited visit to collect. Only the weight photo has a classifier; the rest are collected for a person to review.">
                            Photos to audit
                        </label>
                        <div className="flex items-center gap-4 flex-wrap">
                            {IMAGE_TYPES.map(t => {
                                const missing = missingFor(t.path);
                                return (
                                    <label key={t.path} className="flex items-center gap-1.5 text-sm cursor-pointer"
                                        title={t.help || ''}>
                                        <input type="checkbox" checked={imageTypes.indexOf(t.path) >= 0}
                                            onChange={() => toggleImageType(t.path)} />
                                        <span className={imageTypes.indexOf(t.path) >= 0 ? 'text-gray-900' : 'text-gray-500'}>
                                            {t.label}
                                        </span>
                                        {t.scoreable
                                            ? <span className="text-[11px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-200">AI</span>
                                            : <span className="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 border border-gray-200"
                                                title="No classifier exists for this photo — it is collected for a person to review and is left out of the machine pass rate.">human</span>}
                                        {missing.length ? (
                                            <span className="text-[11px] text-amber-700"
                                                title={'Not found in recent visits for: ' + missing.map(oppLabel).join(', ')}>
                                                · not in {missing.length} selected
                                            </span>
                                        ) : null}
                                    </label>
                                );
                            })}
                        </div>
                        {/* The answer comes from the opportunity, not from a list written down
                            here, so a photo type an app never had cannot be silently audited to
                            nothing. Behind a button because the endpoint samples live visits. */}
                        <div className="mt-2 flex items-center gap-2 flex-wrap">
                            <button type="button" onClick={checkTypeAvail}
                                disabled={!selected.length || availLoading}
                                className="text-xs px-2 py-1 rounded border border-gray-300 hover:border-blue-400 hover:text-blue-700 disabled:opacity-50">
                                {availLoading ? 'Checking…' : 'Check which selected opportunities have these'}
                            </button>
                            {availAnswered.length ? (
                                <span className="text-xs text-gray-500">
                                    checked {availAnswered.length} of {selected.length} selected
                                </span>
                            ) : null}
                        </div>
                        {imageTypes.some(pth => !isScoreablePath(pth)) ? (
                            <p className="text-xs text-gray-600 mt-2 bg-gray-50 border border-gray-200 rounded px-3 py-2">
                                <i className="fa-solid fa-circle-info mr-1 text-gray-400"></i>
                                No classifier exists for equipment or KMC-wrap photos, so they arrive as
                                <span className="font-medium"> AI: not reviewed</span> and wait for a person. They are
                                left out of the match / no-match figures, so adding them does not read as a drop in
                                quality. The classifier is only ever handed the weight photo.
                            </p>
                        ) : null}
                    </div>
                    {maxPerFlw !== '' ? (
                        <p className="text-xs text-gray-600 mt-2 bg-gray-50 border border-gray-200 rounded px-3 py-2">
                            <i className="fa-solid fa-circle-info mr-1 text-gray-400"></i>
                            With a cap set, the workflow first asks which visits exist in the window, then picks up to{' '}
                            <span className="font-medium">{maxPerFlw}</span> per worker and audits exactly those. Every
                            worker counts equally regardless of how busy they were, and the run size is predictable.
                            If that selection step fails for an opportunity it is skipped rather than run uncapped.
                            {imageTypes.length > 1 ? (
                                <span> The cap counts <strong>visits</strong>, not photos: with {imageTypes.length}{' '}
                                    photo types selected each capped visit can carry one of each, so a worker can
                                    contribute up to {Number(maxPerFlw) * imageTypes.length} photos.</span>
                            ) : null}
                        </p>
                    ) : null}
                    <p className="text-xs text-gray-500 mt-3">
                        Sample % is the only volume control this backend honours for a date-range audit —
                        there is no cap on FLW count, and per-FLW visit limits apply only to "last N per FLW"
                        audits. Reduce the window or the percentage to keep a run small.
                    </p>
                </div>

                {/* Where scheduling lives.
                    This page used to offer "Later, once" and "Every day" as well. Both were a
                    setTimeout on this page: they only fired while the tab stayed open, which is
                    not what anyone means by scheduling. Real scheduling is now a server-side
                    record — it mints its own token and runs unattended — so the in-page timer was
                    removed rather than left to quietly not work. Pointing at the real thing here,
                    because a section that simply vanishes reads as a missing feature. */}
                <div className="border-t border-gray-200 pt-4">
                    <div className="text-sm font-semibold text-gray-800 mb-2">When to run</div>
                    <p className="text-sm text-gray-600">
                        This runs <span className="font-medium text-gray-900">now</span>.
                    </p>
                    <p className="text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded p-2 mt-2">
                        <i className="fa-regular fa-clock mr-1"></i>
                        To run this on a schedule instead, use <span className="font-medium">Schedule</span> on the
                        Workflows list page. That one runs on the server — daily, weekly or monthly, at a time you
                        pick — and does not need this tab, or your laptop, to be open.
                    </p>
                </div>

                {/* Submit */}
                <div className="border-t border-gray-200 pt-4">
                    <button onClick={() => { handleCreate(); }}
                        disabled={!selected.length || !startDate || !endDate || isRunning}
                        title={!selected.length ? 'Select at least one opportunity' : (!startDate || !endDate ? 'Set a window' : '')}
                        className={'inline-flex items-center px-6 py-3 rounded-lg font-medium text-white '
                            + (!selected.length || !startDate || !endDate || isRunning ? 'bg-gray-400 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700')}>
                        <i className={'mr-2 fa-solid ' + (isRunning ? 'fa-spinner fa-spin' : 'fa-play')}></i>
                        {isRunning ? 'Creating…'
                            : ('Create ' + selected.length + ' audit' + (selected.length === 1 ? '' : 's') + ' with AI')}
                    </button>
                    <p className="text-xs text-gray-500 mt-2">
                        Opportunities are audited <span className="font-medium">one after another</span>, not in parallel —
                        the scale classifiers degrade above roughly 20 concurrent calls, and each audit holds about 10
                        images in flight. Running five at once was measured at 77 usable classifications/hour against 644
                        for one at a time. The AI pass runs on the server, so you can leave this page.
                    </p>
                    {selected.length > 1 ? (
                        <p className="text-xs text-gray-500 mt-1">
                            <i className="fa-solid fa-clock mr-1 text-gray-400"></i>
                            {selected.length} opportunities queued sequentially. At the best observed rate for scale
                            classifiers (~650 photos/hour) expect roughly
                            <span className="font-medium"> {Math.max(1, Math.round(selected.length * 300 / 650))}–{Math.max(1, Math.round(selected.length * 900 / 650))} hours</span>
                            {' '}for a typical KMC window — reduce Sample % or the date range to shorten it.
                        </p>
                    ) : null}
                </div>
            </div>
            ) : null}

            {/* Armed and waiting. Same idea as the processing screen: the trigger form is put away so
                nothing on screen reads as "not yet set up", and what is pending is stated exactly. */}
            {phase === 'scheduled' ? (
                <div className="bg-white rounded-lg shadow-sm p-6 border-l-4 border-amber-400">
                    <div className="flex items-start gap-3">
                        <i className="fa-solid fa-clock text-xl text-amber-500 mt-0.5"></i>
                        <div className="flex-1">
                            <div className="text-sm font-semibold text-gray-900">
                                Scheduled — starts {new Date(armedFor).toLocaleString()}
                                <span className="ml-2 font-normal text-gray-500">
                                    (in {untilLabel(armedFor - nowTs)}{scheduleMode === 'daily' ? ', then daily' : ''})
                                </span>
                            </div>
                            <div className="text-sm text-gray-600 mt-1">
                                {selected.length} opportunit{selected.length === 1 ? 'y' : 'ies'} · {startDate} → {endDate}
                                {maxPerFlw !== '' && Number(maxPerFlw) > 0
                                    ? ' · max ' + Number(maxPerFlw) + ' photos per worker' : ''}
                                {Number(samplePct) < 100 ? ' · ' + Number(samplePct) + '% sample' : ''}
                            </div>
                            <div className="text-xs text-gray-500 mt-1">
                                {selected.map(id => oppLabel(id)).join(' · ')}
                            </div>
                            <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 mt-3">
                                Leave this tab open. The countdown runs here, not on the server, so closing the
                                page cancels the start. Anything already running is unaffected — that part is
                                server-side and completes on its own.
                            </p>
                            <div className="mt-3 flex items-center gap-2">
                                <button onClick={() => { setArmedFor(null); handleCreate(); }}
                                    className="px-3 py-1.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700">
                                    <i className="fa-solid fa-play mr-1.5"></i>Run now instead
                                </button>
                                <button onClick={() => { setArmedFor(null); setScheduleMode('now'); }}
                                    className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 hover:border-red-400 hover:text-red-700">
                                    Cancel schedule
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            ) : null}

            {/* Processing — its own screen. While a run is in flight this is all there is to look
                at, so there is nothing to misread as "ready to trigger". */}
            {(phase === 'running' || (progress && phase !== 'config')) ? (
                <div className="bg-white rounded-lg shadow-sm p-6">
                    <div className="flex items-center gap-3">
                        <i className={'fa-solid text-xl ' + (progress && progress.status === 'failed' ? 'fa-circle-exclamation text-red-600'
                            : progress && progress.status === 'completed' ? 'fa-circle-check text-green-600'
                                : 'fa-spinner fa-spin text-blue-600')}></i>
                        <div>
                            <div className="text-sm font-semibold text-gray-900">
                                {progress && progress.status === 'completed' ? 'Run finished'
                                    : progress && progress.status === 'failed' ? 'Run failed'
                                        : 'Processing — you can leave this page'}
                            </div>
                            <div className="text-xs text-gray-600 mt-0.5">
                                {(progress && (progress.message || progress.error)) || 'Working…'}
                                {progress && progress.total
                                    ? <span className="ml-1 text-gray-400">(opportunity {progress.processed || 0} of {progress.total})</span>
                                    : null}
                            </div>
                        </div>
                    </div>
                    {progress && progress.total ? (
                        <div className="mt-3 w-full bg-gray-100 rounded-full h-2">
                            <div className="bg-blue-600 h-2 rounded-full transition-all"
                                style={{ width: Math.round(100 * (progress.processed || 0) / progress.total) + '%' }}></div>
                        </div>
                    ) : null}

                    {/* The real state, from the sessions written so far. The server's own message
                        counts only passes and no-matches, so it omits errors entirely — on one run
                        it read "110/140 (1 passed, 0 failed)" while 109 had errored. */}
                    <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                        <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
                            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                                {isRunning ? 'Live — refreshing every 20s' : 'Result so far'}
                            </span>
                            <button onClick={refreshSessions} className="text-xs underline text-gray-500 hover:text-gray-800">
                                refresh now
                            </button>
                        </div>
                        <div className="flex flex-wrap items-center gap-2 text-sm">
                            <span className="px-2 py-1 rounded bg-white border border-gray-200">
                                <span className="font-semibold">{rollup.reviewed}</span>
                                <span className="text-gray-500"> of {rollup.images} photos reviewed</span>
                            </span>
                            <span className="px-2 py-1 rounded bg-green-50 text-green-800 border border-green-200">
                                <span className="font-semibold">{rollup.match}</span> match
                            </span>
                            <span className="px-2 py-1 rounded bg-amber-50 text-amber-800 border border-amber-200">
                                <span className="font-semibold">{rollup.noMatch}</span> no match
                            </span>
                            <span className={'px-2 py-1 rounded border '
                                + (rollup.error ? 'bg-red-50 text-red-800 border-red-200' : 'bg-white text-gray-400 border-gray-200')}>
                                <span className="font-semibold">{rollup.error}</span> errored
                                {rollup.reviewed ? <span className="ml-1">({rollup.errorPct}%)</span> : null}
                            </span>
                            {rollup.notReviewed ? (
                                <span className="px-2 py-1 rounded bg-red-50 text-red-800 border border-red-200">
                                    <span className="font-semibold">{rollup.notReviewed}</span> never reviewed
                                </span>
                            ) : null}
                        </div>
                        {rollup.reviewed > 0 && rollup.errorPct >= 20 ? (
                            <div className="mt-2 text-xs text-red-700">
                                Most calls are failing. Errored photos get no verdict and still need a human —
                                this run will not give a usable read on scale-photo quality.
                            </div>
                        ) : null}
                    </div>
                    {runError ? (
                        <div className="mt-3 rounded-lg px-4 py-3 text-sm border bg-red-50 border-red-200 text-red-800">{runError}</div>
                    ) : null}
                </div>
            ) : null}

            {/* Per-opportunity detail */}
            {(phase !== 'config' && hasCreated) ? (
                <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                    <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between flex-wrap gap-2">
                        <span className="text-sm font-semibold text-gray-800">Per-opportunity detail</span>
                        <button onClick={refreshSessions} className="text-xs underline text-gray-500 hover:text-gray-800">
                            {loadingSessions ? 'Refreshing…' : 'Refresh'}
                        </button>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="min-w-full text-sm">
                            <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                                <tr>
                                    {['Opportunity', 'Scale', 'Agent', 'FLWs', 'Photos', 'Reviewed', 'Match', 'No match', 'Errored', 'Never reviewed'].map((h, i) => (
                                        <th key={h} className={'px-3 py-2 font-medium whitespace-nowrap ' + (i < 3 ? 'text-left' : 'text-right')}
                                            title={h === 'Never reviewed' ? 'Images the AI pass never reached — not a verdict, not an error. Caused by the task stopping before it finished.' : undefined}>{h}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {rollup.byOpp.map(o => (
                                    <tr key={o.opp} className="border-t border-gray-100 hover:bg-gray-50">
                                        <td className="px-3 py-2 whitespace-nowrap">
                                            <span className="font-medium text-gray-900">{o.llo}</span>
                                            <span className="text-xs text-gray-500 ml-1">{meta(o.opp).version}</span>
                                            <span className="text-xs text-gray-400 font-mono ml-1">#{o.opp}</span>
                                        </td>
                                        <td className="px-3 py-2"><ScalePill kind={o.scale} unverified={meta(o.opp).unverified} /></td>
                                        <td className="px-3 py-2 text-xs text-gray-600 whitespace-nowrap">{agentName(effectiveAgent(o.opp))}</td>
                                        <td className="px-3 py-2 text-right">{o.sessions}</td>
                                        <td className="px-3 py-2 text-right">{o.images}</td>
                                        <td className="px-3 py-2 text-right">{o.assessed}</td>
                                        <td className="px-3 py-2 text-right text-green-700">{o.match}</td>
                                        <td className="px-3 py-2 text-right text-amber-700">{o.noMatch}</td>
                                        <td className="px-3 py-2 text-right text-red-700">{o.error}</td>
                                        <td className={'px-3 py-2 text-right font-semibold ' + (o.notReviewed ? 'text-red-700' : 'text-gray-300')}
                                            title={o.unstarted ? (o.unstarted + ' FLW audit(s) here were never started') : undefined}>
                                            {o.notReviewed ? o.notReviewed + (o.unstarted ? ' (' + o.unstarted + ' FLWs)' : '') : '—'}
                                        </td>
                                    </tr>
                                ))}
                                {/* Selected but empty. Stated outright rather than omitted — a missing
                                    row reads as "not selected", which is a different and misleading thing. */}
                                {emptyOpps.map(oid => (
                                    <tr key={'empty-' + oid} className="border-t border-gray-100 bg-amber-50/40">
                                        <td className="px-3 py-2 whitespace-nowrap">
                                            <span className="font-medium text-gray-900">{meta(oid).llo || ('Opportunity ' + oid)}</span>
                                            <span className="text-xs text-gray-500 ml-1">{meta(oid).version}</span>
                                            <span className="text-xs text-gray-400 font-mono ml-1">#{oid}</span>
                                        </td>
                                        <td className="px-3 py-2"><ScalePill kind={scaleOf(oid)} unverified={meta(oid).unverified} /></td>
                                        <td className="px-3 py-2 text-xs text-gray-600 whitespace-nowrap">{agentName(effectiveAgent(oid))}</td>
                                        <td colSpan={7} className="px-3 py-2 text-xs text-amber-800">
                                            <i className="fa-solid fa-circle-info mr-1"></i>
                                            <span className="font-medium">No audit created — 0 photos.</span>{' '}
                                            Either no visits carried the weight photo between {startDate} and {endDate},
                                            or audit creation did not succeed for this opportunity. Re-run this one on its
                                            own to tell the two apart.
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <div className="px-4 py-2 text-xs text-gray-500">
                        {sessions.length} session{sessions.length === 1 ? '' : 's'} across {rollup.byOpp.length} opportunit{rollup.byOpp.length === 1 ? 'y' : 'ies'} ·
                        counts read from the stored session records, not from the create response.
                        {emptyOpps.length ? (
                            <span className="text-amber-800 font-medium">
                                {' · '}{emptyOpps.length} selected opportunit{emptyOpps.length === 1 ? 'y' : 'ies'} produced nothing
                            </span>
                        ) : null}
                    </div>
                </div>
            ) : null}

            {/* Field-worker results — one row per worker, the unit people act on. Expand a row to
                see the photos the AI flagged, with the classifier's own reason. */}
            {/* Also shown while a run is armed: a daily schedule should not blank out the results of
                the run it is repeating. */}
            {(phase === 'results' || phase === 'scheduled') ? (
                <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                    <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between flex-wrap gap-2">
                        <div>
                            <span className="text-sm font-semibold text-gray-800">Results by field worker</span>
                            <span className="ml-2 text-xs text-gray-400">
                                {visibleRows.length}{visibleRows.length !== flwRows.length
                                    ? ' of ' + flwRows.length : ''} worker{visibleRows.length === 1 ? '' : 's'} · worst first · a worker stays
                                Pending until a person reviews their photos
                            </span>
                        </div>
                        <div className="flex items-center gap-2 flex-wrap">
                            <select value={lloFilter} onChange={e => setLloFilter(e.target.value)}
                                className="px-2 py-1 text-sm border border-gray-300 rounded-lg">
                                <option value="">All LLOs</option>
                                {lloOptions.map(l => <option key={l} value={l}>{l}</option>)}
                            </select>
                            <input type="search" value={flwSearch} onChange={e => setFlwSearch(e.target.value)}
                                placeholder="Find a field worker…"
                                className="px-2 py-1 text-sm border border-gray-300 rounded-lg w-48" />
                            {(lloFilter || flwSearch) ? (
                                <button onClick={() => { setLloFilter(''); setFlwSearch(''); }}
                                    className="text-xs underline text-gray-500 hover:text-gray-800">Clear</button>
                            ) : null}
                            <button onClick={refreshSessions} className="text-xs underline text-gray-500 hover:text-gray-800">
                                {loadingSessions ? 'Refreshing…' : 'Refresh'}
                            </button>
                            <button onClick={downloadCsv}
                                className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 hover:border-blue-400 hover:text-blue-700">
                                <i className="fa-solid fa-download mr-1.5"></i>Export CSV
                            </button>
                        </div>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="min-w-full text-sm">
                            <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                                <tr>
                                    <th className="px-3 py-2 text-left font-medium">Field worker</th>
                                    <th className="px-3 py-2 text-left font-medium">LLO · Opportunity</th>
                                    <th className="px-3 py-2 text-right font-medium">Photos</th>
                                    <th className="px-3 py-2 text-center font-medium"
                                        title="What the AI concluded: matched the typed weight / did not match / the call failed">
                                        AI: match / no match / errored
                                    </th>
                                    <th className="px-3 py-2 text-center font-medium"
                                        title="Photos still waiting on a person. The AI verdict is a suggestion; sign-off happens on the review page.">
                                        Needs your review
                                    </th>
                                    <th className="px-3 py-2 text-right font-medium"
                                        title="Of the photos a person has judged, the share marked pass">% passed</th>
                                    <th className="px-3 py-2 text-left font-medium">Review status</th>
                                    <th className="px-3 py-2 text-right font-medium">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {visibleRows.map(r => {
                                    const d = detail[r.id] || {};
                                    // Every photo, not just the flagged ones: a match with its reason is
                                    // evidence too, and a photo the AI never reached is invisible otherwise.
                                    const allRows = d.rows || [];
                                    const aiOf = (x) => x.ai_result || 'not_reviewed';
                                    const flagged = allRows.filter(x => {
                                        const ai = aiOf(x);
                                        const okAi = photoAi === '' ? true
                                            : photoAi === 'flagged' ? (ai === 'no_match' || ai === 'error')
                                                : ai === photoAi;
                                        const okHuman = !photoHuman
                                            || (photoHuman === 'pending' ? !x.result : x.result === photoHuman);
                                        const okType = !photoType || x.question_id === photoType;
                                        return okAi && okHuman && okType;
                                    });
                                    const tally = allRows.reduce((acc, x) => {
                                        acc[aiOf(x)] = (acc[aiOf(x)] || 0) + 1; return acc;
                                    }, {});
                                    // Types actually present in THIS worker's photos, in
                                    // declaration order, with the undeclared ones after.
                                    // Built from the rows rather than from the run's
                                    // settings, so it shows what arrived, not what was asked
                                    // for - the two differ whenever an opportunity's app has
                                    // no such question.
                                    const typeTally = allRows.reduce((acc, x) => {
                                        const q = x.question_id || '';
                                        acc[q] = (acc[q] || 0) + 1; return acc;
                                    }, {});
                                    const presentTypes = IMAGE_TYPES
                                        .map(t => t && t.path).filter(pth => pth && typeTally[pth])
                                        .concat(Object.keys(typeTally).filter(q => q && !typeByPath[q]));
                                    return [
                                        <tr key={r.id} className="border-t border-gray-100 hover:bg-gray-50">
                                            <td className="px-3 py-2 whitespace-nowrap">
                                                <span className="font-medium text-gray-900">{r.flw}</span>
                                                {r.noMatch > 0 ? (
                                                    <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-xs font-semibold"
                                                        title="The AI says the photo does not match the typed weight — review these first.">
                                                        {r.noMatch} to check
                                                    </span>
                                                ) : null}
                                                {r.neverReviewed > 0 ? (
                                                    <span className="ml-1 px-1.5 py-0.5 rounded bg-red-100 text-red-800 text-xs font-semibold"
                                                        title="The AI pass never reached these photos">
                                                        {r.neverReviewed} not reviewed
                                                    </span>
                                                ) : null}
                                            </td>
                                            <td className="px-3 py-2 whitespace-nowrap text-xs">
                                                <span className="font-medium text-gray-800">{r.llo}</span>
                                                <span className="text-gray-500 ml-1">{meta(r.opp).version} · {meta(r.opp).country}</span>
                                                <span className="text-gray-400 font-mono ml-1">#{r.opp}</span>
                                                <span className="ml-1"><ScalePill kind={scaleOf(r.opp)} unverified={meta(r.opp).unverified} /></span>
                                            </td>
                                            {/* One cell, not a column per type: the question is
                                                "what are this worker's photos", and three sparse
                                                columns cost more width than they buy. */}
                                            <td className="px-3 py-2 text-right whitespace-nowrap">
                                                {r.photos}
                                                {r.typeCounts && Object.keys(r.typeCounts).length > 1 ? (
                                                    <div className="text-[11px] text-gray-500 font-mono"
                                                        title={Object.keys(r.typeCounts)
                                                            .map(pth => typeLabel(pth) + ': ' + r.typeCounts[pth]).join(', ')}>
                                                        {IMAGE_TYPES.map(t => t && t.path)
                                                            .filter(pth => pth && r.typeCounts[pth])
                                                            .map(pth => typeInitial(pth) + ' ' + r.typeCounts[pth])
                                                            .join(' \\u00b7 ')}
                                                    </div>
                                                ) : null}
                                            </td>
                                            <td className="px-3 py-2 text-center whitespace-nowrap">
                                                <span className="text-green-700 font-semibold">{r.match}</span>
                                                <span className="text-gray-300"> / </span>
                                                <span className={r.noMatch ? 'text-amber-700 font-semibold' : 'text-gray-300'}>{r.noMatch}</span>
                                                <span className="text-gray-300"> / </span>
                                                <span className={r.error ? 'text-red-700 font-semibold' : 'text-gray-300'}>{r.error}</span>
                                            </td>
                                            {/* An actionable link, not a dead count. The override-and-sign-off
                                                flow already exists on the review page — AI verdict, per-photo
                                                Pass/Fail/Incomplete, then "Complete Image Review". A column
                                                reading "0 / 0 / 9" told nobody that, so nobody went there. */}
                                            <td className="px-3 py-2 text-center whitespace-nowrap">
                                                {r.pending > 0 ? (
                                                    <a href={'/audit/' + r.id + '/bulk/?opportunity_id=' + r.opp}
                                                        target="_blank" rel="noopener noreferrer"
                                                        className="inline-flex items-center px-2 py-1 rounded text-xs font-semibold bg-blue-50 text-blue-700 hover:bg-blue-100">
                                                        {r.pending} photo{r.pending === 1 ? '' : 's'} need review →
                                                    </a>
                                                ) : r.humanDone > 0 ? (
                                                    <span className="text-xs text-green-700 font-semibold">
                                                        <i className="fa-solid fa-check mr-1"></i>
                                                        All {r.humanDone} reviewed
                                                        {r.fail ? <span className="text-red-700 font-normal"> · {r.fail} failed</span> : null}
                                                    </span>
                                                ) : (
                                                    <span className="text-xs text-gray-400">nothing to review yet</span>
                                                )}
                                            </td>
                                            <td className="px-3 py-2 text-right font-semibold">
                                                {r.pctPassed == null ? <span className="text-gray-300">—</span>
                                                    : <span className={r.pctPassed >= 90 ? 'text-green-700' : r.pctPassed >= 70 ? 'text-amber-700' : 'text-red-700'}>{r.pctPassed}%</span>}
                                            </td>
                                            <td className="px-3 py-2 text-xs">
                                                <span className={'px-2 py-0.5 rounded font-semibold '
                                                    + (r.reviewStatus === 'Reviewed' ? 'bg-green-100 text-green-800'
                                                        : r.reviewStatus === 'In review' ? 'bg-blue-100 text-blue-800'
                                                            : 'bg-gray-100 text-gray-600')}>
                                                    {r.reviewStatus}
                                                </span>
                                            </td>
                                            <td className="px-3 py-2 text-right whitespace-nowrap">
                                                <button onClick={() => loadDetail(r.id)}
                                                    className="text-xs underline text-gray-600 hover:text-gray-900 mr-3">
                                                    {expanded === r.id ? 'Hide photos' : 'Photos & AI verdicts'}
                                                </button>
                                                <a className="text-xs font-medium text-blue-600 hover:underline"
                                                    href={'/audit/' + r.id + '/bulk/?opportunity_id=' + r.opp}
                                                    target="_blank" rel="noopener noreferrer">
                                                    Open images →
                                                </a>
                                            </td>
                                        </tr>,
                                        expanded === r.id ? (
                                            <tr key={r.id + '-detail'} className="bg-gray-50 border-t border-gray-100">
                                                <td colSpan={8} className="px-4 py-3">
                                                    {d.loading ? <div className="text-xs text-gray-500">Loading photo detail…</div> : null}
                                                    {d.error ? <div className="text-xs text-red-700">Could not load detail: {d.error}</div> : null}
                                                    {d.rows ? (
                                                        <div className="flex items-center gap-2 flex-wrap text-xs mb-2">
                                                            <span className="font-semibold text-gray-700">Show photos:</span>
                                                            {[['flagged', 'Needs a look', (tally.no_match || 0) + (tally.error || 0)],
                                                              ['match', 'Matched', tally.match || 0],
                                                              ['no_match', 'No match', tally.no_match || 0],
                                                              ['error', 'Errored', tally.error || 0],
                                                              ['not_reviewed', 'Not reviewed', tally.not_reviewed || 0],
                                                              ['', 'All', allRows.length]].map(o => (
                                                                <button key={o[0] || 'all'} onClick={() => setPhotoAi(o[0])}
                                                                    className={'px-2 py-0.5 rounded border '
                                                                        + (photoAi === o[0]
                                                                            ? 'bg-blue-600 text-white border-blue-600'
                                                                            : 'bg-white border-gray-300 text-gray-700 hover:border-blue-400')}>
                                                                    {o[1]} ({o[2]})
                                                                </button>
                                                            ))}
                                                            {presentTypes.length > 1 ? (
                                                                <React.Fragment>
                                                                    <span className="ml-2 text-gray-500">Type:</span>
                                                                    <button onClick={() => setPhotoType('')}
                                                                        className={'px-2 py-0.5 rounded border '
                                                                            + (photoType === ''
                                                                                ? 'bg-blue-600 text-white border-blue-600'
                                                                                : 'bg-white border-gray-300 text-gray-700 hover:border-blue-400')}>
                                                                        All ({allRows.length})
                                                                    </button>
                                                                    {presentTypes.map(pth => (
                                                                        <button key={pth} onClick={() => setPhotoType(pth)}
                                                                            title={isScoreablePath(pth)
                                                                                ? 'Checked by the scale classifier'
                                                                                : 'No classifier for this photo - human review only'}
                                                                            className={'px-2 py-0.5 rounded border '
                                                                                + (photoType === pth
                                                                                    ? 'bg-blue-600 text-white border-blue-600'
                                                                                    : 'bg-white border-gray-300 text-gray-700 hover:border-blue-400')}>
                                                                            {typeLabel(pth)} ({typeTally[pth]})
                                                                            {isScoreablePath(pth) ? '' : ' \\u00b7 human'}
                                                                        </button>
                                                                    ))}
                                                                </React.Fragment>
                                                            ) : null}
                                                            <span className="ml-2 text-gray-500">Human:</span>
                                                            <select value={photoHuman} onChange={e => setPhotoHuman(e.target.value)}
                                                                className="px-1 py-0.5 border border-gray-300 rounded">
                                                                <option value="">any</option>
                                                                <option value="pending">pending</option>
                                                                <option value="pass">passed</option>
                                                                <option value="fail">failed</option>
                                                            </select>
                                                        </div>
                                                    ) : null}
                                                    {d.rows && !flagged.length ? (
                                                        <div className="text-xs text-gray-500">
                                                            No photos match this filter — {allRows.length} in this audit.
                                                        </div>
                                                    ) : null}
                                                    {flagged.length ? (
                                                        <div>
                                                            <div className="text-xs font-semibold text-gray-700 mb-2">
                                                                Showing {flagged.length} of {allRows.length} photo{allRows.length === 1 ? '' : 's'} — every row carries the classifier reason
                                                            </div>
                                                            <div className="overflow-x-auto">
                                                                <table className="min-w-full text-xs">
                                                                    <thead className="text-gray-500 uppercase">
                                                                        <tr>
                                                                            <th className="px-2 py-1 text-left font-medium">Visit</th>
                                                                            <th className="px-2 py-1 text-left font-medium">Child / entity</th>
                                                                            <th className="px-2 py-1 text-right font-medium">Typed weight</th>
                                                                            <th className="px-2 py-1 text-left font-medium">AI verdict</th>
                                                                            <th className="px-2 py-1 text-left font-medium">Why — classifier output</th>
                                                                            <th className="px-2 py-1 text-left font-medium">Human</th>
                                                                            <th className="px-2 py-1 text-right font-medium"></th>
                                                                        </tr>
                                                                    </thead>
                                                                    <tbody>
                                                                        {flagged.map(x => {
                                                                            const rf = (x.related_fields || []).filter(f => f && f.value)[0];
                                                                            const reading = rf ? rf.value : null;
                                                                            return (
                                                                                <tr key={x.id} className="border-t border-gray-200">
                                                                                    <td className="px-2 py-1 whitespace-nowrap text-gray-600">{x.visit_date || '—'}</td>
                                                                                    <td className="px-2 py-1 whitespace-nowrap text-gray-600">{x.entity_name || '—'}</td>
                                                                                    <td className="px-2 py-1 text-right font-mono">{reading != null ? reading : '—'}</td>
                                                                                    <td className="px-2 py-1 whitespace-nowrap">
                                                                                        <span className={'px-1.5 py-0.5 rounded font-semibold '
                                                                                            + (x.ai_result === 'match' ? 'bg-green-100 text-green-800'
                                                                                                : x.ai_result === 'no_match' ? 'bg-amber-100 text-amber-800'
                                                                                                    : x.ai_result === 'error' ? 'bg-red-100 text-red-800'
                                                                                                        : 'bg-gray-100 text-gray-600')}>
                                                                                            {x.ai_result === 'match' ? 'Match'
                                                                                                : x.ai_result === 'no_match' ? 'No match'
                                                                                                    : x.ai_result === 'error' ? 'Errored' : 'Not reviewed'}
                                                                                        </span>
                                                                                    </td>
                                                                                    <td className="px-2 py-1 text-gray-700">
                                                                                        {x.ai_notes ? x.ai_notes
                                                                                            : x.ai_result === 'match'
                                                                                                ? 'Matched the typed weight; no further detail was returned.'
                                                                                                : x.ai_result === 'no_match'
                                                                                                    ? 'Did not match the typed weight; no further detail was returned.'
                                                                                                    : x.ai_result === 'error'
                                                                                                        ? 'The classifier call failed; no verdict was produced.'
                                                                                                        : 'The AI never reached this photo — unreviewed, not passed.'}
                                                                                    </td>
                                                                                    <td className="px-2 py-1 whitespace-nowrap">
                                                                                        {x.result
                                                                                            ? <span className={'font-semibold ' + (x.result === 'pass' ? 'text-green-700' : 'text-red-700')}>{x.result}</span>
                                                                                            : <span className="text-gray-400">pending</span>}
                                                                                    </td>
                                                                                    <td className="px-2 py-1 text-right">
                                                                                        {x.image_url ? (
                                                                                            <a href={x.image_url} target="_blank" rel="noopener noreferrer"
                                                                                                className="text-blue-600 hover:underline">photo →</a>
                                                                                        ) : null}
                                                                                    </td>
                                                                                </tr>
                                                                            );
                                                                        })}
                                                                    </tbody>
                                                                </table>
                                                            </div>
                                                        </div>
                                                    ) : null}
                                                </td>
                                            </tr>
                                        ) : null,
                                    ];
                                })}
                            </tbody>
                        </table>
                    </div>
                    <div className="px-4 py-2 text-xs text-gray-500 border-t border-gray-100">
                        "Pending" means nobody has reviewed that worker's photos yet — an AI match is a suggestion,
                        not a sign-off. "% passed" is computed only over photos a person has actually judged.
                    </div>
                </div>
            ) : null}
        </div>
    );
}
"""

TEMPLATE = {
    "key": "kmc_image_audit",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-weight-scale",
    "color": "purple",
    "multi_opp": True,
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
}


# ── Headless default-run hook ─────────────────────────────────────────────────
# What the "Create audits" button does, with no browser. Registering this is what
# makes the workflow schedulable (see template_supports_default_run), and it is
# the only way KMC image audits can run unattended: the UI drives its
# per-opportunity loop from an open tab, so closing the laptop abandons whatever
# has not started yet.
#
# Everything that varies lives in config.schedule_defaults, so which
# opportunities a schedule covers can be changed with a definition patch rather
# than a code change and a deploy:
#
#     "schedule_defaults": {
#         "opportunity_ids": [1236, 1487, 1488, 1739, 1790],
#         "sample_percentage": 30,
#         "agent_override": {"1739": "scale_dial_read"}
#     }
SCHEDULE_DEFAULTS_KEY = "schedule_defaults"
SESSION_TAG = "kmc_weight_photo"


def _resolve_image_paths(configured, image_types):
    """The photo types a run should audit, as paths, in the order they are declared.

    Anything not declared in image_types is dropped rather than passed through: an audit
    rule naming a path no opportunity has would select nothing and report an empty window,
    which is indistinguishable from a genuinely quiet day. Falls back to the weight photo
    alone, which is what this workflow audited before it could audit anything else, so an
    existing schedule that has never heard of image_paths keeps its exact behaviour.
    """
    declared = [t.get("path") for t in image_types if isinstance(t, dict) and t.get("path")]
    wanted = configured if isinstance(configured, list) else []
    # dict.fromkeys: dedupe while keeping declaration order, so two rules for one path
    # (which would double every image of that type) cannot be built.
    chosen = [p for p in declared if p in set(wanted)]
    dropped = [p for p in dict.fromkeys(wanted) if p not in declared]
    if dropped:
        logger.warning("kmc_image_audit ignoring unknown image paths %s", dropped)
    if chosen:
        return chosen
    scoreable = [t["path"] for t in image_types if isinstance(t, dict) and t.get("scoreable") and t.get("path")]
    return scoreable or list(DEFAULT_IMAGE_PATHS)


def _effective_image_types(cfg):
    """image_types with the older weight-path config keys folded in.

    config has carried weight_image_path / weight_field_path since before this workflow
    could audit anything but the weight photo, and the live workflows set them. Folding
    them into the scoreable entry leaves ONE effective answer for where the weight photo
    is, instead of two config keys that can be edited independently and disagree - at
    which point the picker would offer one path and the audit would select the other.
    """
    types = [dict(t) for t in (cfg.get("image_types") or IMAGE_TYPES) if isinstance(t, dict)]
    weight_path = cfg.get("weight_image_path")
    weight_field = cfg.get("weight_field_path")
    for spec in types:
        if spec.get("scoreable"):
            if weight_path:
                spec["path"] = weight_path
            if weight_field:
                spec["field_path"] = weight_field
            break
    return types


def _image_rules(*, image_paths, image_types):
    """related_fields rules for the chosen photo types.

    One rule per type, each with filter_by_image. Several such rules are OR-ed by
    AuditDataAccess._filter_visits_by_related_fields - a visit is kept if it carries ANY
    of the chosen types, and only images of those types are kept - which is what lets one
    audit cover weight plus equipment plus wrap without also dragging in the immunization
    card and house photos those same visits carry.
    """
    by_path = {t.get("path"): t for t in image_types if isinstance(t, dict) and t.get("path")}
    return [
        {
            "image_path": path,
            # Empty for a type with nothing to compare against; from_dict explicitly
            # allows it ("image-only filter rules are valid") and _extract_field_value
            # returns None for an empty path, so no stray related field is attached.
            "field_path": (by_path.get(path) or {}).get("field_path") or "",
            "label": (by_path.get(path) or {}).get("field_label") or "",
            "filter_by_image": True,
        }
        for path in image_paths
    ]


def _image_audits(*, image_paths, image_types, agent_id):
    """The per-photo-type reviewer payload run_audit_creation accepts.

    Needed the moment a run covers more than the weight photo. With a single global
    ai_agent_id "the same single agent applies to every question_id" (see
    tasks._run_ai_review_on_sessions), so the scale classifier would be handed equipment
    and wrap photos and would dutifully report every one of them as a scale mismatch -
    false flags on photos it was never meant to see, at full gateway cost.

    Giving only the scoreable type a reviewer makes the review task skip the others
    outright ("no reviewer configured for this image type"). They are still collected and
    still shown, simply as not yet reviewed, which is what a human reviewer is for.
    """
    by_path = {t.get("path"): t for t in image_types if isinstance(t, dict) and t.get("path")}
    entries = []
    for path in image_paths:
        spec = by_path.get(path) or {}
        reviewers = []
        if spec.get("scoreable") and agent_id:
            config = {}
            if spec.get("field_path"):
                config["comparison_field"] = spec["field_path"]
                config["label"] = spec.get("field_label") or ""
            reviewers.append({"agent_id": agent_id, "config": config})
        entries.append({"image_path": path, "reviewers": reviewers})
    return entries


def _scheduled_criteria(*, opp_id, opp_meta, window_start, window_end, sample_percentage, image_rules):
    """The same AuditCriteria the render code builds, assembled server-side.

    filter_by_image narrows the IMAGES kept once a visit's form JSON has been parsed,
    at extraction time. It does NOT narrow visit selection: filter_visits_for_audit
    takes no related_fields parameter and the key is dropped before it gets there. A
    visit carrying no weight photo is therefore still selected here, and simply
    contributes no images later - which is harmless for an uncapped run and is exactly
    why a cap needs _capped_flw_visit_ids instead of slicing this selection.
    """
    meta = opp_meta.get(str(opp_id)) or {}
    label = meta.get("llo") or "Opportunity %s" % opp_id
    return {
        "audit_type": "date_range",
        "granularity": "per_flw",
        "start_date": window_start,
        "end_date": window_end,
        "sample_percentage": sample_percentage,
        "related_fields": list(image_rules),
        "title": f"KMC Image Audit - {label} - {window_start} to {window_end}",
        "tag": SESSION_TAG,
    }


def _error_line(opp_id, meta, window_start, window_end, detail):
    """One failure, written so it can be acted on without opening anything else.

    These strings are the ONLY account of a scheduled failure a person sees - they land in
    WorkflowSchedule.last_error and are rendered on the admin schedules table. A bare
    "1236: <exception>" makes the reader look up which LLO 1236 is and which window ran
    before they can even start, so the id, the LLO name and the audited window all go in.
    """
    label = (meta or {}).get("llo") or "Opportunity"
    return f"{label} {opp_id} ({window_start} to {window_end}): {detail}"


class _FormRenamed(Exception):
    """The photo-bearing form matched nothing while the window held visits.

    Raised rather than returning an empty selection so the caller reports a failure for
    that opportunity instead of creating zero sessions and calling it an empty window.
    """


def _capped_flw_visit_ids(*, data_access, opp_id, criteria, cap, photo_form_names):
    """Pick up to ``cap`` photo-bearing VISITS per field worker, most recent first.

    The cap is on visits, not photos. That was the same thing while this workflow audited
    only the weight photo - one per visit - but a visit carries one photo of every selected
    type, so a cap of 5 across three types yields up to 15 photos for a worker. Capping
    photos instead is not available here: selection happens per visit, before the form JSON
    has been parsed and before it is known which photos a visit actually holds.

    Returns ``(flw_visit_ids, visit_ids)`` - the per-worker mapping run_audit_creation
    groups sessions by, and the flat union that lets it skip its own visit-fetch stage.

    The cap is applied to visits of the photo-bearing form only (see PHOTO_FORM_NAMES).
    Sampling stays in the criteria and the backend applies it first, so the order is
    sample then cap. Note the sample is one random draw across the whole opportunity, NOT
    per worker, so it can drop a quiet worker before the cap ever sees them - the cap
    bounds the busy, it cannot rescue the quiet.

    Raises _FormRenamed only when the configured form appears nowhere in the opportunity.
    A window with no photo visits is NOT that - registration-only days are ordinary here -
    so it returns an empty selection instead and lets the run report itself empty.
    """
    selection = dict(criteria)
    selection["deliver_unit_types"] = list(photo_form_names)
    # related_fields is meaningless to the selection layer and is dropped on the way to
    # the backend anyway; leaving it in would only suggest it does something here.
    selection.pop("related_fields", None)

    _ids, visits = data_access.get_visit_ids_for_audit([opp_id], criteria=selection, return_visits=True)

    if not visits:
        # Distinguish "the form no longer exists" from "no photo visits this window".
        #
        # Asking whether the window held OTHER visit types cannot tell those apart: a day
        # of registrations only looks exactly like a rename, and registration-only days
        # are common here - a third of these opportunities' visits are registrations. So
        # the old probe cried wolf on ordinary quiet days and marked the schedule FAILED.
        #
        # What does settle it is whether the form appears in the opportunity AT ALL, which
        # is a different question from whether it appears in this window.
        try:
            known_forms = data_access.get_deliver_unit_types(opp_id)
        except Exception:  # noqa: BLE001 - a lookup failure must not become a false alarm
            logger.warning("kmc_image_audit could not list form names for opportunity %s", opp_id)
            known_forms = []
        if known_forms and not set(photo_form_names) & set(known_forms):
            raise _FormRenamed(
                "form %s appears nowhere in opportunity %s (it has: %s) - the form was "
                "probably renamed upstream; set config.photo_form_names to its new name"
                % (photo_form_names, opp_id, sorted(known_forms))
            )
        return {}, []

    per_flw = {}
    for visit in visits:
        username = visit.get("username")
        if not username:
            continue
        per_flw.setdefault(username, []).append(visit)

    flw_visit_ids = {}
    for username, worker_visits in per_flw.items():
        # Most recent first, so a cap keeps the freshest work rather than an arbitrary
        # slice of whatever order the backend happened to return.
        worker_visits.sort(key=lambda v: (v.get("visit_date") or ""), reverse=True)
        chosen = [v["id"] for v in worker_visits[:cap] if v.get("id") is not None]
        if chosen:
            flw_visit_ids[username] = chosen

    visit_ids = sorted({vid for ids in flw_visit_ids.values() for vid in ids})
    return flw_visit_ids, visit_ids


def run_default(*, definition, access_token, request=None, window=None, cadence=None, **_):
    """Create this workflow's audits for one window, with no UI.

    window is an explicit (start, end) pair when the caller has one (the
    workflow_run_default MCP tool passes it); otherwise it is derived from
    cadence exactly as every other schedulable template derives it, so a daily
    schedule audits yesterday and a monthly one audits last month.

    Opportunities are audited ONE AT A TIME and each call runs eagerly, so the
    whole batch completes inside this task rather than fanning out. That is
    deliberate: the AI classifiers degrade badly under concurrency, and a
    sequential batch is also what makes the run resumable as a single unit.

    A per-opportunity failure is recorded and the loop continues - one
    unreachable opportunity must not cost the others their audits.

    schedule_defaults.max_per_flw caps VISITS per field worker (one photo of each
    selected type per visit - see _capped_flw_visit_ids). It is applied here rather than
    through AuditCriteria because no criteria field expresses "at most N per worker WITHIN
    a date window" - count_per_flw belongs to last_n_per_flw, which ignores the window.

    schedule_defaults.image_paths chooses WHICH photo types to audit. Unset means the
    weight photo alone, which is what this workflow audited before it could audit anything
    else, so an existing schedule keeps its exact behaviour.

    Returns {"run_id", "sessions_created", "status"}; status is "ready" if
    anything was created and "failed" if nothing was.
    """
    from datetime import date

    from connect_labs.audit.data_access import AuditDataAccess, create_mock_request
    from connect_labs.audit.tasks import run_audit_creation
    from connect_labs.workflow.audit_generation import resolve_window, window_preset_for_cadence
    from connect_labs.workflow.data_access import WorkflowDataAccess

    cfg = (definition.data or {}).get("config") or {}
    defaults = cfg.get(SCHEDULE_DEFAULTS_KEY) or {}
    opp_meta = cfg.get("opp_meta") or OPP_META
    agent_for_scale = cfg.get("agent_for_scale") or AGENT_FOR_SCALE
    photo_form_names = cfg.get("photo_form_names") or PHOTO_FORM_NAMES
    image_types = _effective_image_types(cfg)
    # Which photo types this run covers. Unset means the weight photo alone, so a
    # schedule saved before this setting existed audits exactly what it always did.
    image_paths = _resolve_image_paths(defaults.get("image_paths"), image_types)
    image_rules = _image_rules(image_paths=image_paths, image_types=image_types)
    # Only the weight photo has a classifier, so anything beyond it makes this a
    # partly human-reviewed run. That distinction decides how the audit is dispatched
    # below, and is worth recording on the run either way.
    scoreable_paths = [t["path"] for t in image_types if t.get("scoreable") and t["path"] in image_paths]

    # Deduped and sorted. The dialog already dedupes, but config is hand-editable and a
    # repeated id would audit that opportunity twice - the second pass resuming onto the
    # first's checkpoint and re-reporting its sessions, so sessions_created double-counts.
    raw_opp_ids = defaults.get("opportunity_ids")
    raw_opp_ids = raw_opp_ids if isinstance(raw_opp_ids, list) else []
    opp_ids = sorted({int(o) for o in raw_opp_ids if str(o).lstrip("-").isdigit()})
    if not opp_ids:
        # Loud rather than a silent no-op: a schedule that quietly audits nothing
        # every night is worse than one that visibly fails.
        return {
            "run_id": None,
            "sessions_created": 0,
            "status": "failed",
            "error": (
                "config.%s.opportunity_ids is empty - nothing to audit. Set it on "
                "the workflow definition to schedule this workflow." % SCHEDULE_DEFAULTS_KEY
            ),
        }

    if window is not None:
        window_start, window_end = window
    else:
        window_start, window_end = resolve_window(window_preset_for_cadence(cadence), date.today())

    sample_percentage = defaults.get("sample_percentage", 100)
    agent_override = defaults.get("agent_override") or {}
    # A ceiling on photos per field worker, the headless twin of the UI's "max per FLW".
    # Absent or <= 0 means no cap, which is the behaviour this template shipped with.
    try:
        max_per_flw = int(defaults.get("max_per_flw") or 0)
    except (TypeError, ValueError):
        max_per_flw = 0
    # Report what a run WOULD do and create nothing.
    #
    # Plain truthiness, deliberately, because the two ways of misreading this are not
    # equally costly. The dialog always sends a real boolean, but a value set by hand
    # through the API might be the STRING "true" - and a strict `is True` check would
    # read that as armed and spend real classifier budget on someone who asked for a
    # report. Truthiness errs the other way: the worst a stray "false" can do is decline
    # to create audits and tell you why.
    dry_run = bool(defaults.get("dry_run"))

    owner_id = definition.opportunity_id or (definition.opportunity_ids or [None])[0]
    if not owner_id:
        return {
            "run_id": None,
            "sessions_created": 0,
            "status": "failed",
            "error": "workflow has no owning opportunity",
        }

    llo_summary = " / ".join(sorted({(opp_meta.get(str(o)) or {}).get("llo") or str(o) for o in opp_ids}))
    wda = WorkflowDataAccess(access_token=access_token, opportunity_id=owner_id)
    try:
        run = wda.create_run(
            definition.id,
            opportunity_id=owner_id,
            period_start=window_start,
            period_end=window_end,
            initial_state={
                "window_start": window_start,
                "window_end": window_end,
                "selected_opps": opp_ids,
                "sample_percentage": sample_percentage,
                # Same key the render code writes, so the run reads back into the UI's
                # cap field rather than looking uncapped when reopened.
                "max_per_flw": max_per_flw or None,
                # Without these two the dashboard re-derives the agent from OPP_META and
                # the window from a default preset, so a scheduled run is shown as having
                # used a different classifier and a different period than it actually did.
                "agent_override": agent_override,
                "date_preset": "custom",
                # Mirrors what the render code writes, so a scheduled run reads the
                # same in the run-history table as a hand-triggered one.
                "llo_summary": llo_summary,
                # Which photo types this run covered. Without it a run that audited
                # equipment and wrap photos is indistinguishable in the history from a
                # weight-only one, and its lower machine-reviewed share looks like a
                # classifier failure rather than the extra photos it actually was.
                "image_paths": image_paths,
            },
        )
    finally:
        wda.close()

    # A REAL username. create_record puts this straight into the payload it POSTs to
    # /export/labs_record/, and a synthetic "scheduler" — matching no upstream user — made every
    # session creation fail with HTTP 500 while the identical criteria succeeded from the UI
    # (verified: UI run 15241 created 10 sessions / 386 images on the same window and sample).
    # Fall back to empty rather than inventing one; create_record omits the key when falsy.
    username = getattr(definition, "username", None) or (definition.data or {}).get("username") or ""

    sessions_created = 0
    errors = []
    empty = []  # opportunities whose window simply held no matching visits
    planned = []  # dry run only: what each opportunity WOULD have audited
    empty_sessions = 0  # created but with no images - see the blank count below
    for opp_id in opp_ids:
        meta = opp_meta.get(str(opp_id)) or {}
        scale = meta.get("scale") or DIGITAL
        agent_id = agent_override.get(str(opp_id)) or agent_for_scale.get(scale) or agent_for_scale[DIGITAL]
        criteria = _scheduled_criteria(
            opp_id=opp_id,
            opp_meta=opp_meta,
            window_start=window_start,
            window_end=window_end,
            sample_percentage=sample_percentage,
            image_rules=image_rules,
        )
        try:
            extra_kwargs = {}
            # A dry run selects even when uncapped, purely so it can report the volume an
            # armed run would produce - the number most worth seeing before arming one.
            if max_per_flw > 0 or dry_run:
                # Choose the visits here rather than letting the creation task select them,
                # because a cap cannot be expressed in AuditCriteria: count_per_flw applies
                # only to last_n_per_flw, which ignores the date window entirely.
                # request=, NOT access_token= on its own. AuditDataAccess.pipeline raises
                # "Request required for pipeline access" when self.request is None, and
                # get_visit_ids_for_audit reaches that pipeline - so an access_token alone
                # makes every capped or dry run die in selection. Every other headless
                # caller builds the mock request for exactly this reason.
                data_access = AuditDataAccess(opportunity_id=opp_id, request=create_mock_request(access_token, opp_id))
                try:
                    flw_visit_ids, capped_visit_ids = _capped_flw_visit_ids(
                        data_access=data_access,
                        opp_id=opp_id,
                        criteria=criteria,
                        # No cap means "take everything this worker has"; a sentinel keeps
                        # one code path instead of a second uncapped selection branch.
                        cap=max_per_flw if max_per_flw > 0 else UNCAPPED,
                        photo_form_names=photo_form_names,
                    )
                finally:
                    data_access.close()
                if not flw_visit_ids:
                    empty.append(opp_id)
                    continue
                # run_audit_creation only honours flw_visit_ids alongside
                # selected_flw_user_ids; without it the mapping is ignored and the run
                # silently reverts to uncapped.
                criteria["selected_flw_user_ids"] = sorted(flw_visit_ids)
                extra_kwargs["flw_visit_ids"] = flw_visit_ids
                # Passing the flat union too lets the task skip re-selecting visits.
                extra_kwargs["visit_ids"] = capped_visit_ids
                logger.info(
                    "kmc_image_audit opp %s: capped to %d photos across %d workers (cap %d)",
                    opp_id,
                    len(capped_visit_ids),
                    len(flw_visit_ids),
                    max_per_flw,
                )

            if dry_run:
                # Everything above has run for real - the selection, the form filter, the
                # cap, the rename guard - so this reports what an armed run would do
                # having actually asked live data. Only the audit creation is skipped.
                planned.append(
                    {
                        "opportunity_id": opp_id,
                        "llo": meta.get("llo") or str(opp_id),
                        "agent": agent_id,
                        "workers": len(extra_kwargs.get("flw_visit_ids") or {}) or None,
                        "photos": len(extra_kwargs.get("visit_ids") or []) or None,
                        "capped": bool(max_per_flw),
                    }
                )
                logger.info("kmc_image_audit DRY RUN opp %s: %s", opp_id, planned[-1])
                continue

            # One photo type, and it is the scoreable one: the single-agent call this
            # workflow has always made, left EXACTLY as it was. It is what the nightly
            # schedule runs unattended, and it is also the only shape that reports the
            # classifier by name on the run summary - the per-image-type path reports
            # "per-image-type" instead. Nothing here needed changing to add photo types,
            # so nothing here was changed.
            #
            # Anything else needs per-type reviewers, because a single ai_agent_id is
            # applied to EVERY image in the session: the scale classifier would be handed
            # equipment and wrap photos and would report each as a mismatch.
            review_kwargs = (
                {"ai_agent_id": agent_id}
                if image_paths == scoreable_paths and len(image_paths) == 1
                else {
                    "image_audits": _image_audits(image_paths=image_paths, image_types=image_types, agent_id=agent_id)
                }
            )
            eager = run_audit_creation.apply(
                kwargs={
                    "access_token": access_token,
                    "username": username,
                    "opportunities": [{"id": opp_id, "name": meta.get("llo") or str(opp_id)}],
                    "criteria": criteria,
                    "workflow_run_id": run.id,
                    **review_kwargs,
                    **extra_kwargs,
                }
            )
            if eager.successful() and isinstance(eager.result, dict):
                result = eager.result
                sessions = result.get("sessions") or []
                created = len(sessions)
                sessions_created += created

                # Some sessions can come back with no images, and they are worth counting
                # separately rather than reporting as ordinary audits.
                #
                # The cap selects on form name; whether a photo is actually present is
                # only known later, at extraction. So a worker whose capped visits all
                # turned out to have skipped the photo yields a session with 0 images -
                # nothing for a reviewer to open. run_audit_creation creates it anyway on
                # this path (the branch that groups from extracted images skips them, but
                # the pre-grouped branch a cap uses does not), and changing that is a
                # shared-behaviour question for its own PR - four existing tests encode
                # the current behaviour. Reporting it is this template's business either
                # way: silently counting them makes a run look more productive than it was.
                blank = sum(1 for s in sessions if not s.get("images"))
                if blank:
                    empty_sessions += blank
                    logger.info("kmc_image_audit opp %s: %d of %d sessions have no images", opp_id, blank, created)
                # A task can succeed having created nothing - either the window held no matching
                # visits, or the audit app declined. Distinguish them: an explicit failure inside a
                # successful task must not be swallowed, which is how a live HTTP 500 from the
                # record-creation API surfaced as {status: failed, errors: []} with nothing to act on.
                if not created:
                    if result.get("success") is False or result.get("error"):
                        errors.append(
                            _error_line(opp_id, meta, window_start, window_end, result.get("error") or result)
                        )
                    else:
                        empty.append(opp_id)
            else:
                errors.append(_error_line(opp_id, meta, window_start, window_end, eager.result))
        except Exception as exc:  # noqa: BLE001 - one bad opportunity must not end the batch
            logger.exception("kmc_image_audit scheduled run failed for opportunity %s", opp_id)
            errors.append(_error_line(opp_id, meta, window_start, window_end, exc))

    # An empty window is a legitimate outcome, not a failure: a weekly schedule over a quiet period
    # audits nothing and that is correct. Only report failure when something actually went wrong,
    # otherwise a scheduled run cries wolf until nobody reads it.
    if dry_run:
        # Its own status, so a report is never mistaken for a run that created nothing.
        # Errors still count: a dry run that could not even select is a real failure, and
        # is the cheapest possible warning that an armed run would fail the same way.
        status = "failed" if errors and not planned else "dry_run"
    elif sessions_created:
        status = "ready"
    elif errors:
        status = "failed"
    else:
        status = "empty"

    outcome = {
        "run_id": run.id,
        "sessions_created": sessions_created,
        "status": status,
        "errors": errors,
        "empty_opportunities": empty,
    }
    if empty_sessions:
        # Named rather than folded into sessions_created, so "20 sessions" cannot quietly
        # mean "17 reviewable ones and 3 nobody can open".
        outcome["sessions_without_images"] = empty_sessions
    outcome["image_paths"] = image_paths
    if dry_run:
        outcome["dry_run"] = True
        outcome["planned"] = planned

    # Record the outcome ON the run. Without this the run carries only the settings it
    # started with, so the run history shows a row with no hint of what happened and the
    # only account of a partial failure is a Celery log nobody reads. Best effort: losing
    # the summary must not turn a run that DID create audits into a failure.
    try:
        wda = WorkflowDataAccess(access_token=access_token, opportunity_id=owner_id)
        try:
            wda.update_run_state(
                run.id,
                {
                    "sessions_created": sessions_created,
                    "run_status": status,
                    "run_errors": errors,
                    "empty_opportunities": empty,
                    "sessions_without_images": empty_sessions,
                    "max_per_flw": max_per_flw or None,
                    "dry_run": dry_run,
                    "planned": planned,
                    "image_paths": image_paths,
                },
            )
        finally:
            wda.close()
    except Exception:  # noqa: BLE001 - the summary is diagnostics, not the deliverable
        logger.exception("kmc_image_audit could not record the outcome on run %s", run.id)

    logger.info("kmc_image_audit run %s finished: %s", run.id, outcome)
    return outcome


TEMPLATE["supports_default_run"] = True
TEMPLATE["run_default"] = run_default

# What the schedule dialog may write into config.schedule_defaults. Both are read by
# run_default above; declaring them here is what gives them an editing surface, so a
# schedule's volume can be changed without an MCP call or a release.
#
# max_per_flw matters most: the dashboard's own cap field writes RUN state, which a
# scheduled run never reads. Before this, a schedule created right after setting a cap on
# screen ran uncapped and said nothing about it.
TEMPLATE["schedule_options"] = [
    {
        "key": "opportunity_ids",
        "type": "multi_int",
        "label": "Opportunities to audit",
        # opp_names lives on this workflow's own config, so the list stays right per
        # definition instead of being frozen to OPP_META at import.
        "choices_from_config": "opp_names",
        "help": "Each selected opportunity gets its own audit, scored by the agent its scale "
        "hardware requires. Selecting none would audit nothing, so at least one is required.",
    },
    {
        "key": "image_paths",
        "type": "multi_str",
        "label": "Photo types to audit",
        # image_type_names lives on this workflow's own config, derived from IMAGE_TYPES,
        # so the dialog, the dashboard picker and run_default all offer the same set.
        "choices_from_config": "image_type_names",
        # Pre-ticked, so a schedule saved before this option existed does not have to
        # choose anything to keep saving, and keeps auditing exactly what it audited.
        "default": list(DEFAULT_IMAGE_PATHS),
        "help": (
            "Leave the weight photo alone to keep the audit exactly as it has been. Only "
            "the weight photo has a classifier: equipment and KMC-wrap photos are "
            "collected for human review and show as not reviewed, and they are left out "
            "of the machine pass rate so adding them does not read as a drop in quality. "
            "EHA has neither of those questions in its app, so selecting them adds nothing "
            "for EHA - the run summary reports what each opportunity actually yielded."
        ),
    },
    {
        "key": "max_per_flw",
        "type": "int",
        "label": "Max visits per field worker",
        "help": (
            "Blank means no cap. Sampling is proportional, so without a cap the busiest "
            "workers crowd out the rest and a run's size is unpredictable. The cap counts "
            "VISITS: with one photo type that is one photo each, but a visit carries one "
            "photo of every selected type, so N types multiply the photos a capped worker "
            "contributes."
        ),
        "min": 1,
        "max": 500,
    },
    {
        "key": "sample_percentage",
        "type": "int",
        "label": "Sample %",
        "help": "Random sample across the whole opportunity, applied before the cap. Not "
        "per worker, so at low percentages a quiet worker can be missed entirely and which "
        "workers appear varies between runs.",
        "min": 1,
        "max": 100,
    },
    {
        "key": "dry_run",
        "type": "bool",
        "label": "Dry run — report only, create no audits",
        "help": "Selects the photos and records the per-opportunity counts on the run, "
        "without creating audits or calling the AI. The safe way to check a schedule's "
        "settings and volume against live data before arming it.",
    },
]
