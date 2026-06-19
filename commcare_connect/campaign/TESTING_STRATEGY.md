# Campaign Utility Tool — Testing Strategy

**Status:** Active
**Date:** 2026-06-19
**Owner doc location:** This file lives **inside the app** (`commcare_connect/campaign/`) by design. The
Campaign Utility Tool may graduate out of `connect-labs` into its own service; every testing decision
here is made so the test suite travels with the app and keeps working with **zero dependency on
labs-specific test scaffolding**. If you move `commcare_connect/campaign/` somewhere else, the tests in
`campaign/tests/` must come with it and still pass.

Companion docs: the design spec at `docs/superpowers/specs/2026-06-18-campaign-utility-tool-design.md`
(repo-level, may NOT travel — treat its §10 "Testing" as superseded by this file).

---

## 0. TL;DR — what to read if you read nothing else

1. **The unit tests are green and the dangerous bugs were all invisible to them.** The three
   highest-severity defects in the entire build — CSRF breaks every write, the labs OAuth-session
   middleware logs campaign users out, and the OAuth-callback `IntegrityError` — were caught by live
   browsing and whole-branch review, **never by the ~120 passing unit tests**, because the test
   settings don't reproduce the production middleware stack or CSRF enforcement. **The strategy is
   built around closing that live/test gap, not adding more of the tests that already missed.**

2. **~5,400 LOC of React ship verbatim via Babel-in-browser with zero automated coverage.** Its
   correctness rests on (a) byte-fidelity to an approved prototype and (b) the serializer emitting the
   exact field names the JSX reads. A one-key typo silently blanks the UI (already happened:
   `role`/`region`). We pin this with a **serializer↔UI contract test** + **visual/e2e characterization**,
   not by refactoring the JSX.

3. **Lock the dual RBAC matrix against drift.** `services/rbac.py` (server, the real gate) and
   `static/campaign/perms.js` (client, show/hide only) are two hand-maintained permission matrices in
   **different role vocabularies**, bridged at runtime by `app.jsx`'s `ROLE_DISPLAY` map. The bridge
   works today (non-admin roles resolve correctly — verified by reading `app.jsx`), but the two
   matrices have **already drifted**: `perms.js` omits the `training` module entirely, so it returns
   `false` for `training` for every role while `rbac.py` grants `campaign_admin` full training access.
   Harmless now (training tab is unbuilt), but it's the silent-drift class. Fix = a Python contract test
   locking the two matrices together + reconcile `training`; longer-term, make the server the single
   source. See §7.1.

4. **Real vs stubbed is the testability map.** Auth, RBAC, and ORM persistence are real and get full
   functional + security tests. KYC, payments, live CommCare reads, and connection-testing are
   **stubs** — we test them as local state machines and mark the integration seam with a pending
   contract test, but we do **not** test them as if an external system exists.

---

## 0.5 Implementation status (2026-06-19)

Much of this strategy is now **built and green** (campaign suite: 119 passed, 3 stub-skips, up from 65).
Landed this pass:

- **App-owned test infra** — `tests/factories.py` (factory_boy, zero labs deps), `tests/conftest.py`
  (`csrf_client`, `login_as`, `seeded_campaign`).
- **RBAC contract lock** — `tests/test_rbac_contract.py` parses `perms.js` and asserts it equals
  `rbac.py` across every role×module×verb. It **found two real drifts** → both fixed:
  (1) `perms.js` denied `training` to everyone (now `TRAINING_ROLES = ['admin']`); (2) `rbac.py` denied
  `operations_manager` `activities:view` despite granting manage (now granted — a deliberate, commented
  deviation from the literal §4.4 table, since view is implied by manage).
- **Host-interaction repro** — `tests/test_host_integration.py`: a portable "hostile upstream"
  middleware that documents the required host contract (any upstream OAuth-session middleware MUST skip
  `/campaign/`) + the failure mode, and pins the campaign middleware as a good citizen.
- **Server↔client perms-matrix pin** — `tests/test_app_bootstrap_contract.py`: `AppView.perms_matrix`
  equals `rbac.can` for all five roles; the CSRF meta token renders.
- **Exhaustive endpoint RBAC** — `tests/test_endpoint_rbac.py`: every mutating endpoint × every role.
- **Serializer golden keys** — `tests/test_serializer_contract.py`: exact worker + planning key sets.
- **OAuth error branches** — `tests/test_oauth_callback_errors.py`: token-failure (403), network (502),
  identity error (403), missing username (403), inactive-whitelist deny (403), none creating a session.
- **Stub-seam frontier** — `tests/test_integration_seams.py` (`@pytest.mark.stub`): payment/KYC make no
  outbound HTTP, plus greppable skipped placeholders for the real KYC/payment/CommCare integrations.
- **Portable e2e scaffold** — `tests/e2e/` (Playwright, cross-process session injection, no labs
  coupling). Excluded from the default run; **authored without a live run — selectors need first-run
  verification** (see each file's docstring).
- **Markers** registered in `pyproject.toml`: `contract`, `stub` (+ existing `e2e`); `tests/e2e` ignored
  by default `addopts`.

Extended after merging `origin/main` (Plan 4 — Activity + Microplanning, #665):

- **Endpoint RBAC** now covers Plan 4's 6 new endpoints × all 5 roles (`test_endpoint_rbac.py`) — Plan 4's
  own api tests checked only one role. Confirms the asymmetry: `operations_manager` may create
  activities (manage) but is denied microplan create/edit (planning is view-only for it).
- **Serializer goldens** extended to the new `_activity` and `_microplan` key sets.

Still open (see §9 priorities, §11 questions): the server-as-single-source RBAC refactor (frontend, needs
a JS/live verify path — not done blind), per-plan checklist wiring into the PR template, and running the
e2e suite against a live deploy.

---

## 1. Current state of the build (what we are testing)

| Plan                            | Scope                                                       | Status          | Test posture                                                                       |
| ------------------------------- | ----------------------------------------------------------- | --------------- | ---------------------------------------------------------------------------------- |
| 1 — Foundation                  | App, CommCare-HQ OAuth, whitelist, server RBAC, React shell | ✅ merged, live | Auth/RBAC/middleware — **highest security value**, currently weakest live coverage |
| 2 — Data + Overview             | ORM models, seeder, `bootstrap` API, Overview dashboard     | ✅ merged, live | Serializer contract + seeder fidelity                                              |
| 3 — Workers vertical            | Payment/KYC write APIs, fraud guards, 3 sub-tabs            | ✅ merged, live | Mutation + CSRF + guard logic — **template for all future verticals**              |
| 4 — Activity + Microplanning    | Activity/Microplan models, CRUD, tables (map dropped)       | ✅ merged, live | Endpoint RBAC × all roles + activity/microplan serializer goldens added            |
| 5 — Reporting                   | Reporting/monitoring tab                                    | ⏳ unbuilt      | §8 checklist                                                                       |
| 6 — System Admin + Training Hub | Users, Connections (admin-only), public Training Hub        | ⏳ unbuilt      | §8 checklist + **public-endpoint** auth tests                                      |

**Code under test today:** ~2,560 LOC backend Python (`auth/`, `api/`, `services/`, `models.py`,
`middleware.py`), 1,042 LOC of tests across 14 files (65 test functions), and ~5,400 LOC of
verbatim/near-verbatim frontend (`static/campaign/*.jsx|*.js`).

### 1.1 Real vs stubbed — the testability map

| Surface                                               | Real?                               | How we test it                                         |
| ----------------------------------------------------- | ----------------------------------- | ------------------------------------------------------ |
| CommCare-HQ OAuth login (PKCE, identity, callback)    | **Real**                            | Mocked-token unit tests + **portable e2e** (§5.4)      |
| Whitelist + `@dimagi.com` auto-provision              | **Real**                            | Unit (all provisioning branches)                       |
| Server-side RBAC (`require_perm`)                     | **Real**                            | Parametrized role×module×verb + per-endpoint 403       |
| ORM persistence / CRUD round-trips                    | **Real**                            | Integration (API → DB → reload)                        |
| Fraud / KYC payment guards                            | **Real**                            | Unit (`worker_actions`) + e2e persistence              |
| Coverage map boundaries (`/api/boundaries/viewport/`) | **Real** (Nigeria admin boundaries) | Integration (GDAL/GEOS env required)                   |
| Audit log                                             | **Real**                            | Integration assertion on mutating endpoints            |
| **KYC provider** submit/results                       | **Stub**                            | State-machine only + `@pytest.mark.stub` seam          |
| **Payment gateway / disbursement**                    | **Stub**                            | State-machine only; assert **no external side effect** |
| **Live CommCare worker/case reads**                   | **Stub** (seeded rows)              | Seeder fidelity; integration seam marked pending       |
| **Connection test/sync**                              | **Stub**                            | State-machine only                                     |
| Coverage **map** (real Mapbox render)                 | **Dropped** (prototype uses tables) | Test the **tables**; see §6.3 spec drift               |

---

## 2. Guiding principles

1. **Test where the risk is, not where it's easy.** The risk in this app is concentrated at the
   **boundaries**: browser↔server (CSRF, serialization), host↔app (middleware ordering),
   role↔permission (RBAC), and real↔stub (integration seams). Unit-testing pure functions is cheap and
   we keep doing it, but it is not where the bugs have been.

2. **Self-contained & portable.** All fixtures, factories, test settings, and browser tests live under
   `campaign/tests/`. No test may depend on `commcare_connect/conftest.py`, labs factories, or
   labs-specific tooling (gstack browse) to _pass_. The lessons learned from labs integration (esp. the
   middleware-logout bug) are encoded as **app-owned reproductions** (§5.3) so they survive migration.

3. **Characterization before refactor.** With one exception (the RBAC unification, §7.1, which is
   small and fixes a live bug), we pin behavior with tests _before_ restructuring. The verbatim React
   is explicitly **off-limits to refactoring** until e2e/visual tests pin it.

4. **Fidelity to the spec only where we agree with the spec.** The spec is an AI-authored design doc;
   parts of it are already superseded by reality or are demo-only policies we should not harden into
   tests. §6 states, per item, what we test for fidelity, what we deliberately don't enshrine, and what
   spec text needs updating.

5. **Make the known failure modes un-mergeable.** CSRF-enforcement and the middleware interaction are
   defaults in the fixtures, not opt-ins. A new write endpoint cannot get a green test without
   exercising the exact conditions that broke production before.

---

## 3. The central risk: live/test environment divergence

This is the spine of the strategy. Today:

- `pyproject.toml` pins `--ds=config.settings.test`.
- `config/settings/test.py` installs **only** `CampaignOAuthSessionMiddleware` — **not**
  `LabsOAuthSessionMiddleware`. Production (`local.py`, `labs_aws.py`) runs the labs middleware
  _ahead_ of the campaign middleware. The interaction that logged users out (PR #661) **cannot occur**
  under test settings.
- Django's test client does not enforce CSRF unless constructed with `enforce_csrf_checks=True`. Every
  existing mutation test runs without CSRF, so the `CSRF_USE_SESSIONS`/no-cookie trap (PR #662) was
  invisible until a real browser hit it.

**Two structural fixes, both app-owned (§5.2, §5.3):**

- A **CSRF-enforcing client is the default** for every mutation test.
- A **portable "hostile upstream middleware" reproduction** asserts `/campaign/` survives an
  OAuth-session middleware that doesn't recognize campaign sessions — without importing labs.

We do **not** rely on someone remembering to also edit `config/settings/test.py`; the app's tests
reconstruct the production-relevant slice of the stack themselves via `override_settings`, so they keep
working after migration when `config/settings/` is gone.

---

## 4. Test layers & how they run

| Layer                | Marker                   | Speed | Runs                 | What it covers                                                                                  |
| -------------------- | ------------------------ | ----- | -------------------- | ----------------------------------------------------------------------------------------------- |
| Unit                 | (none)                   | ms    | every push           | pure logic: `rbac`, `worker_actions` guards, serializer shape, whitelist/provision, seeder math |
| Integration (API+DB) | `@pytest.mark.django_db` | fast  | every push           | endpoints: RBAC 403s, CRUD persistence, audit writes, CSRF-enforced                             |
| Middleware/auth      | (none)                   | fast  | every push           | OAuth callback branches, token refresh/expiry, **hostile-upstream** repro                       |
| Contract             | `@pytest.mark.contract`  | fast  | every push           | serializer↔UI key golden; RBAC single-source equivalence; stub seams                            |
| E2E (browser)        | in `tests/e2e/`          | slow  | pre-deploy / nightly | real login, tab switch, payment-approve-persists-across-reload                                  |
| Visual               | in `tests/e2e/`          | slow  | pre-deploy / nightly | prototype fidelity screenshots, chart/donut render, no-undefined-bars                           |

E2E/visual live in `campaign/tests/e2e/` following the house convention
(`funder_dashboard/tests/e2e/`, `solicitations/tests/e2e/`) and are **ignored by the default pytest
run** — invoke explicitly. They use `pytest-playwright` (already a dependency), which is portable and
replaces ad-hoc `gstack browse` so a migrated app can still run them.

Run commands:

```bash
# fast suite (every push) — GDAL/GEOS env needed for the boundary path
pytest commcare_connect/campaign/
# contract layer only
pytest commcare_connect/campaign/ -m contract
# browser suite (pre-deploy / nightly), against a running instance
pytest commcare_connect/campaign/tests/e2e/ --base-url=https://labs.connect.dimagi.com
```

---

## 5. Self-contained test infrastructure to build

All of this is new and lives under `campaign/tests/`. It is the foundation everything else sits on.

### 5.1 `campaign/tests/factories.py` — app-owned factories

Replace the repeated inline `Workspace.objects.create(...)/Campaign.objects.create(...)` in every test
file with `factory_boy` factories (dependency already present): `WorkspaceFactory`, `CampaignFactory`,
`DonorFactory`, `RegionFactory` (+`RegionPlan`), `WorkerRoleFactory`, `WorkerFactory`,
`CampaignUserFactory(role=...)`, plus `WorkerFraudPairFactory` (two workers sharing an identifier — the
fraud-guard fixture). Factories must **not** import labs factories. `WorkerFactory` defaults must
produce a serializer-valid row (all JSON fields populated) so contract tests don't need the full seeder.

### 5.2 `campaign/tests/conftest.py` — app-scoped fixtures

- `campaign(db)` — a minimal seeded campaign (via factories, not the demo seeder) for fast tests.
- `seeded_campaign(db)` — full `seed.seed_campaign()` output for fidelity/e2e-shaped tests.
- `campaign_user(role)` — parametrizable; returns a logged-in `CampaignUser` of the given role.
- **`csrf_client`** — `Client(enforce_csrf_checks=True)` **as the default for mutations**. A plain
  `client` is allowed only for GETs. Document the rule: _write tests use `csrf_client`._
- `login_as(role)` — sets up the `campaign_oauth` session + Django user so endpoints see a real
  authenticated campaign user without round-tripping OAuth.
- `bootstrap(client)` — returns the parsed `/campaign/api/bootstrap/` payload for assertions.

### 5.3 `campaign/tests/test_host_integration.py` — portable production-parity

The bug that hurt most (logout) came from a _host_ middleware the app doesn't own. Encode it without
depending on labs:

```python
# A stand-in for ANY upstream OAuth-session middleware that logs out authenticated
# requests lacking ITS session key — mirrors LabsOAuthSessionMiddleware's behavior.
class HostileUpstreamOAuthMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.user.is_authenticated and "labs_oauth" not in request.session:
            logout(request)                      # the production failure mode
            return redirect("/accounts/login/")
        return self.get_response(request)

@override_settings(MIDDLEWARE=[... auth ..., HOSTILE_PATH, CAMPAIGN_MW_PATH, ...])
def test_campaign_paths_survive_hostile_upstream(csrf_client, login_as):
    login_as("campaign_admin")
    resp = csrf_client.get("/campaign/api/bootstrap/")
    assert resp.status_code == 200            # NOT a redirect to login
```

This reproduces PR #661 in-app and forever, even after labs is gone. Pair it with a test that the
campaign middleware **never** mutates/clears a session lacking `campaign_oauth` on non-`/campaign/`
paths (so the app is a good citizen in any host).

### 5.4 `campaign/tests/e2e/` — portable browser tests (Playwright)

House convention; `pytest-playwright` already available. At minimum:

- `test_login_gate.py` — anonymous `/campaign/` → 302 to login; static assets 200; login page renders.
- `test_payment_persists.py` — log in, approve & queue a worker payment, **full reload**, status
  persisted. (This is the one true end-to-end path through browser→CSRF→API→guard→DB and it caught
  nothing-by-unit-test before.)
- `test_tabs_mount.py` — bundle mounts, each tab switches without a console error.

Auth in CI is the known hard part (OAuth needs a real CommCare session). Document the
session-injection approach (seed `campaign_oauth` + Django session cookie directly, bypassing the
OAuth redirect) so e2e doesn't require interactive login — this is portable and host-independent.

---

## 6. Part A — Spec-fidelity testing (and where we disagree with the spec)

"Fidelity" means: the agreed-upon parts of the spec are encoded as executable oracles. The spec is not
ground truth — below is the explicit agree/disagree ledger. **We only write fidelity tests for AGREE
rows.** DISAGREE/SUPERSEDED rows get a behavior-pinning test plus a note and a spec-update task.

| Spec item                                                                             | Verdict                                                        | Test action                                                                                                           |
| ------------------------------------------------------------------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Verbatim prototype is the UI source of truth (§1, §6)                                 | **Agree**                                                      | Visual/e2e characterization (§7.5), no JSX refactor                                                                   |
| Real auth/RBAC/CRUD; stub KYC/payment/CommCare (§2)                                   | **Agree**                                                      | Real → full tests; stub → state-machine + seam                                                                        |
| RBAC matrix (§4.4 table)                                                              | **Agree on the matrix, disagree on the dual-source mechanism** | Encode the §4.4 table as the **single** oracle (§7.1); unify perms.js                                                 |
| `@dimagi.com` auto-provisions as admin (§4.3)                                         | **Agree for demo, flag as demo-only**                          | Test the branch; add `xfail`/skip-marked test asserting a stricter policy is needed before non-demo, + follow-up task |
| Real Mapbox coverage map (§6.4)                                                       | **Superseded** — build dropped it; prototype renders tables    | Test the **coverage tables**; update spec §6.4/§12-Q3 to match reality                                                |
| Money as integers (§5)                                                                | **Agree**                                                      | Assert no float money in serializer/guards                                                                            |
| Seeder dataset shape (§8): MR-2026-R2, 5 regions, 4 donors, 64 workers, 7 fraud pairs | **Agree**                                                      | Fidelity test on counts + invariants (§7.2)                                                                           |
| Derived microplan metrics `fill/cov/util/objective` (§5)                              | **Agree**                                                      | Unit tests on the formulas (when Plan 4 lands)                                                                        |
| "approve payment" persists with no disbursement (§2, §11)                             | **Agree it's a state machine**                                 | Assert state transition + **no external call**; visual check that UI doesn't imply money moved                        |
| Spec §10 "Testing" plan                                                               | **Inadequate** (missed the divergence class)                   | This doc supersedes it; leave a pointer in the spec                                                                   |
| §12 open questions (coverage metric, credential storage)                              | **Still open**                                                 | Encode the chosen answer as a test, or `xfail` with the question linked                                               |

### 6.1 Spec-drift tasks this surfaces (not tests, but do them)

- **Update spec §6.4 and §12-Q3:** the real coverage map was dropped; coverage is tables. The committed
  spec currently misdescribes the shipped product.
- **Flag §4.3 auto-admin** as a demo-only authorization policy with a hardening task before any
  non-demo deployment (today: any `@dimagi.com` CommCare account → full admin).
- **Resolve §12-Q2 (credential storage):** tests should assert connections store masked/placeholder
  credentials only (no real secrets at rest) for the demo.

---

## 7. Part B & C — the test backlog, by area

### 7.1 RBAC — lock the dual matrix, then keep the single source _(do-first)_

**What's actually true (verified in code):**

- `services/rbac.py` is the **real gate** (`require_perm` on every endpoint). Server-side enforcement
  is correct and already unit-tested (`test_rbac.py`).
- `static/campaign/perms.js` is a **second, hand-maintained copy** for show/hide only, in a different
  role vocabulary (`admin` vs `campaign_admin`), with `connections` handled as a `['admin']`
  special-case and `training` **absent from its matrix entirely**.
- The vocabularies are bridged at runtime by `app.jsx`'s `ROLE_DISPLAY` (server id → display name)
  before `perms.js.can()` is called, **so the show/hide is correct today** for the logged-in user and
  the role-preview switcher. (The earlier claim that non-admin roles render full-admin UI was wrong;
  `ROLE_DISPLAY` is the missing piece.)
- **Real drift already exists:** `perms.js.can(role, 'training', verb)` returns `false` for _every_
  role (module missing) while `rbac.py` grants `campaign_admin` full `training` access. No live impact
  yet (training tab unbuilt), but this is precisely the divergence a contract test must catch.
- The server _already_ computes a per-user `perms_matrix` in `AppView.get_context_data` and ships it in
  the inline bootstrap — and the client **ignores it**, recomputing from its static copy. That's the
  single-source opportunity.

**Action (low-risk, verifiable in Python):**

- `test_rbac_contract.py` (`@pytest.mark.contract`) — parse `perms.js`'s `MATRIX`/`CONNECTIONS_ROLES`,
  map client role-ids → server role-ids, and assert `client_can(role, module, verb) ==
rbac.can(server_role, module, verb)` for every (role, module, verb). This **locks the two matrices
  together** and fails on any future drift. It currently catches the `training` divergence (RED) →
  reconcile `training` in `perms.js` (GREEN).
- Pin the server's emitted matrix: `test_app_view_perms_matrix` asserts `AppView` ships a
  `perms_matrix` equal to `rbac.can` for the logged-in role across all modules×verbs.

**Recommended follow-up refactor (frontend, do when a JS test harness or live verify is available):**
make the server the single source — emit the full `rbac.MATRIX` (all roles) in the bootstrap and have
`perms.js` consume it, deleting the static copy and the `app.jsx` bridge. Not done blind here because
the verbatim React can't be exercised in this environment (see Principle 3); the contract test protects
against drift in the meantime.

**Other RBAC tests:**

- `test_endpoint_rbac.py` — every mutating endpoint returns **403 for every role lacking the verb** and
  **2xx for roles that have it** (table-driven, one row per endpoint×role). Extends the existing
  single-role `test_rbac_reporting_user_cannot_write`.

### 7.2 Seeder & data fidelity

- Idempotency: `seed_campaign_demo` twice → identical row counts, stable ids (fixed PRNG `20260603`).
- `--fresh` resets cleanly.
- Invariants: exactly 5 regions, 4 donors, 64 workers, **7 fraud pairs** each sharing a real
  identifier; every worker has a valid `role`/`region` FK; KYC/pay statuses ∈ the allowed enums; money
  fields are integers.
- These double as **spec §8 fidelity** assertions.

### 7.3 Serializer↔UI contract _(prevents the silent-break class)_

- `test_serializer_contract.py` (`@pytest.mark.contract`): assert `bootstrap_payload(campaign)` and
  `_worker(...)` emit **exactly** the expected camelCase key set, stored as a checked-in golden
  (`tests/golden/bootstrap_keys.json`). Adding/removing a key fails until the golden is updated — a
  forcing function to keep server and JSX in lockstep.
- Cross-check the other direction: a maintained manifest (or a grep-based test) of the
  `CUT_DATA`/worker keys the JSX reads, asserting the serializer supplies each. This is what would have
  caught the `role`/`region` drop at test time instead of in the browser.
- Pin the specific regression: a worker row serializes with non-empty `role` and `region` display
  names (the Overview workforce chart groups by `role`).

### 7.4 Auth, OAuth callback, middleware

- Callback branches: admin auto-provision; whitelisted-active; whitelisted-inactive → denied;
  not-listed → denied (no session created); **pre-existing Django user with same email under a
  different username** (the PR #659 `IntegrityError`) → reuses the row.
- PKCE/state validation; token exchange failure → friendly error, no session.
- `CampaignOAuthSessionMiddleware`: token refresh near expiry; hard expiry → re-auth; only acts on
  `/campaign/` paths; never touches `labs_oauth`.
- Hostile-upstream repro (§5.3).

### 7.5 Mutations, CSRF, guards (the per-vertical template)

For **every** write endpoint (current: payments set-status/queue, KYC status/resolve-duplicate/
investigation; future: activities, microplans, users, connections, training):

- Uses `csrf_client` (CSRF-enforced) — a request without the `<meta>` token **403s** (pins PR #662).
- RBAC gate (covered in §7.1's table).
- Persistence: mutate → reload from DB → value stuck; bulk ops atomic.
- Guards: payment approve blocked while fraud flags open or `kyc == rejected`; auto-`hold` when
  `kyc ∈ {pending, review}`; per-day rollup to `Payment.status`.
- Audit log row written with correct module/action.
- Footgun pins: `resolve-duplicate` missing `keep` does **not** silently discard; optimistic-UX paths
  surface server errors.

### 7.6 Stubbed-integration seams

- KYC submit, payment queue, connection test/sync, CommCare reads: assert the **local** state
  transition and assert **no external HTTP** occurs (mock-and-assert-not-called). Mark each
  `@pytest.mark.stub` and leave one `@pytest.mark.skip(reason="real <X> integration not built")`
  contract test per seam, so the integration frontier is greppable and the test turns on when the real
  thing lands.

### 7.7 Coverage map / boundaries

- `/api/boundaries/viewport/` returns Nigeria admin boundaries for a bbox/zoom/level (GDAL/GEOS env).
- It imports only `microplans.core.admin_boundaries` (pure resolver), not microplans views/urls — a
  test asserting no import-time dependency on microplans URLConf protects the isolation contract and
  the future migration.
- Coverage **tables** (not a map) render the agreed metric (§6.3 / spec §12-Q3 once resolved).

### 7.8 Visual / prototype fidelity (in `tests/e2e/`)

- Screenshot characterization of each shipped tab; diff against a baseline to catch unintended
  drift in the verbatim UI.
- Specific anti-regressions: Overview shows 5 workforce bars (no `undefined`), donuts/charts render,
  brand colors/`--accent` applied, fonts loaded. These are the failure modes a serializer typo or a
  dropped key produces.

---

## 8. Per-plan forward checklist (Plans 4–6 gating)

Every new tab/vertical PR must add, before merge:

- [ ] Models: factory in `factories.py`; migration tested (`makemigrations --check`).
- [ ] Serializer: golden key file updated; contract test green (§7.3).
- [ ] RBAC: matrix rows for the new module in `rbac.py` (single source); `test_endpoint_rbac` rows for
      every new endpoint × every role.
- [ ] Mutations: every write endpoint tested with `csrf_client` (CSRF-enforced) + persistence + guards + audit (§7.5).
- [ ] Host integration: inherited hostile-upstream test still green (no new logout/redirect surface).
- [ ] Stubs: any new fake integration marked `@pytest.mark.stub` with a skipped contract seam (§7.6).
- [ ] E2E: at least one browser persistence path for the vertical (§5.4).
- [ ] Visual: baseline screenshot for the new tab (§7.8).
- [ ] **Public endpoints (Plan 6 Training Hub):** explicit tests that public reads need **no** auth and
      that management mutations **do** — the inverse-RBAC case the rest of the app doesn't have.

---

## 9. Priorities

**P0 — foundation & the bug classes that already bit us**

- §5 infra (factories, conftest, `csrf_client` default, hostile-upstream repro).
- §7.1 RBAC unification + tests (fixes a live bug, single source).
- §7.3 serializer contract + the `role`/`region` regression pin.
- §7.5 CSRF-enforced mutation tests for the existing Plan-3 write endpoints.
- §7.4 the `IntegrityError` and denial-path callback branches.

**P1 — confidence on the real surfaces**

- Full §7.1 endpoint-RBAC table; §7.2 seeder fidelity; §7.7 boundaries isolation; §5.4 the one
  payment-persists e2e path.

**P2 — fidelity & polish**

- §7.6 stub seams; §7.8 visual baselines; spec-drift updates (§6.1).

**P3 — forward**

- §8 checklist wired into the PR template; nightly e2e/visual against labs.

---

## 10. Coverage goals & what NOT to chase

- **High coverage (target ~90%+ lines/branches) on:** `services/` (rbac, worker_actions, serializers,
  seed), `auth/`, `api/`, `middleware.py`. These are small, pure, high-value, and portable.
- **Do not chase line coverage on the verbatim `*.jsx`.** It is pinned by e2e/visual + the serializer
  contract, not by JS unit tests. Refactoring it to be unit-testable would forfeit prototype fidelity
  for little gain. If the app later adopts a JS build step, revisit with component tests then.
- **Do not** add tests that assert stub internals as if they were real integrations — they only lock in
  the fake.

---

## 11. Open questions for the team

1. **E2E auth in CI** — confirm the session-injection approach (seed `campaign_oauth` directly) is
   acceptable for portable browser tests, vs requiring a live CommCare OAuth round-trip.
2. **Spec §12-Q3 coverage metric** — which number shades the coverage tables (visited/registered vs
   reached/objective)? A test needs the answer.
3. **Spec §12-Q2 credential storage** — confirm masked/placeholder-only at rest for the demo so the
   connections test can assert it.
4. **§4.3 auto-admin** — is `@dimagi.com → admin` acceptable beyond the demo, or do we add the
   hardening test + gate now?
5. **perms.js end-state** — fully data-driven from the server payload (preferred), or generated from
   `rbac.py` at build time? Determines whether the §7.1 drift-guard test is permanent or temporary.
