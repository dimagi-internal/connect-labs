"""Moving a project's data between labs and a customer's database."""

from datetime import timedelta

import pytest
from django.db import models
from django.utils import timezone

from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.models import HQCase, HQExternalDatabase, HQForm, HQProject, HQSource
from connect_labs.hqdata.store_switch import SwitchRefused, switch_store
from connect_labs.hqdata.sync import sync_source
from connect_labs.hqdata.tests.conftest import read_customer
from connect_labs.hqdata.tests.test_sync import T0, FakeBackend, case, form
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


@pytest.fixture
def project(customer_db):
    project = create_project(UserFactory(), "demo", hq_host=HOST, can_read_domain=lambda: True)
    HQExternalDatabase.objects.create(
        project=project,
        host=customer_db["host"],
        port=customer_db["port"],
        dbname=customer_db["dbname"],
        username=customer_db["username"],
        password=customer_db["password"],
        schema="labs_commcare",
        sslmode="disable",
    )
    return project


@pytest.fixture
def sources(project):
    return (
        HQSource.objects.create(project=project, kind="forms"),
        HQSource.objects.create(project=project, kind="cases"),
    )


def customer_forms(customer_db):
    return read_customer(customer_db, "SELECT form_id FROM labs_commcare.labs_commcare_form ORDER BY 1")


def test_moving_to_the_customers_database_leaves_nothing_in_labs_and_starts_the_read_again(
    project, sources, customer_db
):
    forms, cases = sources
    sync_source(forms, FakeBackend([form("f1"), form("f2")]), now=T0)
    sync_source(cases, FakeBackend([case("c1")]), now=T0)
    assert HQForm.objects.filter(project_id=project.pk).count() == 2

    outcome = switch_store(project, "external")

    assert outcome == {"changed": True, "purged": {"forms": 2, "cases": 1}}
    project.refresh_from_db()
    assert project.store == "external"
    assert (
        HQForm.objects.filter(project_id=project.pk).count() == 0
        and HQCase.objects.filter(project_id=project.pk).count() == 0
    )
    for source in (forms, cases):
        source.refresh_from_db()
        assert source.watermark is None and not source.backfill_complete and source.last_reconciled_at is None
    assert HQExternalDatabase.objects.get(project=project).verified_at is not None

    sync_source(forms, FakeBackend([form("f1"), form("f2"), form("f3")]), now=T0 + timedelta(hours=1))
    assert customer_forms(customer_db) == [("f1",), ("f2",), ("f3",)]
    assert HQForm.objects.filter(project_id=project.pk).count() == 0


def test_moving_back_removes_what_labs_put_in_the_customers_database_but_leaves_their_tables(
    project, sources, customer_db
):
    forms, _ = sources
    switch_store(project, "external")
    sync_source(forms, FakeBackend([form("f1")]), now=T0)
    assert customer_forms(customer_db) == [("f1",)]

    outcome = switch_store(project, "labs")

    assert outcome["purged"] == {"forms": 1, "cases": 0}
    assert customer_forms(customer_db) == []
    assert (
        read_customer(
            customer_db, "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'labs_commcare'"
        )[0][0]
        == 9  # their tables stay, and labs leaves its own (2 of them its pipelines' progress)
    )
    project.refresh_from_db()
    assert project.store == "labs"
    sync_source(forms, FakeBackend([form("f9")]), now=T0 + timedelta(hours=1))
    assert list(HQForm.objects.filter(project_id=project.pk).values_list("form_id", flat=True)) == ["f9"]


def test_switching_to_the_store_it_is_already_in_does_nothing(project, sources):
    assert switch_store(project, "labs") == {"changed": False}


def test_it_refuses_while_a_sync_is_running(project, sources):
    sources[0].claim()
    with pytest.raises(SwitchRefused, match="running"):
        switch_store(project, "external")
    project.refresh_from_db()
    assert project.store == "labs"


def test_a_lease_nobody_has_touched_for_half_an_hour_is_not_a_running_sync(project, sources, customer_db):
    HQSource.objects.filter(pk=sources[0].pk).update(running_since=timezone.now() - timedelta(hours=1))
    assert switch_store(project, "external")["changed"]


def test_it_refuses_a_database_that_has_not_just_passed_every_check(project, sources, customer_db):
    forms, _ = sources
    sync_source(forms, FakeBackend([form("f1")]), now=T0)
    HQExternalDatabase.objects.filter(project=project).update(password="wrong")
    with pytest.raises(SwitchRefused, match="connect"):
        switch_store(project, "external")
    project.refresh_from_db()
    forms.refresh_from_db()
    assert (
        project.store == "labs" and HQForm.objects.filter(project_id=project.pk).count() == 1 and forms.watermark == T0
    )


def test_it_refuses_when_there_is_no_database_to_move_to(sources):
    project = HQProject.objects.create(domain="other")
    with pytest.raises(SwitchRefused, match="Set up the database"):
        switch_store(project, "external")


def test_it_refuses_a_store_that_does_not_exist(project):
    with pytest.raises(SwitchRefused):
        switch_store(project, "somewhere-else")


def test_leaving_a_database_that_has_gone_away_still_works(project, sources, customer_db):
    switch_store(project, "external")
    HQExternalDatabase.objects.filter(project=project).update(port=1)
    outcome = switch_store(project, "labs")
    assert outcome["changed"] and outcome["purged"] is None
    project.refresh_from_db()
    assert project.store == "labs"


def test_deleting_a_project_removes_its_rows_from_the_customers_database(project, sources, customer_db):
    forms, _ = sources
    switch_store(project, "external")
    sync_source(forms, FakeBackend([form("f1")]), now=T0)
    other = create_project(UserFactory(), "other", hq_host=HOST, can_read_domain=lambda: True)
    HQExternalDatabase.objects.create(
        project=other,
        host=customer_db["host"],
        port=customer_db["port"],
        dbname=customer_db["dbname"],
        username=customer_db["username"],
        password=customer_db["password"],
        schema="labs_commcare",
        sslmode="disable",
    )
    HQProject.objects.filter(pk=other.pk).update(store="external")
    other.refresh_from_db()
    sync_source(HQSource.objects.create(project=other, kind="forms"), FakeBackend([form("theirs")]), now=T0)
    assert customer_forms(customer_db) == [("f1",), ("theirs",)] or len(customer_forms(customer_db)) == 2

    project.delete()

    assert not HQProject.objects.filter(pk=project.pk).exists()
    assert not HQExternalDatabase.objects.filter(project_id=project.pk).exists()
    assert customer_forms(customer_db) == [("theirs",)]  # the other project's row is untouched


def test_a_project_can_be_deleted_even_when_its_database_cannot_be_reached(project, sources, customer_db):
    switch_store(project, "external")
    HQExternalDatabase.objects.filter(project=project).update(port=1)
    project.delete()
    assert not HQProject.objects.filter(pk=project.pk).exists()


def test_the_connection_cannot_be_deleted_while_the_project_keeps_its_data_there(project, sources, customer_db):
    switch_store(project, "external")
    with pytest.raises(models.ProtectedError):
        HQExternalDatabase.objects.get(project=project).delete()
    switch_store(project, "labs")
    HQExternalDatabase.objects.get(project=project).delete()
    assert not HQExternalDatabase.objects.filter(project=project).exists()
