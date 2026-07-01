# Prelogin marketing site

The public marketing site served at the site root (`/`) by the `prelogin`
Django app: `connect_labs/prelogin/` (views + URLs), with its templates in
`connect_labs/templates/prelogin/` and assets in
`connect_labs/static/prelogin/`.

## Source of truth: this repo (labs)

**Labs is the source of truth and the staging environment.** The site is
authored and iterated here as ordinary, Django-native source — you edit
`templates/prelogin/home.html`, `static/prelogin/{styles.css,app.js,…}` and the
images directly, preview on `labs.connect.dimagi.com`, then promote to
production (`dimagi/commcare-connect` → `connect.dimagi.com`).

There is no code-generation or import step. The files in this repo *are* the
site — edit them directly. (Historically the HTML was AI-generated in a separate
repo, `dimagi-internal/connect-prelogin`, and transformed into Django form by an
`export-to-django.py` script. **That repo and that pipeline are deprecated.** The
templates already carry the Django form — `{% static %}`, `{{ app_login_url }}`
— so there is nothing to re-run.)

### What it is, structurally

One self-contained `home.html` document with a client-side History-API router in
`app.js`: every clean URL renders the same template, and the router shows the
right `<section data-page="…">` for the current path. Keep it that way — a single
authored file matches how the site is produced and needs no build step.

The marketing routes are enumerated server-side in
`connect_labs/prelogin/urls.py` (`MARKETING_ROUTES` + the `/portfolio/<slug>`
pattern) so direct loads and refreshes resolve instead of 404ing. **No blanket
catch-all** — the same host serves the real app (`/accounts/…`, dashboards), so
each route is listed explicitly. When you add a marketing route, add it both to
`MARKETING_ROUTES` and to `sitemap.xml`.

## Environment differences (labs vs prod)

Two things differ by environment and are **owned by each repo's own config** —
they are *not* part of the site payload and must not be copied between repos:

| | Labs (staging) | Prod (target) |
|---|---|---|
| `robots.txt` | `Disallow: /` via `config/views.py` — staging must not be indexed | `Allow: /` — the `templates/prelogin/robots.txt` served at root |
| Root URL wiring | marketing `robots.txt`/`sitemap.xml` are **not** wired to root | `config/urls.py` serves `robots.txt` + `sitemap.xml` at root via `TemplateView` |

The marketing `templates/prelogin/{robots.txt,sitemap.xml}` exist in *both* repos
(kept for clean copy-parity), but labs simply doesn't serve them. Because the
promotion copies only the three `prelogin` directories — never `config/` — this
split is preserved automatically.

## Linting

`static/prelogin/{styles.css,app.js}` are linted like any other source (prettier,
`--tab-width 2 --single-quote`). `home.html` is excluded from prettier via the
repo-wide `connect_labs/templates/` exclusion (it's a single large authored
document). Labs and prod use identical prettier config, so formatting is stable
across the copy.

## Promoting to production

Trigger: **"create a PR to push the prelogin changes to connect prod."**

The site payload is exactly three directories:

- `connect_labs/prelogin/` (app: `urls.py`, `views.py`, `tests/`)
- `connect_labs/templates/prelogin/` (`home.html`, `robots.txt`, `sitemap.xml`)
- `connect_labs/static/prelogin/` (css, js, images)

Procedure:

1. Make sure the labs changes are committed (this repo is the source).
2. Clone / update `dimagi/commcare-connect`. (Direct push to that repo may be
   denied; push the branch to the `jjackson/commcare-connect` fork and open a
   cross-repo PR.)
3. Branch off prod `main`.
4. Copy the three directories above from labs into the prod checkout. **Do not
   touch** `config/urls.py`, `config/views.py`, settings, or any non-`prelogin`
   file — those carry the prod-only robots/sitemap wiring and indexing policy.
5. Run `pre-commit` / prettier (a no-op if labs is already formatted) and the
   `prelogin` tests.
6. Commit, push to the fork, open a PR to `dimagi/commcare-connect:main`
   following its PR template (the `## Product Description` drives changelog
   automation).

The Connect team reviews and merges; deploy follows their normal process. After
merge, verify on `connect.dimagi.com` (home, a deep `/portfolio/<program>` route,
`/robots.txt`, `/sitemap.xml`).
