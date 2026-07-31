import json
from datetime import date, timedelta

import pytest

from connect_labs.supply.models import EOIRound, EOISubmission, Qualification
from connect_labs.supply.services.eoi_actions import add_months, submit_submission

from . import factories as f

pytestmark = pytest.mark.django_db

TODAY = date.today()


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def test_submit_freezes_profile_snapshot(supplier_client):
    client, member = supplier_client
    member.org.description = "Original description"
    member.org.save()
    f.CertificationFactory(org=member.org, cert_type="ISO 22000")
    rnd = f.EOIRoundFactory(categories=["rutf"], status=EOIRound.Status.OPEN)

    resp = _post(
        client,
        "/supply/api/eoi/submissions/",
        {
            "round_id": rnd.id,
            "categories": ["rutf"],
            "commitments": {"rutf": {"capacity": "20,000 cartons/mo", "regions": ["NG"], "lead_time_days": 21}},
        },
    )
    assert resp.status_code == 200
    sub_id = resp.json()["submission"]["id"]

    assert _post(client, f"/supply/api/eoi/submissions/{sub_id}/submit/", {}).status_code == 200

    # Mutating the live profile must not change what reviewers see.
    member.org.description = "Rewritten after submission"
    member.org.save()

    snapshot = EOISubmission.objects.get(id=sub_id).profile_snapshot
    assert snapshot["description"] == "Original description"
    assert [c["cert_type"] for c in snapshot["certifications"]] == ["ISO 22000"]


def test_cannot_submit_to_closed_round(supplier_client):
    client, member = supplier_client
    rnd = f.EOIRoundFactory(status=EOIRound.Status.OPEN)
    sub = f.EOISubmissionFactory(org=member.org, round=rnd)
    rnd.status = EOIRound.Status.CLOSED
    rnd.save()

    resp = _post(client, f"/supply/api/eoi/submissions/{sub.id}/submit/", {})
    assert resp.status_code == 400
    sub.refresh_from_db()
    assert sub.status == EOISubmission.Status.DRAFT


def test_cannot_submit_twice(supplier_client):
    client, member = supplier_client
    rnd = f.EOIRoundFactory(status=EOIRound.Status.OPEN)
    sub = f.EOISubmissionFactory(org=member.org, round=rnd)
    assert _post(client, f"/supply/api/eoi/submissions/{sub.id}/submit/", {}).status_code == 200
    assert _post(client, f"/supply/api/eoi/submissions/{sub.id}/submit/", {}).status_code == 400


def test_supplier_sees_only_own_submissions(supplier_client):
    client, member = supplier_client
    other = f.SupplierOrgFactory(legal_name="Rival Foods")
    rnd = f.EOIRoundFactory()
    mine = f.EOISubmissionFactory(org=member.org, round=rnd)
    theirs = f.EOISubmissionFactory(org=other, round=rnd)

    ids = [s["id"] for s in client.get("/supply/api/eoi/submissions/").json()["submissions"]]
    assert mine.id in ids
    assert theirs.id not in ids

    # and cannot act on another org's submission
    assert _post(client, f"/supply/api/eoi/submissions/{theirs.id}/submit/", {}).status_code == 404


def test_review_qualify_creates_qualifications(admin_client):
    client, _user = admin_client
    org = f.SupplierOrgFactory()
    rnd = f.EOIRoundFactory(categories=["rutf", "transport"])
    sub = f.EOISubmissionFactory(
        org=org, round=rnd, categories=["rutf", "transport"], status=EOISubmission.Status.SUBMITTED
    )

    resp = _post(
        client,
        f"/supply/api/eoi/submissions/{sub.id}/review/",
        {"decisions": {"rutf": "qualify", "transport": "qualify"}, "notes": "Strong capacity"},
    )
    assert resp.status_code == 200

    sub.refresh_from_db()
    assert sub.status == EOISubmission.Status.QUALIFIED
    quals = Qualification.objects.filter(org=org).order_by("category")
    assert [q.category for q in quals] == ["rutf", "transport"]
    assert all(q.status == Qualification.Status.ACTIVE for q in quals)
    # 18 CALENDAR months, which is what the rule says on screen. The term was
    # 540 days, so the stated rule and the computed date disagreed by up to
    # nine days on the figure that decides whether a supplier is in the
    # registry.
    assert all(q.expires_at == add_months(q.granted_at, 18) for q in quals)
    assert all(q.source_submission_id == sub.id for q in quals)


def test_qualification_term_is_calendar_months_not_540_days():
    """31 August + 18 months is 28/29 February, not 3 March."""
    assert add_months(date(2026, 7, 22), 18) == date(2028, 1, 22)
    # Clamped to the shorter target month rather than rolling into the next one.
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert add_months(date(2024, 8, 31), 6) == date(2025, 2, 28)
    # A December start crosses the year boundary in the month-length lookup.
    assert add_months(date(2026, 6, 30), 6) == date(2026, 12, 30)


def test_qualification_flags_reverification_when_it_outlives_its_certificate(admin_client):
    """A pass granted off a certificate that lapses first says so.

    Without this the registry answered "qualified" for the months between the
    certificate expiring and the pass expiring — off evidence that had lapsed,
    with nothing on screen to show it.
    """
    client, _user = admin_client
    org = f.SupplierOrgFactory()
    # Expires well inside an 18-month term.
    cert_expiry = date.today() + timedelta(days=200)
    f.CertificationFactory(org=org, cert_type="UNICEF RUTF approval", expiry_date=cert_expiry)
    rnd = f.EOIRoundFactory(categories=["rutf"])
    sub = f.EOISubmissionFactory(org=org, round=rnd, categories=["rutf"], status=EOISubmission.Status.DRAFT)
    # Freeze the profile the way submission does, so the snapshot carries the cert.
    submit_submission(sub)

    resp = _post(
        client,
        f"/supply/api/eoi/submissions/{sub.id}/review/",
        {"decisions": {"rutf": "qualify"}},
    )
    assert resp.status_code == 200

    qual = Qualification.objects.get(org=org, category="rutf")
    assert qual.expires_at == add_months(qual.granted_at, 18)
    assert qual.verify_at == cert_expiry


def test_qualification_carries_no_verify_date_when_certificates_outlast_it(admin_client):
    client, _user = admin_client
    org = f.SupplierOrgFactory()
    f.CertificationFactory(org=org, cert_type="ISO 22000", expiry_date=date.today() + timedelta(days=2000))
    rnd = f.EOIRoundFactory(categories=["rutf"])
    sub = f.EOISubmissionFactory(org=org, round=rnd, categories=["rutf"], status=EOISubmission.Status.DRAFT)
    submit_submission(sub)
    _post(client, f"/supply/api/eoi/submissions/{sub.id}/review/", {"decisions": {"rutf": "qualify"}})
    assert Qualification.objects.get(org=org, category="rutf").verify_at is None


def test_review_partial_qualify(admin_client):
    client, _user = admin_client
    org = f.SupplierOrgFactory()
    sub = f.EOISubmissionFactory(org=org, categories=["rutf", "transport"], status=EOISubmission.Status.SUBMITTED)
    _post(
        client,
        f"/supply/api/eoi/submissions/{sub.id}/review/",
        {"decisions": {"rutf": "qualify", "transport": "reject"}},
    )
    sub.refresh_from_db()
    assert sub.status == EOISubmission.Status.QUALIFIED
    assert [q.category for q in Qualification.objects.filter(org=org)] == ["rutf"]


def test_review_all_rejected(admin_client):
    client, _user = admin_client
    org = f.SupplierOrgFactory()
    sub = f.EOISubmissionFactory(org=org, categories=["rutf"], status=EOISubmission.Status.SUBMITTED)
    _post(client, f"/supply/api/eoi/submissions/{sub.id}/review/", {"decisions": {"rutf": "reject"}})
    sub.refresh_from_db()
    assert sub.status == EOISubmission.Status.REJECTED
    assert Qualification.objects.filter(org=org).count() == 0


def test_cannot_review_a_draft_submission(admin_client):
    client, _user = admin_client
    sub = f.EOISubmissionFactory(categories=["rutf"], status=EOISubmission.Status.DRAFT)
    resp = _post(client, f"/supply/api/eoi/submissions/{sub.id}/review/", {"decisions": {"rutf": "qualify"}})
    assert resp.status_code == 400


def test_review_rejects_categories_outside_submission(admin_client):
    client, _user = admin_client
    sub = f.EOISubmissionFactory(categories=["rutf"], status=EOISubmission.Status.SUBMITTED)
    resp = _post(
        client,
        f"/supply/api/eoi/submissions/{sub.id}/review/",
        {"decisions": {"warehousing": "qualify"}},
    )
    assert resp.status_code == 400


def test_reviewer_cannot_manage_rounds(reviewer_client):
    client, _user = reviewer_client
    resp = _post(client, "/supply/api/eoi/rounds/", {"title": "Sneaky round", "categories": ["rutf"]})
    assert resp.status_code == 403


def test_supplier_cannot_reach_review_queue(supplier_client):
    client, _member = supplier_client
    assert client.get("/supply/api/eoi/review-queue/").status_code == 403


def test_admin_creates_and_transitions_round(admin_client):
    client, _user = admin_client
    resp = _post(client, "/supply/api/eoi/rounds/", {"title": "OES Supply Base 2026-B", "categories": ["rutf"]})
    assert resp.status_code == 200
    rid = resp.json()["round"]["id"]
    assert EOIRound.objects.get(id=rid).status == EOIRound.Status.DRAFT

    assert _post(client, f"/supply/api/eoi/rounds/{rid}/transition/", {"status": "open"}).status_code == 200
    assert EOIRound.objects.get(id=rid).status == EOIRound.Status.OPEN
    assert _post(client, f"/supply/api/eoi/rounds/{rid}/transition/", {"status": "draft"}).status_code == 400


def test_round_created_with_dates_serializes_back(admin_client):
    """The browser sends opens_at/closes_at as ISO strings. Passing them raw
    into objects.create() left str on the in-memory instance and the response
    serializer's .isoformat() 500ed — on the very request that created the
    row, so the round landed in the DB while the caller saw a failure."""
    client, _user = admin_client
    resp = _post(
        client,
        "/supply/api/eoi/rounds/",
        {"title": "Dated round", "categories": ["rutf"], "opens_at": None, "closes_at": "2026-08-18"},
    )
    assert resp.status_code == 200
    assert resp.json()["round"]["closes_at"] == "2026-08-18"


def test_round_rejects_malformed_date(admin_client):
    client, _user = admin_client
    resp = _post(
        client,
        "/supply/api/eoi/rounds/",
        {"title": "Bad date", "categories": ["rutf"], "closes_at": "18/08/2026"},
    )
    assert resp.status_code == 400


def test_supplier_round_list_shows_open_only(supplier_client):
    client, _member = supplier_client
    open_round = f.EOIRoundFactory(status=EOIRound.Status.OPEN)
    f.EOIRoundFactory(status=EOIRound.Status.DRAFT)
    f.EOIRoundFactory(status=EOIRound.Status.CLOSED)
    ids = [r["id"] for r in client.get("/supply/api/eoi/rounds/").json()["rounds"]]
    assert ids == [open_round.id]


def test_renewed_certification_wins_the_live_column(supplier_client):
    """Two certs of one type (a renewal beside the original) must serialize in
    insertion order, so the frozen-vs-live panel's per-type last-wins dedupe
    deterministically shows the renewal. cert_type-only ordering left the tie
    in undefined database order — the live column showed the original or the
    renewal by coin flip, and the walkthrough's divergence beat flickered."""
    client, member = supplier_client
    f.CertificationFactory(org=member.org, cert_type="UNICEF RUTF approval", issuer="UNICEF Supply Division")
    f.CertificationFactory(org=member.org, cert_type="UNICEF RUTF approval", issuer="UNICEF Supply Division (renewed)")
    body = client.get("/supply/api/bootstrap/").json()
    same_type = [c for c in body["org"]["certifications"] if c["cert_type"] == "UNICEF RUTF approval"]
    assert [c["issuer"] for c in same_type] == ["UNICEF Supply Division", "UNICEF Supply Division (renewed)"]


def test_registry_filters(admin_client):
    client, _user = admin_client
    ng = f.SupplierOrgFactory(legal_name="Savanna Nutrients", country="NG")
    et = f.SupplierOrgFactory(legal_name="Rift Valley Therapeutics", country="ET")
    today = date.today()
    f.QualificationFactory(org=ng, category="rutf", granted_at=today, expires_at=today + timedelta(days=400))
    f.QualificationFactory(org=ng, category="transport", granted_at=today, expires_at=today + timedelta(days=20))
    f.QualificationFactory(org=et, category="rutf", granted_at=today, expires_at=today + timedelta(days=400))
    # expired qualification must never appear
    f.QualificationFactory(
        org=et, category="warehousing", granted_at=today - timedelta(days=600), expires_at=today - timedelta(days=1)
    )

    all_rows = client.get("/supply/api/registry/").json()["registry"]
    cats = {r["org"]["legal_name"]: sorted(q["category"] for q in r["qualifications"]) for r in all_rows}
    assert cats["Savanna Nutrients"] == ["rutf", "transport"]
    assert cats["Rift Valley Therapeutics"] == ["rutf"]

    ng_only = client.get("/supply/api/registry/?country=NG").json()["registry"]
    assert [r["org"]["legal_name"] for r in ng_only] == ["Savanna Nutrients"]

    rutf_only = client.get("/supply/api/registry/?category=rutf").json()["registry"]
    assert len(rutf_only) == 2
    assert all(q["category"] == "rutf" for r in rutf_only for q in r["qualifications"])

    expiring = client.get("/supply/api/registry/?expiring_within_days=30").json()["registry"]
    assert [r["org"]["legal_name"] for r in expiring] == ["Savanna Nutrients"]
    assert [q["category"] for q in expiring[0]["qualifications"]] == ["transport"]


def test_registry_qualification_names_who_granted_it_and_from_which_application(admin_client):
    """The registry answers "can this supplier be issued a solicitation today";
    the next question is always "who decided that, and against what". Granted and
    expires alone made the judgment visible but not defensible."""
    client, _user = admin_client
    reviewer = f.UserFactory(name="Tomas Berhane")
    org = f.SupplierOrgFactory(legal_name="Evidence Foods Ltd")
    rnd = f.EOIRoundFactory(title="OES Supply Base 2026-A", status=EOIRound.Status.CLOSED)
    sub = f.EOISubmissionFactory(org=org, round=rnd, status=EOISubmission.Status.QUALIFIED)
    f.EOIReviewFactory(submission=sub, reviewer=reviewer, decisions={"rutf": "qualify"})
    f.QualificationFactory(
        org=org,
        category="rutf",
        source_submission=sub,
        granted_at=TODAY,
        expires_at=TODAY + timedelta(days=400),
    )

    registry = client.get("/supply/api/registry/").json()["registry"]
    row = next(r for r in registry if r["org"]["legal_name"] == "Evidence Foods Ltd")
    qual = row["qualifications"][0]

    assert qual["granted_by"] == "Tomas Berhane"
    assert qual["source_round"] == "OES Supply Base 2026-A"
    assert qual["source_submission_id"] == sub.id


def test_a_qualification_with_no_recorded_reviewer_reports_the_gap(admin_client):
    """None rather than a blank: "unknown decision-maker" is itself the finding an
    auditor would write up, so the API must not hide it."""
    client, _user = admin_client
    org = f.SupplierOrgFactory(legal_name="Unattributed Foods Ltd")
    f.QualificationFactory(
        org=org, category="rutf", source_submission=None, granted_at=TODAY, expires_at=TODAY + timedelta(days=400)
    )

    registry = client.get("/supply/api/registry/").json()["registry"]
    row = next(r for r in registry if r["org"]["legal_name"] == "Unattributed Foods Ltd")

    assert row["qualifications"][0]["granted_by"] is None
    assert row["qualifications"][0]["source_round"] is None


def test_the_seeded_registry_records_a_reviewer_for_every_qualification():
    """One seeder path created its reviews with reviewer=None, so most of the demo
    roster would have rendered "not recorded" — which reads as the product failing
    to capture the decision-maker rather than as the seeder being lazy."""
    from django.core.management import call_command

    from connect_labs.supply.models import Qualification
    from connect_labs.supply.serializers import qualification_dict

    call_command("seed_supply_demo", "--reset")
    missing = [
        q.org.legal_name
        for q in Qualification.objects.select_related("org", "source_submission")
        if qualification_dict(q)["granted_by"] is None
    ]
    assert missing == [], f"qualifications with no recorded reviewer: {missing}"


def test_a_closed_round_reports_what_it_decided():
    """A closed round rendered a bare em-dash with 14 applications behind it.

    The count alone says a round happened; the breakdown says what it decided,
    which is what makes the row something other than a dead end for every
    decision it holds.
    """
    from django.core.management import call_command

    from connect_labs.supply.serializers import round_dict

    call_command("seed_supply_demo", "--reset")
    closed = EOIRound.objects.filter(status=EOIRound.Status.CLOSED).first()
    assert closed is not None, "the seeded world needs a closed round"

    row = round_dict(closed)
    breakdown = row["submission_breakdown"]
    assert set(breakdown) == {"submitted", "qualified", "rejected"}
    # Every application is accounted for, or the breakdown is a second,
    # disagreeing count of the same thing.
    assert sum(breakdown.values()) == row["submission_count"]
    assert breakdown["qualified"] + breakdown["rejected"] > 0


def test_the_review_payload_reaches_decided_applications_not_only_pending_ones():
    """The rounds table counted 8 applications beside a queue showing 4.

    `review_queue` is deliberately a worklist and holds only what awaits a
    decision — true, unstated, and indistinguishable from an inconsistency. The
    surface needs the decided ones too before it can explain the gap.
    """
    from django.core.management import call_command

    from connect_labs.supply.api.bootstrap import _staff_world
    from connect_labs.supply.decorators import Actor
    from connect_labs.supply.models import StaffRole

    call_command("seed_supply_demo", "--reset")
    # Whichever staff role is granted eoi_review — asked of the permission
    # matrix rather than hardcoded, so the test does not pin a role name.
    from connect_labs.supply.rbac import ROLE_PERMS

    reviewer_roles = [r for r, perms in ROLE_PERMS.items() if "eoi_review" in perms]
    staff = StaffRole.objects.filter(role__in=reviewer_roles).select_related("user").first()
    assert staff is not None, f"the seeded world needs one of {reviewer_roles}"

    world = _staff_world(Actor(staff.user, staff.role, None))
    assert len(world["eoi_submissions"]) == EOISubmission.objects.count()
    assert len(world["review_queue"]) < len(
        world["eoi_submissions"]
    ), "the queue must be a strict subset, or there is no gap to explain"
    assert any(s["status"] != "submitted" for s in world["eoi_submissions"])
