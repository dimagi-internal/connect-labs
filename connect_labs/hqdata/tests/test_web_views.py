from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.models import HQCase, HQForm, HQProject, HQProjectMember, HQSource, HQSyncRun
from connect_labs.hqdata.tests.conftest import make_hq_user
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


def add_project(user, domain="demo", **kwargs):
    project = create_project(user, domain, hq_host=HOST, can_read_domain=lambda: True)
    for kind in (HQSource.KIND_FORMS, HQSource.KIND_CASES):
        HQSource.objects.get_or_create(project=project, kind=kind)
    if kwargs:
        HQProject.objects.filter(pk=project.pk).update(**kwargs)
        project.refresh_from_db()
    return project


def post(client, project, action, **fields):
    return client.post(f"/hq/projects/{project.pk}/act/", {"action": action, **fields})


# -- the door ---------------------------------------------------------------


def test_anonymous_goes_to_the_login_page_and_comes_back(client):
    resp = client.get("/hq/projects/3/")
    assert resp.status_code == 302 and resp["Location"] == "/hq/login/?next=/hq/projects/3/"
    assert client.get("/hq/login/").status_code == 200


def test_a_connect_person_without_commcare_is_sent_to_connect_theirs_first(client):
    connect_user = UserFactory()
    client.force_login(connect_user)
    resp = client.get("/hq/")
    assert resp.status_code == 302 and resp["Location"] == "/labs/commcare/initiate/"
    assert client.session["commcare_oauth_next"] == "/hq/"


def test_a_commcare_session_whose_token_is_gone_is_signed_out(client):
    user = make_hq_user(client)
    user.cchq_token.delete()
    resp = client.get("/hq/")
    assert resp.status_code == 302 and resp["Location"].startswith("/hq/login/")
    assert "_auth_user_id" not in client.session


def test_a_connect_person_with_commcare_connected_uses_the_same_pages(client):
    user = UserFactory()
    from connect_labs.labs.models import UserCCHQToken

    UserCCHQToken.objects.create(
        user=user, access_token="a", refresh_token="r", expires_at=timezone.now() + timedelta(hours=1)
    )
    client.force_login(user)
    with mock.patch(
        "connect_labs.hqdata.identity.fetch_identity",
        return_value={"username": "ann@example.org", "email": "ann@example.org", "name": "Ann", "domains": ["demo"]},
    ):
        resp = client.get("/hq/")
    assert resp.status_code == 200 and b"demo" in resp.content


# -- projects ---------------------------------------------------------------


def test_home_lists_my_projects_and_offers_only_unregistered_domains(client):
    user = make_hq_user(client, domains=("demo", "other", "third"))
    add_project(user, "demo")
    stranger = UserFactory()
    add_project(stranger, "other")  # registered by someone else: I am not a member
    body = client.get("/hq/").content.decode()
    assert "/hq/projects/" in body and ">demo<" in body
    assert '<option value="third">' in body and '<option value="demo">' not in body
    assert '<option value="other">' not in body  # already registered: joining is not "adding"


def test_adding_a_project_needs_commcare_to_list_it_and_to_let_me_read_it(client):
    user = make_hq_user(client, domains=("demo",))
    with mock.patch("connect_labs.hqdata.web.views.CommCareDataAccess") as access:
        access.return_value.verify_hq_access.return_value = True
        resp = client.post("/hq/projects/new/", {"domain": "demo"})
    project = HQProject.objects.get(domain="demo")
    assert resp["Location"] == f"/hq/projects/{project.pk}/"
    assert project.sync_owner == user
    assert set(project.sources.values_list("kind", flat=True)) == {"forms", "cases"}
    assert HQProjectMember.objects.get(project=project, user=user).role == "admin"
    access.assert_called_once_with(None, "demo", cchq_access_token="hq-access")


def test_a_domain_commcare_does_not_list_for_me_is_never_even_tried(client):
    make_hq_user(client, domains=("demo",))
    with mock.patch("connect_labs.hqdata.web.views.CommCareDataAccess") as access:
        client.post("/hq/projects/new/", {"domain": "someone-elses"})
    assert not HQProject.objects.exists() and not access.called


def test_commcare_refusing_to_let_me_read_the_forms_creates_nothing(client):
    make_hq_user(client, domains=("demo",))
    with mock.patch("connect_labs.hqdata.web.views.CommCareDataAccess") as access:
        access.return_value.verify_hq_access.return_value = False
        resp = client.post("/hq/projects/new/", {"domain": "demo"}, follow=True)
    assert not HQProject.objects.exists() and b"Edit Data" in resp.content


@pytest.mark.parametrize("domain", ["", "../x", "A B", "a" * 64, "demo/../x"])
def test_a_malformed_domain_is_refused_before_anything_else(client, domain):
    make_hq_user(client, domains=(domain,) if domain else ())
    with mock.patch("connect_labs.hqdata.web.views.CommCareDataAccess") as access:
        client.post("/hq/projects/new/", {"domain": domain})
    assert not HQProject.objects.exists() and not access.called


def test_the_project_page_shows_what_is_stored_and_how_it_is_going(client):
    user = make_hq_user(client)
    project = add_project(user)
    HQForm.objects.create(project_id=project.pk, form_id="f1")
    HQForm.objects.create(project_id=project.pk, form_id="f2", deleted_at=timezone.now())
    HQCase.objects.create(project_id=project.pk, case_id="c1")
    HQSyncRun.objects.create(source=project.sources.first(), mode="full", status="error", error="HQ went away")
    body = client.get(f"/hq/projects/{project.pk}/").content.decode()
    assert "1 forms and 1 cases stored" in body and "HQ went away" in body and "Delete this project" in body


def test_a_project_i_am_not_in_is_not_found_whether_or_not_it_exists(client):
    make_hq_user(client)
    other = add_project(UserFactory(), "secret")
    assert client.get(f"/hq/projects/{other.pk}/").status_code == 404
    assert client.get("/hq/projects/99999/").status_code == 404
    assert post(client, other, "sync_now").status_code == 404
    assert post(client, other, "delete_project", confirm_domain="secret").status_code == 404
    assert HQProject.objects.filter(pk=other.pk).exists()


def test_an_expired_membership_is_not_found_either(client):
    user = make_hq_user(client)
    project = add_project(user)
    HQProjectMember.objects.filter(project=project).update(verified_at=timezone.now() - timedelta(days=2))
    # sync_identity restamps only domains CommCare still lists for me, and it lists none:
    from connect_labs.hqdata.models import HQIdentity

    HQIdentity.objects.filter(user=user).update(domains=[], refreshed_at=timezone.now())
    assert client.get(f"/hq/projects/{project.pk}/").status_code == 404


# -- actions ----------------------------------------------------------------


def test_any_member_can_sync_but_only_an_admin_can_change_storage(client, queued):
    admin = UserFactory()
    project = add_project(admin)
    member = make_hq_user(client, username="m@example.org")
    HQProjectMember.objects.create(project=project, user=member, role="member")

    post(client, project, "sync_now")
    queued.delay.assert_called_once_with(project.pk)

    post(client, project, "set_storage", storage_mode="persistent")
    project.refresh_from_db()
    assert project.storage_mode == "live" and project.sync_owner == admin
    assert HQProjectMember.objects.get(project=project, user=member).role == "member"
    for action in ("pause", "full_resync", "add_source", "toggle_source", "delete_project"):
        post(client, project, action, confirm_domain="demo", kind="cases")
    project.refresh_from_db()
    assert project.status == "active" and HQProject.objects.filter(pk=project.pk).exists()
    assert queued.delay.call_count == 1


def test_an_admin_turning_persistence_on_takes_the_sync_on_with_their_own_sign_in(client):
    user = make_hq_user(client)
    project = add_project(user)
    HQProject.objects.filter(pk=project.pk).update(sync_owner=None, status="needs_reauth")
    HQSource.objects.filter(project=project).delete()
    post(client, project, "set_storage", storage_mode="persistent")
    project.refresh_from_db()
    assert project.is_persistent and project.sync_owner == user and project.status == "active"
    assert project.sources.count() == 2


def test_nobody_to_sync_as_is_said_not_silently_queued(client, queued):
    user = make_hq_user(client)
    project = add_project(user, sync_owner=None)
    resp = post(client, project, "sync_now")
    assert not queued.delay.called and resp.status_code == 302
    post(client, project, "take_over")
    project.refresh_from_db()
    assert project.sync_owner == user
    post(client, project, "sync_now")
    queued.delay.assert_called_once_with(project.pk)


def test_a_full_re_read_asks_for_a_reconcile(client, queued):
    user = make_hq_user(client)
    project = add_project(user)
    post(client, project, "full_resync")
    queued.delay.assert_called_once_with(project.pk, HQSyncRun.MODE_RECONCILE)


def test_adding_a_source_narrows_it_and_a_duplicate_is_refused(client):
    user = make_hq_user(client)
    project = add_project(user)
    post(client, project, "add_source", kind="cases", case_type="child", app_id="ignored")
    source = project.sources.get(case_type="child")
    assert source.app_id == "" and source.kind == "cases"
    post(client, project, "add_source", kind="cases", case_type="child")
    assert project.sources.filter(case_type="child").count() == 1
    post(client, project, "add_source", kind="forms", app_id="app1", xmlns="http://x/1")
    assert project.sources.filter(kind="forms", app_id="app1", xmlns="http://x/1").exists()
    post(client, project, "add_source", kind="nonsense")
    assert project.sources.count() == 4


def test_turning_a_source_off_is_scoped_to_this_project(client):
    user = make_hq_user(client)
    project = add_project(user)
    elsewhere = add_project(UserFactory(), "other")
    foreign = elsewhere.sources.first()
    assert post(client, project, "toggle_source", source_id=foreign.pk).status_code == 404
    foreign.refresh_from_db()
    assert foreign.enabled
    mine = project.sources.first()
    post(client, project, "toggle_source", source_id=mine.pk)
    mine.refresh_from_db()
    assert not mine.enabled


def test_deleting_a_project_needs_its_name_and_removes_its_data_and_only_its_data(client):
    user = make_hq_user(client)
    project = add_project(user)
    other = add_project(UserFactory(), "other")
    HQForm.objects.create(project_id=project.pk, form_id="f1")
    HQForm.objects.create(project_id=other.pk, form_id="f2")

    post(client, project, "delete_project", confirm_domain="nope")
    assert HQProject.objects.filter(pk=project.pk).exists() and HQForm.objects.filter(project_id=project.pk).exists()

    resp = post(client, project, "delete_project", confirm_domain="DEMO")
    assert resp["Location"] == "/hq/"
    assert not HQProject.objects.filter(pk=project.pk).exists()
    assert not HQForm.objects.filter(project_id=project.pk).exists()
    assert HQForm.objects.filter(project_id=other.pk).exists()


def test_actions_are_post_only(client):
    user = make_hq_user(client)
    project = add_project(user)
    assert client.get(f"/hq/projects/{project.pk}/act/").status_code == 405
    assert client.get("/hq/projects/new/").status_code == 405


# -- pipelines --------------------------------------------------------------

SCHEMA = (
    '{"grouping_key": "username", "terminal_stage": "aggregated", '
    '"fields": [{"name": "forms", "path": "form.meta.username", "aggregation": "count"}]}'
)


def store_a_form(project, form_id, username):
    HQForm.objects.create(
        project_id=project.pk,
        form_id=form_id,
        xmlns="http://x/1",
        form_json={"id": form_id, "received_on": "2026-11-01T10:00:00Z", "form": {"meta": {"username": username}}},
    )


def test_saving_a_pipeline_and_running_it_shows_the_rows(client):
    user = make_hq_user(client)
    project = add_project(user)
    store_a_form(project, "f1", "ann")
    store_a_form(project, "f2", "ann")
    resp = client.post(
        f"/hq/projects/{project.pk}/pipelines/", {"name": "per worker", "store_kind": "forms", "schema": SCHEMA}
    )
    pipeline = project.pipelines.get(name="per worker")
    assert resp["Location"] == f"/hq/projects/{project.pk}/pipelines/{pipeline.pk}/" and pipeline.created_by == user

    resp = client.post(
        f"/hq/projects/{project.pk}/pipelines/{pipeline.pk}/",
        {"action": "run", "name": "per worker", "store_kind": "forms", "schema": SCHEMA},
    )
    body = resp.content.decode()
    assert resp.status_code == 200 and "1 row" in body and "ann" in body and '<th class="p-2">forms</th>' in body


@pytest.mark.parametrize(
    "schema,message",
    [
        ("{not json", "not valid JSON"),
        ("[1, 2]", "must be a JSON object"),
        ('{"fields": []}', "list of fields"),
        ('{"fields": [{"name": "n", "path": "form.x", "aggregation": "count"}], "joins": [{"a": 1}]}', "join"),
    ],
)
def test_a_schema_that_cannot_run_is_refused_with_the_reason(client, schema, message):
    user = make_hq_user(client)
    project = add_project(user)
    resp = client.post(
        f"/hq/projects/{project.pk}/pipelines/",
        {"name": "bad", "store_kind": "forms", "schema": schema},
        follow=True,
    )
    assert not project.pipelines.exists() and message in resp.content.decode()


def test_a_duplicate_name_is_refused(client):
    user = make_hq_user(client)
    project = add_project(user)
    for _ in range(2):
        client.post(f"/hq/projects/{project.pk}/pipelines/", {"name": "p", "store_kind": "forms", "schema": SCHEMA})
    assert project.pipelines.count() == 1


def test_pipelines_of_a_project_i_am_not_in_do_not_exist_for_me(client):
    make_hq_user(client)
    other = add_project(UserFactory(), "secret")
    from connect_labs.hqdata.models import HQPipeline

    pipeline = HQPipeline.objects.create(
        project=other,
        name="p",
        store_kind="forms",
        schema={"fields": [{"name": "n", "path": "form.x", "aggregation": "count"}]},
    )
    assert client.get(f"/hq/projects/{other.pk}/pipelines/").status_code == 404
    assert (
        client.post(
            f"/hq/projects/{other.pk}/pipelines/", {"name": "x", "store_kind": "forms", "schema": SCHEMA}
        ).status_code
        == 404
    )
    assert client.get(f"/hq/projects/{other.pk}/pipelines/{pipeline.pk}/").status_code == 404
    assert client.post(f"/hq/projects/{other.pk}/pipelines/{pipeline.pk}/", {"action": "run"}).status_code == 404
    # and a pipeline id from one project does not open under another I do belong to
    mine = add_project(HQProject.objects.get(pk=other.pk).members.first().user, "mine")
    assert mine is not None


def test_a_pipeline_id_from_another_project_does_not_open_under_mine(client):
    user = make_hq_user(client)
    mine = add_project(user)
    other = add_project(UserFactory(), "secret")
    from connect_labs.hqdata.models import HQPipeline

    pipeline = HQPipeline.objects.create(project=other, name="p", store_kind="forms", schema={})
    assert client.get(f"/hq/projects/{mine.pk}/pipelines/{pipeline.pk}/").status_code == 404


def test_only_its_author_or_an_admin_can_delete_a_pipeline(client):
    admin = UserFactory()
    project = add_project(admin)
    from connect_labs.hqdata.models import HQPipeline

    author = make_hq_user(client, username="a@example.org")
    HQProjectMember.objects.create(project=project, user=author, role="member")
    other_members_pipeline = HQPipeline.objects.create(
        project=project, name="theirs", store_kind="forms", schema={}, created_by=admin
    )
    mine = HQPipeline.objects.create(project=project, name="mine", store_kind="forms", schema={}, created_by=author)

    client.post(f"/hq/projects/{project.pk}/pipelines/{other_members_pipeline.pk}/", {"action": "delete"})
    assert HQPipeline.objects.filter(pk=other_members_pipeline.pk).exists()
    client.post(f"/hq/projects/{project.pk}/pipelines/{mine.pk}/", {"action": "delete"})
    assert not HQPipeline.objects.filter(pk=mine.pk).exists()
