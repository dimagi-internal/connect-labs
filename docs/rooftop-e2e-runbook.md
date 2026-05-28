# Rooftop Surveys — end-to-end runbook ("FLW sees pins on phone")

The goal of Stage A is one outcome: **an FLW opens the Connect app and sees their
assigned rooftop pins, ready to navigate and survey.** This runbook walks the full
path. Steps marked **[automated]** are done by the labs app; **[gated]** steps need
a human/admin action the agent can't do autonomously (feature flag, org-admin web
UI, or the Android emulator).

The rooftop labs app is **deployed**: https://labs.connect.dimagi.com/rooftop-surveys/<opp_id>/setup/

---

## Step 1 — Connect program + opportunity  [atom-able]

Create via the ace-connect MCP (PM org `ai-demo-space`):

```
connect_create_program(organization_slug="ai-demo-space", name="Rooftop Verification (e2e)", delivery_type="ace", currency="USD", ...)
connect_create_opportunity(organization_slug="ai-demo-space", program_id=<id>, name="Rooftop Verification — Gwange", end_date="2026-09-30", deliver_app=<id>, learn_app=<id>, ...)
```

`connect_create_opportunity` requires deliver/learn app IDs (Step 3). For a real
run this is the `connect-opp-setup` ACE skill.

## Step 2 — Enable the MICROPLANNING feature flag on the opp  [gated: admin]

Microplanning (WorkArea model, web map, assignment, mobile case push) is behind
the `microplanning` feature flag (`commcare_connect/flags`). It must be enabled
for the opportunity. There is **no MCP atom** for this — enable via Django admin /
flags admin on production Connect, or a DB/flags-API action by someone with access.
**Until enabled, none of the microplanning surfaces appear.**

## Step 3 — A microplanning-aware deliver app  [gated: app build]

The CommCare deliver app must:
- present the FLW's assigned `work-area` cases (case type `work-area`), and
- submit a `deliver_unit` block carrying `work_area_id` (links the visit to the
  WorkArea; auto-advances status), and optionally a `work_area_update` block for
  the inaccessibility/substitution flow (status → REQUEST_FOR_INACCESSIBLE with
  reason + photo).

This is a specific app pattern (not the stock ACE deliver app). Build via Nova or
clone an existing microplanning-enabled app. Reference: `dimagi/commcare-connect`
`commcare_connect/form_receiver/processor.py` (the `work_area_update` /
`deliver_unit.work_area_id` handling) and `microplanning/serializers.py`
(`work-area` case shape).

## Step 4 — Generate the sampling frame  [automated, deployed]

In the labs app at `/rooftop-surveys/<opp_id>/setup/`:
1. Draw the intervention area (and optional comparison) on the satellite map.
2. Set frame config (defaults are the R-pilot values: 25 PSUs, 8+8, conf 0.7,
   area 9–330 m²).
3. **Preview frame** → footprints fetched from Overture (cached), sampled into
   PSUs + pins, rendered (red primaries / amber alternates within cluster hulls).
4. **Save frame** → persists `rooftop_area` + `rooftop_frame` LabsRecords.
5. **Download Connect import CSV** → the work-area import file (one tiny WorkArea
   per pin: centroid, ~16 m boundary square, building_count=1, expected_visit_count=1).

A ready-made sample for Gwange/Maiduguri (396 pins) is at
`/tmp/rooftop_artifacts/gwange_work_areas.csv` (regenerate any time from the lib;
see `commcare_connect/rooftop_surveys/sampling/frame.py` +
`workarea.py`).

## Step 5 — Load work areas into Connect microplanning  [gated: org-admin web, OR proposed API]

**Today (web):** as an org admin, open the opp's microplanning home
(`/<org>/microplanning/<opp_id>/`) → upload the CSV from Step 4 via "Import Work
Areas". This is an org-admin **web** action (no OAuth API), so it needs a logged-in
browser session — the agent can't drive it headless.

**Proposed (API):** `docs/rooftop-prod-workarea-write-api.md` specifies a
`POST /export/opportunity/<id>/work_areas/` endpoint (mirrors `LabsRecordDataView`,
`export` scope) so labs/ACE could push `workarea.to_api_payload(...)` directly.
Lands in `dimagi/commcare-connect` via human review.

## Step 6 — Assign work areas to a test FLW  [gated: org-admin web]

In microplanning home → assignment UI → assign the work-area group(s) to the FLW's
`OpportunityAccess`. `save_assignment` pushes the `work-area` cases to the opp's
CommCare HQ domain, owned by the FLW. (Optionally the proposed API collapses
create+assign+push into one call.)

## Step 7 — FLW device confirms the pins  [gated: emulator]

Register/invite a test FLW (`connect_send_flw_invite` / ACE test user), then drive
the Android emulator (ace-mobile MCP: `mobile_ensure_avd_running`,
`mobile_run_recipe`, `mobile_capture_ui_dump`) to log in as the FLW and confirm the
assigned `work-area` cases appear with their pins. **This screenshot is the proof
we reached the right starting point.**

---

## What's automated vs. gated (summary)

| Step | State |
|------|-------|
| 1. Program + opp | atom-able (needs apps from Step 3) |
| 2. MICROPLANNING flag | **gated** — admin, no atom |
| 3. Microplanning-aware deliver app | **gated** — app build |
| 4. Frame → CSV | **automated + deployed** |
| 5. Import CSV | **gated** — org-admin web (or proposed API) |
| 6. Assign to FLW | **gated** — org-admin web |
| 7. Emulator confirms | **gated** — emulator |

The labs half (Steps 4) is done end-to-end and verified locally (screenshots:
satellite map + 396 pins). The Connect half (Steps 2, 5, 6) is gated on
admin/web/emulator access the overnight agent run can't clear; the write-API draft
(Step 5) is the path to fully automating it.
