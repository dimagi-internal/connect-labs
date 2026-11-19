"""Round 3, step 3: the new menu, the retired pages' redirects, the weekly rule and the Overview's two halves.

Sophie Feintuch's walk of the demo (Nov 2026). THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain import overview_halves, weekly
from connect_labs.supply_chain.navigation import MENU_TABS, RETIRED_TO
from connect_labs.supply_chain.tests import purchase_walk_world as walk
from connect_labs.supply_chain.tests.test_llo_pages import world  # noqa: F401 -- fixture

pytestmark = pytest.mark.django_db

da = walk.da
account = walk.account
client_in_programme = walk.client_in_programme

RETIRED_LABELS = ("Network", "Map", "Stock", "Workers", "Plan", "Distribution", "Where it went", "Sourcing", "Orders")


def _nav_labels(client, name="supply_chain:overview"):
    body = client.get(reverse(name)).content.decode()
    nav = body.split('<nav data-testid="supply-tabs"', 1)[1].split("</nav>", 1)[0]
    return [label.strip() for label in re.findall(r'<a href="[^"]+"[^>]*>([^<]+)</a>', nav)]


def test_the_menu_is_the_new_one_in_order(client_in_programme, world):  # noqa: F811
    labels = _nav_labels(client_in_programme)
    # Tests joins the menu only for a program that turned it on.
    assert [label for label in labels if label != "Tests"] == [label for _, label in MENU_TABS if label != "Tests"]
    assert labels[:2] == ["Overview", "Start here"]
    for retired in RETIRED_LABELS:
        assert retired not in labels


def test_every_retired_tab_names_a_tab_that_is_in_the_menu():
    names = {name for name, _ in MENU_TABS}
    assert set(RETIRED_TO.values()) <= names


@pytest.mark.retired_redirects
def test_the_old_pages_send_a_plain_visit_to_the_page_that_took_their_job(client_in_programme, world):  # noqa: F811
    for old, new in (
        ("supply_chain:network", "supply_chain:stock_by_llo"),
        ("supply_chain:workers", "supply_chain:stock_by_llo"),
        ("supply_chain:stock", "supply_chain:stock_by_llo"),
        ("supply_chain:distribution", "supply_chain:this_week"),
        ("supply_chain:plan", "supply_chain:this_week"),
    ):
        response = client_in_programme.get(reverse(old))
        assert response.status_code == 302, old
        assert response["Location"].startswith(reverse(new)), (old, response["Location"])


@pytest.mark.retired_redirects
def test_an_llo_named_in_an_old_link_lands_on_that_llos_page(client_in_programme, world):  # noqa: F811
    north = world["orgs"]["north"]["id"]
    response = client_in_programme.get(reverse("supply_chain:stock"), {"partner": north})
    assert response["Location"].split("?")[0] == reverse("supply_chain:llo_stock", args=[north])
    response = client_in_programme.get(reverse("supply_chain:distribution"), {"partner": north})
    assert response["Location"].split("?")[0] == reverse("supply_chain:llo_week", args=[north])


@pytest.mark.retired_redirects
def test_the_redirect_keeps_the_program_and_the_day(client_in_programme, world):  # noqa: F811
    response = client_in_programme.get(reverse("supply_chain:stock"), {"as_of": "2026-09-01", "program_id": "20931"})
    assert "as_of=2026-09-01" in response["Location"] and "program_id=20931" in response["Location"]


@pytest.mark.retired_redirects
def test_the_pages_that_stay_are_not_redirected(client_in_programme, world):  # noqa: F811
    for name in ("supply_chain:flow", "supply_chain:distribution_record"):
        assert client_in_programme.get(reverse(name)).status_code == 200, name


@pytest.mark.retired_redirects
def test_a_form_posting_to_an_old_address_is_left_alone(client_in_programme, world):  # noqa: F811
    response = client_in_programme.post(reverse("supply_chain:workers"), {})
    assert response.status_code != 302 or "stock-by-llo" not in response["Location"]


# ---- the weekly rule ------------------------------------------------------------------------------------


def test_an_flw_is_short_only_when_they_cannot_cover_the_visits_before_the_next_handout(monkeypatch):
    picture = {
        "handout": {"next_on": "2026-11-23"},
        "issue": [
            {"supply_point_id": 1, "name": "flw_001", "need_week": 87, "held": 83, "short": True, "stock_known": True},
            {"supply_point_id": 2, "name": "flw_002", "need_week": 40, "held": 0, "short": False, "stock_known": True},
            {"supply_point_id": 3, "name": "flw_003", "need_week": 40, "held": 0, "short": True, "stock_known": False},
        ],
        "stores": [],
    }
    monkeypatch.setattr(weekly, "plan_picture", lambda program_id, memo=None: picture)
    status = weekly.flw_status(1)
    assert status[1]["short"] and status[1]["short_by"] == 4
    # Ending the week at zero is the plan, not a problem; and an unknown stock is not a shortage.
    assert not status[2]["short"] and not status[3]["short"]
    assert "needs 87, holds 83, 4 short" in status[1]["words"]
    assert weekly.short_by_org(1, {1: 10, 2: 10, 3: 10}) == {10: [status[1]]}


def test_a_stores_run_out_day_is_the_plans_one(monkeypatch):
    monkeypatch.setattr(
        weekly, "plan_picture", lambda program_id, memo=None: {"stores": [{"supply_point_id": 5, "words": "x"}]}
    )
    assert weekly.store_runs_dry(1) == {5: {"supply_point_id": 5, "words": "x"}}
    monkeypatch.setattr(weekly, "plan_picture", lambda program_id, memo=None: None)
    assert weekly.store_runs_dry(1) == {} and weekly.flw_status(1) == {}


# ---- the Overview, in two halves --------------------------------------------------------------------------


def test_the_overview_has_buying_and_distribution_each_with_attention_then_where_things_stand(
    client_in_programme, world  # noqa: F811
):
    body = client_in_programme.get(reverse("supply_chain:overview")).content.decode()
    order = [
        'data-testid="buying-half"',
        'data-testid="buying-attention"',
        'data-testid="buying-stands"',
        'data-testid="distribution-half"',
        'data-testid="distribution-attention"',
        'data-testid="distribution-stands"',
    ]
    positions = [body.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert body.count("Needs attention") == 2 and body.count("Where things stand") == 2
    # Each LLO is one row, linking to its own page.
    table = body.split('data-testid="distribution-table"')[1].split("</table>")[0]
    assert reverse("supply_chain:llo_stock", args=[world["orgs"]["north"]["id"]]) in table


def test_buying_attention_groups_by_whose_move_it_is():
    class _Move:
        def __init__(self, text, rule="owed", href="/x/", detail=""):
            self.text, self.rule, self.href, self.detail = text, rule, href, detail

    class _Row:
        title, url, under_tender, silent_names = "Round 1", "/r/", False, ["Acacia"]
        ours = [_Move("Decide: extend, close or award")]
        theirs = []
        partners = [{"name": "Savanna", "state": "waiting"}, {"name": "North Star", "state": "ordered"}]

    groups = {g["group"]: g["items"] for g in overview_halves.buying_attention([_Row()])}
    assert [i["text"] for i in groups["Waiting on us"]] == ["Decide: extend, close or award"]
    assert [i["text"] for i in groups["Waiting on suppliers"]] == ["1 supplier has not replied"]
    assert [i["text"] for i in groups["Waiting on partners"]] == ["Savanna has not ordered"]


def test_distribution_attention_lists_issues_unconfirmed_handouts_and_what_partners_are_asked():
    rows = [{"name": "North Star", "unconfirmed_handouts": 2, "flws_low": 0, "week_url": "/w/", "url": "/l/"}]
    chase = [
        {"audience": "partner", "items": [{"ask": "Ask North Star for a count", "what": "Overdue", "href": "/c/"}]}
    ]
    groups = {g["group"]: g["items"] for g in overview_halves.distribution_attention(rows, [], chase)}
    assert groups["Handouts not yet confirmed"][0]["text"] == "North Star: 2 handouts to confirm"
    assert groups["Waiting on partners"][0]["text"] == "Ask North Star for a count"


def test_start_here_is_a_plain_how_to_that_links_to_the_new_pages(client_in_programme, world):  # noqa: F811
    body = client_in_programme.get(reverse("supply_chain:start_here")).content.decode()
    for name in ("this_week", "stock_by_llo", "purchases", "issues", "partners", "suppliers", "catalogue"):
        assert reverse(f"supply_chain:{name}") in body, name
    assert "data-state=" not in body


def test_what_the_old_menu_held_is_still_reachable_from_access_and_llos(client_in_programme, world):  # noqa: F811
    team = client_in_programme.get(reverse("supply_chain:programme_team")).content.decode()
    assert reverse("supply_chain:update_links") in team.split('data-testid="access-links"')[1]
    partners = client_in_programme.get(reverse("supply_chain:partners")).content.decode()
    housekeeping = partners.split('data-testid="llo-housekeeping"')[1]
    assert (
        reverse("supply_chain:organisations") in housekeeping
        and reverse("supply_chain:dispensing_rules") in housekeeping
    )
