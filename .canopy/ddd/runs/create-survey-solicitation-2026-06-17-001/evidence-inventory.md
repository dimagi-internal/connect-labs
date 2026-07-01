# Evidence Inventory — create-survey-solicitation

Generated: 2026-06-17T23:10:00Z

## Summary

- documented: 2 items
- implemented: 9 items
- assumed: 0 items
- Total: 11 items

## Items

### [EV-001] Create-solicitation entry point from a micro-plan

- **kind:** implemented
- **ref:** connect_labs/microplans/views.py:1227-1228,1275-1276
- **summary:** The plan review + group pages set `create_solicitation_url = /solicitations/create/?source_program_id=<pid>&source_plan_id=<plan>|source_group_id=<grp>`.
- **claim_hint:** A program owner can launch a solicitation directly from an approved micro-plan.

### [EV-002] "Create solicitation" button renders live

- **kind:** implemented
- **ref:** connect_labs/templates/microplans/group.html:39, review.html (verified live: plan 4494 review page)
- **summary:** Button present on labs prod with href `source_plan_id=4494`.
- **claim_hint:** The recruit-a-firm action is reachable from the plan the owner just designed.

### [EV-003] Plan snapshotted into the solicitation at creation

- **kind:** implemented
- **ref:** solicitations/views.py:382-406 (\_snapshot_from_query) + forms.py:201-225
- **summary:** Create view snapshots the source plan into `plans_json`/`source_plan_ids_json`; verified live the form pre-seeds title + scope + `plans_json={plan_id:4494, name:'R6 — Attakar × Gura'}`.
- **claim_hint:** The R6 plan rides into the solicitation as a snapshotted coverage area.

### [EV-004] "Coverage areas (from micro-plan)" panel on create form

- **kind:** implemented
- **ref:** templates/solicitations/solicitation_form.html:247-252 (verified live: coverage_panel=true)
- **summary:** Create form lists the snapshotted plans with helper text; respondents will select the ones they can cover.
- **claim_hint:** The owner confirms the coverage area the firm will bid on before publishing.

### [EV-005] Respondent coverage selector ("Which areas can you cover?")

- **kind:** implemented
- **ref:** solicitations/forms.py:248-253 (ResponseForm.select_plans, CheckboxSelectMultiple)
- **summary:** Public response form adds a coverage checkbox list bound to the snapshot plans.
- **claim_hint:** An independent survey firm selects the specific coverage area(s) it can staff.

### [EV-006] Selected coverage captured on the response, gated

- **kind:** implemented
- **ref:** solicitations/views.py:619-644 + forms.py:308-322 (get_selected_plans)
- **summary:** Response stores `selected_plan_names` resolved against the authoritative snapshot; submit blocked unless ≥1 coverage area selected.
- **claim_hint:** The firm's application is bound to a concrete coverage area, captured for the reviewer.

### [EV-007] R6 two-arm plan is the real coverage area

- **kind:** implemented
- **ref:** microplans/study_seed.py + MCP microplans_study_ensure (live, plan 4494)
- **summary:** R6 = single plan, input_areas both wards arm-tagged (Attakar=intervention 403, Gura=comparison 437), 840 work areas (596 primary/244 alternate).
- **claim_hint:** The coverage area offered to the firm is the exact two-arm sample the household survey runs — the same plan VM R6 consumes.

### [EV-008] The missing middle between two demos

- **kind:** documented
- **ref:** .canopy/ddd/context.md (## This run) + verified-monitoring/demo_config.json
- **summary:** R6 plan grounds VM Round 6 records (68.1% verified vs 8.9% self-report, ~300 households/round). The firm was previously only narrative framing.
- **claim_hint:** Recruiting the firm is the missing middle connecting study-design to verified-monitoring.

### [EV-009] Award→provision→run is a DEFERRED capability gap

- **kind:** implemented (absence verified)
- **ref:** grep: no provision/create_opportunity/WorkArea-push in solicitations/ (only award_response in data_access.py)
- **summary:** v1 captures the response for review; no code provisions a Connect opportunity or pushes work areas on award. Award flow untouched by #616.
- **claim_hint:** Award→provision→enumerators-run-R6 is aspirational end-state, NOT a shipped mechanism. (Honesty constraint.)

### [EV-010] Labs-only routing fix (PR #618)

- **kind:** implemented
- **ref:** solicitations/data_access.py:43-89 (PR #618) + labs/synthetic/local_records_backend.py
- **summary:** Before #618, solicitations on labs-only programs (10008) hit prod and 404'd; create-from-plan failed on the demo program. #618 routes them to the local-records backend.
- **claim_hint:** The shipped create-from-plan flow now actually persists on the synthetic demo program.

### [EV-011] Entry point is contract-tested

- **kind:** documented
- **ref:** microplans/tests/test_create_solicitation_link.py:26-72
- **summary:** Tests assert the review + group contexts emit `create_solicitation_url` with the source_program_id + source_plan_id/source_group_id contract.
- **claim_hint:** The create-from-plan entry point is contract-tested, not incidental.
