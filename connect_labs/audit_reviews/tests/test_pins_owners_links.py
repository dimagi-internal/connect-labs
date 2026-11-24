"""Pinned timeline items, review owners, and link previews for comments."""

import json
import socket

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, override_settings

from connect_labs.audit_reviews import api_views, links, service
from connect_labs.audit_reviews.tests.test_api import dao  # noqa: F401  (fixture)
from connect_labs.audit_reviews.tests.test_api import WORKFLOW, _user
from connect_labs.audit_reviews.tests.test_service import CASES, FakeDao, _pending, kc_state, source_for

pytestmark = pytest.mark.django_db

CASE = CASES["go_live_first_run"]


@pytest.fixture(autouse=True)
def _fake_snapshot_reads(monkeypatch):
    monkeypatch.setattr(service, "read_run_state", lambda run: kc_state(run.kc, CASE["D"]["metrics"]))


@pytest.fixture
def review():
    """(source, review key) for one open review."""
    last = CASE["D"]["runs"][-1]["run_id"]
    source, d = source_for({**CASE, "opts": {"goLiveRunId": last}})
    service.sync(source, FakeDao(CASE), d)
    ent = _pending(source, "open")[0]
    service.record_decisions(source, [{"kind": "open", "entity": ent}], user=None, by="S")
    key = next(r["key"] for r in service.world(source)["reviews"] if r["ent"] == ent)
    return source, key


def _event(source, key, event_id, type, payload):
    return service.record_event(
        source, review_key=key, event_id=event_id, type=type, payload=payload, user=None, by="Staff One"
    )


def _rv(source, key):
    return next(r for r in service.world(source)["reviews"] if r["key"] == key)


# --- Pins --------------------------------------------------------------------------------------


def test_comments_and_actions_can_be_pinned_and_unpinned(review):
    source, key = review
    _event(source, key, "c1", "comment", {"text": "Spoke to the LLO lead"})
    _event(source, key, "a1", "action", {"action": "investigate"})
    _event(source, key, "p1", "pin", {"target": "a1"})
    _event(source, key, "p2", "pin", {"target": "c1"})
    assert _rv(source, key)["pinned"] == ["a1", "c1"]
    _event(source, key, "u1", "unpin", {"target": "a1"})
    rv = _rv(source, key)
    assert rv["pinned"] == ["c1"]
    # Every team event carries its id, and pinning is itself on the timeline.
    team = [e for e in rv["events"] if e.get("team")]
    assert {e["id"] for e in team} == {"c1", "a1", "p1", "p2", "u1"}


def test_only_this_reviews_comments_and_actions_can_be_pinned(review):
    source, key = review
    _event(source, key, "c1", "comment", {"text": "x"})
    _event(source, key, "p1", "pin", {"target": "c1"})
    for target in ["nope", "p1", "", None, 5]:
        with pytest.raises(service.Refused, match="Only a comment or an action"):
            _event(source, key, f"bad-{target}", "pin", {"target": target})


# --- Owners ------------------------------------------------------------------------------------


@pytest.fixture
def people():
    User = get_user_model()
    return {
        "dimagi": User.objects.create(username="d1", email="ann@dimagi.com", name="Ann Owner"),
        "inactive": User.objects.create(username="d2", email="old@dimagi.com", name="Old", is_active=False),
        "associate": User.objects.create(username="d3", email="x@dimagi-associate.com", name="Assoc"),
        "outside": User.objects.create(username="o1", email="someone@example.org", name="Outside"),
        "no_email": User.objects.create(username="canopy:probe-test", email=None),
    }


def test_only_active_dimagi_com_people_may_own_reviews(review, people):
    source, _ = review
    ids = {p["id"] for p in service.owner_candidates(source)}
    assert ids == {people["dimagi"].pk}
    assert service.owner_candidates(source) == [{"id": people["dimagi"].pk, "name": "Ann Owner"}]


def test_the_owner_is_the_latest_word_and_can_be_cleared(review, people):
    source, key = review
    assert _rv(source, key).get("owner") is None  # no owner: none in the answer
    _event(source, key, "o1", "owner", {"user_id": people["dimagi"].pk, "name": "Spoofed"})
    assert _rv(source, key)["owner"] == {"user_id": people["dimagi"].pk, "name": "Ann Owner"}
    _event(source, key, "o2", "owner", {"user_id": None})
    assert _rv(source, key).get("owner") is None


@pytest.mark.parametrize("who", ["inactive", "associate", "outside", "no_email"])
def test_someone_outside_the_pool_cannot_own_a_review(review, people, who):
    source, key = review
    with pytest.raises(service.Refused, match="cannot own"):
        _event(source, key, f"o-{who}", "owner", {"user_id": people[who].pk})


@pytest.mark.parametrize("bad", ["5", True, 1.5, {"id": 1}])
def test_a_malformed_owner_is_refused(review, bad):
    source, key = review
    with pytest.raises(service.Refused, match="from the list"):
        _event(source, key, "o-bad", "owner", {"user_id": bad})


def test_a_wider_rule_needs_only_a_new_pool(review, people, monkeypatch):
    source, key = review
    monkeypatch.setitem(service.OWNER_POOLS, "everyone", lambda s: get_user_model().objects.filter(is_active=True))
    with override_settings(AUDIT_REVIEWS_OWNER_POOL="everyone"):
        _event(source, key, "o3", "owner", {"user_id": people["outside"].pk})
        assert _rv(source, key)["owner"]["name"] == "Outside"


def test_an_unknown_pool_falls_back_to_dimagi_com(review, people):
    source, _ = review
    with override_settings(AUDIT_REVIEWS_OWNER_POOL="typo"):
        assert {p["id"] for p in service.owner_candidates(source)} == {people["dimagi"].pk}


def test_the_world_answer_lists_owners_and_says_who_is_asking(dao, people):  # noqa: F811
    request = RequestFactory().get(f"/labs/audit-reviews/api/{WORKFLOW}/world/?opportunity_id=10301")
    request.user = people["dimagi"]
    body = json.loads(api_views.world_api(request, WORKFLOW).content)
    assert body["owner_candidates"] == [{"id": people["dimagi"].pk, "name": "Ann Owner"}]
    assert body["me"] == {"id": people["dimagi"].pk, "name": "Ann Owner"}


# --- Link previews -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/doc",
        "ftp://example.org/x",
        "https://user:pw@example.org/",
        "https://example.org:8443/",
        "https://example.org:notaport/",
        "javascript:alert(1)",
        "",
        "https://example.org/" + "a" * 2001,
    ],
)
def test_links_labs_will_not_fetch(url):
    with pytest.raises(links.Refused):
        links.check_url(url)


@pytest.mark.parametrize(
    "page, title",
    [
        ("<html><head><title> Weekly  plan &amp; notes </title></head></html>", "Weekly plan & notes"),
        ('<meta property="og:title" content="KC review notes"><title>Other</title>', "KC review notes"),
        ('<meta content="Reversed order" property="og:title">', "Reversed order"),
        ("<title>Google Docs: Sign-in</title>", None),
        ("<title>Log in to Confluence</title>", None),
        ("<p>no title</p>", None),
    ],
)
def test_titles_come_from_og_title_or_title_never_a_sign_in_page(page, title):
    assert links.title_from_html(page) == title


def _answers(*hops):
    """A fake fetcher answering each call with the next (status, headers, body)."""
    seen = []

    def get(url):
        seen.append(url)
        return hops[len(seen) - 1]

    get.seen = seen
    return get


def test_a_public_page_gives_its_title_through_a_redirect():
    get = _answers(
        (302, {"Location": "/final"}, b""),
        (200, {"Content-Type": "text/html; charset=utf-8"}, b"<title>Public doc</title>"),
    )
    assert links.fetch_title("https://example.org/start", get=get) == "Public doc"
    assert get.seen == ["https://example.org/start", "https://example.org/final"]


def test_a_private_google_doc_sends_to_sign_in_and_gets_no_title():
    get = _answers((302, {"Location": "https://accounts.google.com/ServiceLogin?continue=x"}, b""))
    assert links.fetch_title("https://docs.google.com/document/d/abc/edit", get=get) is None
    assert len(get.seen) == 1  # the sign-in page itself is never fetched


def test_a_redirect_to_plain_http_or_too_many_hops_is_not_followed():
    with pytest.raises(links.Refused):
        links.fetch_title("https://example.org/", get=_answers((301, {"Location": "http://example.org/"}, b"")))
    loop = _answers(*[(302, {"Location": "/again"}, b"")] * 5)
    assert links.fetch_title("https://example.org/", get=loop) is None
    assert len(loop.seen) == links.MAX_REDIRECTS + 1


def test_a_non_html_answer_has_no_title():
    get = _answers((200, {"Content-Type": "application/pdf"}, b"%PDF"))
    assert links.fetch_title("https://example.org/a.pdf", get=get) is None


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.5", "::1"])
def test_a_host_that_resolves_inside_the_network_is_never_contacted(address, monkeypatch):
    def resolver(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    monkeypatch.setattr(links.netguard.socket, "getaddrinfo", resolver)
    opened = []
    monkeypatch.setattr(links.urllib3, "HTTPSConnectionPool", lambda *a, **k: opened.append(a) or None)
    cache.clear()
    assert links.preview("https://internal.example.org/x") == {"url": "https://internal.example.org/x", "title": None}
    assert opened == []


def test_previews_are_cached(monkeypatch):
    cache.clear()
    calls = []
    monkeypatch.setattr(links, "fetch_title", lambda url: calls.append(url) or "Cached title")
    assert links.preview("https://example.org/p")["title"] == "Cached title"
    assert links.preview("https://example.org/p")["title"] == "Cached title"
    assert calls == ["https://example.org/p"]


def test_the_link_preview_api_is_staff_only_and_says_why_it_refuses(dao, monkeypatch):  # noqa: F811
    cache.clear()
    monkeypatch.setattr(links, "fetch_title", lambda url: "A title")

    def call(user, url):
        request = RequestFactory().get(
            f"/labs/audit-reviews/api/{WORKFLOW}/link-preview/", {"url": url, "opportunity_id": 10301}
        )
        request.user = user
        return api_views.link_preview_api(request, WORKFLOW)

    assert call(_user(staff=False), "https://example.org/").status_code == 403
    ok = call(_user(), "https://example.org/")
    assert ok.status_code == 200 and json.loads(ok.content)["title"] == "A title"
    bad = call(_user(), "http://example.org/")
    assert bad.status_code == 400 and "https" in json.loads(bad.content)["error"]
