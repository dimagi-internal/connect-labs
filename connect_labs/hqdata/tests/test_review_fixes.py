"""Regressions found in review of the CommCare-project branch: one test per defect."""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

import connect_labs.hqdata.mcp_tools  # noqa: F401 -- registers the tools
from connect_labs.hqdata.access import create_project, may_use_project, refresh_memberships
from connect_labs.hqdata.backends.hq_api import HQApiBackend
from connect_labs.hqdata.identity import hq_host
from connect_labs.hqdata.models import HQCase, HQIdentity, HQPipeline, HQProject, HQProjectMember, HQSource
from connect_labs.hqdata.sync import drop_labs_cache_copies
from connect_labs.hqdata.tests.conftest import make_hq_user
from connect_labs.hqdata.tests.test_web_views import SCHEMA, add_project, post
from connect_labs.labs.analysis.config import AnalysisPipelineConfig, DataSourceConfig
from connect_labs.labs.analysis.utils import get_config_hash
from connect_labs.mcp import token_scopes
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


def _config(**source):
    return AnalysisPipelineConfig(
        grouping_key="username", fields=[], data_source=DataSourceConfig(type="hq_store", project_id=3, **source)
    )


def test_two_pipelines_over_one_project_with_different_narrowing_do_not_share_a_cache_key():
    forms = get_config_hash(_config(store_kind="forms"))
    assert get_config_hash(_config(store_kind="forms", xmlns="http://x/1")) != forms
    assert get_config_hash(_config(store_kind="forms", app_id="app1")) != forms
    assert get_config_hash(_config(store_kind="cases", case_type="child")) != get_config_hash(
        _config(store_kind="cases", case_type="household")
    )
    assert get_config_hash(_config(store_kind="forms")) == forms  # and it is stable


def test_the_sync_owners_token_is_re_read_when_hq_rejects_the_one_a_long_job_started_with():
    owner = UserFactory()
    project = HQProject.objects.create(domain="demo", sync_owner=owner)
    with mock.patch(
        "connect_labs.labs.integrations.commcare.cchq_tokens.get_valid_cchq_access_token", return_value="old"
    ):
        access = HQApiBackend.for_project(project).access
    with mock.patch(
        "connect_labs.labs.integrations.commcare.cchq_tokens.get_valid_cchq_access_token", return_value="new"
    ):
        assert access._refresh_token() is True
        assert access.access_token == "new"
        assert access._refresh_token() is False  # nothing newer to try: the 401 then stands


def test_switching_back_to_labs_works_when_the_customers_database_has_gone_away():
    from connect_labs.hqdata.store_switch import switch_store
    from connect_labs.hqdata.stores import StoreUnavailable

    project = HQProject.objects.create(domain="demo", store=HQProject.STORE_EXTERNAL)
    with mock.patch("connect_labs.hqdata.store_switch.store_for", side_effect=StoreUnavailable("gone")):
        result = switch_store(project, HQProject.STORE_LABS)
    project.refresh_from_db()
    assert result["changed"] and project.store == HQProject.STORE_LABS


def test_deleting_a_project_or_leaving_labs_drops_the_copies_labs_cached():
    project = HQProject.objects.create(domain="demo")
    with mock.patch("connect_labs.labs.analysis.backends.sql.cache.SQLCacheManager.delete_all_cache") as drop:
        project.delete()
    assert drop.call_count == 1
    with mock.patch(
        "connect_labs.labs.analysis.backends.sql.cache.SQLCacheManager.delete_all_cache", side_effect=RuntimeError
    ):
        drop_labs_cache_copies(5)  # never raises: the rows are already purged


def test_one_commcare_account_can_be_both_a_connect_persons_link_and_a_commcare_only_login(client):
    connect_user = UserFactory()
    HQIdentity.objects.create(user=connect_user, hq_host=hq_host(), hq_username="simon@example.org", domains=[])
    make_hq_user(client, username="simon@example.org")  # the CommCare-only login: same host + username
    assert HQIdentity.objects.filter(hq_username="simon@example.org").count() == 2


def test_only_an_admin_can_take_over_syncing_and_taking_over_does_not_un_pause(client):
    from django.test import Client

    admin = make_hq_user(client, username="admin@example.org")
    project = add_project(admin, status=HQProject.STATUS_PAUSED, sync_owner=None)

    member_client = Client()
    member = make_hq_user(member_client, username="member@example.org")
    HQProjectMember.objects.create(project=project, user=member, role=HQProjectMember.ROLE_MEMBER)
    post(member_client, project, "take_over")
    project.refresh_from_db()
    assert project.sync_owner is None and project.status == HQProject.STATUS_PAUSED  # a member cannot

    post(client, project, "take_over")
    project.refresh_from_db()
    assert project.sync_owner == admin
    assert project.status == HQProject.STATUS_PAUSED  # a pause an admin chose stays


def test_a_commcare_token_can_follow_and_cancel_the_job_a_sync_hands_back_and_nothing_more():
    from types import SimpleNamespace

    from connect_labs.mcp import server as mcp_server

    token = SimpleNamespace(claims={"auth_method": "pat"}, scopes=[token_scopes.COMMCARE_SCOPE_STRING])
    assert mcp_server.allowed_tools(token) == token_scopes.COMMCARE_TOOLS | token_scopes.JOB_TOOLS
    narrowed = SimpleNamespace(
        claims={"auth_method": "pat"},
        scopes=[token_scopes.COMMCARE_SCOPE_STRING, token_scopes.TOKEN_SCOPE_STRINGS[token_scopes.NO_USERVISIT_DATA]],
    )
    assert mcp_server.allowed_tools(narrowed) == frozenset()


def test_renaming_a_pipeline_to_a_name_in_use_says_so_instead_of_failing(client):
    user = make_hq_user(client)
    project = add_project(user)
    HQPipeline.objects.create(project=project, name="taken", store_kind="forms", schema={}, created_by=user)
    mine = HQPipeline.objects.create(project=project, name="mine", store_kind="forms", schema={}, created_by=user)
    resp = client.post(
        f"/commcare/projects/{project.pk}/pipelines/{mine.pk}/",
        {"action": "save", "name": "taken", "store_kind": "forms", "schema": SCHEMA},
    )
    assert resp.status_code == 200 and b"already exists" in resp.content
    mine.refresh_from_db()
    assert mine.name == "mine"


def test_a_domain_listed_on_the_account_is_not_re_stamped_unless_the_person_can_still_read_it():
    alice = UserFactory()
    project = create_project(alice, "demo", hq_host=HOST, can_read_domain=lambda: True)
    old = timezone.now() - timedelta(days=3)
    HQProjectMember.objects.filter(project=project).update(verified_at=old)

    refresh_memberships(alice, ["demo"], hq_host=HOST, can_read=lambda domain: False)
    assert not may_use_project(alice, project)  # demoted: not re-stamped, so it ages out
    assert HQProjectMember.objects.filter(project=project).exists()  # and never deleted on a probe alone

    refresh_memberships(alice, ["demo"], hq_host=HOST, can_read=lambda domain: True)
    assert may_use_project(alice, project)


def test_a_case_property_named_like_a_lookup_filters_on_the_property():
    me = UserFactory()
    project = create_project(me, "demo", hq_host=HOST, can_read_domain=lambda: True)
    HQSource.objects.create(project=project, kind="cases")
    HQCase.objects.create(project_id=project.pk, case_id="c1", case_type="t", properties={"in": "yes", "regex": "x"})
    HQCase.objects.create(project_id=project.pk, case_id="c2", case_type="t", properties={"in": "no"})

    def ids(**props):
        got = get_tool("hq_cases").handler(user=me, project_id=project.pk, properties=props)
        return [c["case_id"] for c in got["cases"]]

    assert ids(**{"in": "yes"}) == ["c1"]
    assert ids(regex="x") == ["c1"]
    assert ids(**{"in": "no"}) == ["c2"]
