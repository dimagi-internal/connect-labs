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

The site payload is exactly three directories, and the path prefix changes
between repos (labs' `connect_labs/` → prod's `commcare_connect/`) — everything
after that prefix is identical:

- `connect_labs/prelogin/` → `commcare_connect/prelogin/` (app: `urls.py`, `views.py`, `tests/`)
- `connect_labs/templates/prelogin/` → `commcare_connect/templates/prelogin/` (`home.html`, `robots.txt`, `sitemap.xml`)
- `connect_labs/static/prelogin/` → `commcare_connect/static/prelogin/` (css, js, images)

Local clone: use the existing checkout at
`C:\Users\Mathew Theis\Documents\Connect\commcare-connect` (not a fresh clone
elsewhere) — it already has `origin` (`dimagi/commcare-connect`) and a personal
`fork` remote configured.

**Check for backlog divergence before copying anything.** Promotions don't
necessarily happen every time labs changes — `diff -rq` the three labs
directories against their prod counterparts first. If files *other than* the
ones you're promoting already differ (this has happened: `contact.html`,
`app.js`, `contact-form.js`, `views.py` etc. were all ahead of prod from
earlier unpromoted labs work), a blanket directory copy will silently bundle
that unrelated backlog into your PR. In that case, don't copy the directories
wholesale — instead:

```
git diff <base>..origin/main -- connect_labs/templates/prelogin/home.html connect_labs/static/prelogin/styles.css > /tmp/promo.patch
sed 's#connect_labs/#commcare_connect/#g' /tmp/promo.patch > /tmp/promo-prod.patch
git apply /tmp/promo-prod.patch   # run inside the commcare-connect checkout
```
scoped to just the files your change actually touched, so the PR stays a clean
diff of only what you meant to promote. Full directory copy is fine only when
`diff -rq` confirms nothing else has diverged.

Procedure:

1. Make sure the labs changes are committed and merged (this repo is the source).
2. Update the local `commcare-connect` checkout (`git fetch origin && git checkout main && git reset --hard origin/main`), and branch off from there.
3. Apply the change — full directory copy or scoped patch per the divergence check above. **Do not touch** `config/urls.py`, `config/views.py`, settings, or any non-`prelogin` file — those carry the prod-only robots/sitemap wiring and indexing policy.
4. Run `pre-commit` / prettier (a no-op if labs is already formatted) and the
   `prelogin` tests.
5. Commit, push to your `fork` remote, open a cross-repo PR to
   `dimagi/commcare-connect:main` following its PR template (the
   `## Product Description` drives changelog automation).
6. To ping a reviewer: `gh pr create --reviewer <handle>` fails on fork-based
   cross-repo PRs ("does not have the correct permissions to execute
   RequestReviews") — `@mention` them in a follow-up `gh pr comment` instead.

The Connect team reviews and merges; deploy follows their normal process. After
merge, verify on `connect.dimagi.com` (home, a deep `/portfolio/<program>` route,
`/robots.txt`, `/sitemap.xml`).
