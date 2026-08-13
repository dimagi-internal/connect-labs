"""Tests for connect_labs/audit/link_helpers.py's request-optional URL resolvers.

resolve_org_slug / build_absolute_url / resolve_urls_by_blob exist so classifier-fail
rows can get their image/form/connect URLs resolved at AI-review/duplicate-detection
time (no live HTTP request) as well as at the human save/complete time (a live
request, session-cached org data) -- see classifier_fail_sync.py and tasks.py.
"""

from django.test import override_settings

from connect_labs.audit import link_helpers

# Forces a real, isolated cache backend for the caching test below -- without
# this, the project's configured default CACHES backend may not actually
# cache (e.g. DummyCache) or may not be process-isolated between test runs.
_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class _FakeRequest:
    def __init__(self, organization_data):
        self.session = {"labs_oauth": {"organization_data": organization_data}}
        self.user = None

    def build_absolute_uri(self, path):
        return f"https://live-request.example{path}"


# ── resolve_org_slug ────────────────────────────────────────────────────────────


def test_resolve_org_slug_uses_session_cached_data_when_request_given(monkeypatch):
    """With a live request, the session-cached org list is used -- no extra API call."""

    def _boom(access_token):
        raise AssertionError("fetch_user_organization_data should not be called when a request is given")

    monkeypatch.setattr(link_helpers, "fetch_user_organization_data", _boom)

    request = _FakeRequest({"opportunities": [{"id": 42, "organization": "acme-health"}]})
    assert link_helpers.resolve_org_slug("tok", 42, request=request) == "acme-health"


@override_settings(CACHES=_LOCMEM)
def test_resolve_org_slug_falls_back_to_live_api_call_without_request(monkeypatch):
    """Without a request (background/Celery context), fetch_user_organization_data
    is called directly with the access token."""
    captured = {}

    def _fake_fetch(access_token):
        captured["access_token"] = access_token
        return {"opportunities": [{"id": 42, "organization": "acme-health"}]}

    monkeypatch.setattr(link_helpers, "fetch_user_organization_data", _fake_fetch)

    # Distinct access_token from the other resolve_org_slug tests below --
    # resolve_org_slug caches its result keyed by a hash of the token
    # (_ORG_DATA_CACHE_TTL), so reusing the same literal token across tests
    # would let one test's cached result leak into another's assertions.
    assert link_helpers.resolve_org_slug("tok-fallback", 42, request=None) == "acme-health"
    assert captured["access_token"] == "tok-fallback"


@override_settings(CACHES=_LOCMEM)
def test_resolve_org_slug_returns_empty_when_opportunity_not_found(monkeypatch):
    monkeypatch.setattr(link_helpers, "fetch_user_organization_data", lambda access_token: {"opportunities": []})
    assert link_helpers.resolve_org_slug("tok-not-found", 42, request=None) == ""


@override_settings(CACHES=_LOCMEM)
def test_resolve_org_slug_caches_the_live_api_call_per_token(monkeypatch):
    """A second call with the SAME token, in the same short window, must not
    re-fetch -- this is what lets a batch run resolve URLs for many sessions
    of one user without repeating the org-data call once per session."""
    call_count = {"n": 0}

    def _fake_fetch(access_token):
        call_count["n"] += 1
        return {"opportunities": [{"id": 42, "organization": "acme-health"}]}

    monkeypatch.setattr(link_helpers, "fetch_user_organization_data", _fake_fetch)

    token = "tok-cache-dedup"
    assert link_helpers.resolve_org_slug(token, 42, request=None) == "acme-health"
    assert link_helpers.resolve_org_slug(token, 42, request=None) == "acme-health"
    assert call_count["n"] == 1


@override_settings(CACHES=_LOCMEM)
def test_resolve_org_slug_does_not_cache_a_transient_fetch_failure(monkeypatch):
    """Regression: a failed fetch_user_organization_data call (returns None,
    coerced to a blank result) must not be cached -- otherwise a transient
    failure gets "stuck" returning a blank org_slug for the rest of the TTL
    window instead of retrying on the next call."""
    call_count = {"n": 0}

    def _flaky_fetch(access_token):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # simulates fetch_user_organization_data's own failure path
        return {"opportunities": [{"id": 42, "organization": "acme-health"}]}

    monkeypatch.setattr(link_helpers, "fetch_user_organization_data", _flaky_fetch)

    token = "tok-flaky"
    assert link_helpers.resolve_org_slug(token, 42, request=None) == ""
    assert link_helpers.resolve_org_slug(token, 42, request=None) == "acme-health"
    assert call_count["n"] == 2  # the failed first call was NOT cached, so the second call retried


def test_resolve_org_slug_never_falls_back_to_a_live_fetch_when_request_is_given(monkeypatch):
    """Regression: a request means we're on an interactive path (Save/Complete,
    a CSV export GET) -- resolve_org_slug must NEVER fall back to a live,
    ~30s-timeout fetch there even when the session's cached org data doesn't
    list the requested opportunity, or a reviewer's save/export request could
    hang for up to 30s. Returning "" in that case is the accepted trade-off;
    only the request-less (background/Celery) path may make a live call."""

    def _boom(access_token):
        raise AssertionError("fetch_user_organization_data must not be called when a request is given")

    monkeypatch.setattr(link_helpers, "fetch_user_organization_data", _boom)

    # Session data only knows about opportunity 42, not 99.
    request = _FakeRequest({"opportunities": [{"id": 42, "organization": "other-org"}]})
    assert link_helpers.resolve_org_slug("tok-request-no-fallback", 99, request=request) == ""


# ── build_absolute_url ───────────────────────────────────────────────────────────


def test_build_absolute_url_uses_request_when_given():
    request = _FakeRequest({})
    assert link_helpers.build_absolute_url("/foo/bar", request=request) == "https://live-request.example/foo/bar"


def test_build_absolute_url_falls_back_without_request():
    """No request and no DB access (unmarked test) -- falls back to the
    Celery-safe default rather than raising."""
    assert link_helpers.build_absolute_url("/foo/bar", request=None) == "https://localhost/foo/bar"


# ── resolve_urls_by_blob ─────────────────────────────────────────────────────────


class _FakeDataAccess:
    def __init__(self, visits):
        self._visits = visits
        self.access_token = "tok"

    def get_visits_batch(self, visit_ids, opportunity_id):
        return self._visits


def test_resolve_urls_by_blob_builds_all_three_urls_per_image(monkeypatch):
    monkeypatch.setattr(link_helpers, "resolve_hq_link_base", lambda access_token, opp_id: "https://hq/forms")
    monkeypatch.setattr(link_helpers, "resolve_org_slug", lambda access_token, opp_id, request=None: "acme-health")

    data_access = _FakeDataAccess(
        visits=[{"id": 1, "xform_id": "xf1", "user_id": 7, "user_visit_id": 99}],
    )
    visit_images = {"1": [{"blob_id": "blobA"}]}

    urls = link_helpers.resolve_urls_by_blob(
        data_access=data_access,
        access_token="tok",
        opportunity_id=42,
        visit_images=visit_images,
    )

    assert urls["blobA"]["form_url"] == "https://hq/forms/xf1/"
    assert urls["blobA"]["connect_url"] == (
        "https://connect.dimagi.com/a/acme-health/opportunity/42/user_visits/?user=7&visit_id=99"
    )
    # request=None -> falls back through build_absolute_url's Site-based default,
    # which itself falls back to "localhost" without DB access in this test.
    assert urls["blobA"]["image_url"].startswith("https://localhost/")
    assert "blobA" in urls["blobA"]["image_url"]


def test_resolve_urls_by_blob_groups_by_each_images_own_opportunity(monkeypatch):
    """A session's visit_images can carry images sourced from more than one
    opportunity (e.g. a multi-opp combined session, muac_picture_audit /
    weekly_dual_track_audit) -- each group must be resolved against ITS OWN
    opportunity (its own get_visits_batch/HQ-metadata/org-slug lookups), not
    a single opportunity_id assumed for the whole batch."""
    hq_bases = {555: "https://hq-a/forms", 777: "https://hq-b/forms"}
    org_slugs = {555: "org-a", 777: "org-b"}
    monkeypatch.setattr(link_helpers, "resolve_hq_link_base", lambda access_token, opp_id: hq_bases[opp_id])
    monkeypatch.setattr(link_helpers, "resolve_org_slug", lambda access_token, opp_id, request=None: org_slugs[opp_id])

    class _MultiOppDataAccess:
        access_token = "tok"

        def get_visits_batch(self, visit_ids, opportunity_id):
            if opportunity_id == 555:
                return [{"id": 1, "xform_id": "xf1", "user_id": 7, "user_visit_id": 99}]
            if opportunity_id == 777:
                return [{"id": 2, "xform_id": "xf2", "user_id": 8, "user_visit_id": 100}]
            raise AssertionError(f"Unexpected opportunity_id: {opportunity_id}")

    visit_images = {
        "1": [{"blob_id": "blobA", "opportunity_id": 555}],
        "2": [{"blob_id": "blobB", "opportunity_id": 777}],
    }

    urls = link_helpers.resolve_urls_by_blob(
        data_access=_MultiOppDataAccess(),
        access_token="tok",
        opportunity_id=1,  # neither image's real opportunity -- fallback only
        visit_images=visit_images,
    )

    assert urls["blobA"]["form_url"] == "https://hq-a/forms/xf1/"
    assert urls["blobA"]["connect_url"].startswith("https://connect.dimagi.com/a/org-a/opportunity/555/")
    assert urls["blobB"]["form_url"] == "https://hq-b/forms/xf2/"
    assert urls["blobB"]["connect_url"].startswith("https://connect.dimagi.com/a/org-b/opportunity/777/")


def test_resolve_urls_by_blob_one_groups_failure_does_not_discard_another_groups_success(monkeypatch):
    """Regression: each opportunity group is resolved in its own try/except --
    a failure processing one group (here, opportunity 777) must not wipe out
    URLs a PRIOR group (opportunity 555) in the same call already resolved."""

    def _hq_base(access_token, opp_id):
        if opp_id == 777:
            raise RuntimeError("HQ metadata service down")
        return "https://hq-a/forms"

    monkeypatch.setattr(link_helpers, "resolve_hq_link_base", _hq_base)
    monkeypatch.setattr(link_helpers, "resolve_org_slug", lambda access_token, opp_id, request=None: "acme")

    class _MultiOppDataAccess:
        access_token = "tok"

        def get_visits_batch(self, visit_ids, opportunity_id):
            return [{"id": vid, "xform_id": f"xf{vid}", "user_id": 7, "user_visit_id": 99} for vid in visit_ids]

    visit_images = {
        "1": [{"blob_id": "blobA", "opportunity_id": 555}],
        "2": [{"blob_id": "blobB", "opportunity_id": 777}],
    }

    urls = link_helpers.resolve_urls_by_blob(
        data_access=_MultiOppDataAccess(),
        access_token="tok",
        opportunity_id=1,
        visit_images=visit_images,
    )

    # blobA's opportunity (555) resolved fine and must still be present, even
    # though blobB's opportunity (777) raised while resolving HQ metadata --
    # NOT expected to raise, and blobA must not have been discarded.
    assert urls["blobA"]["form_url"] == "https://hq-a/forms/xf1/"
    assert "blobB" not in urls


def test_resolve_urls_by_blob_falls_back_to_default_opportunity_when_image_has_none(monkeypatch):
    """An image without its own opportunity_id uses the passed-in default --
    the common (single-opportunity session) case, unchanged from before."""
    monkeypatch.setattr(link_helpers, "resolve_hq_link_base", lambda access_token, opp_id: f"https://hq/{opp_id}")
    monkeypatch.setattr(link_helpers, "resolve_org_slug", lambda access_token, opp_id, request=None: "acme")

    data_access = _FakeDataAccess(visits=[{"id": 1, "xform_id": "xf1", "user_id": 7, "user_visit_id": 99}])
    urls = link_helpers.resolve_urls_by_blob(
        data_access=data_access,
        access_token="tok",
        opportunity_id=42,
        visit_images={"1": [{"blob_id": "blobA"}]},  # no "opportunity_id" key on the image
    )
    assert urls["blobA"]["form_url"] == "https://hq/42/xf1/"


def test_resolve_urls_by_blob_empty_visit_images_returns_empty_dict():
    """The short-circuit gates on visit_images directly (not a separate
    visit_ids input that could silently diverge from it -- see
    test_resolve_urls_by_blob_builds_all_three_urls_per_image for the case
    this replaces: there's no longer a way for a caller's visit_ids to be
    empty/stale while visit_images still holds real images)."""
    data_access = _FakeDataAccess(visits=[])
    assert (
        link_helpers.resolve_urls_by_blob(
            data_access=data_access,
            access_token="tok",
            opportunity_id=42,
            visit_images={},
        )
        == {}
    )


def test_resolve_urls_by_blob_returns_empty_dict_when_visit_batch_fetch_fails():
    class _BoomingDataAccess:
        access_token = "tok"

        def get_visits_batch(self, visit_ids, opportunity_id):
            raise RuntimeError("Connect API down")

    urls = link_helpers.resolve_urls_by_blob(
        data_access=_BoomingDataAccess(),
        access_token="tok",
        opportunity_id=42,
        visit_images={"1": [{"blob_id": "blobA"}]},
    )
    assert urls == {}
