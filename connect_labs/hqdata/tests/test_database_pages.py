"""The project page's database controls."""

from unittest import mock

import pytest

from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.models import HQExternalDatabase, HQForm, HQProjectMember, HQSource
from connect_labs.hqdata.sync import sync_source
from connect_labs.hqdata.tests.conftest import make_hq_user
from connect_labs.hqdata.tests.test_sync import T0, FakeBackend, form
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


def post(client, project, action, **fields):
    return client.post(f"/hq/projects/{project.pk}/act/", {"action": action, **fields}, follow=True)


def details(customer_db, **over):
    return {
        "host": customer_db["host"],
        "port": str(customer_db["port"]),
        "dbname": customer_db["dbname"],
        "schema": "labs_commcare",
        "username": customer_db["username"],
        "password": customer_db["password"],
        "sslmode": "verify-full",
        **over,
    }


@pytest.fixture
def mine(client, customer_db):
    user = make_hq_user(client)
    project = create_project(user, "demo", hq_host=HOST, can_read_domain=lambda: True)
    with mock.patch("connect_labs.hqdata.web.views.check_host", return_value=""):
        yield project


def save_usable(client, project, customer_db, **over):
    """Save through the page, then relax TLS the way only a test can: the test cluster has none."""
    post(client, project, "save_database", **details(customer_db, **over))
    HQExternalDatabase.objects.filter(project=project).update(sslmode="disable")


def messages_of(response):
    return [str(m) for m in response.context["messages"]]


def test_saving_the_details_keeps_the_password_out_of_every_page(client, mine, customer_db):
    resp = post(client, mine, "save_database", **details(customer_db))
    record = HQExternalDatabase.objects.get(project=mine)
    assert record.password == customer_db["password"] and record.host == customer_db["host"]
    page = client.get(f"/hq/projects/{mine.pk}/").content.decode()
    assert customer_db["password"] not in page and customer_db["password"] not in resp.content.decode()
    assert 'placeholder="unchanged"' in page


def test_a_blank_password_keeps_the_one_saved(client, mine, customer_db):
    post(client, mine, "save_database", **details(customer_db))
    post(client, mine, "save_database", **details(customer_db, password="", dbname="renamed"))
    record = HQExternalDatabase.objects.get(project=mine)
    assert record.password == customer_db["password"] and record.dbname == "renamed"


@pytest.mark.parametrize(
    "over,message",
    [
        ({"sslmode": "disable"}, "certificate"),
        ({"schema": 'x"; DROP'}, "schema"),
        ({"port": "99999"}, "port"),
        ({"dbname": ""}, "database name"),
        ({"password": ""}, "password"),
    ],
)
def test_bad_details_are_refused_with_the_reason_and_nothing_is_saved(client, mine, customer_db, over, message):
    resp = post(client, mine, "save_database", **details(customer_db, **over))
    assert not HQExternalDatabase.objects.filter(project=mine).exists()
    assert any(message in m for m in messages_of(resp))


def test_a_host_labs_will_not_connect_to_is_refused_at_save(client, customer_db):
    user = make_hq_user(client)
    project = create_project(user, "demo", hq_host=HOST, can_read_domain=lambda: True)
    resp = post(client, project, "save_database", **details(customer_db, host="169.254.169.254"))
    assert not HQExternalDatabase.objects.filter(project=project).exists()
    assert any("link-local" in m for m in messages_of(resp))


def test_only_a_project_admin_can_touch_the_database(client, customer_db):
    admin = UserFactory()
    project = create_project(admin, "demo", hq_host=HOST, can_read_domain=lambda: True)
    member = make_hq_user(client, username="m@example.org")
    HQProjectMember.objects.create(project=project, user=member, role="member")
    for action in ("save_database", "check_database", "use_database", "use_labs", "remove_database"):
        resp = post(client, project, action, **details(customer_db))
        assert any("project admin" in m for m in messages_of(resp)), action
    assert not HQExternalDatabase.objects.filter(project=project).exists()
    page = client.get(f"/hq/projects/{project.pk}/").content.decode()
    assert "Use a database you host" not in page


def test_checking_the_connection_reports_each_step(client, mine, customer_db):
    save_usable(client, mine, customer_db)
    resp = post(client, mine, "check_database")
    text = " | ".join(messages_of(resp))
    assert "Labs can connect and sign in" in text and "Labs can add, change and remove rows" in text
    assert customer_db["password"] not in text


def test_using_the_database_moves_the_project_and_queues_a_fresh_read(client, mine, customer_db):
    save_usable(client, mine, customer_db)
    source = HQSource.objects.create(project=mine, kind="forms")
    sync_source(source, FakeBackend([form("f1")]), now=T0)
    with mock.patch("connect_labs.hqdata.web.views.sync_project_task") as queued:
        resp = post(client, mine, "use_database")
    mine.refresh_from_db()
    assert mine.store == "external" and not HQForm.objects.filter(project_id=mine.pk).exists()
    queued.delay.assert_called_once_with(mine.pk)
    assert any("in your database" in m for m in messages_of(resp))
    page = client.get(f"/hq/projects/{mine.pk}/").content.decode()
    assert "Labs keeps none of this project" in page and customer_db["password"] not in page


def test_a_database_that_fails_its_checks_is_not_moved_to(client, mine, customer_db):
    save_usable(client, mine, customer_db, password="wrong")
    with mock.patch("connect_labs.hqdata.web.views.sync_project_task") as queued:
        resp = post(client, mine, "use_database")
    mine.refresh_from_db()
    assert mine.store == "labs" and not queued.delay.called
    assert any("connect" in m for m in messages_of(resp))


def test_where_the_project_keeps_its_data_pins_the_connection_except_for_the_password(client, mine, customer_db):
    save_usable(client, mine, customer_db)
    with mock.patch("connect_labs.hqdata.web.views.sync_project_task"):
        post(client, mine, "use_database")
    resp = post(client, mine, "save_database", **details(customer_db, host="elsewhere.example.org", password=""))
    assert any("Move it back to labs" in m for m in messages_of(resp))
    assert HQExternalDatabase.objects.get(project=mine).host == customer_db["host"]
    # ...but rotating the password is exactly what must stay possible
    post(client, mine, "save_database", **details(customer_db, password=customer_db["password"]))
    resp = post(client, mine, "remove_database")
    assert (
        any("Move it back" in m for m in messages_of(resp))
        and HQExternalDatabase.objects.filter(project=mine).exists()
    )


def test_details_can_be_removed_once_the_data_is_back_in_labs(client, mine, customer_db):
    post(client, mine, "save_database", **details(customer_db))
    post(client, mine, "remove_database")
    assert not HQExternalDatabase.objects.filter(project=mine).exists()
