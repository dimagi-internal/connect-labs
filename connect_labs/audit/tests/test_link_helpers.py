"""Tests for connect_labs/audit/link_helpers.py's request-optional URL resolvers.

resolve_org_slug / build_absolute_url / resolve_urls_by_blob exist so classifier-fail
rows can get their image/form/connect URLs resolved at AI-review/duplicate-detection
time (no live HTTP request) as well as at the human save/complete time (a live
request, session-cached org data) -- see classifier_fail_sync.py and tasks.py.
"""

from connect_labs.audit import link_helpers


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


def test_resolve_org_slug_falls_back_to_live_api_call_without_request(monkeypatch):
    """Without a request (background/Celery context), fetch_user_organization_data
    is called directly with the access token."""
    captured = {}

    def _fake_fetch(access_token):
        captured["access_token"] = access_token
        return {"opportunities": [{"id": 42, "organization": "acme-health"}]}

    monkeypatch.setattr(link_helpers, "fetch_user_organization_data", _fake_fetch)

    assert link_helpers.resolve_org_slug("tok", 42, request=None) == "acme-health"
    assert captured["access_token"] == "tok"


def test_resolve_org_slug_returns_empty_when_opportunity_not_found(monkeypatch):
    monkeypatch.setattr(link_helpers, "fetch_user_organization_data", lambda access_token: {"opportunities": []})
    assert link_helpers.resolve_org_slug("tok", 42, request=None) == ""


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
        visit_ids=[1],
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


def test_resolve_urls_by_blob_empty_visit_ids_returns_empty_dict():
    data_access = _FakeDataAccess(visits=[])
    assert link_helpers.resolve_urls_by_blob(
        data_access=data_access,
        access_token="tok",
        opportunity_id=42,
        visit_ids=[],
        visit_images={"1": [{"blob_id": "blobA"}]},
    ) == {}


def test_resolve_urls_by_blob_returns_empty_dict_when_visit_batch_fetch_fails():
    class _BoomingDataAccess:
        access_token = "tok"

        def get_visits_batch(self, visit_ids, opportunity_id):
            raise RuntimeError("Connect API down")

    urls = link_helpers.resolve_urls_by_blob(
        data_access=_BoomingDataAccess(),
        access_token="tok",
        opportunity_id=42,
        visit_ids=[1],
        visit_images={"1": [{"blob_id": "blobA"}]},
    )
    assert urls == {}
