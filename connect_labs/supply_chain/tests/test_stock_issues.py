"""Stock issues and custodians: missing, taken, damaged or expired stock, with its outcome.

The rules under test: reporting an issue takes the quantity off the point at once, linked to
the issue; a recovery or replacement puts it back linked to the same issue; a write-off posts
nothing more; the custodian in post that day is recorded as responsible; and a custodian's
history is kept, never overwritten. Placeholder names and figures only (public repo).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Movement, SupplyPoint, scope_key
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services import issues, ledger

pytestmark = pytest.mark.django_db

PROGRAM = 10982


def _access(program_id=PROGRAM):
    return SupplyDataAccess(access_token="placeholder", program_id=program_id, caller=SYSTEM)


def _op(name, **payload):
    return call_operation(name, _access(), payload)


def _day(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def clinic():
    Commodity.objects.create(
        scope_key=scope_key(program_id=PROGRAM), slug="a-product", name="A Product", base_unit="unit"
    )
    point = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="a-clinic", name="A Clinic", kind="facility", source="we_recorded"
    )
    _op(
        "movement_record",
        data={
            "kind": "adjustment",
            "to_supply_point_id": point.pk,
            "occurred_on": _day(30),
            "commodity_slug": "a-product",
            "quantity": "100",
            "quantity_unit": "unit",
            "batch": "B1",
            "source": "we_recorded",
        },
    )
    return point


def _held(point):
    return ledger.balance(PROGRAM, point, unit="unit").amount


def _report(point, **over):
    data = {
        "supply_point_id": point.pk,
        "kind": "missing",
        "occurred_on": _day(2),
        "commodity_slug": "a-product",
        "batch": "B1",
        "quantity": "10",
        "quantity_unit": "unit",
        "reported_by": "A Reporter",
        "evidence": "a message from the clinic",
        "source": "partner_reported",
    }
    data.update(over)
    return _op("stock_issue_report", data=data)


def test_reporting_an_issue_takes_the_stock_off_the_point_linked_to_the_issue(clinic):
    issue = _report(clinic)
    assert issue["status"] == "open" and issue["outcome"] == ""
    assert _held(clinic) == Decimal("90")
    loss = Movement.objects.get(stock_issue_id=issue["id"])
    assert (loss.kind, loss.quantity, loss.from_supply_point_id) == ("loss", Decimal("10"), clinic.pk)
    assert loss.batch == "B1"


def test_an_expired_issue_posts_an_expiry_movement(clinic):
    issue = _report(clinic, kind="expired")
    assert Movement.objects.get(stock_issue_id=issue["id"]).kind == "expiry"


def test_a_recovery_puts_the_stock_back_linked_to_the_issue(clinic):
    issue = _report(clinic, kind="taken", taken_by="A Neighbour")
    resolved = _op(
        "stock_issue_resolve",
        issue_id=issue["id"],
        data={"outcome": "recovered", "resolved_on": _day(0), "source": "we_recorded"},
    )
    assert resolved["status"] == "resolved" and resolved["outcome"] == "recovered"
    assert _held(clinic) == Decimal("100")
    legs = list(Movement.objects.filter(stock_issue_id=issue["id"]).order_by("id").values_list("kind", "quantity"))
    assert legs == [("loss", Decimal("10")), ("return", Decimal("10"))]


def test_a_partial_recovery_leaves_the_rest_lost(clinic):
    issue = _report(clinic)
    _op(
        "stock_issue_resolve",
        issue_id=issue["id"],
        data={"outcome": "recovered", "quantity_recovered": "4", "resolved_on": _day(0), "source": "we_recorded"},
    )
    assert _held(clinic) == Decimal("94")


def test_a_write_off_posts_nothing_more(clinic):
    issue = _report(clinic)
    _op(
        "stock_issue_resolve",
        issue_id=issue["id"],
        data={"outcome": "written_off", "resolved_on": _day(0), "source": "we_recorded"},
    )
    assert _held(clinic) == Decimal("90")
    assert Movement.objects.filter(stock_issue_id=issue["id"]).count() == 1


def test_a_replacement_is_a_receipt_linked_to_the_issue(clinic):
    issue = _report(clinic)
    _op(
        "stock_issue_resolve",
        issue_id=issue["id"],
        data={"outcome": "replaced", "resolved_on": _day(0), "source": "we_recorded"},
    )
    assert Movement.objects.filter(stock_issue_id=issue["id"], kind="receipt").count() == 1


def test_a_resolved_issue_cannot_be_resolved_again(clinic):
    issue = _report(clinic)
    data = {"outcome": "written_off", "resolved_on": _day(0), "source": "we_recorded"}
    _op("stock_issue_resolve", issue_id=issue["id"], data=data)
    with pytest.raises(ValueError, match="already resolved"):
        _op("stock_issue_resolve", issue_id=issue["id"], data=data)


def test_cannot_recover_more_than_was_lost(clinic):
    issue = _report(clinic)
    with pytest.raises(ValueError, match="cannot come back"):
        _op(
            "stock_issue_resolve",
            issue_id=issue["id"],
            data={"outcome": "recovered", "quantity_recovered": "11", "resolved_on": _day(0), "source": "we_recorded"},
        )


def test_following_up_moves_the_status_and_names_who_took_it(clinic):
    issue = _report(clinic, kind="taken")
    updated = _op(
        "stock_issue_update",
        issue_id=issue["id"],
        data={"status": "following_up", "taken_by": "A Neighbour", "source": "we_recorded"},
    )
    assert (updated["status"], updated["taken_by"]) == ("following_up", "A Neighbour")


def test_the_custodian_in_post_that_day_is_responsible(clinic):
    first = _op(
        "custodian_assign",
        data={
            "supply_point_id": clinic.pk,
            "name": "First Custodian",
            "phone": "000",
            "security_measures": ["locked_room"],
            "starts_on": _day(60),
            "source": "we_recorded",
        },
    )
    second = _op(
        "custodian_assign",
        data={
            "supply_point_id": clinic.pk,
            "name": "Second Custodian",
            "security_measures": ["locked_box", "facility_agreement"],
            "starts_on": _day(5),
            "source": "we_recorded",
        },
    )
    before = _report(clinic, occurred_on=_day(10))
    after = _report(clinic, occurred_on=_day(1))
    assert before["custodian_id"] == first["id"]
    assert after["custodian_id"] == second["id"]


def test_a_custodian_change_keeps_history(clinic):
    for name, days in (("First Custodian", 60), ("Second Custodian", 5)):
        _op(
            "custodian_assign",
            data={"supply_point_id": clinic.pk, "name": name, "starts_on": _day(days), "source": "we_recorded"},
        )
    everyone = _op("custodian_list", supply_point_id=clinic.pk)
    assert [c["name"] for c in everyone] == ["Second Custodian", "First Custodian"]
    assert [c["current"] for c in everyone] == [True, False]
    assert [c["name"] for c in _op("custodian_list", supply_point_id=clinic.pk, current_only=True)] == [
        "Second Custodian"
    ]
    assert everyone[1]["ended_on"] == _day(5)


def test_a_custodian_cannot_start_before_the_one_it_replaces(clinic):
    _op(
        "custodian_assign",
        data={"supply_point_id": clinic.pk, "name": "First Custodian", "starts_on": _day(5), "source": "we_recorded"},
    )
    with pytest.raises(ValueError, match="cannot start before"):
        _op(
            "custodian_assign",
            data={
                "supply_point_id": clinic.pk,
                "name": "Second Custodian",
                "starts_on": _day(9),
                "source": "we_recorded",
            },
        )


def test_the_summary_counts_open_issues_per_partner(clinic):
    from connect_labs.labs.models import LabsOrg

    org = LabsOrg.objects.create(slug="a-partner", name="A Partner")
    clinic.managed_by_org = org
    clinic.save()
    worker = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="a-worker", name="A Worker", kind="facility", parent=clinic, source="we_recorded"
    )
    one = _report(clinic)
    _report(worker, quantity="1")
    _op("stock_issue_update", issue_id=one["id"], data={"status": "following_up", "source": "we_recorded"})
    done = _report(clinic, quantity="2")
    _op(
        "stock_issue_resolve",
        issue_id=done["id"],
        data={"outcome": "written_off", "resolved_on": _day(0), "source": "we_recorded"},
    )
    summary = _op("stock_issue_summary")
    assert (summary["total_open"], summary["open"], summary["following_up"]) == (2, 1, 1)
    assert summary["oldest_days"] == 2
    # The worker's holding belongs to the partner that runs the clinic above it.
    assert [(p["name"], p["total_open"]) for p in summary["by_partner"]] == [("A Partner", 2)]
    assert issues.open_issue_summary(PROGRAM)["total_open"] == 2


def test_an_earlier_free_text_loss_still_reads_as_a_loss(clinic):
    _op(
        "movement_record",
        data={
            "kind": "loss",
            "from_supply_point_id": clinic.pk,
            "occurred_on": _day(3),
            "commodity_slug": "a-product",
            "quantity": "5",
            "quantity_unit": "unit",
            "note": "dropped in the rain",
            "source": "we_recorded",
        },
    )
    loss = Movement.objects.get(note="dropped in the rain")
    assert loss.stock_issue_id is None and _held(clinic) == Decimal("95")


# ---- expiry warnings ---------------------------------------------------


def _receive(point, *, batch, days_to_expiry, quantity="20"):
    _op(
        "movement_record",
        data={
            "kind": "adjustment",
            "to_supply_point_id": point.pk,
            "occurred_on": _day(10),
            "commodity_slug": "a-product",
            "quantity": quantity,
            "quantity_unit": "unit",
            "batch": batch,
            "expiry": (date.today() + timedelta(days=days_to_expiry)).isoformat(),
            "source": "we_recorded",
        },
    )


def _expiring():
    return [c for c in _op("checks_list")["checks"] if c["kind"] == "stock_expiring"]


def test_a_batch_inside_the_warning_period_is_flagged_and_one_outside_is_not(clinic):
    _receive(clinic, batch="SOON", days_to_expiry=30)
    _receive(clinic, batch="LATER", days_to_expiry=400)
    found = _expiring()
    assert [c["facts"]["batch"] for c in found] == ["SOON"]
    assert found[0]["category"] == "threshold" and found[0]["facts"]["days_left"] == 30
    assert found[0]["facts"]["warning_days"] == 90


def test_an_expired_batch_is_flagged_as_expired(clinic):
    _receive(clinic, batch="OLD", days_to_expiry=-3)
    found = _expiring()
    assert found[0]["facts"]["expired"] is True and found[0]["facts"]["days_left"] == -3


def test_the_warning_period_is_the_programmes_to_set(clinic):
    _receive(clinic, batch="SOON", days_to_expiry=30)
    _op("commodity_upsert", data={"slug": "a-product", "name": "A Product", "expiry_warning_days": 14})
    assert _expiring() == []
    _op("commodity_upsert", data={"slug": "a-product", "name": "A Product", "expiry_warning_days": 45})
    assert len(_expiring()) == 1


def test_a_batch_that_has_all_gone_is_not_a_warning(clinic):
    _receive(clinic, batch="SOON", days_to_expiry=30, quantity="20")
    _report(clinic, batch="SOON", quantity="20", kind="expired")
    assert _expiring() == []
