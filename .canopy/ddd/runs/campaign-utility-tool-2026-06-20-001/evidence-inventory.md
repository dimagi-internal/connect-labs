# Evidence Inventory — Campaign Utility Tool

Generated: 2026-06-20T23:40:00Z

## Summary

- documented: 5 items (EV-001..005) — the requirements (Spec doc, Data Model PDF, design doc)
- implemented: 9 items (EV-006..014) — shipped + deployed code/workflows
- assumed: 2 items (EV-015, EV-016) — gaps that gate rendering
- Total: 16 items

## Items

### [EV-001] The problem is fragmentation (documented)

Spec doc: campaign ops fragmented across CommCare, KYC systems, payment platforms, reporting/GIS, spreadsheets → the tool is the single unified interface.

### [EV-002] The tool orchestrates, it doesn't replace (documented)

Spec §2: worker registration stays in CommCare; KYC stays external; the tool reads via APIs and triggers writes through existing system APIs.

### [EV-003] A precise 2-owner data model (documented)

Data Model PDF: CommCare HQ owns Campaign/Region/Donor/Worker/KYC/AppUser; the Utility owns Payment/Activity/Microplan/Reporting/AuditLog/Connection; cross-owner FKs need a sync/API contract.

### [EV-004] The compelling workflows (documented)

Spec §3-5: Overview, Payments (review+approve, fraud flagging), KYC validation, Microplanning & Budget, Reporting + export.

### [EV-005] Architecture: isolated app, reads roster from CommCare (documented)

Design doc: /campaign/ Django app, CommCare OAuth + RBAC, later reads Worker/KYC/Region/Donor from live CommCare Case/Form API.

### [EV-006] Workers ARE CommCare cases on real geography (implemented)

WorkerCase + generator: synthetic CommCare cases on real Nigeria AdminBoundary geography, national-scale, deterministic.

### [EV-007] Read FROM the CommCare Case API (implemented)

Synthetic CommCare project space + Case-API short-circuit; the demo path equals the production read path; go-real is a per-domain flip.

### [EV-008] Real fraud guard on payments + KYC (implemented)

Approving a payment is server-side-blocked when a worker has open fraud flags or rejected KYC; writes land on the case.

### [EV-009] True national scale (implemented)

National pipeline across 37 states; 50k-worker bootstrap cliff measured (~38 MB) and fixed via summary bootstrap + paginated endpoint.

### [EV-010] One-screen real-time overview (implemented)

Server-computed KPIs over all workers — donuts, progress, workforce — accurate at any scale.

### [EV-011] Reporting export + coverage map (implemented)

CSV export + custom-report builder (5 types) + geographic coverage map (region choropleth + worker GPS), reusing the labs Mapbox stack.

### [EV-012] RBAC + audit logging (implemented)

CommCare OAuth + 5-role RBAC + audit rows on every privileged action.

### [EV-013] Live + deployed (implemented)

Running at /campaign/ (health 200); national campaign viewable at /campaign/?campaign=MR-NAT-2026.

### [EV-014] National data on demand via MCP (implemented)

campaign_build_national MCP tool stands up the national dataset in-app against real geography.

### [EV-015] GAP: national campaign may not be built in labs yet (assumed)

Until MR-NAT-2026 is built in labs prod, /campaign/ shows the 64-worker demo; the map needs the national campaign populated. **Gates the map + scale scenes.**

### [EV-016] GAP: recorder needs CommCare-OAuth auth path (assumed)

The campaign app uses CommCare OAuth (separate from the labs session); the recorder must establish/import that session before capturing /campaign/.

Next step: run /ddd-why-brief to draft the why-brief from this evidence.
