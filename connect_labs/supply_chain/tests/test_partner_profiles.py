"""A partner's main contact and defaults: the data award emails and alerts read.

Placeholder names only (public repo).
"""

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import PartnerProfile, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.partners import services

pytestmark = pytest.mark.django_db

PROGRAM = 10984


def _op(name, **payload):
    return call_operation(
        name, SupplyDataAccess(access_token="placeholder", program_id=PROGRAM, caller=SYSTEM), payload
    )


@pytest.fixture
def partner():
    return LabsOrg.objects.create(slug="partner-b", name="Partner B")


def test_a_partner_with_no_profile_has_an_empty_main_contact(partner):
    contact = services.main_contact(PROGRAM, partner.pk)
    assert contact["email"] == "" and contact["copy_to"] == []


def test_setting_the_profile_is_read_back_as_the_main_contact(partner):
    place = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="wh", name="Warehouse", kind="central_store", source="we_recorded"
    )
    result = _op(
        "partner_profile_set",
        data={
            "org_id": partner.pk,
            "contact_name": "Ada Lead",
            "contact_email": "ada@example.org",
            "contact_phone": "+000 1",
            "contact_connect_username": "ada.lead",
            "copy_to": [{"name": "Sam", "email": "sam@example.org"}],
            "default_buying_model": "partner_buys",
            "handout_every_days": 14,
            "handout_from_id": place.pk,
        },
    )
    assert result["contact_email"] == "ada@example.org"
    contact = services.main_contact(PROGRAM, partner.pk)
    assert contact["name"] == "Ada Lead" and contact["connect_username"] == "ada.lead"
    assert contact["copy_to"] == [{"name": "Sam", "email": "sam@example.org"}]
    assert _op("partner_profile_list")[0]["handout_from_id"] == place.pk


def test_a_second_set_changes_only_the_fields_sent(partner):
    _op("partner_profile_set", data={"org_id": partner.pk, "contact_email": "a@example.org", "contact_name": "A"})
    _op("partner_profile_set", data={"org_id": partner.pk, "contact_phone": "123"})
    assert PartnerProfile.objects.filter(org=partner).count() == 1
    profile = PartnerProfile.objects.get(org=partner)
    assert (profile.contact_email, profile.contact_name, profile.contact_phone) == ("a@example.org", "A", "123")


def test_a_bad_email_or_foreign_place_is_refused(partner):
    with pytest.raises(ValueError):
        _op("partner_profile_set", data={"org_id": partner.pk, "contact_email": "nope"})
    with pytest.raises(ValueError):
        _op("partner_profile_set", data={"org_id": partner.pk, "handout_from_id": 99999999})


def test_copy_to_text_round_trips():
    from connect_labs.supply_chain.partners.forms import copy_to_text, parse_copy_to

    people = parse_copy_to("Sam <sam@example.org>\nlee@example.org, Kim <kim@example.org>")
    assert people == [
        {"name": "Sam", "email": "sam@example.org"},
        {"name": "", "email": "lee@example.org"},
        {"name": "Kim", "email": "kim@example.org"},
    ]
    assert parse_copy_to(copy_to_text(people)) == people


# ---- the screens ------------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.partners import views as partner_views  # noqa: F401

    account = django_user_model.objects.create_user(username="pm2", password="x", email="pm2@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "partners.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


def test_the_contact_form_saves_and_the_partners_card_shows_it(scoped, partner):
    SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="wh2",
        name="Warehouse",
        kind="central_store",
        managed_by_org=partner,
        source="we_recorded",
    )
    page = scoped.get(reverse("supply_chain:partners"))
    assert page.status_code == 200 and "No main contact recorded" in page.content.decode()
    form = scoped.get(reverse("supply_chain:partner_contact") + f"?org={partner.pk}")
    assert form.status_code == 200
    posted = scoped.post(
        reverse("supply_chain:partner_contact"),
        {
            "org": partner.pk,
            "contact_name": "Ada Lead",
            "contact_email": "ada@example.org",
            "copy_to": "Sam <sam@example.org>",
            "default_buying_model": "partner_buys",
        },
    )
    assert posted.status_code == 302
    body = scoped.get(reverse("supply_chain:partners")).content.decode()
    assert "Ada Lead" in body and "ada@example.org" in body
    again = scoped.get(reverse("supply_chain:partner_contact") + f"?org={partner.pk}").content.decode()
    assert "sam@example.org" in again
