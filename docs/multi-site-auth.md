# Multi-site authentication contract

Labs and each standalone satellite site — `supply`, `campaign`, and any future
site — run inside **one Django project**, against **one `users.User` table**,
behind **one session cookie**. That has one consequence you must design around:

> **Authentication is global. Authorization is per-surface.**

`request.user.is_authenticated` is true across the whole host the moment a user
signs into *any* site. So "is this request authenticated?" is never enough to
decide "may this request use labs?" — a login on satellite X would otherwise be
a login on labs and on every other site. Each surface must enforce its own
authorization on top of the shared authentication.

## The labs boundary

`connect_labs/labs/oauth_session.py::LabsOAuthSessionMiddleware` is the labs
authorization boundary (active in the `labs_aws` and `local` settings). On every
request:

- If the user is authenticated **and** the path is **not** skip-listed **and**
  the session has no live `labs_oauth` token → the user is **logged out**.

Labs access therefore requires a **labs OAuth session**, which only the labs
OAuth flow (`/labs/login/` → `/labs/callback/`) establishes. A satellite's own
login (e.g. `/supply/login/`, a plain Django password login) never sets
`labs_oauth`, so a satellite session is torn down the instant it touches a labs
path (`/labs/*`, `/microplans/*`, `/funder/*`, `/solicitations/*`, …). The
boundary is **fail-closed**: any path that isn't explicitly skip-listed is
treated as labs and gated.

This is why a satellite login cannot become a labs login, and it's verified by
`test_missing_labs_oauth_payload_logs_user_out` and
`test_new_satellite_prefix_is_honored_via_setting`.

## The three user cases

| User | Has `labs_oauth` (did labs OAuth)? | Satellite membership? | Labs access | Satellite access |
|------|:---:|:---:|:---:|:---:|
| Labs-only (most Dimagi staff) | yes | no | ✅ | ❌ (no satellite membership) |
| Satellite-only (e.g. a supplier who self-registered) | no | yes | ❌ (logged out on labs paths) | ✅ (via the satellite's own auth) |
| Both (e.g. you) | yes | yes | ✅ | ✅ |

One person can hold several authorizations at once; each surface checks its own.

## Adding a new satellite site N — checklist

A satellite is a self-contained site that should be liftable into its own
deployment. Keep it decoupled from labs (no `connect_labs.labs` imports; reach
the shared user table via `get_user_model()` / `settings.AUTH_USER_MODEL`).

1. **Mount it** — `INSTALLED_APPS` (in `local.py` / `labs_aws.py` / `test.py`,
   deliberately *not* `base.py`) and one `path("siteN/", include(...))` line in
   `config/urls.py`.
2. **Skip-list its prefix** — add `"/siteN/"` to `LABS_SATELLITE_URL_PREFIXES`
   in `config/settings/base.py`. **If you skip this step, site N's own users get
   logged out on every request to it** (campaign learned this the hard way in PR
   #661). This is one config line, next to where you wired the app — no edit to
   labs code, no import from labs.
3. **Enforce site N's own authorization** — do **not** rely on Django's global
   authentication alone. Gate every state-changing / data view on membership in
   site N (a decorator/mixin that checks your own membership model, the way
   `supply` uses `@require_perm` + `SupplierMember` and `campaign` uses
   `@campaign_login_required` + `CampaignUser`). Global `is_authenticated` is
   satisfied by a login on *any* site.
4. **Don't let open signup mint privileged identities.** If site N has open
   self-registration into the shared user table, reject email domains that grant
   elevated access elsewhere (labs derives Dimagi/admin from the email domain —
   see `connect_labs/utils/dimagi_user.py`). `supply` does this in
   `SignupForm.clean_email`; mirror it. Otherwise an anonymous signup can claim a
   privileged identity, or squat a real user's username, in the shared table.
5. **Add a host-integration test** — assert your prefix is in
   `oauth_session.get_skip_path_prefixes()` (see
   `connect_labs/supply/tests/test_host_integration.py`) so a future change can't
   silently break the contract.

## Defense in depth, not a single point of failure

The middleware is the primary boundary, but treat these as complementary:

- **Signup hygiene** (step 4) stops a satellite from *creating* a privileged or
  labs-shaped identity in the shared table in the first place.
- **Per-surface authorization** (step 3) means that even a request that somehow
  reached a satellite view without going through the middleware is still gated by
  the site's own membership check.

See the 2026-07-29 security audit for the finding that motivated documenting
this contract.
