"""Did this supplier deliver when they said, and all of it?

THIS REPOSITORY IS PUBLIC. Every supplier name and figure here is invented.

The domain recorded what a supplier PROMISED -- `promised_lead_time_days` on
every contract -- and what actually happened -- a receipt, with a date and a
quantity -- and never once compared the two. So the supplier directory was a
list of names, the award page said who had been chosen and never how the last
one went, and "which of these has ever let us down" could not be asked.

OTIF (on time, in full) is the sector's standard answer and is computable
from what is already stored. Four decisions make it honest rather than
flattering:

**A supplier who promised nothing is not on time.** A contract with no
promised lead time cannot be judged, so it is EXCLUDED from the rate and
counted separately. Scoring it as a pass would reward never committing to a
date, which is the opposite of what this measures.

**An order still in transit is not late yet.** It is unfinished, not failed.
It is excluded too, and counted, so a supplier cannot look good by having
everything outstanding.

**The denominator is always shown.** "2 of 2" and "200 of 200" are different
claims, and a bare "100%" hides which one you have. No rate is reported
without the count it came from.

**Refused goods are not delivered.** In full means accepted, not shipped.
"""

from datetime import date, timedelta

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services.performance import supplier_performance

pytestmark = pytest.mark.django_db

PROGRAM = 10955


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def access():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def world(access):
    call_operation(
        "commodity_upsert",
        access,
        {
            "data": {
                "slug": "a-placeholder-good",
                "name": "A placeholder good",
                "base_unit": "unit",
                "pack_unit": "box",
            }
        },
    )
    supplier = call_operation("supplier_create", access, {"data": {"name": "A Placeholder Seller"}})
    buyer = call_operation("org_upsert", access, {"data": {"slug": "a-buyer", "name": "A Placeholder Buyer"}})
    store = call_operation(
        "supply_point_upsert",
        access,
        {"data": {"slug": "a-store", "name": "A placeholder store", "kind": "central_store", "source": "we_recorded"}},
    )
    return {"access": access, "supplier": supplier, "buyer": buyer, "store": store}


def _order(world, *, signed, promised_days, quantity="100"):
    return call_operation(
        "contract_create",
        world["access"],
        {
            "data": {
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "a-placeholder-good",
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["buyer"]["id"],
                "delivery_supply_point_id": world["store"]["id"],
                "quantity": quantity,
                "quantity_unit": "box",
                "currency": "USD",
                "status": "placed",
                "signed_on": signed,
                "source": "we_recorded",
                **({"promised_lead_time_days": promised_days} if promised_days is not None else {}),
            }
        },
    )


def _delivered(world, contract, *, on, accepted, refused=None):
    lines = [{"quantity_accepted": accepted, "quantity_unit": "box"}]
    if refused:
        lines[0]["quantity_rejected"] = refused
        lines[0]["rejection_reason"] = "damaged in transit"
    return call_operation(
        "receipt_record",
        world["access"],
        {
            "data": {
                "contract_id": contract["id"],
                "supply_point_id": world["store"]["id"],
                "received_on": on,
                "source": "we_recorded",
                "lines": lines,
            }
        },
    )


def _for_supplier(world):
    rows = supplier_performance(world["access"])
    return next(r for r in rows if r["supplier_id"] == world["supplier"]["id"])


class TestOnTimeAndInFull:
    def test_an_order_that_arrived_early_and_complete_counts_both_ways(self, world):
        c = _order(world, signed=days_ago(40), promised_days=30)
        _delivered(world, c, on=days_ago(15), accepted="100")

        row = _for_supplier(world)
        assert row["measurable"] == 1
        assert row["on_time"] == 1
        assert row["in_full"] == 1
        assert row["otif"] == 1

    def test_an_order_that_arrived_late_but_complete_is_in_full_and_not_on_time(self, world):
        """The two halves are reported separately, and this is why.

        A supplier who always delivers everything a fortnight late is a
        different problem from one who is punctual and short, and a single
        OTIF number cannot tell you which you have.
        """
        c = _order(world, signed=days_ago(60), promised_days=30)
        _delivered(world, c, on=days_ago(5), accepted="100")

        row = _for_supplier(world)
        assert row["on_time"] == 0
        assert row["in_full"] == 1
        assert row["otif"] == 0

    def test_goods_rejected_on_arrival_were_not_delivered(self, world):
        """In full means accepted, not shipped.

        The fixture records a real rejection now. It first passed
        `quantity_refused`, which is not the field -- the model's is
        `quantity_rejected` -- and the permissive payload schema dropped it
        silently. The test still passed, because ninety accepted of a hundred
        ordered is short whether or not anybody wrote down why, so it was
        pinning a SHORTFALL while claiming to pin a rejection.

        That the two reach the same verdict is the design, not a coincidence:
        `in_full` asks whether what we accepted covers what we ordered, and
        rejected goods were not accepted. What the corrected fixture buys is
        that the scenario is now the one the name describes.
        """
        c = _order(world, signed=days_ago(40), promised_days=30)
        _delivered(world, c, on=days_ago(20), accepted="90", refused="10")

        row = _for_supplier(world)
        assert row["on_time"] == 1
        assert row["in_full"] == 0, "ten boxes were refused, so the order did not arrive in full"
        assert row["otif"] == 0


class TestWhatCannotBeJudgedIsNotScored:
    def test_a_supplier_who_promised_no_date_is_not_counted_as_punctual(self, world):
        """The decision that keeps this from being flattering.

        Scoring an unpromised order as on-time would make "never commit to a
        date" the winning strategy.
        """
        c = _order(world, signed=days_ago(40), promised_days=None)
        _delivered(world, c, on=days_ago(5), accepted="100")

        row = _for_supplier(world)
        assert row["measurable"] == 0
        assert row["no_promise"] == 1
        assert row["otif_rate"] is None, "a rate out of nothing is not a rate"

    def test_an_order_still_on_its_way_is_unfinished_not_failed(self, world):
        _order(world, signed=days_ago(5), promised_days=30)

        row = _for_supplier(world)
        assert row["measurable"] == 0
        assert row["not_yet_due"] == 1

    def test_a_supplier_cannot_look_good_by_delivering_nothing(self, world):
        """One late order and three outstanding is not 0 of 1 dressed as fine."""
        late = _order(world, signed=days_ago(90), promised_days=30)
        _delivered(world, late, on=days_ago(5), accepted="100")
        for _ in range(3):
            _order(world, signed=days_ago(2), promised_days=60)

        row = _for_supplier(world)
        assert row["measurable"] == 1
        assert row["otif"] == 0
        assert row["otif_rate"] == 0.0
        assert row["not_yet_due"] == 3, "the outstanding orders are visible, not hidden"


class TestTheDenominatorIsAlwaysShown:
    def test_a_rate_never_appears_without_the_count_behind_it(self, world):
        c = _order(world, signed=days_ago(40), promised_days=30)
        _delivered(world, c, on=days_ago(15), accepted="100")

        row = _for_supplier(world)
        assert row["otif_rate"] == 1.0
        assert row["measurable"] == 1, "'100%' out of one order is a different claim from out of a hundred"


class TestLeadTime:
    def test_it_reports_what_was_promised_against_what_happened(self, world):
        c = _order(world, signed=days_ago(60), promised_days=30)
        _delivered(world, c, on=days_ago(20), accepted="100")

        row = _for_supplier(world)
        assert row["promised_days_median"] == 30
        assert row["actual_days_median"] == 40
        assert row["days_late_worst"] == 10


# ---------------------------------------------------------------------------
# On the page. A measure like this misleads through its presentation more
# easily than through its arithmetic, so the screen is tested too.
# ---------------------------------------------------------------------------

import re  # noqa: E402

from django.urls import reverse  # noqa: E402


@pytest.fixture
def screen(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="perf", password="x", email="perf@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    monkeypatch.setattr("connect_labs.supply_chain.views._access", _scoped)
    monkeypatch.setattr("connect_labs.supply_chain.views.has_program_context", lambda request: True)
    return client


def _page(screen, world):
    body = screen.get(reverse("supply_chain:supplier_detail", args=[world["supplier"]["id"]])).content.decode()
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


class TestThePageSaysWhatItCounted:
    def test_a_rate_is_shown_as_a_count_out_of_a_count(self, screen, world):
        """Never a bare percentage. "100%" out of one order and out of two
        hundred are different claims and a reader cannot tell them apart."""
        c = _order(world, signed=days_ago(40), promised_days=30)
        _delivered(world, c, on=days_ago(15), accepted="100")

        text = _page(screen, world)
        assert "1 of 1" in text
        assert "100%" not in text

    def test_nothing_to_judge_is_not_dressed_as_a_bad_record(self, screen, world):
        """MUTATED: the template's `{% if performance.measurable %}` was
        removed so the zero branch rendered the grid. "0 of 0" appeared where
        this asserts it does not. Reverted."""
        c = _order(world, signed=days_ago(40), promised_days=None)
        _delivered(world, c, on=days_ago(5), accepted="100")

        text = _page(screen, world)
        assert "Nothing to judge yet" in text
        assert "0 of 0" not in text

    def test_what_was_left_out_of_the_count_is_said(self, screen, world):
        """A denominator that quietly drops orders is the other way this lies."""
        c = _order(world, signed=days_ago(40), promised_days=30)
        _delivered(world, c, on=days_ago(15), accepted="100")
        _order(world, signed=days_ago(2), promised_days=60)

        text = _page(screen, world)
        assert "1 of 1" in text
        assert "1 still on the way" in text
