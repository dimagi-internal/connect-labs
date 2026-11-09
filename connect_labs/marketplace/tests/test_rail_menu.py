"""The marketplace's expandable rail menu: which pages carry it, and which item is lit."""

import pytest
from django.test import RequestFactory
from django.urls import reverse

from connect_labs.labs.rail_menu import Group, Item, Menu, build
from connect_labs.marketplace.menu import MARKETPLACE_MENU, marketplace_rail_context


class _User:
    def __init__(self, signed_in=True):
        self.is_authenticated = signed_in


def _req(path, signed_in=True, **get):
    r = RequestFactory().get(path, get)
    r.user = _User(signed_in)
    return r


def _active(menu):
    return [i["label"] for g in menu["groups"] for i in g["items"] if i["active"]]


def test_headings_are_the_agreed_ones():
    menu = build(MARKETPLACE_MENU, _req(reverse("marketplace:home")))
    assert [g["label"] for g in menu["groups"]] == [
        "Overview",
        "Targeting",
        "Solicitations",
        "Network",
        "Cost-Effectiveness",
        "Suppliers",
    ]
    labels = [i["label"] for g in menu["groups"] for i in g["items"]]
    assert "Supply" not in labels and "Funds and portfolio" not in labels
    assert "Malaria PMC explorer" not in labels  # a deep dive, reached from Targeting
    assert labels[-1] == "Supplier marketplace"


@pytest.mark.parametrize(
    "path, lit",
    [
        ("marketplace:home", "Programs"),
        ("marketplace:network", "Partners"),
        ("targeting:index", "Targeting"),
        ("targeting:pmc", "Targeting"),
        ("solicitations:public_list", "Calls for partners"),
        ("pulse:index", "Live delivery"),
        ("pulse:network", "Network growth"),
        ("pulse:cea", "Cost-effectiveness ranking"),
        ("pulse:costs", "Cost data"),
        ("supply_chain:market", "Supplier marketplace"),
    ],
)
def test_each_page_lights_its_own_item_and_only_it(path, lit):
    menu = build(MARKETPLACE_MENU, _req(reverse(path)))
    assert _active(menu) == [lit]


def test_drill_downs_light_their_parent():
    assert _active(build(MARKETPLACE_MENU, _req("/labs/pulse/opp/123/"))) == ["Live delivery"]
    assert _active(build(MARKETPLACE_MENU, _req("/labs/marketplace/org/some-org/"))) == ["Partners"]
    assert _active(build(MARKETPLACE_MENU, _req("/labs/marketplace/rounds/x/"))) == ["Calls for partners"]


def test_public_and_kiosk_pulse_pages_never_carry_it():
    for path in ("/labs/pulse/p/tok/", "/labs/pulse/r/report/", "/labs/pulse/v/nightmap/"):
        assert build(MARKETPLACE_MENU, _req(path)) is None


def test_a_page_outside_the_menu_has_none():
    assert build(MARKETPLACE_MENU, _req("/labs/supply/")) is None
    assert build(MARKETPLACE_MENU, _req("/somewhere/else/")) is None


def test_never_in_present_mode():
    url = reverse("pulse:cea")
    assert marketplace_rail_context(_req(url))["expandable_rail_menu"]
    assert marketplace_rail_context(_req(url, present="1")) == {}


def test_a_visitor_gets_the_menu_with_only_the_pages_they_can_open():
    menu = build(MARKETPLACE_MENU, _req(reverse("solicitations:public_list"), signed_in=False))
    labels = [i["label"] for g in menu["groups"] for i in g["items"]]
    assert labels == ["Programs", "Calls for partners", "Supplier marketplace"]
    assert _active(menu) == ["Calls for partners"]
    # A page only a signed-in person can open carries no menu for a visitor.
    assert build(MARKETPLACE_MENU, _req(reverse("pulse:cea"), signed_in=False)) is None


def test_the_marketplace_landing_page_carries_the_menu_for_a_visitor():
    menu = build(MARKETPLACE_MENU, _req(reverse("marketplace:home"), signed_in=False))
    assert _active(menu) == ["Programs"]


def test_an_item_can_be_hidden_per_person():
    menu = Menu(
        key="t",
        title="T",
        subtitle="",
        groups=(
            Group(
                "G",
                (
                    Item("Programs", "marketplace:home"),
                    Item("Partners", "marketplace:network", visible=lambda r: False),
                ),
            ),
        ),
    )
    built = build(menu, _req(reverse("marketplace:home")))
    assert [i["label"] for i in built["groups"][0]["items"]] == ["Programs"]


@pytest.mark.django_db
def test_the_rail_is_drawn_on_a_page_and_a_visitor_sees_only_what_they_can_open(client, django_user_model):
    url = reverse("solicitations:public_list")
    visitor = client.get(url).content.decode()
    assert 'data-testid="marketplace-rail"' in visitor and ">Calls for partners<" in visitor
    assert ">Cost data<" not in visitor and ">Partners<" not in visitor
    client.force_login(django_user_model.objects.create(username="rail-viewer"))
    page = client.get(url).content.decode()
    assert 'data-testid="marketplace-rail"' in page
    assert 'aria-current="page"' in page
    assert ">Calls for partners<" in page and ">Cost data<" in page


def test_the_sidebar_icon_is_wired_to_toggle_the_rail():
    menu = build(MARKETPLACE_MENU, _req(reverse("pulse:cea")))
    assert menu["sidebar_url"] == reverse("marketplace:home")


@pytest.mark.django_db
def test_the_rail_carries_the_sidebar_hook(client, django_user_model):
    client.force_login(django_user_model.objects.create(username="rail-hook"))
    page = client.get(reverse("solicitations:public_list")).content.decode()
    assert f'data-sidebar-url="{reverse("marketplace:home")}"' in page
