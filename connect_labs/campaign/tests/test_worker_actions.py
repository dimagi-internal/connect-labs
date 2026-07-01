import pytest

from connect_labs.campaign.models import Campaign, Worker, Workspace
from connect_labs.campaign.services import worker_actions as wa


def _w(c, **kw):
    base = dict(
        campaign=c,
        worker_id="W1",
        first="A",
        last="B",
        name="A B",
        gender="F",
        region_id="kano",
        lga="Dala",
        role_id="vaccinator",
        rate=4500,
        days_worked=10,
        days_approved=0,
        amount=45000,
        kyc="approved",
        pay="pending",
        fraud_rules=[],
        linked=[],
        investigation=None,
        documents=[],
    )
    base.update(kw)
    return Worker.objects.create(**base)


@pytest.fixture
def campaign(db):
    ws = Workspace.objects.create(country="Nigeria", name="Nigeria", slug="nigeria")
    return Campaign.objects.create(workspace=ws, name="C", code="X", days_total=28)


def test_can_approve_pay(campaign):
    assert wa.can_approve_pay(_w(campaign, worker_id="A", fraud_rules=[], kyc="approved")) is True
    assert wa.can_approve_pay(_w(campaign, worker_id="B", fraud_rules=["dup"])) is False
    assert wa.can_approve_pay(_w(campaign, worker_id="C", kyc="rejected")) is False


def test_set_pay_blocks_flagged_on_approve(campaign):
    clean = _w(campaign, worker_id="W10", fraud_rules=[])  # noqa: F841
    flagged = _w(campaign, worker_id="W11", fraud_rules=["Duplicate NIN"])  # noqa: F841
    qs = Worker.objects.filter(worker_id__in=["W10", "W11"])
    updated, blocked = wa.set_pay(qs, "approved")
    assert [w.worker_id for w in updated] == ["W10"]
    assert blocked == ["W11"]
    assert Worker.objects.get(worker_id="W10").pay == "approved"
    assert Worker.objects.get(worker_id="W10").days_approved == 10  # full approve
    assert Worker.objects.get(worker_id="W11").pay == "pending"  # untouched


def test_set_pay_reject_allowed_for_flagged(campaign):
    flagged = _w(campaign, worker_id="W12", fraud_rules=["x"], pay="pending")  # noqa: F841
    updated, blocked = wa.set_pay(Worker.objects.filter(worker_id="W12"), "rejected")
    assert blocked == [] and updated[0].pay == "rejected"


def test_queue_pay_guard_and_clamp(campaign):
    w = _w(campaign, worker_id="W13", days_worked=10, fraud_rules=[])
    out = wa.queue_pay(w, 7)
    assert out.pay == "approved" and out.days_approved == 7
    out2 = wa.queue_pay(_w(campaign, worker_id="W14", days_worked=5), 99)
    assert out2.days_approved == 5  # clamped
    with pytest.raises(wa.FraudGuardError):
        wa.queue_pay(_w(campaign, worker_id="W15", fraud_rules=["x"]), 3)


def test_set_kyc_guard(campaign):
    assert wa.set_kyc(_w(campaign, worker_id="W16", fraud_rules=[]), "approved").kyc == "approved"
    assert wa.set_kyc(_w(campaign, worker_id="W17"), "review").kyc == "review"
    with pytest.raises(wa.FraudGuardError):
        wa.set_kyc(_w(campaign, worker_id="W18", fraud_rules=["x"]), "approved")


def test_resolve_duplicate(campaign):
    keep = wa.resolve_duplicate(
        _w(campaign, worker_id="W19", duplicate=True, fraud_rules=["x"], linked=[{"id": "Z"}], kyc="pending"),
        keep=True,
    )
    assert keep.duplicate is False and keep.fraud_rules == [] and keep.linked == [] and keep.kyc == "pending"
    arch = wa.resolve_duplicate(_w(campaign, worker_id="W20", duplicate=True, fraud_rules=["x"]), keep=False)
    assert arch.duplicate is False and arch.fraud_rules == [] and arch.kyc == "rejected"


def test_save_investigation_stamps_note(campaign):
    w = _w(campaign, worker_id="W21", investigation=None)
    out = wa.save_investigation(w, status="Under Review", outcome=None, note="looks dup", by_name="Amara")
    assert out.investigation["status"] == "Under Review"
    assert out.investigation["notes"][0]["by"] == "Amara"
    assert out.investigation["notes"][0]["text"] == "looks dup"
    assert out.investigation["notes"][0]["at"]  # server-stamped, non-empty
    # second note prepends
    out2 = wa.save_investigation(out, status="Resolved", outcome="false positive", note="cleared", by_name="Ngozi")
    assert out2.investigation["status"] == "Resolved" and out2.investigation["outcome"] == "false positive"
    assert out2.investigation["notes"][0]["text"] == "cleared" and len(out2.investigation["notes"]) == 2
