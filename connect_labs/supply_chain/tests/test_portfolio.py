"""The portfolio: the first supply screen that spans programmes.

**The gate is access, and everything else here is secondary to it.** Supply is
programme-scoped by construction, and a portfolio must never become a way to
see a programme you could not otherwise reach. So the first two tests are the
ones that decide whether this feature is shippable at all:

  1. a viewer who can reach two of the portfolio's three programmes sees
     exactly those two -- asserted on the RENDERED rows, because a row is what
     leaks;
  2. and the page SAYS one is hidden, asserted on the rendered text rather
     than on a context variable, because a reader who is shown two of three
     chains with no mention of the third has been misinformed about the
     operation -- which is the one thing this view exists not to do.

Both were mutated before being trusted; the mutations are recorded on each
test.

No partner name, quantity or price appears here: every programme name,
commodity and figure below is an invented placeholder, per the repo's
public-repo rule.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Commodity, Round, scope_key
from connect_labs.supply_chain.portfolio.models import Portfolio

pytestmark = pytest.mark.django_db

# Three programmes in the portfolio; the viewer below holds only the first two.
ONE = 10951
TWO = 10952
THREE = 10953

NAMES = {ONE: "Placeholder One", TWO: "Placeholder Two", THREE: "Placeholder Three"}


def _sign_in(client, django_user_model, program_ids, username="jo"):
    """A signed-in viewer holding exactly `program_ids`.

    The session's cached `organization_data` is what `get_org_data` reads, and
    `get_org_data` is the single source this view is allowed to ask -- the same
    one the programme picker uses.
    """
    user = django_user_model.objects.create_user(username=username, password="x")
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "placeholder-token",
        "organization_data": {
            "organizations": [],
            "programs": [{"id": pid, "name": NAMES[pid]} for pid in program_ids],
            "opportunities": [],
        },
    }
    session.save()
    return user


def _portfolio(program_ids=(ONE, TWO, THREE)):
    return Portfolio.objects.create(
        slug="a-placeholder-portfolio",
        name="A Placeholder Portfolio",
        program_ids=list(program_ids),
    )


def _url(portfolio):
    return reverse("supply_chain:portfolio", args=[portfolio.slug])


def _rows(body):
    """The programme ids the page actually rendered a row for, in order."""
    return [int(pid) for pid in re.findall(r'data-programme-id="(\d+)"', body)]


def _a_chain_in(program_id, *, slug="a-placeholder-product", unit="placeholder unit"):
    """Enough of a chain for a programme to be more than empty."""
    Commodity.objects.create(
        scope_key=scope_key(program_id=program_id),
        slug=slug,
        name=slug.replace("-", " "),
        category="consumable",
        base_unit=unit,
    )
    Round.objects.create(
        program_id=program_id,
        label="A placeholder round",
        status="draft",
        lines=[{"commodity_slug": slug, "quantity": "10", "quantity_unit": unit}],
    )


# ---------------------------------------------------------------------------
# 1. Access. The gate.
# ---------------------------------------------------------------------------


def test_a_viewer_sees_only_the_portfolios_programmes_they_can_already_reach(client, django_user_model):
    """The defect that would make this feature unshippable.

    MUTATED: the view was changed to iterate `portfolio.program_ids` directly
    and build a row for every one of them, with no reachability filter. This
    test went red. The 200 is asserted alongside the rows because that is what
    the mutation actually produced -- a 403 for the whole page, raised by
    `SupplyDataAccess` on the unreachable programme. Which is the point of the
    next test: the layer below refuses too, so this one would pass on a view
    that had no filter of its own, and something has to pin the filter itself.
    """
    _sign_in(client, django_user_model, [ONE, TWO])
    for program_id in (ONE, TWO, THREE):
        _a_chain_in(program_id)
    portfolio = _portfolio()

    response = client.get(_url(portfolio))
    body = response.content.decode()

    assert response.status_code == 200
    assert _rows(body) == [ONE, TWO]
    assert f'data-programme-id="{THREE}"' not in body
    assert NAMES[THREE] not in body


def test_the_view_leaves_the_unreachable_one_out_even_with_the_layer_below_removed(
    client, django_user_model, monkeypatch
):
    """The leak test proper, with the second gate taken away.

    `SupplyDataAccess` authorises its own scope, so the test above goes red on
    a filter-less view by 403-ing rather than by leaking -- which is good
    defence in depth and a bad way to learn that this view filters. Here the
    data-access layer is built as SYSTEM, so it refuses nothing, and the
    view's own reachability check is the only thing standing between the
    portfolio and a programme the viewer does not hold.

    MUTATED, with this substitution in place: the reachability check removed
    from the view. Programme THREE's row rendered in full, 200 and all -- an
    actual leak -- and this test went red. Reverted.
    """
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain import data_access as data_access_module

    def _unauthorised(*args, caller=None, **kwargs):
        return data_access_module.SupplyDataAccess(*args, caller=SYSTEM, **kwargs)

    monkeypatch.setattr("connect_labs.supply_chain.portfolio.views.SupplyDataAccess", _unauthorised)

    _sign_in(client, django_user_model, [ONE, TWO])
    for program_id in (ONE, TWO, THREE):
        _a_chain_in(program_id)
    portfolio = _portfolio()

    response = client.get(_url(portfolio))
    body = response.content.decode()

    assert response.status_code == 200
    assert _rows(body) == [ONE, TWO]


def test_the_page_says_when_it_is_hiding_one(client, django_user_model):
    """Asserted on the rendered text, not on a context variable.

    A portfolio that shows two of three chains without mentioning the third
    misrepresents the operation. The count and the reason both have to reach
    the screen.

    MUTATED: the "not shown" paragraph was removed from the template while the
    `hidden` context value stayed correct. This test went red; a test written
    against the context variable would not have. Reverted.
    """
    _sign_in(client, django_user_model, [ONE, TWO])
    portfolio = _portfolio()

    body = client.get(_url(portfolio)).content.decode()

    assert "1 of this portfolio's 3 programmes is not shown" in body
    assert "your account cannot reach it" in body


def test_a_viewer_who_can_reach_none_of_them_is_told_so_rather_than_shown_an_empty_page(client, django_user_model):
    _sign_in(client, django_user_model, [])
    portfolio = _portfolio()

    body = client.get(_url(portfolio)).content.decode()

    assert _rows(body) == []
    assert "3 of this portfolio's 3 programmes are not shown" in body


def test_a_programme_id_that_is_not_a_number_is_hidden_rather_than_rendered(client, django_user_model):
    """Unreachable is unreachable. It is counted as hidden, never as a row.

    Nothing reachable can have a non-integer id, so the honest handling is the
    same as for a programme the viewer does not hold: leave it out, and say
    that something was left out.
    """
    _sign_in(client, django_user_model, [ONE])
    portfolio = _portfolio([ONE, "not-a-programme-id"])

    body = client.get(_url(portfolio)).content.decode()

    assert _rows(body) == [ONE]
    assert "1 of this portfolio's 2 programmes is not shown" in body


def test_a_portfolio_nobody_has_named_is_not_found(client, django_user_model):
    _sign_in(client, django_user_model, [ONE, TWO])

    assert client.get(reverse("supply_chain:portfolio", args=["no-such-portfolio"])).status_code == 404


def test_the_page_needs_a_sign_in(client):
    _portfolio()

    response = client.get(_url(Portfolio.objects.get()))

    assert response.status_code == 302


# ---------------------------------------------------------------------------
# 2. What each row says
# ---------------------------------------------------------------------------


def test_rows_keep_the_portfolios_own_order_and_are_not_ranked(client, django_user_model):
    """Ordered by something STATED, never by a computed severity.

    `DomainHomeView`'s docstring records that a "Needs you" priority banner was
    built and removed, because prioritising is a judgement about what matters
    today and the database does not contain what it would take to make it.
    This view knows no more than that one does. So the order on screen is the
    portfolio's own list order, and reversing the list reverses the page.
    """
    _sign_in(client, django_user_model, [ONE, TWO])
    # TWO carries a chain and ONE is empty, so any severity ranking would put
    # one of them first regardless of the list.
    _a_chain_in(TWO)

    forwards = client.get(_url(_portfolio([ONE, TWO]))).content.decode()
    Portfolio.objects.all().delete()
    backwards = client.get(_url(_portfolio([TWO, ONE]))).content.decode()

    assert _rows(forwards) == [ONE, TWO]
    assert _rows(backwards) == [TWO, ONE]


def test_a_programme_with_nothing_recorded_reads_as_nothing_recorded_yet(client, django_user_model):
    """Not as a broken row, and not as a grid of zeroes.

    Two of the three chains this was built for are seeded by tasks that have
    not landed, so an empty programme is a state the page will really be in.
    """
    _sign_in(client, django_user_model, [ONE, TWO])
    _a_chain_in(ONE)
    portfolio = _portfolio([ONE, TWO])

    body = client.get(_url(portfolio)).content.decode()

    assert "Nothing recorded yet" in body


def test_each_row_counts_only_its_own_programme_and_keeps_its_own_units(client, django_user_model):
    """There is no conversion between a carton and a jerry can.

    `chain_summary` only produces quantities once a single commodity is named,
    and the portfolio names each programme's OWN -- so two rows can carry two
    different units and neither is ever added to the other.

    The arithmetic assertion is the one with teeth: three rounds exist across
    the portfolio, two in one programme and one in the other, and no row
    reports three.

    MUTATED: `_row` changed to build every row's access against the first
    programme in the list. Both rows then reported 2 and the second row named
    the wrong commodity. Red on three assertions. Reverted.
    """
    _sign_in(client, django_user_model, [ONE, TWO])
    _a_chain_in(ONE, slug="placeholder-alpha", unit="placeholder carton")
    Round.objects.create(program_id=ONE, label="A second placeholder round", status="draft", lines=[])
    _a_chain_in(TWO, slug="placeholder-beta", unit="placeholder jerry can")
    portfolio = _portfolio([ONE, TWO])

    response = client.get(_url(portfolio))
    body = response.content.decode()
    rows = response.context["rows"]

    assert rows[0]["summary"]["source"]["demand"]["rounds"] == 2
    assert rows[1]["summary"]["source"]["demand"]["rounds"] == 1
    assert rows[0]["commodity_slug"] == "placeholder-alpha"
    assert rows[1]["commodity_slug"] == "placeholder-beta"
    assert "placeholder alpha" in body
    assert "placeholder beta" in body


# ---------------------------------------------------------------------------
# 3. What is blocked, including the case where nothing is owed a date
# ---------------------------------------------------------------------------


def _an_order_awaited_in(program_id, *, lead_time_days, slug="a-placeholder-product"):
    """An order placed, not arrived, at a store in `program_id`.

    `lead_time_days=None` is the case the design calls out: an order that
    carries a quantity and a supplier and no promised date. That is the honest
    state, not missing data, and it is the one a funder asks about first.
    """
    from datetime import date, timedelta

    from connect_labs.supply_chain.models import Contract, Supplier, SupplyPoint

    scope = scope_key(program_id=program_id)
    commodity = Commodity.objects.filter(scope_key=scope, slug=slug).first() or Commodity.objects.create(
        scope_key=scope, slug=slug, name=slug.replace("-", " "), category="consumable", base_unit="placeholder unit"
    )
    supplier = Supplier.objects.enrol(scope_key=scope, name="A Placeholder Supplier")
    store = SupplyPoint.objects.create(
        program_id=program_id,
        slug=f"a-placeholder-store-{program_id}",
        name="A Placeholder Store",
        kind="central_store",
        source="we_recorded",
    )
    return Contract.objects.create(
        program_id=program_id,
        supplier=supplier,
        commodity=commodity,
        buyer_of_record="programme_org",
        status="placed",
        signed_on=date.today() - timedelta(days=30),
        quantity=100,
        quantity_unit="placeholder unit",
        delivery_supply_point=store,
        promised_lead_time_days=lead_time_days,
        source="we_recorded",
    )


def test_an_order_nobody_has_promised_a_date_for_says_so_rather_than_trailing_off(client, django_user_model):
    """Design section 6a, on the portfolio.

    The stock page renders its expected date behind an `{% if %}`, so with no
    lead time ever promised the sentence simply stops after the supplier's
    name -- and silence there reads as "fine" when the truth is "blocked, and
    we cannot tell you until when". The portfolio must not repeat that.

    MUTATED: `_awaited` changed to count only consignments that HAVE a date
    (the shape the stock template's `{% if %}` has). The row then said nothing
    was owed at all, and this test went red on both assertions. Reverted.
    """
    _sign_in(client, django_user_model, [ONE, TWO])
    _an_order_awaited_in(ONE, lead_time_days=None)
    portfolio = _portfolio([ONE, TWO])

    response = client.get(_url(portfolio))
    body = response.content.decode()

    assert response.context["rows"][0]["awaited"] == {
        "consignments": 1,
        "overdue": 0,
        "undated": 1,
        "next_expected": None,
    }
    # The wording changed when the page was rewritten to lead with the
    # situation rather than the sourcing lifecycle; what it must SAY did not.
    # Both halves are asserted separately so a rewrite that keeps "blocked"
    # while dropping the reason, or vice versa, still goes red.
    assert "Blocked" in body
    assert "no arrival date has been promised" in body
    assert "1 of 1 consignment still owed" in body


def test_an_order_past_the_date_that_was_promised_is_counted_as_overdue(client, django_user_model):
    """The other half of the same sentence, so "undated" cannot pass as "late"."""
    _sign_in(client, django_user_model, [ONE, TWO])
    _an_order_awaited_in(ONE, lead_time_days=7)
    portfolio = _portfolio([ONE, TWO])

    response = client.get(_url(portfolio))
    body = response.content.decode()
    awaited = response.context["rows"][0]["awaited"]

    assert awaited["consignments"] == 1
    assert awaited["overdue"] == 1
    assert awaited["undated"] == 0
    assert "No arrival date has been promised" not in body
    assert "past the date promised" in body
    assert "1 of 1 consignment" in body


def test_a_chain_nobody_owes_anything_says_that_rather_than_going_quiet(client, django_user_model):
    _sign_in(client, django_user_model, [ONE, TWO])
    _a_chain_in(ONE)
    portfolio = _portfolio([ONE, TWO])

    body = client.get(_url(portfolio)).content.decode()

    assert "Nothing is owed to this chain." in body


def test_a_row_links_into_that_programmes_own_overview(client, django_user_model):
    """A portfolio is a way in, so every row has to be one."""
    _sign_in(client, django_user_model, [ONE, TWO])
    portfolio = _portfolio([ONE, TWO])

    body = client.get(_url(portfolio)).content.decode()

    assert f"{reverse('supply_chain:home')}?program_id={ONE}" in body


# ---------------------------------------------------------------------------
# 5. The total, and the one case where there must not be one.
# ---------------------------------------------------------------------------


def _stock_in(program_id, *, slug, unit, point_slug, quantity):
    """Stock actually resting somewhere, through the domain's own write path.

    `_a_chain_in` gives a programme a catalogue and a round, which is enough
    for it to stop being empty and nothing like enough to have a balance. The
    situation this page leads with is read from the LEDGER, so a test about
    what it totals has to put goods on it.
    """
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.operations import call_operation

    access = SupplyDataAccess(access_token="unused", program_id=program_id, caller=SYSTEM)
    if not Commodity.objects.filter(scope_key=scope_key(program_id=program_id), slug=slug).exists():
        Commodity.objects.create(
            scope_key=scope_key(program_id=program_id),
            slug=slug,
            name=slug.replace("-", " "),
            category="consumable",
            base_unit=unit,
        )
    point = call_operation(
        "supply_point_upsert",
        access,
        {
            "data": {
                "slug": point_slug,
                "name": point_slug.replace("-", " "),
                "kind": "facility",
                "source": "we_recorded",
            }
        },
    )
    call_operation(
        "movement_record",
        access,
        {
            "data": {
                "kind": "receipt",
                "occurred_on": "2026-09-01",
                "to_supply_point_id": point["id"],
                "commodity_slug": slug,
                "quantity": str(quantity),
                "quantity_unit": unit,
                "source": "we_recorded",
            }
        },
    )
    return point


def test_a_chain_holding_one_unit_is_totalled(client, django_user_model):
    """The ordinary case, asserted so the refusal below is not vacuously true.

    A test that only proves a total is WITHHELD would pass on a page that
    never totals anything at all. This is the other half.
    """
    _sign_in(client, django_user_model, [ONE])
    _a_chain_in(ONE, slug="placeholder-alpha", unit="placeholder carton")
    _stock_in(ONE, slug="placeholder-alpha", unit="placeholder carton", point_slug="store-one", quantity=30)
    _stock_in(ONE, slug="placeholder-alpha", unit="placeholder carton", point_slug="store-two", quantity=12)

    response = client.get(_url(_portfolio([ONE])))
    situation = response.context["rows"][0]["situation"]

    assert situation["total"] == 42
    assert situation["units_differ"] is False
    assert "42 placeholder cartons" in response.content.decode()


def test_a_chain_holding_two_units_is_not_totalled_and_says_why(client, django_user_model):
    """Cartons and jerry cans are not added, here or anywhere in this domain.

    This is the guard the situation summary rests on, and it was UNTESTED when
    written: mutating `total` to sum regardless of unit, and `units_differ` to
    False, left all fourteen tests green. A page that reported "42" for thirty
    cartons and twelve jerry cans would have shipped.

    MUTATED, after writing: both mutations above were reapplied and this test
    went red on the total and on the sentence. Reverted.
    """
    _sign_in(client, django_user_model, [ONE])
    _a_chain_in(ONE, slug="placeholder-alpha", unit="placeholder carton")
    _stock_in(ONE, slug="placeholder-alpha", unit="placeholder carton", point_slug="store-one", quantity=30)
    _stock_in(ONE, slug="placeholder-beta", unit="placeholder jerry can", point_slug="store-two", quantity=12)

    response = client.get(_url(_portfolio([ONE])))
    situation = response.context["rows"][0]["situation"]
    body = response.content.decode()

    assert situation["total"] is None, "two units were added together"
    assert situation["units_differ"] is True
    assert "in units that cannot be added together" in body
    # And neither figure is lost: each place still carries its own.
    assert "30 placeholder cartons" in body
    assert "12 placeholder jerry cans" in body
    assert "42" not in _rows_region(body), "a total was rendered for two units"


def _rows_region(body):
    """The card bodies, without the page chrome a stray number could hide in."""
    return "".join(re.findall(r'<section data-programme-id="\d+".*?</section>', body, re.S))
