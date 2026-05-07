# Pre-login Marketing Website (`prelogin_website` app)

**Status:** Design approved 2026-05-07
**Author:** jjackson + Claude
**Target:** Labs first; portable to `dimagi/commcare-connect` (prod) by manual code copy.

## Problem

`labs.connect.dimagi.com/` currently 302s authenticated and anonymous visitors alike to `/labs/overview/`. There is no public marketing page. Marketing (Gillian) is iterating on a single-page HTML mockup in a separate GitHub repo and will continue to vibe-code revisions there. We need:

1. A Django app that serves Gillian's HTML at the labs root URL.
2. A structure that accommodates manual re-imports of her HTML over time.
3. A code shape that can be copy-pasted into `dimagi/commcare-connect` (prod) with no labs-specific dependencies, so the same site eventually serves `connect.dimagi.com`.

## Non-goals

- Automating sync from Gillian's repo (manual `cp` is fine for now).
- Multi-page routing infrastructure beyond a stub for the next page.
- Prod-connect deployment in this PR — that's a future, separate task.
- CMS, i18n, or any dynamic content. Pages are static templates served via `TemplateView`.
- Migrating other public routes (`/about/`, etc.) into this app.

## App layout

New Django app at `commcare_connect/prelogin_website/`:

```
commcare_connect/prelogin_website/
├── __init__.py
├── apps.py
├── urls.py                          # path("", views.home) + room for more
├── views.py                         # TemplateView for home; helpers later
├── templates/prelogin_website/
│   └── home.html                    # Gillian's mockup, mockup-banner stripped
├── static/prelogin_website/
│   ├── images/                      # empty; ready for marketing assets
│   ├── css/                         # empty; for when CSS is externalized
│   └── js/                          # empty; same
└── tests.py                         # smoke test
```

The mockup is a self-contained HTML document (own `<!DOCTYPE>`, header, footer, fonts loaded from Google Fonts). It does **not** extend `base.html`. This is deliberate: marketing chrome should not mix with the authenticated app chrome, and a standalone template makes the prod-connect copy a true drop-in.

## URL routing

In `config/urls.py`, replace the existing root redirect:

```python
# before
path("", RedirectView.as_view(url="/labs/overview/", permanent=False), name="home"),

# after
path("", include("commcare_connect.prelogin_website.urls", namespace="prelogin_website")),
```

`prelogin_website/urls.py`:

```python
from django.urls import path
from . import views

app_name = "prelogin_website"

urlpatterns = [
    path("", views.home, name="home"),
    # path("<slug:page>/", views.page, name="page"),  # uncomment when 2nd page lands
]
```

When Gillian ships multi-page HTML, we'll either:
- Register each page as its own explicit `path(...)` (recommended for clarity), or
- Implement a single `views.page(slug)` that maps slugs to template names.

That decision is made at the time, not now.

## Views

```python
from django.conf import settings
from django.views.generic import TemplateView


class HomeView(TemplateView):
    template_name = "prelogin_website/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["app_login_url"] = getattr(
            settings, "PRELOGIN_APP_LOGIN_URL", "/labs/overview/"
        )
        return ctx


home = HomeView.as_view()
```

No login decorator. Public route. Anonymous and authenticated visitors see the same page (no auto-redirect for logged-in users). The login CTA in the marketing nav links to `app_login_url`, which is a Django setting:

- **Labs default:** `/labs/overview/`
- **Prod connect:** override via `PRELOGIN_APP_LOGIN_URL` in that repo's settings.

This keeps the template free of hardcoded environment paths.

## Template integration

The template at `templates/prelogin_website/home.html` is the mockup HTML with two changes from the file Gillian provides:

1. **Strip the `.mockup-bar`** element (the "MOCKUP" banner at the top) and its CSS rule.
2. **Replace the login link's `href`** with `{{ app_login_url }}` (Django template syntax).

Both edits are applied at copy-time, manually, and re-applied each time we re-import. They're documented in a one-line comment near the top of `home.html`.

External resources (Google Fonts, the YouTube embed) load directly from their CDNs — no proxying.

## Settings wiring

Append `"commcare_connect.prelogin_website"` to `LOCAL_APPS` in `config/settings/base.py`. No migrations are needed — the app has no models.

`collectstatic` automatically picks up `prelogin_website/static/`; no extra `STATICFILES_DIRS` entry needed.

## Tests

`prelogin_website/tests.py`:

```python
from django.test import TestCase
from django.urls import reverse


class PreloginWebsiteTests(TestCase):
    def test_home_renders(self):
        resp = self.client.get(reverse("prelogin_website:home"))
        assert resp.status_code == 200
        assert b"Connect" in resp.content  # sanity

    def test_login_url_in_context(self):
        resp = self.client.get(reverse("prelogin_website:home"))
        assert resp.context["app_login_url"] == "/labs/overview/"
```

Static page; deeper testing is wasted effort. The smoke test catches "I broke the URLconf" and "I broke the context."

## Re-import workflow (manual)

When Gillian ships a new revision of her HTML in her GitHub repo:

1. Download the latest single-file HTML from her repo.
2. Strip the `.mockup-bar` element and its CSS rule.
3. Replace the login CTA's `href` with `{{ app_login_url }}`.
4. Overwrite `templates/prelogin_website/home.html`.
5. If she added images/fonts/JS as separate files, drop them into `static/prelogin_website/{images,css,js}/` and update template references to `{% static 'prelogin_website/...' %}`.
6. Run tests; commit.

We can codify this into a small script later if cadence becomes painful, but YAGNI for now.

## Prod-connect portability

By design, this app:

- Has zero dependencies on labs-specific modules (`LabsRecordAPIClient`, `labs/context.py`, the labs middleware, proxy models).
- Has zero models, so no migrations to coordinate.
- Reads its one environment-specific value (`PRELOGIN_APP_LOGIN_URL`) from Django settings.

To land in prod connect later:

1. Copy `commcare_connect/prelogin_website/` into `dimagi/commcare-connect`.
2. Add `"commcare_connect.prelogin_website"` to that repo's `LOCAL_APPS`.
3. Mount it at `/` in `config/urls.py` (likely replacing or competing with whatever currently lives there).
4. Set `PRELOGIN_APP_LOGIN_URL` in prod settings to the prod login path.

That's a separate PR against a different repo; not in scope here.

## Implementation checklist

- [ ] Create `commcare_connect/prelogin_website/` with the file skeleton above.
- [ ] Strip `.mockup-bar` and rewrite the login CTA's `href` in `home.html`.
- [ ] Add app to `LOCAL_APPS`.
- [ ] Replace the root redirect in `config/urls.py` with the include.
- [ ] Add smoke tests; run `pytest commcare_connect/prelogin_website/`.
- [ ] Verify locally: `python manage.py runserver`, hit `/`, confirm marketing renders and login link goes to `/labs/overview/`.
- [ ] Commit; open PR with the standard `## Product Description` block describing the new public landing page.
