"""Network, Workers and Worker, driven through the browser, live and as of a day.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.

The world: a partner store received 300 sachets and handed 150 to each of two
workers. worker-baobab dispensed 40 on an approved visit, had a further visit
of 10 rejected (so posted and reversed), counted 110 two days ago, and reports
receiving 50 sachets that no store recorded. worker-acacia dispensed 30 on a
visit still pending, read from a protocol line (so estimated), and counted 115
against a ledger of 120.
"""

import datetime
import re
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.models import (
    Commodity,
    DispensingRule,
    Item,
    Movement,
    StockCount,
    SupplyPoint,
    WorkerVisit,
)
from connect_labs.supply_chain.stock.services import posting
from connect_labs.supply_chain.stock.services.dispensing import validate_lines
from connect_labs.supply_chain.stock.services.visit_reader import outcome_key

pytestmark = pytest.mark.django_db

PROGRAM = 20883
TODAY = timezone.localdate()


class _ProgramContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.labs_context = {"program_id": PROGRAM} if request.user.is_authenticated else {}
        return self.get_response(request)


@pytest.fixture
def da():
    SyntheticOpportunity.objects.create(
        opportunity_id=PROGRAM,
        program_id=PROGRAM,
        labs_only=True,
        enabled=True,
        label="worker screens",
        allowed_domains=["dimagi.com"],
    )
    registry.invalidate_cache()
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def client_in_program(client, django_user_model, monkeypatch, settings, da):
    from connect_labs.supply_chain import api_views, form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.network import views as network_views  # noqa: F401
    from connect_labs.supply_chain.stock import visit_views  # noqa: F401

    settings.MIDDLEWARE = [*settings.MIDDLEWARE, f"{__name__}._ProgramContextMiddleware"]
    for module in ("api_views", "form_views", "views", "network.views", "stock.visit_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", lambda request: da)
    client.force_login(django_user_model.objects.create_user(username="sophie", password="x"))
    return client


@contextmanager
def recorded(days_ago):
    """Write as if on that day, so the as-of rewind (which works on recorded time) sees it then."""
    day = TODAY - timedelta(days=days_ago)
    at = timezone.make_aware(datetime.datetime.combine(day, datetime.time(9, 0)))
    with seed_overrides(PROGRAM, channel="command", recorded_at=at):
        yield


def _worker(world, name, *, dispensed, status, estimated, counted, days_ago=45):
    with recorded(60):
        worker = _issue(world, name)
    with recorded(days_ago):
        _visit(world, worker, name, dispensed=dispensed, status=status, estimated=estimated, days_ago=days_ago)
    with recorded(2):
        StockCount.objects.create(
            program_id=PROGRAM,
            supply_point=worker,
            item=world["item"],
            commodity=world["item"].commodity,
            kind="self_reported",
            counted_on=TODAY - timedelta(days=2),
            quantity=Decimal(counted),
            quantity_unit="sachet",
            source="commcare_form",
        )
    return worker


def _issue(world, name):
    item, store, commodity = world["item"], world["store"], world["item"].commodity
    worker = SupplyPoint.objects.create(
        program_id=PROGRAM,
        opportunity_id=PROGRAM,
        slug=f"user-{name}",
        name=name,
        kind="user_held",
        connect_username=name,
        parent=store,
        source="connect_visit",
        min_months_of_stock=Decimal("1"),
        max_months_of_stock=Decimal("2"),
    )
    Movement.objects.create(
        program_id=PROGRAM,
        kind="distribution",
        occurred_on=TODAY - timedelta(days=60),
        from_supply_point=store,
        to_supply_point=worker,
        item=item,
        commodity=commodity,
        quantity=Decimal("150"),
        quantity_unit="sachet",
        source="we_recorded",
    )
    return worker


def _visit(world, worker, name, *, dispensed, status, estimated, days_ago):
    item = world["item"]
    posting.post_visit_consumption(
        program_id=PROGRAM,
        opportunity_id=PROGRAM,
        point=worker,
        item=item,
        quantity=Decimal(dispensed),
        unit="sachet",
        occurred_on=TODAY - timedelta(days=days_ago),
        visit_id=f"v-{name}",
        estimated=estimated,
    )
    WorkerVisit.objects.create(
        program_id=PROGRAM,
        opportunity_id=PROGRAM,
        visit_id=f"v-{name}",
        xform_id=f"xf-{name}",
        supply_point=worker,
        visit_date=TODAY - timedelta(days=days_ago),
        status=status,
        outcomes={outcome_key(item.pk): "dispensed"},
        answers={"form.x": str(dispensed)},
    )


@pytest.fixture
def world(da):
    with recorded(70):
        commodity = Commodity.objects.create(
            scope_key=f"prog:{PROGRAM}",
            slug="rutf",
            name="RUTF",
            base_unit="sachet",
            pack_unit="carton",
            base_per_pack=150,
        )
        item = Item.objects.create(
            scope_key=f"prog:{PROGRAM}",
            sku="rutf",
            name="RUTF 150",
            commodity=commodity,
            base_unit="sachet",
            pack_unit="carton",
            base_per_pack=150,
        )
        store = SupplyPoint.objects.create(
            program_id=PROGRAM, slug="partner", name="Partner store", kind="regional_store", source="we_recorded"
        )
        # The store receives exactly what it hands on: its own balance is 0 and its subtree is its workers'.
        Movement.objects.create(
            program_id=PROGRAM,
            kind="receipt",
            occurred_on=TODAY - timedelta(days=70),
            to_supply_point=store,
            item=item,
            commodity=commodity,
            quantity=Decimal("300"),
            quantity_unit="sachet",
            source="we_recorded",
        )
        DispensingRule.objects.create(
            program_id=PROGRAM,
            opportunity_id=PROGRAM,
            item=item,
            resupply_point=store,
            active_from=TODAY - timedelta(days=90),
            lines=validate_lines([{"kind": "stated", "paths": ["form.x"], "unit": "sachet"}], item),
        )
    world = {"item": item, "store": store}
    baobab = _worker(world, "worker-baobab", dispensed=40, status="approved", estimated=False, counted=110)
    acacia = _worker(world, "worker-acacia", dispensed=30, status="pending", estimated=True, counted=115)

    with recorded(30):
        # A later visit of baobab's, posted and then rejected: its stock came back.
        rejected = posting.post_visit_consumption(
            program_id=PROGRAM,
            opportunity_id=PROGRAM,
            point=baobab,
            item=item,
            quantity=Decimal("10"),
            unit="sachet",
            occurred_on=TODAY - timedelta(days=30),
            visit_id="v-rejected",
            estimated=False,
        )
        posting.post_visit_reversal(rejected, reason="visit rejected")
        WorkerVisit.objects.create(
            program_id=PROGRAM,
            opportunity_id=PROGRAM,
            visit_id="v-rejected",
            xform_id="xf-rejected",
            supply_point=baobab,
            visit_date=TODAY - timedelta(days=30),
            status="rejected",
            outcomes={outcome_key(item.pk): "reversed"},
            answers={"form.x": "10"},
        )
    with recorded(10):
        # A visit of acacia's, approved over a payment cap, whose form did not say what it gave.
        WorkerVisit.objects.create(
            program_id=PROGRAM,
            opportunity_id=PROGRAM,
            visit_id="v-silent",
            xform_id="xf-silent",
            supply_point=acacia,
            visit_date=TODAY - timedelta(days=10),
            status="over_limit",
            outcomes={outcome_key(item.pk): "no_answer"},
            answers={},
        )
    with recorded(20):
        # A receipt baobab reports that no store recorded.
        StockCount.objects.create(
            program_id=PROGRAM,
            supply_point=baobab,
            item=item,
            commodity=commodity,
            kind="reported_receipt",
            counted_on=TODAY - timedelta(days=20),
            quantity=Decimal("50"),
            quantity_unit="sachet",
            source="commcare_form",
        )
    return {**world, "worker-baobab": baobab, "worker-acacia": acacia}


def names_in_order(body):
    return re.findall(r'data-testid="worker-name"[^>]*>([^<]+)<', body)


def text_of(body):
    """The page as a reader sees it: tags dropped, whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def get(client, name, *args, **params):
    response = client.get(reverse(f"supply_chain:{name}", args=args), params)
    assert response.status_code == 200, response.content[:500]
    return response.content.decode()


# ---- Workers -------------------------------------------------------------


def test_workers_are_listed_by_name_with_no_worst_first(client_in_program, world):
    body = get(client_in_program, "workers")
    assert names_in_order(body) == ["worker-acacia", "worker-baobab"]


def test_workers_sort_by_a_column_when_asked(client_in_program, world):
    body = get(client_in_program, "workers", sort="on_hand", dir="desc")
    assert names_in_order(body) == ["worker-acacia", "worker-baobab"]  # 120 before 110
    body = get(client_in_program, "workers", sort="on_hand", dir="asc")
    assert names_in_order(body) == ["worker-baobab", "worker-acacia"]
    body = get(client_in_program, "workers", sort="variance", dir="asc")
    assert names_in_order(body) == ["worker-acacia", "worker-baobab"]  # -5 before 0


def test_every_column_sorts_both_ways(client_in_program, world):
    from connect_labs.supply_chain.stock.visit_views import SORTS

    body = get(client_in_program, "workers")
    for key in SORTS:
        assert f"sort={key}&amp;" in body, f"no header link sorts by {key}"
        for direction in ("asc", "desc"):
            assert sorted(names_in_order(get(client_in_program, "workers", sort=key, dir=direction))) == [
                "worker-acacia",
                "worker-baobab",
            ]


def test_an_unknown_sort_falls_back_to_the_name(client_in_program, world):
    body = get(client_in_program, "workers", sort="worst", dir="desc")
    assert names_in_order(body) == ["worker-acacia", "worker-baobab"]


def test_the_unapproved_and_estimated_parts_are_said_on_the_figure(client_in_program, world):
    body = get(client_in_program, "workers")
    # The parts are said as parts of what was DISPENSED, never as stock on hand:
    # a short line under acacia's figure, and none under baobab's, which rests on nothing unsettled.
    lines = [text_of(x) for x in re.findall(r'data-testid="given-out-line">(.*?)</div>', body, re.S)]
    assert lines == ["of 30 given out: 30 on unapproved visits · 30 estimated"]
    text = text_of(body)
    assert "120 sachets" in text and "110 sachets" in text
    assert "on hand —" not in text


def test_the_variance_says_its_sign_and_its_day(client_in_program, world):
    text = text_of(get(client_in_program, "workers"))
    assert "Count − ledger on count day" in text
    assert "−5 sachets" in text
    assert f"counted {(TODAY - timedelta(days=2)).strftime('%-d %b')}" in text


def test_a_receipt_no_store_recorded_is_a_fact_on_the_row(client_in_program, world):
    text = text_of(get(client_in_program, "workers"))
    day = (TODAY - timedelta(days=20)).strftime("%-d %b %Y")
    assert f"Reports receiving 50 sachets on {day}; no store recorded a delivery" in text


# ---- Network -------------------------------------------------------------


def test_the_network_shows_a_stores_workers_and_their_totals(client_in_program, world):
    body = get(client_in_program, "network")
    assert 'data-testid="network-tree"' in body
    text = text_of(body)
    assert "2 workers" in text
    # 120 + 110 on hand below the partner store; 40 + 30 dispensed there.
    assert "230 sachets on hand of 70 given out: 30 on unapproved visits · 30 estimated" in text


def test_a_store_row_shows_what_came_in_once_never_a_hop_summed_issued(client_in_program, world):
    body = get(client_in_program, "network")
    tree = text_of(re.search(r'data-testid="network-tree".*?</section>', body, re.S).group(0))
    assert "300 sachets came in from outside" in tree
    assert "issued" not in tree.lower()


def _testid_text(body, testid):
    match = re.search(rf'data-testid="{testid}"[^>]*>(.*?)</', body, re.S)
    assert match, f"no {testid}"
    return text_of(match.group(1)).strip()


def own_line(body, name):
    """The text of one tree node's own row (not what is below it)."""
    for row in re.findall(r'data-testid="network-node">\s*<div[^>]*>(.*?)</div>', body, re.S):
        if re.search(rf">\s*{re.escape(name)}\s*<", row):
            return text_of(row)
    raise AssertionError(f"no tree row for {name}")


def test_the_tree_is_the_directory_every_point_once(client_in_program, world):
    SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="old-depot",
        name="Old depot",
        kind="facility",
        source="we_recorded",
        status="inactive",
    )
    body = get(client_in_program, "network")
    # Four points, four nodes, and no second per-kind table listing them again.
    assert body.count('data-testid="network-node"') == 4
    assert "<table" not in body
    names = re.findall(r'data-testid="point-name"[^>]*>([^<]+)<', body)
    assert sorted(names) == ["Old depot", "Partner store", "worker-acacia", "worker-baobab"]
    # What only the old tables said now rides on the node.
    assert "user-worker-acacia" in body
    assert "band 1–2 months" in text_of(body)
    assert "inactive" in own_line(body, "Old depot")
    for point in SupplyPoint.objects.filter(program_id=PROGRAM):
        assert reverse("supply_chain:supply_point_edit", args=[point.pk]) in body


def test_a_worker_sits_under_the_store_that_supplies_them(client_in_program, world):
    body = get(client_in_program, "network")
    store_at = body.index(">Partner store<")
    assert store_at < body.index(">worker-acacia<") and store_at < body.index(">worker-baobab<")
    assert "Show the 2 workers Partner store supplies" in text_of(body)


def test_a_store_that_passes_stock_on_is_not_an_alarm(client_in_program, world):
    """The partner store holds nothing because it hands everything on: that is how it works."""
    line = own_line(get(client_in_program, "network"), "Partner store")
    assert "passes stock on" in line
    assert "none left" not in line


def test_an_empty_store_over_an_empty_subtree_is_still_an_alarm(client_in_program, world):
    item, commodity = world["item"], world["item"].commodity
    depot = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="empty-depot", name="Empty depot", kind="regional_store", source="we_recorded"
    )
    cedar = SupplyPoint.objects.create(
        program_id=PROGRAM,
        opportunity_id=PROGRAM,
        slug="user-worker-cedar",
        name="worker-cedar",
        kind="user_held",
        connect_username="worker-cedar",
        parent=depot,
        source="connect_visit",
        min_months_of_stock=Decimal("1"),
        max_months_of_stock=Decimal("2"),
    )
    for kind, frm, to in (("receipt", None, depot), ("distribution", depot, cedar)):
        Movement.objects.create(
            program_id=PROGRAM,
            kind=kind,
            occurred_on=TODAY - timedelta(days=20),
            from_supply_point=frm,
            to_supply_point=to,
            item=item,
            commodity=commodity,
            quantity=Decimal("20"),
            quantity_unit="sachet",
            source="we_recorded",
        )
    posting.post_visit_consumption(
        program_id=PROGRAM,
        opportunity_id=PROGRAM,
        point=cedar,
        item=item,
        quantity=Decimal("20"),
        unit="sachet",
        occurred_on=TODAY - timedelta(days=10),
        visit_id="v-cedar",
        estimated=False,
    )
    body = get(client_in_program, "network")
    depot_line = own_line(body, "Empty depot")
    assert "none left" in depot_line and "passes stock on" not in depot_line
    # A worker is meant to hold stock: empty is the finding.
    cedar_line = own_line(body, "worker-cedar")
    assert "none left" in cedar_line and "passes stock on" not in cedar_line


def test_a_worker_in_the_tree_links_to_their_page(client_in_program, world):
    body = get(client_in_program, "network")
    assert reverse("supply_chain:worker_detail", args=[world["worker-baobab"].pk]) in body


# ---- Worker --------------------------------------------------------------


def test_a_worker_page_shows_the_timeline_and_the_visits_behind_it(client_in_program, world):
    worker = world["worker-acacia"]
    body = get(client_in_program, "worker_detail", worker.pk)
    assert 'data-testid="worker-timeline"' in body
    assert "xf-worker-acacia" in body
    assert "form.x" in body
    assert "Not yet approved" in body
    text = text_of(body)
    assert "Issued" in text and "150 sachets" in text
    # On hand is a bare figure, the sum behind it a short line under it; Dispensed does not repeat it.
    assert _testid_text(body, "worker-on-hand") == "120 sachets"
    assert (
        _testid_text(body, "worker-on-hand-sum")
        == "150 issued − 30 given out · 30 on unapproved visits · 30 estimated"
    )
    assert _testid_text(body, "worker-dispensed") == "30 sachets"
    assert "not yet approved," not in text
    # The chart's figures, as a table, for anyone who cannot see the chart.
    assert 'data-testid="timeline-table"' in body


def test_a_worker_page_says_what_the_count_is_against(client_in_program, world):
    text = text_of(get(client_in_program, "worker_detail", world["worker-acacia"].pk))
    assert "Count − ledger on count day" in text
    assert "−5 sachets" in text
    assert "ledger that day 120 sachets" in text


def test_a_worker_page_carries_the_unrecorded_receipt(client_in_program, world):
    text = text_of(get(client_in_program, "worker_detail", world["worker-baobab"].pk))
    day = (TODAY - timedelta(days=20)).strftime("%-d %b %Y")
    assert f"Reports receiving 50 sachets on {day}; no store recorded a delivery" in text


def test_a_rejected_visit_is_shown_reversed_not_arrived(client_in_program, world):
    body = get(client_in_program, "worker_detail", world["worker-baobab"].pk)
    text = text_of(body)
    assert "Reversed — stock put back" in text
    assert "Rejected" in text
    assert "Not yet approved" not in text  # a rejected visit is not one still waiting
    arrived = text.split("Stock that arrived")[1]
    assert "10 sachets" not in arrived


def _visit_rows(body):
    """[(day-or-blank, signed quantities, read as)] for each listed visit row, in order."""
    rows = []
    for row in re.findall(r'<tr data-testid="visit-row">(.*?)</tr>', body, re.S):
        moved = re.search(r'data-testid="visit-moved"[^>]*>(.*?)</td>', row, re.S).group(1).strip()
        read_as = text_of(re.search(r'data-testid="visit-read-as">(.*?)</td>', row, re.S).group(1)).strip()
        rows.append((moved, read_as))
    return rows


def _screening(world, worker, visit_id, days_ago):
    WorkerVisit.objects.create(
        program_id=PROGRAM,
        opportunity_id=PROGRAM,
        visit_id=visit_id,
        xform_id=f"xf-{visit_id}",
        supply_point=worker,
        visit_date=TODAY - timedelta(days=days_ago),
        status="approved",
        outcomes={outcome_key(world["item"].pk): "nothing_given"},
        answers={"form.x": "0"},
    )


def test_each_visit_says_what_it_moved_and_those_that_moved_stock_come_first(client_in_program, world):
    acacia = world["worker-acacia"]
    # Two screenings, newer than the visit that dispensed: they gave none.
    _screening(world, acacia, "v-screen-1", 3)
    _screening(world, acacia, "v-screen-2", 4)

    body = get(client_in_program, "worker_detail", acacia.pk)
    assert '<th scope="col" class="text-right px-4 py-2">Sachets</th>' in body
    # The dispensing visit first, with its figure; then the one that did not say, blank.
    assert _visit_rows(body) == [("−30", "Gave some out"), ("", "Did not say")]
    # The screenings are counted on one row, not listed among them.
    count = re.search(r'data-testid="visits-gave-none-count"[^>]*>(.*?)</summary>', body, re.S).group(1)
    assert text_of(count).strip() == "2 visits gave none"
    assert "xf-v-screen-1" in body.split('data-testid="visits-gave-none"', 1)[1]


def test_a_rejected_visit_reads_its_dispense_and_the_put_back(client_in_program, world):
    body = get(client_in_program, "worker_detail", world["worker-baobab"].pk)
    rows = _visit_rows(body)
    assert ("−10 · +10", "Reversed — stock put back") in rows
    assert ("−40", "Gave some out") in rows
    assert 'data-testid="visits-gave-none"' not in body


def test_too_few_days_say_when_an_estimate_comes_and_the_rate_so_far(client_in_program, world):
    """77 given out over 11 days: no rate yet. Each screen gives the day one comes, and ~7 a day so far."""
    with recorded(60):
        cedar = _issue(world, "worker-cedar")
    with recorded(10):
        _visit(world, cedar, "worker-cedar", dispensed=77, status="approved", estimated=False, days_ago=10)
    # The first dispensing day counts as one of the thirty.
    on = TODAY - timedelta(days=10) + timedelta(days=29)
    when = f"{on.day} {on.strftime('%b')}" + ("" if on.year == TODAY.year else f" {on.year}")

    detail = get(client_in_program, "worker_detail", cedar.pk)
    figure = re.search(r'data-testid="stockout-figure">(.*?)</dd>', detail, re.S).group(1)
    assert text_of(figure).strip() == f"estimate from {when}"
    notes = [text_of(n).strip() for n in re.findall(r'data-testid="stockout-note">(.*?)</dd>', detail, re.S)]
    assert notes == ["after 30 days of dispensing", "~7 a day so far"]
    card = detail.split("Days to stock-out", 1)[1].split("</div>", 1)[0]
    assert "unknown" not in text_of(card)
    assert "monthly rate means anything" not in detail

    listing = get(client_in_program, "workers")
    assert f"estimate from {when}" in text_of(listing)
    assert "~7 a day so far" in text_of(listing)
    assert "monthly rate means anything" not in listing

    network = get(client_in_program, "network")
    node = network.split(">worker-cedar<", 1)[1].split('data-testid="network-node"', 1)[0]
    assert f"cover estimate from {when}" in text_of(node)
    assert "cover unknown" not in text_of(node)


def test_the_chart_key_and_the_arrivals_carry_no_sentences(client_in_program, world):
    body = get(client_in_program, "worker_detail", world["worker-baobab"].pk)
    key = re.search(r'<ul[^>]*aria-label="Key">(.*?)</ul>', body, re.S).group(1)
    items = [text_of(i).strip() for i in re.findall(r"<li[^>]*>(.*?)</li>", key, re.S)]
    assert items == ["issued", "dispensed", "put back · rejected visit", "worker's count", "ledger"]
    assert "is a reversal of that visit" not in body


def test_a_worker_in_another_programme_is_a_404(client_in_program, world):
    other = SupplyPoint.objects.create(
        program_id=PROGRAM + 1, slug="x", name="x", kind="user_held", connect_username="x", source="we_recorded"
    )
    assert client_in_program.get(reverse("supply_chain:worker_detail", args=[other.pk])).status_code == 404


def test_a_store_is_not_a_worker_page(client_in_program, world):
    url = reverse("supply_chain:worker_detail", args=[world["store"].pk])
    assert client_in_program.get(url).status_code == 404


def test_a_visit_that_did_not_say_is_counted_and_said_in_words(client_in_program, world):
    rows = text_of(get(client_in_program, "workers"))
    assert "worker-acacia" in rows
    page = text_of(get(client_in_program, "worker_detail", world["worker-acacia"].pk))
    assert "Did not say" in page
    assert "1 visit did not say, so not counted" in page
    assert "Approved" in page  # over a payment cap is still approved


@pytest.mark.parametrize("page", ["workers", "network", "worker_detail", "movements", "flow"])
def test_no_code_reaches_the_reader(client_in_program, world, page):
    from connect_labs.supply_chain.tests.test_no_raw_codes import CODES, visible_text

    args = [world["worker-baobab"].pk] if page == "worker_detail" else []
    params = {"supply_point_id": world["worker-baobab"].pk} if page == "movements" else {}
    codes = [*CODES, "no_answer", "nothing_given", "not_counted", "unit_refused", "over_limit", "below_min"]
    for worker in ("worker-baobab", "worker-acacia"):
        if page == "worker_detail":
            args = [world[worker].pk]
        text = visible_text(get(client_in_program, page, *args, **params))
        assert [c for c in codes if re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", text)] == []


# ---- Where it went ----------------------------------------------------------


def _flow(da, world, **extra):
    from connect_labs.supply_chain.operations import call_operation

    return call_operation("stock_flow", da, {"item_id": world["item"].pk, **extra})


def _final(flow):
    names = {n["id"]: n["name"] for n in flow["nodes"]}
    return {(names[link["source"]], names[link["target"]]): link["series"][-1] for link in flow["links"]}


def test_the_flow_follows_every_sachet_from_arrival_to_the_visits(da, world):
    flow = _flow(da, world)

    assert _final(flow) == {
        ("Received, no order linked", "Partner store"): 300,
        ("Partner store", "worker-acacia"): 150,
        ("Partner store", "worker-baobab"): 150,
        ("worker-acacia", "Given out at visits"): 30,
        # 40 approved, plus 10 given and then put back when the visit was rejected.
        ("worker-baobab", "Given out at visits"): 40,
    }
    columns = {n["name"]: n["column"] for n in flow["nodes"]}
    assert columns == {
        "Received, no order linked": 0,
        "Partner store": 1,
        "worker-acacia": 2,
        "worker-baobab": 2,
        "Given out at visits": 3,
    }
    assert flow["unit"] == "sachet"


def test_the_flow_carries_each_route_forward_week_by_week(da, world):
    flow = _flow(da, world)
    baobab = f"p{world['worker-baobab'].pk}"
    baobab_given = next(link for link in flow["links"] if link["target"] == "given" and link["source"] == baobab)

    # Running totals, ending at today's figure. The rejected visit and its reversal fall in one
    # week, so that week nets them and the line never shows the 10 that went back.
    series = baobab_given["series"]
    assert len(series) == len(flow["weeks"])
    assert series[0] == 0 and series[-1] == 40
    assert series == sorted(series)


def test_the_flow_as_of_a_day_stops_on_that_day(da, world):
    flow = _flow(da, world, as_of=(TODAY - timedelta(days=50)).isoformat())

    final = _final(flow)
    assert final[("Partner store", "worker-baobab")] == 150
    assert ("worker-baobab", "Given out at visits") not in final


def test_the_flow_page_draws_from_the_payload_and_has_its_tab(client_in_program, world):
    body = get(client_in_program, "flow")

    assert 'id="flow-data"' in body and "supply_chain/flow.js" in body
    assert "Where it went" in body
    assert 'data-tile="given"' in body


# ---- the stock page's movement list --------------------------------------


def test_the_movement_list_shows_a_reversal_going_back_in(client_in_program, world):
    worker = world["worker-baobab"]
    text = text_of(get(client_in_program, "movements", supply_point_id=worker.pk, item_id=world["item"].pk))
    assert "Reversed (visit v-rejected)" in text
    assert "back into worker-baobab" in text
    # The two consumption rows name their visits.
    assert "Consumption (visit v-rejected)" in text
    assert "Consumption (visit v-worker-baobab)" in text


# ---- as of a past day ------------------------------------------------------


def test_as_of_hides_the_write_controls(client_in_program, world):
    worker = world["worker-acacia"]
    past = {"as_of": TODAY.isoformat()}
    new_point = reverse("supply_chain:supply_point_create")
    record_count = reverse("supply_chain:stock_count_record")
    rules = reverse("supply_chain:dispensing_rule_create")

    assert new_point in get(client_in_program, "network")
    assert new_point not in get(client_in_program, "network", **past)
    assert record_count in get(client_in_program, "worker_detail", worker.pk)
    detail_past = get(client_in_program, "worker_detail", worker.pk, **past)
    assert record_count not in detail_past
    assert "read-only" in detail_past
    assert rules not in get(client_in_program, "workers", **past)


def test_as_of_shows_the_day_as_it_stood(client_in_program, world):
    """Before any visit, each worker held all 150 they were given."""
    past = {"as_of": (TODAY - timedelta(days=50)).isoformat()}
    text = text_of(get(client_in_program, "workers", **past))
    assert text.count("150 sachets") >= 2
    assert 'data-testid="given-out-line"' not in get(client_in_program, "workers", **past)
    detail = get(client_in_program, "worker_detail", world["worker-acacia"].pk, **past)
    assert "xf-worker-acacia" not in detail  # that visit had not happened yet


# ---- cost ------------------------------------------------------------------


def _queries(client, name):
    with CaptureQueriesContext(connection) as captured:
        get(client, name)
    return len(captured.captured_queries)


@pytest.mark.parametrize("page", ["workers", "network", "stock", "flow"])
def test_the_page_costs_the_same_whatever_the_number_of_workers(client_in_program, world, page):
    _queries(client_in_program, page)  # warm any per-process caches
    few = _queries(client_in_program, page)
    for n in range(6):
        _worker(world, f"worker-extra-{n}", dispensed=5, status="approved", estimated=n % 2 == 0, counted=140)
    assert _queries(client_in_program, page) == few


# ---- the filters these pages speak through --------------------------------


class TestFigureWords:
    def test_the_given_out_line_says_its_parts_are_dispensing(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import given_out_line

        parts = {
            "dispensed": {"amount": "90.0000", "unit": "sachet"},
            "unapproved": {"amount": "38.0000", "unit": "sachet"},
            "estimated": {"amount": "60", "unit": "sachet"},
        }
        assert given_out_line(parts) == "of 90 given out: 38 on unapproved visits · 60 estimated"
        assert given_out_line({"unapproved": {"amount": "0"}}) == ""
        assert (
            given_out_line({"dispensed": {"amount": "2", "unit": "sachet"}, "estimated": {"unconfirmed": ["x"]}})
            == "of 2 given out: unknown estimated"
        )

    def test_the_on_hand_sum_comes_to_the_figure(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import stock_sum

        row = {
            "issued": {"amount": "300", "unit": "sachet"},
            "dispensed": {"amount": "201", "unit": "sachet"},
            "on_hand": {"amount": "99", "unit": "sachet"},
            "unapproved": {"amount": "11", "unit": "sachet"},
        }
        assert stock_sum(row) == "300 issued − 201 given out · 11 on unapproved visits"
        # Anything neither issued nor dispensed (a correction, a return) is its own term.
        assert stock_sum({**row, "on_hand": {"amount": "103", "unit": "sachet"}}) == (
            "300 issued − 201 given out + 4 other · 11 on unapproved visits"
        )
        assert stock_sum({**row, "on_hand": {"amount": "95", "unit": "sachet"}, "unapproved": None}) == (
            "300 issued − 201 given out − 4 other"
        )
        # A figure that is not a number leaves only the parts.
        assert stock_sum({**row, "on_hand": {"unconfirmed": ["mixed units"]}}) == "11 on unapproved visits"

    def test_too_few_days_read_as_a_date_and_a_rate_so_far(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import (
            cover_notes,
            cover_text,
            stockout_notes,
            stockout_text,
        )

        reason = "only 28 days of dispensing have been recorded here; at least 30 are needed"
        row = {
            "days_to_stockout": {"unconfirmed": [reason]},
            "months_of_stock": {"unconfirmed": [reason]},
            "amc": {"unconfirmed": [reason]},
            "amc_basis": "consumption",
            "rate_days": 28,
            "rate_days_needed": 30,
            "rate_estimate_from": "2026-10-09",
            "rate_per_day_so_far": {"amount": "6.6071", "unit": "sachet"},
        }
        assert stockout_text(row) == "estimate from 9 Oct"
        assert stockout_notes(row) == ["after 30 days of dispensing", "~7 a day so far"]
        assert cover_text(row) == "cover estimate from 9 Oct"
        assert cover_notes(row) == ["after 30 days of dispensing", "~7 a day so far"]
        # A store rated on what it releases; a trickle; a wait that crosses a year.
        crossing = {
            **row,
            "amc_basis": "releases",
            "rate_estimate_from": "2027-01-05",
            "rate_per_day_so_far": {"amount": "0.3", "unit": "sachet"},
        }
        assert stockout_text(crossing) == "estimate from 5 Jan 2027"
        assert stockout_notes(crossing) == ["after 30 days of releases", "under 1 a day so far"]
        # Blocked by something other than the rate: the reason stands, no date.
        negative = {**row, "days_to_stockout": {"unconfirmed": ["a negative balance"]}}
        assert stockout_text(negative) == "unknown"
        assert stockout_notes(negative) == ["a negative balance"]
        # No dispensing at all is left as it was; a figure has no notes.
        other = {"days_to_stockout": {"unconfirmed": ["no consumption yet"]}, "amc": {"unconfirmed": ["x"]}}
        assert stockout_text({**other, "rate_days": None}) == "unknown"
        assert stockout_notes({**other, "rate_days": None, "rate_days_needed": 30}) == ["no consumption yet"]
        assert stockout_notes({"days_to_stockout": "12.5", "rate_days": 60, "rate_days_needed": 30}) == []
        assert stockout_text({"days_to_stockout": "12.5"}) == "12 days"

    def test_a_visit_s_ledger_lines_read_signed(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import moved_text

        assert moved_text([{"quantity": "-5", "unit": "sachet"}], "sachet") == "−5"
        assert moved_text(
            [{"quantity": "-10", "unit": "sachet"}, {"quantity": "+10", "unit": "sachet"}], "sachet"
        ) == ("−10 · +10")
        assert moved_text([{"quantity": "-1", "unit": "carton"}], "sachet") == "−1 carton"
        assert moved_text([], "sachet") == ""

    def test_a_difference_always_says_its_sign(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import signed_figure

        assert signed_figure({"amount": "-5.0000", "unit": "sachet"}) == "−5 sachets"
        assert signed_figure({"amount": "12", "unit": "sachet"}) == "+12 sachets"
        assert signed_figure({"amount": "0", "unit": "sachet"}) == "0 sachets"
        assert signed_figure({"unconfirmed": ["no pack size"]}) == "Unconfirmed"

    def test_days_and_months_read_as_words(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import days_text, months_text

        assert days_text("102.60") == "102 days"
        assert days_text("0.40") == "under a day"
        assert days_text({"unconfirmed": ["no rate yet"]}) == "unknown"
        assert days_text(None) == "—"
        assert months_text("3.40") == "3.4 months of cover"
        assert months_text("1.00") == "1 month of cover"
        assert months_text({"unconfirmed": ["no rate yet"]}) == "cover unknown"


class TestPassThrough:
    """The rule behind "passes stock on" (network/views.py passes_stock_on, band_of)."""

    @staticmethod
    def row(kind, own, below=None, issued="10"):
        return {
            "kind": kind,
            "on_hand": {"amount": own, "unit": "sachet"},
            "issued": {"amount": issued, "unit": "sachet"},
            "status": "stockout" if own == "0" else "ok",
            "subtree": None if below is None else {"on_hand": {"amount": below, "unit": "sachet"}},
        }

    def test_an_empty_store_whose_points_below_hold_stock_passes_it_on(self):
        from connect_labs.supply_chain.network.views import band_of, passes_stock_on

        store = self.row("regional_store", "0", below="230")
        assert passes_stock_on(store) and band_of(store) is None

    def test_empty_is_still_an_alarm_everywhere_else(self):
        from connect_labs.supply_chain.network.views import band_of, passes_stock_on

        for row in (
            self.row("regional_store", "0", below="0"),  # nothing below either
            self.row("facility", "0"),  # nothing below it at all
            self.row("user_held", "0"),  # a worker is meant to hold stock
        ):
            assert not passes_stock_on(row)
            assert band_of(row) == "stockout"

    def test_a_store_holding_some_itself_shows_its_own_band(self):
        from connect_labs.supply_chain.network.views import band_of

        assert band_of(self.row("regional_store", "40", below="270")) == "ok"


# ---- a Stock Management form in the visit list ----------------------------


def _stock_form(world, worker, visit_id, *, counts):
    """A Stock Management visit: nothing dispensed, only what the app recorded."""
    WorkerVisit.objects.create(
        program_id=PROGRAM,
        opportunity_id=PROGRAM,
        visit_id=visit_id,
        xform_id=f"xf-{visit_id}",
        supply_point=worker,
        visit_date=TODAY - timedelta(days=5),
        status="approved",
        form_name="Stock Management",
        outcomes={},
        answers={},
    )
    for kind, quantity, days_ago in counts:
        StockCount.objects.create(
            program_id=PROGRAM,
            supply_point=worker,
            item=world["item"],
            commodity=world["item"].commodity,
            kind=kind,
            counted_on=TODAY - timedelta(days=days_ago),
            quantity=Decimal(quantity),
            quantity_unit="sachet",
            source="commcare_form",
            form_submission_id=f"xf-{visit_id}",
            visit_id=visit_id,
        )


def _read_as(body):
    return [text_of(cell).strip() for cell in re.findall(r'data-testid="visit-read-as">(.*?)</td>', body, re.S)]


def test_a_stock_form_says_what_it_recorded(client_in_program, world):
    worker = world["worker-acacia"]
    _stock_form(world, worker, "v-stock", counts=[("self_reported", 50, 5), ("reported_receipt", 100, 11)])

    cells = _read_as(get(client_in_program, "worker_detail", worker.pk))

    received = (TODAY - timedelta(days=11)).strftime("%-d %b %Y")
    assert f"Reported receiving 100 sachets on {received} Recorded a balance of 50 sachets" in cells
    assert not any("Not read for this item" in cell for cell in cells)


def test_a_stock_form_that_recorded_nothing_for_this_item_says_so(client_in_program, world):
    worker = world["worker-acacia"]
    _stock_form(world, worker, "v-stock-empty", counts=[])

    cells = _read_as(get(client_in_program, "worker_detail", worker.pk))

    assert "Stock form — nothing recorded for this item" in cells


# ---- the reversal on the chart ---------------------------------------------


def test_the_reversed_workers_chart_marks_the_reversal(client_in_program, world):
    body = get(client_in_program, "worker_detail", world["worker-baobab"].pk)
    assert 'data-kind="reversed"' in body
    assert 'data-kind="reversal-mark"' in body
    assert "10 sachets put back (a visit rejected after it was counted)" in body
    assert 'data-testid="reversal-key"' in body


def test_the_stock_page_rates_workers_as_a_plan_per_point_would(client_in_program, world, da):
    """The grouped pass is the Stock page's only change: its figures are the per-point plan's."""
    from connect_labs.supply_chain.stock.operations import network_stock_payload

    grouped = network_stock_payload(da, several_items="refuse")
    planned = network_stock_payload(da, per_point=True)
    for g, p in zip(grouped["points"], planned["points"]):
        for key in g:
            assert g[key] == p[key], (g["name"], key, g[key], p[key])
    assert grouped == planned
    assert any(p["amc"] and p["amc"].get("amount") for p in grouped["points"])
