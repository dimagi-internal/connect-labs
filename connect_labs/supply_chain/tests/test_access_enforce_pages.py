"""The main Supply pages, drawn for a partner with enforcement off and on: they degrade, they never fall over.

THIS REPOSITORY IS PUBLIC. Every name and id is invented; see access_relations_world.py.
"""

import sys

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config.models import ScopeConfig
from connect_labs.supply_chain import identity
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.tests import access_relations_world as worlds

pytestmark = [pytest.mark.django_db, pytest.mark.real_standing]

CONNECT = {"X": 501, "Y": 502, "Z": 503, "W": 504, "N": 505}

# The main list pages. Each is drawn for one organisation in one programme.
PAGES = [
    "supply_chain:overview",
    "supply_chain:purchases",
    "supply_chain:suppliers",
    "supply_chain:orders",
    "supply_chain:network",
    "supply_chain:stock",
    "supply_chain:movements",
    "supply_chain:issues",
    "supply_chain:partners",
    "supply_chain:distribution",
    "supply_chain:catalogue",
    "supply_chain:checks",
]


@pytest.fixture
def w():
    return worlds.build()


@pytest.fixture
def draw(client, monkeypatch, django_user_model):
    """Draw a page as `who` in `program_id`: a real partner access object, every module's `_access` pointed at it."""
    monkeypatch.setattr(identity, "_caller_organizations", lambda access: getattr(access, "connect_orgs", None))
    user = django_user_model.objects.create_user(username="reader", email="reader@example.org", password="x")
    client.force_login(user)

    def drawn(name, program_id, who, query=""):
        def scoped(request):
            da = SupplyDataAccess(program_id=program_id, caller=SYSTEM)
            da.caller = None
            da.user = request.user
            da.connect_orgs = [{"id": CONNECT[who], "slug": f"conn-{CONNECT[who]}"}]
            return da

        for module_name, module in list(sys.modules.items()):
            if module_name.startswith("connect_labs.supply_chain") and hasattr(module, "_access"):
                monkeypatch.setattr(module, "_access", scoped)
            if module_name.startswith("connect_labs.supply_chain") and hasattr(module, "has_program_context"):
                monkeypatch.setattr(module, "has_program_context", lambda request: True)
        return client.get(reverse(name) + query)

    return drawn


def turn_on(program_id):
    ScopeConfig.objects.update_or_create(
        scope_type="program",
        scope_key=str(program_id),
        namespace="supply",
        defaults={"data": {"acl_enforce_reads": True}},
    )


@pytest.mark.parametrize("name", PAGES)
@pytest.mark.parametrize("who", ["X", "N"])
@pytest.mark.parametrize("program", [worlds.B, worlds.C])
def test_a_partner_page_with_enforcement_on_is_as_alive_as_with_it_off(w, draw, name, who, program):
    off = draw(name, program, who)
    turn_on(program)
    on = draw(name, program, who)
    assert off.status_code < 500, (name, "baseline is broken in this harness")
    assert on.status_code < 500, f"{name} fell over for {who} in {program} under enforcement"
    if off.status_code == 200:
        assert on.status_code == 200, name


@pytest.mark.parametrize(
    "name,sentence",
    [
        ("supply_chain:purchases", "You have no purchases in this program."),
        ("supply_chain:suppliers", "You have no suppliers in this programme."),
        ("supply_chain:orders", "You have no orders in this programme."),
        ("supply_chain:movements", "You have no stock movements in this programme."),
    ],
)
def test_an_empty_list_says_so_in_the_partners_words(w, draw, name, sentence):
    turn_on(worlds.C)
    response = draw(name, worlds.C, "N")
    assert response.status_code == 200, name
    body = response.content.decode()
    assert sentence in body
    assert 'data-testid="acl-scoped-note"' in body


def test_the_same_empty_list_keeps_its_old_words_when_enforcement_is_off(w, draw):
    body = draw("supply_chain:orders", worlds.C, "N").content.decode()
    assert "You have no orders in this programme." not in body
    assert 'data-testid="acl-scoped-note"' not in body


def test_the_runner_never_sees_the_partner_note(w, draw):
    turn_on(worlds.C)
    body = draw("supply_chain:orders", worlds.C, "Z").content.decode()
    assert 'data-testid="acl-scoped-note"' not in body
    assert "C-to-X" in body and "C-to-Z" in body


def test_the_orders_page_lists_only_the_partners_order(w, draw):
    turn_on(worlds.C)
    body = draw("supply_chain:orders", worlds.C, "X").content.decode()
    assert "C-to-X" in body
    assert "C-to-Z" not in body


def test_the_tender_board_lists_only_the_tenders_the_partner_may_list(w, draw):
    turn_on(worlds.B)
    mine = draw("supply_chain:purchases", worlds.B, "X").content.decode()
    other = draw("supply_chain:purchases", worlds.B, "W").content.decode()
    assert "B tender" in mine
    assert "B tender" not in other
    assert "You have no purchases in this program." in other


def test_tampering_with_the_partner_parameter_shows_nothing_more(w, draw):
    """`?partner=` is a display filter. Under enforcement a partner naming another organisation sees only its own."""
    turn_on(worlds.C)
    z = w.org("Z").pk
    for name in ("supply_chain:network", "supply_chain:movements", "supply_chain:issues"):
        body = draw(name, worlds.C, "X", f"?partner={z}").content.decode()
        assert "Z&#x27;s depot" not in body and "Z's depot" not in body, name


def test_without_enforcement_the_same_parameter_does_show_the_other_partners_store(w, draw):
    """The control for the test above: the page really can show it, so its absence is the enforcement's doing."""
    z = w.org("Z").pk
    seen = [
        "Z's depot" in (b := draw(name, worlds.C, "X", f"?partner={z}").content.decode()) or "Z&#x27;s depot" in b
        for name in ("supply_chain:network", "supply_chain:movements")
    ]
    assert any(seen)


def test_known_gap_the_stock_page_still_draws_the_whole_programmes_points_from_an_aggregate(w, draw):
    """NOT filtered yet (phase 4: aggregates). When it is, this test fails and must be deleted with a real one."""
    turn_on(worlds.C)
    body = draw("supply_chain:stock", worlds.C, "X").content.decode()
    assert "Z&#x27;s depot" in body or "Z's depot" in body
