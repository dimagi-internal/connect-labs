import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from connect_labs.supply.models import (
    RFP,
    Award,
    Bid,
    EOIRound,
    EOISubmission,
    Qualification,
    StaffRole,
    SupplierMember,
    SupplierOrg,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


def _snapshot():
    return {
        "orgs": SupplierOrg.objects.count(),
        "quals": sorted(Qualification.objects.values_list("org__legal_name", "category", "expires_at")),
        "subs": sorted(EOISubmission.objects.values_list("org__legal_name", "round__title", "status")),
        "bids": Bid.objects.count(),
        "awards": Award.objects.count(),
    }


def test_seed_is_idempotent_and_deterministic():
    call_command("seed_supply_demo")
    first = _snapshot()
    call_command("seed_supply_demo")
    assert _snapshot() == first


def test_seed_personas_and_roles():
    call_command("seed_supply_demo")
    roles = dict(StaffRole.objects.values_list("user__username", "role"))
    assert roles["oes-lead@oes.example"] == "procurement_admin"
    assert roles["oes-review@oes.example"] == "reviewer"
    assert roles["gov-ng@oes.example"] == "gov_observer"
    assert roles["usg@oes.example"] == "funder"
    assert StaffRole.objects.get(user__username="gov-ng@oes.example").country == "NG"

    member = SupplierMember.objects.get(user__username="supplier@savanna.example")
    assert member.org.legal_name == "Savanna Nutrients Ltd"
    assert member.user.check_password("oes-demo-2026")


def test_seed_demo_logins_can_sign_in(client):
    call_command("seed_supply_demo")
    resp = client.post("/supply/login/", {"email": "oes-lead@oes.example", "password": "oes-demo-2026"})
    assert resp.status_code == 302 and resp.url == "/supply/"
    assert client.get("/supply/api/bootstrap/").json()["role"] == "procurement_admin"


def test_seed_world_shape():
    call_command("seed_supply_demo")
    assert SupplierOrg.objects.count() == 16
    # no RUTF manufacturer in Sudan — it is supplied through Port Sudan
    sudan = SupplierOrg.objects.filter(country="SD")
    assert sudan.exists()
    assert not Qualification.objects.filter(org__country="SD", category="rutf").exists()

    assert EOIRound.objects.filter(status=EOIRound.Status.CLOSED).count() == 1
    assert EOIRound.objects.filter(status=EOIRound.Status.OPEN).count() == 1

    open_round = EOIRound.objects.get(status=EOIRound.Status.OPEN)
    statuses = set(open_round.submissions.values_list("status", flat=True))
    assert statuses == {"draft", "submitted", "qualified", "rejected"}

    # a live solicitation mid-flight, and one fully awarded
    live = RFP.objects.get(title="RUTF Northeast Nigeria Q3 2026")
    assert live.status == RFP.Status.PUBLISHED
    assert live.lots.count() == 3
    assert all(lot.lot_bids.count() >= 3 for lot in live.lots.all())

    awarded = RFP.objects.get(title="RUTF Ethiopia Q2 2026")
    assert awarded.status == RFP.Status.AWARDED
    assert Award.objects.filter(lot__rfp=awarded).count() == 1


def test_seeded_registry_has_expiring_certifications():
    call_command("seed_supply_demo")
    from datetime import date, timedelta

    from connect_labs.supply.models import Certification

    soon = date.today() + timedelta(days=60)
    assert Certification.objects.filter(expiry_date__lte=soon).exists()


def test_seeded_supplier_sees_eligible_solicitations(client):
    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "supplier@savanna.example", "password": "oes-demo-2026"})
    body = client.get("/supply/api/bootstrap/").json()
    assert body["role"] == "supplier"
    assert body["org"]["legal_name"] == "Savanna Nutrients Ltd"
    assert any(q["category"] == "rutf" for q in body["org"]["qualifications"])
    assert any(r["title"] == "RUTF Northeast Nigeria Q3 2026" for r in body["eligible_rfps"])
