"""The "how a purchase works" guide: the steps in order, the programme's next step highlighted.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.guide import STEPS, steps_for
from connect_labs.supply_chain.tests import test_workflow_views as pinned

client_in_programme = pinned.client_in_programme
da = pinned.da


def _next(facts):
    return [s.key for s in steps_for(facts) if s.next]


def test_a_new_programme_starts_at_the_first_step():
    steps = steps_for({})
    assert [s.key for s in steps] == [key for key, *_ in STEPS]
    assert _next({}) == ["partners"]
    assert not any(s.done for s in steps)


def test_suppliers_on_file_moves_the_next_step_on_to_quantities():
    # "Check partners" has no record of its own: it is done once anything after it is.
    assert _next({"suppliers": 3}) == ["quantities"]


def test_a_tender_with_nothing_sent_is_waiting_to_ask_for_quotes():
    assert _next({"suppliers": 3, "tenders": 1}) == ["ask"]


def test_asking_for_quotes_then_recording_replies_then_awarding():
    base = {"suppliers": 3, "tenders": 1, "tenders_open_or_later": 1}
    assert _next(base) == ["replies"]
    assert _next({**base, "outreach": 4, "quotes": 2}) == ["compare"]
    assert _next({**base, "quotes": 2, "tenders_closed_or_later": 1}) == ["award"]
    assert _next({**base, "quotes": 2, "awards": 1}) == ["order"]


def test_after_the_orders_it_is_warehouses_then_deliveries_and_then_nothing_is_next():
    bought = {"suppliers": 3, "tenders": 1, "quotes": 2, "awards": 1, "orders": 2}
    assert _next(bought) == ["warehouses"]
    assert _next({**bought, "stock_points": 3}) == ["deliveries"]
    done = steps_for({**bought, "stock_points": 3, "receipts": 1})
    assert all(s.done for s in done) and not any(s.next for s in done)


def test_a_later_step_never_leaves_an_earlier_one_open():
    # An order exists but no tender is recorded (an order placed outside Supply): the guide
    # does not send the programme back to step 1.
    assert _next({"orders": 1}) == ["warehouses"]


@pytest.mark.django_db
def test_the_sourcing_page_shows_the_guide_with_the_next_step_marked(client_in_programme):
    from connect_labs.supply_chain.models import Tender

    Tender.objects.create(program_id=pinned.PROGRAM, label="Round 1", status="open")
    body = client_in_programme.get(reverse("supply_chain:procurement_tender_board")).content.decode()

    assert 'data-testid="purchase-guide"' in body and "How a purchase works" in body
    assert body.count('data-testid="guide-step"') == len(STEPS)
    assert body.count('data-state="next"') == 1
    marked = body.split('data-state="next"', 1)[1].split("</li>", 1)[0]
    assert "Record replies" in marked and "Next step" in marked
    assert reverse("supply_chain:orders") in body


@pytest.mark.django_db
def test_the_orders_page_shows_the_guide_too(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:orders")).content.decode()
    assert 'data-testid="purchase-guide"' in body and 'data-state="next"' in body


def _guide_tag(body):
    return body.split('data-testid="purchase-guide"', 1)[0].rsplit("<details", 1)[1]


@pytest.mark.django_db
def test_the_orders_page_folds_the_guide_to_one_line_but_keeps_it_expandable(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:orders")).content.decode()
    assert " open " not in _guide_tag(body) + " "
    assert "Next: Check partners" in body and body.count('data-testid="guide-step"') == len(STEPS)


@pytest.mark.django_db
def test_the_sourcing_page_folds_the_guide_once_every_step_is_done(client_in_programme, monkeypatch):
    from connect_labs.supply_chain import guide

    done = {"suppliers": 3, "tenders": 1, "quotes": 2, "awards": 1, "orders": 2, "stock_points": 3, "receipts": 1}
    monkeypatch.setattr(guide, "facts_for", lambda program_id: done)
    body = client_in_programme.get(reverse("supply_chain:procurement_tender_board")).content.decode()
    assert f"All {len(STEPS)} steps done" in body
    assert " open" not in _guide_tag(body)


@pytest.mark.django_db
def test_the_sourcing_page_keeps_the_guide_open_while_a_step_remains(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:procurement_tender_board")).content.decode()
    assert " open" in _guide_tag(body) and "Next: " in body
