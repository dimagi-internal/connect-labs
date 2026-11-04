"""Editing a supplier's contacts (Sophie's RUTF feedback, Suppliers: contacts that drive the emails).

THIS REPOSITORY IS PUBLIC. Every name and address here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.reference.people import contacts_problem, merge_contacts
from connect_labs.supply_chain.tests.test_issue_screens import user  # noqa: F401
from connect_labs.supply_chain.tests.test_issue_screens import PROGRAM, SCOPE  # noqa: F401
from connect_labs.supply_chain.tests.test_issue_screens import scoped as _scoped  # noqa: F401

pytestmark = pytest.mark.django_db


def test_merge_keeps_unseen_keys_and_drops_blank_rows():
    stored = [{"name": "Ann", "email": "ann@x.invalid", "tag": "kept"}]
    rows = [
        {"name": "Ann Ade", "email": "ann@x.invalid", "use": "to", "source_index": 0},
        {"name": "", "email": "", "phone": ""},
    ]
    assert merge_contacts(stored, rows) == [{"name": "Ann Ade", "email": "ann@x.invalid", "use": "to", "tag": "kept"}]


def test_problems():
    assert contacts_problem([{"use": "to"}, {"use": "to"}])
    assert contacts_problem([{"use": "cc", "email": "a@x.invalid"}])
    assert contacts_problem([{"use": "cc"}, {"use": "to"}, {"use": "history"}]) == ""
    assert contacts_problem([{"name": "no markers at all"}]) == ""


@pytest.fixture
def scoped(_scoped, monkeypatch):  # noqa: F811
    """The shared screen fixture, plus the reference screens' own copy of `_access`."""
    from connect_labs.supply_chain import views
    from connect_labs.supply_chain.reference import views as reference_views

    monkeypatch.setattr(reference_views, "_access", views._access)
    return _scoped


def _make(scoped_client, contacts):
    from connect_labs.labs.models import LabsOrg
    from connect_labs.supply_chain.models import Supplier, SupplierProfile

    org = LabsOrg.objects.create(slug="dabs-foods", name="Dabs Foods", country="NG")
    SupplierProfile.objects.create(org=org, contacts=contacts)
    return Supplier.objects.create(scope_key=SCOPE, org=org)


def _post(rows, total=None):
    data = {
        "contacts-TOTAL_FORMS": total if total is not None else len(rows),
        "contacts-INITIAL_FORMS": 0,
        "contacts-MIN_NUM_FORMS": 0,
        "contacts-MAX_NUM_FORMS": 1000,
    }
    for i, row in enumerate(rows):
        for key, value in row.items():
            data[f"contacts-{i}-{key}"] = value
    return data


def test_the_page_shows_what_is_stored(scoped):
    s = _make(scoped, [{"name": "Ann Ade", "email": "ann@dabs.example.invalid", "use": "to", "role": "sales"}])
    page = scoped.get(reverse("supply_chain:supplier_contacts", args=[s.pk]))
    assert page.status_code == 200
    assert b"ann@dabs.example.invalid" in page.content
    detail = scoped.get(reverse("supply_chain:supplier_detail", args=[s.pk]))
    assert b"Edit contacts" in detail.content and b"main contact" in detail.content


def test_saving_sets_to_cc_and_history(scoped):
    s = _make(scoped, [])
    rows = [
        {"name": "Ann Ade", "email": "Ann Ade <ann@dabs.example.invalid>", "use": "to", "phone": "+234 000"},
        {"name": "Cy Chi", "email": "cy@dabs.example.invalid", "use": "cc"},
        {"name": "Old", "email": "old@dabs.example.invalid", "use": "history"},
    ]
    response = scoped.post(reverse("supply_chain:supplier_contacts", args=[s.pk]), _post(rows))
    assert response.status_code == 302
    s.refresh_from_db()
    from connect_labs.supply_chain.procurement.services.contacts import recipients

    r = recipients(s)
    assert r.to == "ann@dabs.example.invalid" and r.cc == ["cy@dabs.example.invalid"]
    assert s.contacts[0]["phone"] == "+234 000"
    assert s.contacts[2]["use"] == "history"


def test_two_main_contacts_are_refused_and_nothing_is_lost(scoped):
    s = _make(scoped, [{"name": "Ann", "email": "ann@dabs.example.invalid"}])
    rows = [
        {"name": "A", "email": "a@dabs.example.invalid", "use": "to"},
        {"name": "B", "email": "b@dabs.example.invalid", "use": "to"},
    ]
    response = scoped.post(reverse("supply_chain:supplier_contacts", args=[s.pk]), _post(rows))
    assert response.status_code == 200 and b"Only one person can be the main contact" in response.content
    s.refresh_from_db()
    assert s.contacts == [{"name": "Ann", "email": "ann@dabs.example.invalid"}]


def test_an_address_that_is_not_one_is_refused(scoped):
    s = _make(scoped, [])
    response = scoped.post(
        reverse("supply_chain:supplier_contacts", args=[s.pk]), _post([{"name": "A", "email": "ask by phone"}])
    )
    assert response.status_code == 200 and b"does not contain an email address" in response.content


def test_removing_a_row_removes_the_contact(scoped):
    s = _make(scoped, [{"name": "Ann", "email": "ann@dabs.example.invalid"}])
    rows = [{"name": "Ann", "email": "ann@dabs.example.invalid", "source_index": 0, "DELETE": "on"}]
    scoped.post(reverse("supply_chain:supplier_contacts", args=[s.pk]), _post(rows))
    s.refresh_from_db()
    assert s.contacts == []
