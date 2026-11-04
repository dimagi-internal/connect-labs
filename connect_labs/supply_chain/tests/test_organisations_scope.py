"""The Organisations page opens on the organisations this programme uses (Sophie's RUTF feedback, S3).

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from datetime import date

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.models import PartnerPlan, SupplyPoint, Tender
from connect_labs.supply_chain.network.org_scope import org_ids_in_programme
from connect_labs.supply_chain.tests import chlorine_world

pytestmark = pytest.mark.django_db

PROGRAM = chlorine_world.PROGRAM
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme
da = chlorine_world.da
world = chlorine_world.world


@pytest.fixture
def orgs(da):
    made = {
        slug: LabsOrg.objects.create(slug=slug, name=name)
        for slug, name in [
            ("scope-planned", "Planned Partner"),
            ("scope-runs", "Runs A Place"),
            ("scope-invited", "Invited To Tender"),
            ("scope-split", "Split Partner"),
            ("scope-elsewhere", "Elsewhere Distributor"),
        ]
    }
    PartnerPlan.objects.create(
        program_id=PROGRAM,
        org=made["scope-planned"],
        distribution_model="mixed",
        starts_on=date(2026, 1, 1),
        source="we_recorded",
    )
    SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="scope-store",
        name="Scope store",
        kind="central_store",
        managed_by_org=made["scope-runs"],
        source="we_recorded",
    )
    tender = Tender.objects.create(
        program_id=PROGRAM,
        label="Scope tender",
        partner_splits=[{"key": "s1", "org_id": made["scope-split"].pk, "commodity_slug": "x", "quantity": "1"}],
    )
    tender.invited_orgs.add(made["scope-invited"])
    return made


def _names(body):
    return [
        n
        for n in ("Planned Partner", "Runs A Place", "Invited To Tender", "Split Partner", "Elsewhere Distributor")
        if n in body
    ]


def test_the_default_list_is_the_organisations_the_programme_uses(orgs):
    ids = org_ids_in_programme(PROGRAM)
    assert {orgs[s].pk for s in ("scope-planned", "scope-runs", "scope-invited", "scope-split")} <= ids
    assert orgs["scope-elsewhere"].pk not in ids


def test_the_page_opens_on_that_list_and_offers_all(client_in_programme, orgs):
    body = client_in_programme.get(reverse("supply_chain:organisations")).content.decode()
    assert _names(body) == ["Planned Partner", "Runs A Place", "Invited To Tender", "Split Partner"]
    total = LabsOrg.objects.real().count()
    assert f"Show all {total} organisations" in body
    assert "changes every programme" in body


def test_show_all_lists_every_organisation(client_in_programme, orgs):
    body = client_in_programme.get(reverse("supply_chain:organisations") + "?show=all").content.decode()
    assert "Elsewhere Distributor" in body
    assert "Show only this programme" in body


def test_search_narrows_the_shown_list(client_in_programme, orgs):
    url = reverse("supply_chain:organisations")
    body = client_in_programme.get(url + "?q=split").content.decode()
    assert _names(body) == ["Split Partner"]
    body = client_in_programme.get(url + "?show=all&q=elsewhere").content.decode()
    assert _names(body) == ["Elsewhere Distributor"]
    body = client_in_programme.get(url + "?q=elsewhere").content.decode()
    assert _names(body) == [] and "Show all" in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name, detail",
    [
        ("supply_chain:issues", "An issue is open until it is followed up"),
        ("supply_chain:partners", "Each partner runs its own network"),
        ("supply_chain:distribution", "Only confirmed amounts move stock"),
        ("supply_chain:network", "down to each field worker's own holding"),
        ("supply_chain:stock", "A field worker's own balance on their phone"),
        ("supply_chain:orders", "a local"),
        ("supply_chain:organisations", "Everyone is shared across every programme"),
    ],
)
def test_a_page_does_not_say_its_purpose_twice_its_detail_sits_behind_a_disclosure(client_in_programme, name, detail):
    """The purpose line under the tabs already says what the page is for: the page's own lede is one
    short line, and anything further (every fact kept) opens from it."""
    body = client_in_programme.get(reverse(name)).content.decode()
    before, found, after = body.partition(detail)
    assert found, f"{name} lost: {detail}"
    opened = before.rfind("<details")
    assert opened != -1 and "</details>" not in before[opened:], f"{name}: {detail!r} is not inside a <details>"
