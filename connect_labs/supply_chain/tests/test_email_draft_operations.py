"""The draft operations: one supplier at a time, and a whole round at once.

Every draft operation is a read. The one write in this family is marking a
reminder sent, and that is `outreach_update` setting `last_reminder_on` --
the column was already there, and a second operation that writes it would be
a second way to say the same thing.

Every supplier, person and price here is invented.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import agent_operations, call_operation, get_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10733
TODAY = date(2026, 9, 28)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def round_(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150},
    )
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Kano round",
            "delivery_point": {"name": "Central store", "city": "Kano", "country_name": "Nigeria"},
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        },
    )
    op(da, "tender_open", tender_id=tender["id"])

    def supplier(name, **extra):
        return op(da, "supplier_create", data={"name": name, **extra})

    silent = supplier("Harmattan Foods", contacts=[{"name": "Ada Bello", "email": "ada@harmattan.example.invalid"}])
    recently_asked = supplier("Lagoon Nutrition")
    quoted = supplier("Northwind Nutrition")
    complete = supplier("Sahel Provisions")
    unsent = supplier("Savanna Mills")

    def invite(s, sent_on, **extra):
        data = {"tender_id": tender["id"], "supplier_id": s["id"], **extra}
        if sent_on:
            data["sent_on"] = sent_on.isoformat()
        return op(da, "outreach_log", data=data)

    rows = {
        "silent": invite(silent, TODAY - timedelta(days=19)),
        "recently_asked": invite(recently_asked, TODAY - timedelta(days=3)),
        "quoted": invite(quoted, TODAY - timedelta(days=19), responded=True, response_kind="quote"),
        "complete": invite(complete, TODAY - timedelta(days=19), responded=True, response_kind="quote"),
        "unsent": invite(unsent, None),
    }
    open_quote = op(
        da,
        "quote_record",
        data={
            "tender_id": tender["id"],
            "supplier_id": quoted["id"],
            "commodity_slug": "rutf",
            "as_quoted_amount": "50.00",
            "as_quoted_currency": "USD",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
            "received_on": "2026-09-10",
        },
    )
    full_quote = op(
        da,
        "quote_record",
        data={
            "tender_id": tender["id"],
            "supplier_id": complete["id"],
            "commodity_slug": "rutf",
            "as_quoted_amount": "48.00",
            "as_quoted_currency": "USD",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
            "pack_spec_source": "stated_on_quote",
            "base_per_pack_stated": 150,
            "base_unit_grams_stated": 92,
            "freight_basis": "included",
            "duties_basis": "included",
            "shelf_life_months_stated": 24,
            "moq": "100",
            "moq_unit": "carton",
            "lead_time_days": 30,
            "validity_until": "2026-12-31",
            "received_on": "2026-09-12",
        },
    )
    return {
        "tender": tender,
        "rows": rows,
        "suppliers": {"silent": silent, "quoted": quoted, "complete": complete, "unsent": unsent},
        "open_quote": open_quote,
        "full_quote": full_quote,
    }


def drafts(da, round_, **extra):
    return op(da, "tender_drafts_render", tender_id=round_["tender"]["id"], today=TODAY.isoformat(), **extra)


def by(result, kind):
    return {d["supplier_name"]: d for d in result["drafts"] if d["kind"] == kind}


# ---- registration ---------------------------------------------------------


def test_the_new_drafts_are_read_only_operations_offered_over_mcp():
    for name in ("reminder_render", "tender_drafts_render", "request_render", "followup_render"):
        assert name in agent_operations(), name
        assert get_operation(name).is_write is False, name


def test_marking_a_reminder_sent_is_documented_on_outreach_update():
    operation = get_operation("outreach_update")
    data = operation.input_schema["properties"]["data"]["properties"]
    assert data["last_reminder_on"]["format"] == "date"
    assert "last_reminder_on" in operation.summary


# ---- one supplier at a time -----------------------------------------------


def test_request_render_returns_a_subject_and_a_signed_text(da, round_):
    result = op(
        da,
        "request_render",
        tender_id=round_["tender"]["id"],
        supplier_id=round_["suppliers"]["silent"]["id"],
        commodity_slug="rutf",
    )
    assert result["subject"] == "Quotation request: 2,000 cartons of RUTF, delivered Kano"
    assert result["text"].startswith("Dear Ada Bello,")
    assert result["to"] == "ada@harmattan.example.invalid"
    # Nobody is signed in on a SYSTEM caller, so the gap is left visible.
    assert "[your name]" in result["text"]


def test_followup_render_names_the_quote(da, round_):
    result = op(da, "followup_render", quote_id=round_["open_quote"]["id"])
    assert "your quotation of 10 Sep 2026, USD 50.00 per carton" in result["text"]
    assert result["subject"] == "Follow-up on your quotation of 10 Sep 2026 — 2,000 cartons of RUTF, delivered Kano"


def test_reminder_render_names_the_request_date(da, round_):
    result = op(da, "reminder_render", outreach_id=round_["rows"]["silent"]["id"], today=TODAY.isoformat())
    assert result["subject"] == "Reminder: quotation request of 9 Sep 2026 — 2,000 cartons of RUTF, delivered Kano"
    assert "On 9 Sep 2026 we asked for a quotation for 2,000 cartons of RUTF" in result["text"]
    assert result["outreach_id"] == round_["rows"]["silent"]["id"]


def test_a_reminder_for_an_invitation_never_sent_is_refused(da, round_):
    with pytest.raises(ValueError, match="request_render"):
        op(da, "reminder_render", outreach_id=round_["rows"]["unsent"]["id"])


def test_the_signature_is_the_signed_in_persons(da, round_, django_user_model):
    user = django_user_model.objects.create_user(
        username="sophie", password="x", email="sophie@dimagi.com", name="Sophie Example"
    )
    signed_in = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM, user=user)
    result = op(signed_in, "followup_render", quote_id=round_["open_quote"]["id"])
    tail = [line for line in result["text"].splitlines() if line.strip()][-2:]
    assert tail == ["Sophie Example", "Dimagi"]


# ---- a whole round --------------------------------------------------------


def test_a_silent_supplier_past_the_interval_gets_a_reminder_with_the_default_stated(da, round_):
    result = drafts(da, round_)
    reminders = by(result, "reminder")
    assert set(reminders) == {"Harmattan Foods"}
    assert result["reminder_interval_days"] == 7
    assert result["reminder_interval_is_default"] is True
    assert "7 days" in result["reminder_interval_note"] and "default" in result["reminder_interval_note"]
    reminder = reminders["Harmattan Foods"]
    assert reminder["outreach_id"] == round_["rows"]["silent"]["id"]
    assert "9 Sep 2026" in reminder["why"] and "19 days" in reminder["why"]
    assert reminder["subject"].startswith("Reminder: quotation request of 9 Sep 2026")


def test_the_tenders_own_interval_is_used_when_it_sets_one(da, round_):
    op(da, "tender_update", tender_id=round_["tender"]["id"], data={"reminder_interval_days": 2})
    result = drafts(da, round_)
    assert set(by(result, "reminder")) == {"Harmattan Foods", "Lagoon Nutrition"}
    assert result["reminder_interval_is_default"] is False


def test_a_supplier_who_replied_or_quoted_is_not_chased(da, round_):
    reminded = set(by(drafts(da, round_), "reminder"))
    assert "Northwind Nutrition" not in reminded
    assert "Sahel Provisions" not in reminded


def test_marking_the_reminder_sent_restarts_the_interval(da, round_):
    op(
        da,
        "outreach_update",
        outreach_id=round_["rows"]["silent"]["id"],
        data={"last_reminder_on": (TODAY - timedelta(days=1)).isoformat()},
    )
    assert "Harmattan Foods" not in by(drafts(da, round_), "reminder")
    later = op(
        da, "tender_drafts_render", tender_id=round_["tender"]["id"], today=(TODAY + timedelta(days=7)).isoformat()
    )
    reminder = by(later, "reminder")["Harmattan Foods"]
    assert "This is our second reminder; we last wrote on 27 Sep 2026." in reminder["text"]


def test_a_quote_with_outstanding_questions_gets_a_follow_up_and_a_complete_one_does_not(da, round_):
    followups = by(drafts(da, round_), "followup")
    assert set(followups) == {"Northwind Nutrition"}
    assert followups["Northwind Nutrition"]["quote_id"] == round_["open_quote"]["id"]
    assert "outstanding" in followups["Northwind Nutrition"]["why"]


def test_an_invitation_logged_but_never_sent_gets_the_request(da, round_):
    requests = by(drafts(da, round_), "request")
    assert set(requests) == {"Savanna Mills"}
    assert requests["Savanna Mills"]["subject"].startswith("Quotation request:")
    assert "no send date" in requests["Savanna Mills"]["why"]


def test_every_draft_carries_supplier_kind_subject_text_and_why(da, round_):
    for draft in drafts(da, round_)["drafts"]:
        for key in ("supplier_id", "supplier_name", "kind", "subject", "text", "why"):
            assert draft.get(key), (key, draft)


def test_a_closed_tender_is_not_chased(da, round_):
    op(da, "tender_close", tender_id=round_["tender"]["id"])
    result = drafts(da, round_)
    assert not by(result, "reminder")
    assert not by(result, "request")


def test_it_writes_nothing(da, round_):
    from connect_labs.supply_chain.history.models import OperationCall

    before = OperationCall.objects.count()
    drafts(da, round_)
    assert OperationCall.objects.count() == before


# ---- the page -------------------------------------------------------------


@pytest.fixture
def client_in_programme(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.procurement import views as procurement_views

    account = django_user_model.objects.create_user(username="drafts", password="x", email="drafts@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    monkeypatch.setattr(procurement_views, "_access", _scoped)
    monkeypatch.setattr(procurement_views, "has_program_context", lambda request: True)
    return client


def test_the_tender_page_lists_the_rounds_drafts_with_copy_buttons(client_in_programme, round_):
    response = client_in_programme.get(
        reverse("supply_chain:procurement_tender_detail", args=[round_["tender"]["id"]])
    )
    assert response.status_code == 200
    body = response.content.decode()
    start = body.index('id="drafts"')
    panel = body[start : body.index("</details>", start)]
    assert "Draft emails" in panel
    assert "Northwind Nutrition" in panel
    assert "Follow-up on your quotation of 10 Sep 2026" in panel
    assert "navigator.clipboard.writeText" in panel
