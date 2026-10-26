"""Labs Admin › Access cache: an admin makes labs ask Connect again what one person
may access -- the cached tree MCP and agents read, and a signed-in session's copy."""

from unittest import mock

import pytest
from django.core.cache import cache
from django.test import RequestFactory, override_settings
from django.urls import reverse

from connect_labs.audit_trail.models import AuditEvent
from connect_labs.labs import context
from connect_labs.labs.integrations.connect import oauth
from connect_labs.labs.tests.test_settings import LABS_SETTINGS
from connect_labs.users.models import User

OLD = {"organizations": [{"slug": "dimagi"}], "programs": [], "opportunities": [{"id": 1487}]}
NEW = {"organizations": [], "programs": [], "opportunities": []}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def admin(db):
    return User.objects.create_user(username="staff", email="staff@dimagi.com", password="pw")


@pytest.fixture
def person(db):
    return User.objects.create_user(username="ace", email="ace@dimagi-ai.com", password="pw")


def _cached(owner):
    return cache.get(oauth._org_data_cache_key("", owner))


def test_forgetting_drops_the_owners_cached_tree_and_leaves_a_mark():
    cache.set(oauth._org_data_cache_key("", "ace"), OLD, 3600)
    cache.set(oauth._org_data_cache_key("", "someone_else"), OLD, 3600)

    mark = oauth.forget_user_organization_data("ace")

    assert _cached("ace") is None
    assert _cached("someone_else") == OLD
    assert oauth.organization_data_forgotten_mark("ace") == mark
    assert oauth.organization_data_forgotten_mark("someone_else") is None


def _session_request(user, labs_oauth):
    request = RequestFactory().get("/")
    request.user = user
    request.session = mock.MagicMock()
    store = {"labs_oauth": labs_oauth}
    request.session.get.side_effect = store.get
    request.session.__setitem__.side_effect = store.__setitem__
    return request, store


@pytest.mark.django_db
def test_a_session_refetches_once_after_its_person_was_cleared(person):
    request, store = _session_request(person, {"access_token": "tok", "organization_data": OLD})
    oauth.forget_user_organization_data("ace")

    with mock.patch.object(oauth, "fetch_user_organization_data", return_value=NEW) as fetch:
        assert context._refetch_if_forgotten(request, store["labs_oauth"])["organization_data"] == NEW
        fetch.assert_called_once_with("tok", force_refresh=True, owner="ace")

        # The session now carries the mark: a later request does not fetch again.
        later, _ = _session_request(person, store["labs_oauth"])
        context._refetch_if_forgotten(later, store["labs_oauth"])
        assert fetch.call_count == 1


@pytest.mark.django_db
def test_a_session_left_alone_when_nobody_cleared_it(person):
    request, store = _session_request(person, {"access_token": "tok", "organization_data": OLD})
    with mock.patch.object(oauth, "fetch_user_organization_data") as fetch:
        assert context._refetch_if_forgotten(request, store["labs_oauth"])["organization_data"] == OLD
        fetch.assert_not_called()


@pytest.mark.django_db
def test_a_cleared_session_that_cannot_reach_connect_sees_nothing(person):
    request, store = _session_request(person, {"access_token": "tok", "organization_data": OLD})
    oauth.forget_user_organization_data("ace")
    with mock.patch.object(oauth, "fetch_user_organization_data", return_value=None):
        assert context._refetch_if_forgotten(request, store["labs_oauth"])["organization_data"] == {}
    # Nothing stored: the next request tries Connect again.
    assert store["labs_oauth"]["organization_data"] == OLD
    assert context._FORGOTTEN_MARK_SEEN not in store["labs_oauth"]


@override_settings(**LABS_SETTINGS)
def test_an_admin_clears_a_persons_access_cache_by_email(client, admin, person):
    cache.set(oauth._org_data_cache_key("", "ace"), OLD, 3600)
    client.force_login(admin)

    resp = client.post(reverse("labs_admin:access_cache"), {"who": "ACE@dimagi-ai.com"})

    assert resp.status_code == 302
    assert _cached("ace") is None
    assert oauth.organization_data_forgotten_mark("ace")
    event = AuditEvent.objects.filter(resource_type="org_data_cache").latest("occurred_at")
    assert event.resource_id == "ace"
    assert event.username == "staff"


@pytest.mark.django_db
def test_forgetting_a_labs_context_clears_every_copy(person):
    from datetime import timedelta

    from django.utils import timezone

    from connect_labs.audit import link_helpers
    from connect_labs.labs.models import UserConnectToken

    UserConnectToken.objects.create(user=person, access_token="tok", expires_at=timezone.now() + timedelta(hours=1))
    cache.set(oauth._org_data_cache_key("", "ace"), OLD, 3600)
    cache.set(oauth._org_data_cache_key("tok"), OLD, 3600)
    cache.set(link_helpers._org_data_key("tok"), OLD, 3600)
    before = oauth.access_generation("ace")

    context.forget_labs_context(person)

    assert _cached("ace") is None
    assert cache.get(oauth._org_data_cache_key("tok")) is None
    assert cache.get(link_helpers._org_data_key("tok")) is None
    # Every per-person access cache keyed on the generation now misses.
    assert oauth.access_generation("ace") != before


@override_settings(**LABS_SETTINGS)
def test_an_unknown_person_clears_nothing(client, admin):
    client.force_login(admin)
    resp = client.post(reverse("labs_admin:access_cache"), {"who": "nobody@example.com"}, follow=True)
    assert b"No labs user is nobody@example.com" in resp.content
    assert not AuditEvent.objects.filter(resource_type="org_data_cache").exists()


@override_settings(**LABS_SETTINGS)
def test_only_an_admin_may_clear(client, person):
    outsider = User.objects.create_user(username="ext", email="partner@external.com", password="pw")
    cache.set(oauth._org_data_cache_key("", "ace"), OLD, 3600)
    client.force_login(outsider)
    assert client.post(reverse("labs_admin:access_cache"), {"who": "ace"}).status_code == 403
    assert _cached("ace") == OLD
