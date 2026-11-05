"""The Issues page and its forms.

Placeholder names and figures only (public repo).
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.models import Commodity, Custodian, Movement, StockIssue, SupplyPoint

pytestmark = pytest.mark.django_db

PROGRAM = 10507
SCOPE = f"prog:{PROGRAM}"


@pytest.fixture
def user(client, django_user_model):
    account = django_user_model.objects.create_user(username="katherine", password="x", email="k@dimagi.com")
    client.force_login(account)
    return account


@pytest.fixture
def scoped(client, user, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.stock import issue_views  # noqa: F401

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "stock.issue_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def world():
    rutf = Commodity.objects.create(scope_key=SCOPE, slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton")
    partner = LabsOrg.objects.create(slug="a-partner", name="A Partner")
    other = LabsOrg.objects.create(slug="another-partner", name="Another Partner")
    mine = SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="mine",
        name="Mine Store",
        kind="facility",
        managed_by_org=partner,
        source="we_recorded",
    )
    theirs = SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="theirs",
        name="Their Store",
        kind="facility",
        managed_by_org=other,
        source="we_recorded",
    )
    for point in (mine, theirs):
        Movement.objects.create(
            program_id=PROGRAM,
            kind="adjustment",
            occurred_on=date(2026, 1, 1),
            to_supply_point=point,
            commodity=rutf,
            quantity=Decimal("50"),
            quantity_unit="carton",
            source="we_recorded",
        )
    return rutf, partner, other, mine, theirs


def report(point, rutf, **over):
    data = {
        "supply_point": point.pk,
        "kind": "missing",
        "taken_by": "",
        "occurred_on": "2026-02-01",
        "commodity": rutf.pk,
        "item": "",
        "batch": "B1",
        "quantity": "3",
        "quantity_unit": "carton",
        "reported_by": "A Reporter",
        "evidence": "a WhatsApp message",
        "source": "partner_reported",
    }
    data.update(over)
    return data


def test_the_nav_has_an_issues_tab():
    from connect_labs.supply_chain.navigation import SUPPLY_TABS

    assert ("supply_chain:issues", "Issues") in SUPPLY_TABS


def test_reporting_through_the_form_takes_the_stock_off_and_lists_the_issue(scoped, world):
    rutf, _partner, _other, mine, _theirs = world
    response = scoped.post(reverse("supply_chain:issue_report"), report(mine, rutf))
    assert response.status_code == 302, getattr(response, "context", None) and response.context["form"].errors
    issue = StockIssue.objects.get()
    assert (issue.status, issue.supply_point_id, issue.quantity) == ("open", mine.pk, Decimal("3"))
    assert Movement.objects.filter(stock_issue=issue, kind="loss").count() == 1
    page = scoped.get(reverse("supply_chain:issues"))
    assert page.status_code == 200
    html = page.content.decode()
    assert "Mine Store" in html and "a WhatsApp message" in html and "1" in html


def test_a_taken_by_name_is_dropped_unless_somebody_took_it(scoped, world):
    rutf, _partner, _other, mine, _theirs = world
    scoped.post(reverse("supply_chain:issue_report"), report(mine, rutf, kind="missing", taken_by="A Neighbour"))
    assert StockIssue.objects.get().taken_by == ""


def test_the_page_rolls_up_across_partners_and_narrows_to_one(scoped, world):
    rutf, partner, other, mine, theirs = world
    scoped.post(reverse("supply_chain:issue_report"), report(mine, rutf))
    scoped.post(reverse("supply_chain:issue_report"), report(theirs, rutf, quantity="1"))
    everyone = scoped.get(reverse("supply_chain:issues")).content.decode()
    assert "Mine Store" in everyone and "Their Store" in everyone
    own = scoped.get(reverse("supply_chain:issues"), {"partner": partner.pk}).content.decode()
    assert "A Partner" in own
    assert "Their Store" not in own.split("Who is responsible")[0]


def test_a_partners_report_form_offers_only_its_own_stores(scoped, world):
    _rutf, partner, _other, _mine, _theirs = world
    html = scoped.get(reverse("supply_chain:issue_report"), {"partner": partner.pk}).content.decode()
    assert "Mine Store" in html and "Their Store" not in html


def test_following_up_then_closing_through_the_screens(scoped, world):
    rutf, _partner, _other, mine, _theirs = world
    scoped.post(reverse("supply_chain:issue_report"), report(mine, rutf, kind="taken", taken_by="A Neighbour"))
    issue = StockIssue.objects.get()
    r = scoped.post(
        reverse("supply_chain:issue_follow_up", args=[issue.pk]),
        {"status": "following_up", "taken_by": "A Neighbour", "evidence": "x", "note": "", "source": "we_recorded"},
    )
    assert r.status_code == 302
    issue.refresh_from_db()
    assert issue.status == "following_up"
    r = scoped.post(
        reverse("supply_chain:issue_resolve", args=[issue.pk]),
        {
            "outcome": "recovered",
            "resolved_on": "2026-02-10",
            "quantity_recovered": "",
            "note": "",
            "source": "we_recorded",
        },
    )
    assert r.status_code == 302
    issue.refresh_from_db()
    assert (issue.status, issue.outcome, issue.quantity_recovered) == ("resolved", "recovered", Decimal("3"))
    assert Movement.objects.filter(stock_issue=issue, kind="return").count() == 1


def test_naming_a_custodian_through_the_form_keeps_history(scoped, world):
    _rutf, _partner, _other, mine, _theirs = world
    post = {
        "supply_point": mine.pk,
        "name": "First Custodian",
        "phone": "000",
        "role": "",
        "starts_on": "2026-01-01",
        "security_measures": ["locked_room", "separate_shelf"],
        "note": "key held by the matron",
        "source": "we_recorded",
    }
    assert scoped.post(reverse("supply_chain:custodian_assign"), post).status_code == 302
    assert (
        scoped.post(
            reverse("supply_chain:custodian_assign"), {**post, "name": "Second", "starts_on": "2026-03-01"}
        ).status_code
        == 302
    )
    first, second = Custodian.objects.order_by("starts_on")
    assert (first.ended_on, second.ended_on) == (date(2026, 3, 1), None)
    assert first.security_measures == ["locked_room", "separate_shelf"]
    html = scoped.get(reverse("supply_chain:issues")).content.decode()
    assert "Second" in html and "First Custodian" not in html


def test_an_earlier_loss_with_a_note_is_still_shown(scoped, world):
    rutf, _partner, _other, mine, _theirs = world
    Movement.objects.create(
        program_id=PROGRAM,
        kind="loss",
        occurred_on=date(2026, 1, 5),
        from_supply_point=mine,
        commodity=rutf,
        quantity=Decimal("2"),
        quantity_unit="carton",
        note="dropped in the rain",
        source="we_recorded",
    )
    html = scoped.get(reverse("supply_chain:issues")).content.decode()
    assert "dropped in the rain" in html and "Earlier losses" in html


def _photo(name="shelf.jpg"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, b"\xff\xd8\xff not really a photo", content_type="image/jpeg")


def test_a_photo_given_with_the_report_is_kept_as_the_issues_evidence(scoped, world):
    from connect_labs.supply_chain.models import Document

    rutf, _partner, _other, mine, _theirs = world
    response = scoped.post(reverse("supply_chain:issue_report"), {**report(mine, rutf), "upload": _photo()})

    assert response.status_code == 302
    issue = StockIssue.objects.get()
    document = Document.objects.get()
    assert (document.stock_issue_id, document.kind, document.filename) == (issue.pk, "photo", "shelf.jpg")
    assert document.sha256 and document.is_stored
    html = scoped.get(reverse("supply_chain:issues")).content.decode()
    assert "Evidence for stock issue" in html and "Add a photo or message" in html


def test_evidence_can_be_added_to_an_issue_later_by_file_or_link(scoped, world):
    from connect_labs.supply_chain.models import Document

    rutf, _partner, _other, mine, _theirs = world
    scoped.post(reverse("supply_chain:issue_report"), report(mine, rutf))
    issue = StockIssue.objects.get()
    url = reverse("supply_chain:issue_evidence", args=[issue.pk])

    assert scoped.get(url).status_code == 200
    scoped.post(
        url,
        {"kind": "photo", "title": "Message", "external_url": "", "upload": _photo("msg.png"), "source": "document"},
    )
    scoped.post(
        url, {"kind": "other", "title": "Chat", "external_url": "https://example.test/chat", "source": "document"}
    )

    assert set(Document.objects.filter(stock_issue=issue).values_list("filename", "external_url")) == {
        ("msg.png", ""),
        ("", "https://example.test/chat"),
    }


def test_evidence_for_another_programmes_issue_is_not_found(scoped, world):
    assert scoped.get(reverse("supply_chain:issue_evidence", args=[999999])).status_code == 404


def test_expiring_soon_is_a_warning_on_the_issues_page_and_takes_nothing_off_stock(scoped, world):
    rutf, _partner, _other, mine, _theirs = world
    response = scoped.post(reverse("supply_chain:issue_report"), report(mine, rutf, kind="expiring", quantity="40"))
    assert response.status_code == 302, getattr(response, "context", None) and response.context["form"].errors
    issue = StockIssue.objects.get()
    assert issue.kind == "expiring" and not Movement.objects.filter(stock_issue=issue).exists()
    page = scoped.get(reverse("supply_chain:issues"))
    assert page.status_code == 200
    html = page.content.decode()
    assert "Expiring soon" in html and "A warning, not a loss" in html


# ---- the follow-up log and field workers who keep stock at home ---------------------------


def test_a_follow_up_log_line_names_who_what_and_when_and_moves_the_issue_on(scoped, world):
    rutf, _partner, _other, mine, _theirs = world
    scoped.post(reverse("supply_chain:issue_report"), report(mine, rutf))
    issue = StockIssue.objects.get()
    url = reverse("supply_chain:issue_log", args=[issue.pk])
    assert scoped.get(url).status_code == 200
    done = scoped.post(
        url, {"by": "A Supervisor", "followed_on": "2026-11-03", "note": "called the clinic; 8 cartons to be returned"}
    )
    assert done.status_code == 302
    issue.refresh_from_db()
    assert issue.status == "following_up"
    [line] = issue.follow_ups.all()
    assert (line.by, str(line.followed_on)) == ("A Supervisor", "2026-11-03")
    html = scoped.get(reverse("supply_chain:issues")).content.decode()
    assert 'data-testid="issue-log"' in html and "3 Nov 2026" in html and "8 cartons to be returned" in html
    assert "Log a follow-up" in html


def test_a_closed_issue_takes_no_more_log_lines(scoped, world):
    rutf, _partner, _other, mine, _theirs = world
    scoped.post(reverse("supply_chain:issue_report"), report(mine, rutf))
    issue = StockIssue.objects.get()
    StockIssue.objects.filter(pk=issue.pk).update(status="resolved", outcome="written_off")
    response = scoped.post(
        reverse("supply_chain:issue_log", args=[issue.pk]), {"by": "A", "followed_on": "2026-11-03", "note": "x"}
    )
    assert response.status_code == 200 and not issue.follow_ups.exists()


def test_the_log_is_on_the_operation_so_the_api_and_mcp_read_it_too(scoped, world):
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.operations import call_operation

    rutf, _partner, _other, mine, _theirs = world
    scoped.post(reverse("supply_chain:issue_report"), report(mine, rutf))
    issue = StockIssue.objects.get()
    access = SupplyDataAccess(access_token="placeholder", program_id=PROGRAM, caller=SYSTEM)
    call_operation(
        "stock_issue_follow_up_add",
        access,
        {"issue_id": issue.pk, "data": {"by": "A Person", "note": "asked for a photo", "followed_on": "2026-11-04"}},
    )
    [listed] = [i for i in call_operation("stock_issue_list", access, {}) if i["id"] == issue.pk]
    assert listed["follow_ups"] == [
        {
            "id": listed["follow_ups"][0]["id"],
            "followed_on": "2026-11-04",
            "by": "A Person",
            "note": "asked for a photo",
        }
    ]


def test_a_field_worker_who_keeps_stock_at_home_can_be_the_custodian(scoped, world):
    from connect_labs.supply_chain.models import PartnerPlan

    _rutf, partner, _other, mine, _theirs = world
    worker = SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="flw-home",
        name="flw_home",
        kind="user_held",
        parent=mine,
        connect_username="flw_home",
        source="we_recorded",
    )
    PartnerPlan.objects.create(
        program_id=PROGRAM,
        org=partner,
        distribution_model="direct_to_workers",
        starts_on=date(2026, 1, 1),
        source="we_recorded",
    )
    html = scoped.get(reverse("supply_chain:issues")).content.decode()
    assert "flw_home" in html and "keeps stock at home" in html
    posted = scoped.post(
        reverse("supply_chain:custodian_assign"),
        {
            "supply_point": worker.pk,
            "name": "Home Worker",
            "starts_on": "2026-11-01",
            "security_measures": ["locked_box"],
            "note": "a lockable box under the bed",
            "source": "partner_reported",
        },
    )
    assert posted.status_code == 302, posted.context["form"].errors if posted.context else None
    assert Custodian.objects.get(supply_point=worker).security_measures == ["locked_box"]
    again = scoped.get(reverse("supply_chain:issues")).content.decode()
    assert "Home Worker" in again and "a lockable box under the bed" in again
