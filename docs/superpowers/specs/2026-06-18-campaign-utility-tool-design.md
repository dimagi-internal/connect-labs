# Campaign Utility Tool — Design

**Status:** Draft for review
**Date:** 2026-06-18
**Source artifacts:** [Spec doc](https://drive.google.com/drive/folders/1cNpjEn_Smy6mHx5rWzflp20hbx65dlbG) (Spec: Campaign Management Utility), Data Model PDF (12 datasets / 6 domains / 2 owners), and the standalone HTML prototype (`Campaign Utility Tool (standalone).html`).

---

## 1. Context & Goals

Campaign operations (vaccination / health campaigns) are today fragmented across CommCare, KYC systems, payment platforms, GIS tools, and spreadsheets. The **Campaign Utility Tool** is a single, tab-based orchestration and visibility layer over those systems — a comprehensive real-time view of a campaign plus the administrative workflows (payment review/approval, KYC/fraud review, activity & microplan management, reporting) on top of it.

This build is a **standalone solution inside the Connect Labs repo** that may later graduate into its own app. It deliberately keeps **minimal coupling to the rest of Connect Labs**: it reuses only (a) the workflow system's **React-via-Babel render mechanism**, (b) the **CommCare HQ OAuth / integration** plumbing, and (c) the microplans **map/boundary** layer. It has its **own authentication, its own users/RBAC, and its own Django ORM data** — it does **not** use Connect OAuth, the labs context middleware, or the LabsRecord API.

**Near-term goal:** a polished, interactive demo on **synthetic data** that looks and feels like a real product, with real persistence (Django ORM) so demo actions stick. External integrations (KYC provider, payment gateway, live CommCare reads) are stubbed/seeded now and wired later.

### Design principles
- **Pixel-faithful to the prototype.** The prototype's design system and UI are the source of truth and are shipped **verbatim**; we do not re-style or re-lay-out.
- **Self-contained.** Own URL namespace, own login, own data. Removing the rest of labs should not break this app (beyond the three named reuse points).
- **Real where it's cheap, stubbed where it's not.** Auth, RBAC, and CRUD persistence are real. KYC/payment/CommCare network integrations are simulated for the demo.

---

## 2. Scope

### In scope — Phase 1 MVP (all tabs)
1. **Overview** — campaign KPIs, funder contributions, KYC/payment donuts, workforce distribution, fraud/verification alerts, filters.
2. **Workers**
   - **Payments** sub-tab — list, filters, bulk approve/reject, per-worker payment drawer with **per-day** approve/reject/undo; approval blocked while fraud flags exist.
   - **KYC** sub-tab — list, status filters, KYC review modal (documents, fraud/linked-records panel, investigation notes), CSV upload (simulated), submit-for-verification.
   - **Profiles** sub-tab — master/detail with participation, verification timeline, attendance heat-grid, registration/fraud panel.
3. **Activity**
   - **Activity Details** sub-tab — list, filters, activity drawer (assigned workers), create/edit activity, "sync to CommCare" flag (simulated).
   - **Microplanning & Budget** sub-tab — region-grouped expandable tables for Microplans / Workforce / Targets / Budget; microplan drawer; create/edit microplan, target, budget modals; **coverage map** (real boundaries).
4. **Reporting & Monitoring** — trend chart, household monitoring, performance donut, geographic coverage table, custom report modal, export.
5. **System Administration**
   - **User Management** sub-tab — **real**: whitelist add/remove, role assignment, activate/deactivate, role matrix, activity log.
   - **Connection Settings** sub-tab — external connection CRUD + simulated test/sync.
6. **Training Hub** — public (no login) video/resource hub + admin upload/publish/archive; low-bandwidth mode.

### Real (server-enforced) vs simulated
- **Real:** CommCare HQ OAuth login, whitelist gate, RBAC (server-side), all CRUD persistence (Django ORM), coverage map (real Nigeria boundaries), audit log.
- **Simulated for the demo:** KYC provider submission/results, payment gateway/disbursement, live CommCare worker/case reads (seeded instead), external connection "test"/sync, account-activation/password-reset emails (we use CommCare OAuth, so there are no app-managed passwords).

### Out of scope (per the source spec, restated)
KYC provider replacement; payment disbursement execution; worker registration (stays in CommCare); direct writes to external systems; advanced analytics/forecasting; offline support; native mobile apps; training assessments/certifications; **multi-country workspaces** (schema models it; demo ships one **Nigeria** workspace); fraud-investigation as a separate Phase-2 module (basic investigation notes are in MVP KYC review; the standalone Phase-2 KYC investigation tab is deferred).

### Scope boundaries confirmed with stakeholder
- Account auth is **CommCare HQ OAuth** — no app-managed passwords, no invite/activation emails. "Whitelisting" = adding a CommCare username/email to the allowlist with a role.
- Bootstrap: **any `@dimagi.com` CommCare user auto-provisions as Campaign Administrator**; all other users must be explicitly whitelisted by an admin.
- Single **Nigeria** country workspace for the demo; login gates on the in-app whitelist only (not CommCare domain membership).

---

## 3. Architecture Overview

A new Django app `commcare_connect/campaign/`, mounted at `/campaign/`, with its own login, its own ORM data, and the prototype shipped as a static React SPA.

```
Browser (React SPA, prototype verbatim, Babel-in-browser)
   │  fetch JSON
   ▼
campaign/ Django app
   ├─ auth: CommCare HQ OAuth + whitelist + RBAC   (own session: campaign_oauth)
   ├─ views: page shell + JSON API endpoints (RBAC-enforced)
   ├─ services: data access over its own ORM models
   └─ models: Campaign, Worker, KycRecord, Payment, Activity, Microplan,
              Connection, AuditLog, CampaignUser (+ supporting)
   │
   ├─ reuse → microplans.core.admin_boundaries (Nigeria State/LGA/Ward)  [coverage map]
   ├─ reuse → labs.integrations.commcare (OAuth views/client pattern)    [login]
   └─ reuse → React/ReactDOM/Babel + connect_map.js static               [render]
```

### Isolation from the rest of labs
- **No** Connect OAuth, **no** `LabsContextMiddleware`, **no** `LabsRecordAPIClient`.
- Its own OAuth-session middleware **scoped to `/campaign/`** paths (mirrors `labs/oauth_session.py`, but reads `session["campaign_oauth"]` and never touches `labs_oauth`).
- Its own base template + nav (the prototype's `shell.jsx`), not the labs base nav.

### Reuse ledger
| Reused (allowed) | How |
|---|---|
| React / ReactDOM / Babel standalone + `static/maps/connect_map.js`, `mapboxgl`, `MAPBOX_TOKEN` | Same loading pattern as `templates/workflow/run.html`; the prototype is already authored in this idiom |
| CommCare HQ OAuth (`labs/integrations/commcare/oauth_views.py`, `api_client.py`) | Pattern copied/adapted into `campaign/auth/` with a whitelist check in the callback |
| `microplans.core.admin_boundaries` + `/microplans/boundaries/viewport/` contract | Coverage map: real Nigeria State→LGA→Ward boundaries |
| **Built fresh** | The `campaign/` app, all 8 tabs (prototype verbatim), ORM models + migrations, JSON APIs, RBAC enforcement, synthetic seeder |
| **Not reused** | microplans plan/sampling engine (different domain); labs base nav; Connect OAuth; LabsRecord |
| **Future workflow hook** | Payment-approval & KYC-review are natural future workflow runs; CommCare-form→metrics is a natural pipeline. Data access is shaped so these can populate the same models later. Not built now. |

---

## 4. Authentication, Identity & RBAC

### 4.1 Login (CommCare HQ OAuth)
Authorization-code + PKCE against CommCare HQ (`settings.COMMCARE_HQ_URL`, `/oauth/authorize/` & `/oauth/token/`, scope `access_apis`), adapted from `labs/integrations/commcare/oauth_views.py`.

Routes (own namespace `campaign`):
- `/campaign/login/` — login landing
- `/campaign/login/initiate/` — start OAuth (PKCE, state)
- `/campaign/login/callback/` — exchange code → token; **fetch identity → whitelist check → provision/login**
- `/campaign/logout/`

Session: `request.session["campaign_oauth"] = {access_token, refresh_token, expires_at, token_type, identity: {username, email, name, domains}}`. A `/campaign/`-scoped session middleware refreshes/expies the token (copy of `labs/oauth_session.py`, different session key + path prefix).

### 4.2 Identity
After token exchange, GET `{COMMCARE_HQ_URL}/api/v0.5/identity/` → `{username, email, first_name, last_name, domains[]}`.

### 4.3 Whitelist + provisioning (callback logic)
```
identity = commcare /api/v0.5/identity/
email = identity.email ; username = identity.username
if email endswith one of settings.CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS (default ["dimagi.com"]):
    user = get_or_create CampaignUser(username), role=ADMIN, active=True   # auto-provision
elif CampaignUser exists, active, for username/email:
    user = that CampaignUser
else:
    deny → friendly "not authorized" page (no session created)
login Django user (users.User, get-or-created from CommCare identity), attach campaign session
write AuditLog(login)
```

### 4.4 Roles (the spec's five)
`CampaignUser.role ∈ { campaign_admin, payment_admin, compliance_admin, operations_manager, reporting_user }`.

RBAC matrix (module × role → permission set), from the source spec/PDF:

| Role | Overview | Workers | KYC & Verification | Payments | Activities | Planning & Budget | Reporting | User Mgmt |
|---|---|---|---|---|---|---|---|---|
| Campaign Admin | Full | Full | Full | Full | Full | Full | Full | Full |
| Payment Admin | View | View | — | View + Approve | — | — | View, Export | — |
| Compliance Admin | View | View | View, Create, Edit, Approve | — | — | — | View, Export | — |
| Operations Manager | View | View | View | View | Create, Edit, Manage | View | View, Export | — |
| Reporting User | View | View | View | View | View | View | View, Export | — |

Permission verbs: `view, create, edit, approve, manage, export, delete` (`Full = all`).

### 4.5 Enforcement — server-side is the gate
- A `campaign_login_required` mixin/decorator (auth + whitelist + token-fresh) on all `/campaign/` views except `/campaign/login/*`, `/campaign/logout/`, and the **public** Training Hub view/endpoints.
- A `require_perm(module, verb)` check on every mutating JSON endpoint and read endpoint that carries restricted data. The endpoint derives the role from the session's `CampaignUser`.
- The frontend keeps `perms.js` for **show/hide only** (UX); it is **not** trusted. The same matrix is encoded server-side (single source of truth in Python; `perms.js` mirrors it).
- User Management endpoints require `campaign_admin`.

---

## 5. Data Model (Django ORM)

New app models with migrations. Naming maps 1:1 to the prototype's `data.js` shapes (so React components are unchanged) and to the Data Model PDF's 12 datasets. Money stored as integers (minor units / whole NGN as in the prototype). All records carry `campaign` FK (and most carry `region`/`lga`) for filtering.

> Ownership note from the PDF: "CommCare HQ-owned" datasets (Campaign, Region, Donor, Worker, KYC, App User) are seeded locally for the demo and later sourced from CommCare. "Utility-owned" datasets (Payment, Activity, Microplan, Reporting, Audit Log, Connection) are authoritative in this app.

### Core / geography
- **Workspace** — `id, country (default "Nigeria"), name, slug`. One row for the demo.
- **Campaign** — `id, workspace, name, code, round, country, status (Active|Planned|Completed), period_start, period_end, days_elapsed, days_total, target_pop`.
- **Region** — `id, campaign, name (state), lgas (JSON list), settlements, wards`.
- **Donor** — `id, campaign, name, short, committed (int), color (hex)`.
- **WorkerRole** — `id, campaign, name, rate (int daily)`.

### Workforce / compliance
- **Worker** (`W#####`) — `id, campaign, first, last, name, gender (M|F), phone, region(FK), lga, role(FK WorkerRole), rate, days_worked, days_approved, amount, kyc (approved|pending|review|rejected), pay (paid|approved|pending|rejected|hold), bank, acct, nin, passport (nullable), enrolled (date), attendance (%), prior_campaigns, duplicate (bool), dup_with (nullable)`.
- **WorkerFraudFlag** — `worker(FK), rule (str), linked_worker(FK nullable), shared (nin|acct|phone|photo)`. (Encodes `fraudRules[]` + `linked[]`.)
- **KycRecord** — `worker(OneToOne), status, documents (JSON: [{type,status}]), investigation (JSON: {status, notes:[{at,by,text}], outcome} nullable), verification_history (JSON: [{date,event,by,result}])`.

### Financial / operational
- **Payment** — `worker(FK), campaign, status (paid|approved|pending|rejected|hold), amount, paid_amount, pending_amount, flag (nullable)`.
- **PaymentDay** — `payment(FK), date, units, rate, amount, status (approved|pending|rejected), flag (nullable)`. (Encodes `dailyRecords[]` / `dailyForWorker`.) Approval/rejection of a day persists here; "approve & queue" rolls up to `Payment.status`. Guard: blocked if worker has open fraud flags or `kyc == rejected`; auto-`hold` if `kyc ∈ {pending, review}`.
- **Activity** (`ACT-##`) — `id, campaign, name, donor(FK), region(FK), status (Active|At risk|Planned|Completed), start, end, requests, workers, target, reached, synced (bool — CommCare sync flag)`.
- **ActivityAssignment** — `activity(FK), worker(FK)` (for the assigned-workers table).
- **Microplan** (`MP-###`) — `id, campaign, region(FK), lga, settlements, wards, planned_wf, actual_wf, roles (JSON: [{role_id, role, rate, planned, actual}]), budget, spent, planned_to_date, target, objective, goal_pct, reached, doses, doses_used, cold_boxes, vehicles, status (On track|Behind|At risk|Planned), owner, updated`. Derived (computed, not stored): `fill = actual_wf/planned_wf`, `cov = reached/objective`, `util = spent/budget`, `objective = round(target*goal_pct/100)`.

### Reporting
- **ReportDay** — `campaign, day, enrolled, attended, paid`.
- **HouseholdStats** — `campaign, registered, visited, members, members_reached, coverage (JSON: [{region, hh, visited}])`.
- **CustomReport** — `campaign, type, date_range, group_by, columns (JSON)`.

### Access / integration
- **CampaignUser** — `user(FK users.User), campaign/workspace, commcare_username, email, name, role, scope (All regions|<region>), status (active|inactive|deactivated), last_login, invited_by(FK nullable), created_at`.
- **Connection** — `id, workspace, name, purpose (kyc|payments|reporting|data|other), system_type, icon, endpoint (URL), auth (api_key|bearer|basic), credential (encrypted/masked), status (connected|error|disabled), last_sync, freq, records, error (nullable)`.
- **ConnectionSyncEvent** — `connection(FK), at, result, rows, dur`.
- **TrainingVideo** — `id, workspace, title, topic, role, lang, dur, size, views, status (published|draft|archived), color (gradient), media_ref (nullable)`.
- **AuditLog** — `id, workspace, at, user(FK), action, module (workers|payments|kyc|activities|planning|reporting|users|connections|auth), ip (masked)`.

---

## 6. Frontend

### 6.1 Verbatim prototype, Babel-in-browser
A Django view renders `templates/campaign/app.html`:
- `<head>`: Work Sans `@font-face` (self-hosted woff2), Font Awesome 6.5.1 CSS, the authored `@keyframes` (`cutFade`, `cutPop`, `cutSlide`, `cutToast` — authored fresh per the prototype analysis), and `window.MAPBOX_TOKEN` / `window.BOUNDARY_VIEWPORT_URL`.
- `<div id="root">` mount node.
- Loads React/ReactDOM/Babel + `connect_map.js` + `mapbox-gl` (same sources as `workflow/run.html`).
- Loads the prototype modules as `<script type="text/babel">` **in dependency order**:
  `data-api.js` → `perms.js` → `primitives.jsx` → `shell.jsx` → tab modules (`tab_overview`, `tab_workers`, `tab_workers_kyc`, `tab_workers_profile`, `tab_activity`, `tab_planning`, `tab_planning_detail`, `tab_reporting`, `tab_users`, `tab_connections`, `tab_training`) → `app.jsx`.
- **Dropped from the prototype:** the bundler wrapper and the dev-only `tweaks-panel.jsx` (its seeded config — accent, density, scenario, showAlerts — becomes app defaults).

### 6.2 The data seam — `data-api.js` replaces `data.js`
The prototype's `data.js` (PRNG-seeded mock data + helpers `summarize`, `money`, `moneyK`, `num`, `sharedLabel`) is replaced by `data-api.js`, which:
- Exposes the **same `window.CUT_DATA` shape and helper functions** so no component changes.
- Loads initial data from a JSON blob injected via `{{ campaign_bootstrap|json_script:"campaign-data" }}` (first paint with no spinner), then refreshes/mutates via the JSON API.
- Routes mutations (approve payment, review KYC, create activity, edit microplan, connection CRUD, user CRUD) to the API and updates local state on success (with the existing toast confirmations).

### 6.3 Design system
Preserved exactly: the `CUTC` color constants (`purple #16006D`, `body #5F6A7D`, borders, surface), the four selectable accents (default CommCare blue `#5D70D2`) via `--accent*` CSS vars, semantic colors, Work Sans typography, 12px card radius, pill badges, the full `primitives.jsx` component kit (Button, Card, Stat, Badge, Progress, Donut, PillTabs, Table, Modal, Drawer, Toast, Avatar, Empty, Check) and `shell.jsx` (TopBar, Sidebar, Dropdown, Page, PageHead). This kit is the app's reusable design foundation for future tabs.

### 6.4 Charts & map
- Charts stay **hand-rolled SVG** (donuts, trend area/line, bars) exactly as in the prototype — no charting dependency added.
- **Coverage map upgrade:** where the prototype stubs "View map", render a real Mapbox map via `window.ConnectMap`, shaded by coverage %, using Nigeria State/LGA boundaries. To stay isolated from the microplans urlconf, the app exposes its **own** `GET /campaign/api/boundaries/viewport/` endpoint (`window.BOUNDARY_VIEWPORT_URL`) that imports only the pure `microplans.core.admin_boundaries` resolver — no dependency on microplans views/URLs. This satisfies the spec's geographic-coverage requirement and reuses existing boundary infra.

---

## 7. Backend API Surface (JSON, RBAC-enforced)

All under `/campaign/api/`. Reads return the `data.js`-shaped objects; writes return the updated object + a toast message. Every endpoint checks `campaign_login_required` (except public training) and `require_perm`.

- `GET  /api/bootstrap/` — full initial payload (campaign, regions, donors, roles, workers, summaries, activities, microplans, report days, households, connections, users, videos) filtered by role/scope. (Also inlined via `json_script` for first paint.)
- `GET  /api/workers/` `?status&role&region&fraud&q` — payments/worker list.
- `GET  /api/workers/<id>/` — profile + daily payment breakdown + history.
- `POST /api/payments/approve/` `{worker_ids[]}` / `/reject/` — bulk; RBAC `payments:approve`.
- `POST /api/payments/<worker_id>/day/` `{date, action: approve|reject|undo}` — per-day.
- `POST /api/payments/<worker_id>/queue/` — approve & queue (guarded by fraud/KYC).
- `GET  /api/kyc/` `?status&q` ; `POST /api/kyc/<worker_id>/review/` `{decision, investigation?}` ; `POST /api/kyc/<worker_id>/submit/` (simulated provider) ; `POST /api/kyc/upload/` (simulated CSV: returns valid/duplicate/invalid tallies).
- `GET/POST /api/activities/` ; `POST /api/activities/<id>/sync/` (simulated CommCare sync; flips `synced`).
- `GET/POST /api/microplans/` ; `POST /api/microplans/<id>/` (edit) ; `POST /api/targets/`, `/api/budgets/`.
- `GET  /api/reporting/` `?campaign&date_range&region&role&donor` ; `POST /api/reporting/custom/` ; `GET /api/reporting/export/` (CSV).
- `GET/POST /api/users/`, `POST /api/users/<id>/role/`, `/api/users/<id>/status/`, `GET /api/users/audit/` — **admin only**.
- `GET/POST /api/connections/`, `POST /api/connections/<id>/test/` (simulated), `GET /api/connections/sync-history/`.
- `GET  /api/boundaries/viewport/` `?bbox&zoom&level` — Nigeria admin boundaries for the coverage map (wraps `microplans.core.admin_boundaries`).
- **Public:** `GET /campaign/training/`, `GET /api/public/training/` (published videos only); admin training mutations require auth.

---

## 8. Synthetic / Seed Data

A management command `python manage.py seed_campaign_demo` (idempotent, `--fresh` to reset) reproduces the prototype's seeded dataset into the ORM:
- Campaign: **Measles–Rubella Vaccination Campaign**, `MR-2026-R2`, Round 2, Nigeria, ~28-day window.
- 5 regions (Kano, Kaduna, Sokoto, Bauchi, Borno) with LGAs; 4 donors (Gavi, BMGF, UNICEF, WHO); 5 worker roles with rates.
- 64 workers with **7 fraud pairs** sharing an identifier (NIN/acct/phone/photo); per-worker daily payment records; KYC docs + a couple of investigations.
- 6 activities; per-LGA microplans (workforce by role, budget/spend, doses/cold-boxes/vehicles, targets); 16 report days; household stats.
- Seed connections (CommCare HQ data source, KYC provider, payment gateway, GIS) with mixed statuses; seed training videos; seed the role matrix; one Nigeria workspace.

Uses a fixed PRNG seed (matching the prototype's `mulberry32(20260603)` intent) for reproducibility.

---

## 9. Wiring

- `config/settings/base.py`: add `commcare_connect.campaign` to `LOCAL_APPS`; add `CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS = env.list(..., default=["dimagi.com"])`. CommCare OAuth client vars already exist (`COMMCARE_OAUTH_CLIENT_ID/SECRET`, `COMMCARE_HQ_URL`).
- `config/settings/local.py` + `labs_aws.py`: insert `commcare_connect.campaign.middleware.CampaignOAuthSessionMiddleware` after `AuthenticationMiddleware` (scoped to `/campaign/`; independent of labs middleware).
- `config/urls.py`: `path("campaign/", include("commcare_connect.campaign.urls", namespace="campaign"))`.
- Static under `commcare_connect/static/campaign/`; templates under `commcare_connect/templates/campaign/`.

### Directory layout
```
commcare_connect/campaign/
  __init__.py  apps.py  urls.py  middleware.py
  auth/  (oauth_views.py, identity.py, whitelist.py, decorators.py)
  models.py
  api/  (views per domain: overview, workers, payments, kyc, activities, planning, reporting, users, connections, training)
  services/  (data_access.py, rbac.py, seed.py, boundaries.py [thin wrapper over microplans.core.admin_boundaries])
  migrations/
  management/commands/seed_campaign_demo.py
  tests/
commcare_connect/static/campaign/
  data-api.js perms.js primitives.jsx shell.jsx app.jsx
  tab_overview.jsx tab_workers.jsx tab_workers_kyc.jsx tab_workers_profile.jsx
  tab_activity.jsx tab_planning.jsx tab_planning_detail.jsx tab_reporting.jsx
  tab_users.jsx tab_connections.jsx tab_training.jsx
  campaign.css (fonts + keyframes)
commcare_connect/templates/campaign/
  app.html  login.html  training_public.html  not_authorized.html
```

---

## 10. Testing

- **Unit:** RBAC matrix (each role × module × verb), payment-approval guards (fraud/KYC blocks, per-day rollup), microplan derived metrics, whitelist/bootstrap provisioning (`@dimagi.com` auto-admin; non-listed denied), seeder idempotency.
- **API:** each endpoint's permission gate (403 for wrong role), CRUD round-trips persist, public training endpoint requires no auth, admin training mutation does.
- **Auth (mocked):** OAuth callback with mocked CommCare token + `/identity/` → provisioning paths (admin, whitelisted, denied); token refresh/expiry in the campaign middleware.
- **Frontend smoke (gstack browse):** after deploy, load `/campaign/`, confirm the bundle mounts, tabs switch, a payment approval persists across reload, the coverage map renders real boundaries.
- Run via `pytest commcare_connect/campaign/` (GDAL/GEOS env vars required locally for the boundary code path).

---

## 11. Future Hooks (not built now)
- **Workflow:** payment-approval and KYC-review as workflow runs; CommCare-form→worker/payment/reporting metrics as a pipeline populating the same models.
- **Live CommCare reads:** swap the seeded Worker/KYC/Region/Donor data for live CommCare Case/Form API reads using the existing CommCare API client + the user's `campaign_oauth` token.
- **Real integrations:** KYC provider, payment gateway via the Connection configs.
- **Multi-country workspaces** + CommCare-domain-scoped login.
- **Phase 2:** standalone fraud-investigation module, custom-report builder, OAuth2 connection auth + retry/alerting, training-hub low-bandwidth delivery hardening.

---

## 12. Open Questions
1. **Worker/KYC source for the demo** — confirmed seeded now; is there a specific CommCare domain we should later read from (deliver_app `cc_domain`)? Deferred.
2. **Credential storage for Connections** — masked in UI; store encrypted-at-rest or placeholder-only for the demo? Proposed: placeholder/masked only (no real secrets) since connection testing is simulated.
3. **Coverage map data** — coverage % per region comes from `HouseholdStats.coverage` / microplan rollups; confirm the exact metric to shade by (visited/registered vs reached/objective). Proposed: household visited/registered for the Reporting map, microplan reached/objective for the Planning map.
