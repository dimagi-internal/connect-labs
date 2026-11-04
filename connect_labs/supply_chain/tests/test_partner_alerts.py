"""Alerts per partner, the channel interface, and the planning looks-ahead.

THIS REPOSITORY IS PUBLIC. Every name and address here is invented.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import checks
from connect_labs.supply_chain.alerts import service
from connect_labs.supply_chain.alerts.models import AlertNotice, AlertSubscription
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.partners import planning

pytestmark = pytest.mark.django_db

PROGRAM = 10985


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def sent():
    outbox = []

    def _capture(subject, message, recipient_list, html_message=None, from_email=None):
        outbox.append({"subject": subject, "body": message, "to": recipient_list})
        return object()

    with (
        patch.object(service, "send_labs_email", side_effect=_capture),
        patch.object(service, "email_enabled", return_value=True),
    ):
        yield outbox


@pytest.fixture
def world():
    a = LabsOrg.objects.create(slug="alert-a", name="Alert Partner A")
    b = LabsOrg.objects.create(slug="alert-b", name="Alert Partner B")

    def worker(slug, org, name):
        store = SupplyPoint.objects.create(
            program_id=PROGRAM, slug=f"{slug}-store", name=f"{name} store", kind="central_store",
            managed_by_org=org, source="we_recorded",
        )  # fmt: skip
        point = SupplyPoint.objects.create(
            program_id=PROGRAM, slug=slug, name=name, kind="user_held", parent=store,
            connect_username=slug, source="we_recorded",
        )  # fmt: skip
        return store, point

    return a, b, worker("w-amina", a, "Amina"), worker("w-bola", b, "Bola")


def _subscribe(da, **data):
    data.setdefault("recipient_email", "staff@example.org")
    return op(da, "alert_subscription_create", data=data)


class TestPartnerScoping:
    def test_a_partners_alert_hears_only_about_its_own_places(self, da, world, sent):
        a, b, (_, amina), (_, bola) = world
        _subscribe(da, check_kinds=["stock_never_reported"], partner_org_id=a.pk)
        service.run_alerts()
        subjects = {n.subject["id"]: n.subject for n in AlertNotice.objects.all()}
        assert set(subjects) == {amina.pk}
        assert subjects[amina.pk]["partner"] == "Alert Partner A"

    def test_the_summary_across_partners_hears_all_and_names_each_partner(self, da, world, sent):
        a, b, (_, amina), (_, bola) = world
        _subscribe(da, check_kinds=["stock_never_reported"], cadence="immediate", recipient_email="run@example.org")
        service.run_alerts()
        by_id = {n.subject["id"]: n.subject.get("partner") for n in AlertNotice.objects.all()}
        assert by_id == {amina.pk: "Alert Partner A", bola.pk: "Alert Partner B"}
        assert "Partner: Alert Partner B" in sent[0]["body"]

    def test_a_partner_alert_is_not_widened_by_a_check_that_touches_no_place_of_theirs(self, da, world, sent):
        a, *_ = world
        _subscribe(da, check_kinds=["commodity_course_undefined"], partner_org_id=a.pk)
        op(da, "commodity_upsert", data={"slug": "p", "name": "P", "base_unit": "sachet"})
        service.run_alerts()
        assert AlertNotice.objects.count() == 0

    def test_partner_setup_makes_a_partner_alert_each_and_a_daily_summary_and_repeats_safely(self, da, world):
        a, b, *_ = world
        first = op(
            da,
            "alert_partner_setup",
            partners=[{"org_id": a.pk, "emails": ["a@example.org"]}, {"org_id": b.pk, "emails": ["b@example.org"]}],
            summary_emails=["run@example.org"],
        )
        assert first["count"] == 3
        assert op(da, "alert_partner_setup", partners=[{"org_id": a.pk, "emails": ["a@example.org"]}])["count"] == 0
        summary = AlertSubscription.objects.get(recipient_email="run@example.org")
        assert summary.partner_org_id is None and summary.cadence == "daily_digest"
        assert "delivery_after_stockout" in summary.check_kinds and "tender_start_by" in summary.check_kinds
        mine = AlertSubscription.objects.get(recipient_email="a@example.org")
        assert mine.partner_org_id == a.pk and mine.cadence == "immediate"


class TestChannels:
    def test_a_whatsapp_alert_needs_a_phone_number(self, da):
        with pytest.raises(ValueError, match="phone number"):
            op(da, "alert_subscription_create", data={"check_kinds": ["stock_never_reported"], "channel": "whatsapp"})

    def test_a_whatsapp_notice_is_logged_as_not_sent_never_as_sent(self, da, world, sent):
        sub = op(
            da,
            "alert_subscription_create",
            data={"check_kinds": ["stock_never_reported"], "channel": "whatsapp", "recipient_phone": "+10000000000"},
        )
        assert sub["channel"] == "whatsapp" and sub["recipient_phone"] == "+10000000000"
        service.run_alerts()
        notices = AlertNotice.objects.all()
        assert notices and {n.delivery for n in notices} == {"no_channel"}
        assert sent == [], "no email goes out for a WhatsApp alert"


class TestPlanningChecks:
    def test_planning_checks_run_only_when_named(self, da):
        with patch.object(planning, "planning_checks", side_effect=AssertionError("forecast ran")):
            checks.run_checks(da)

    def test_a_delivery_that_arrives_after_the_stock_runs_out_is_a_check(self, da, world):
        a, b, (store, amina), _ = world
        today = date.today()
        item = SimpleNamespace(name="A Product")
        order = {
            "contract_id": 7, "reference": "PO-7", "supplier": {"name": "A Supplier"},
            "expected_on": today + timedelta(days=20),
        }  # fmt: skip
        late = {"stores": [{"supply_point_id": store.pk, "runs_dry_on": (today + timedelta(days=5)).isoformat()}]}
        with (
            patch.object(planning, "_rule_items", return_value=[item]),
            patch.object(planning, "_forecast", return_value=late),
            patch(
                "connect_labs.supply_chain.stock.services.network._expected_inbound", return_value={store.pk: [order]}
            ),
        ):
            found = checks.run_checks(da, kinds=["delivery_after_stockout"])
        assert [c["kind"] for c in found] == ["delivery_after_stockout"]
        assert found[0]["facts"]["days_without_stock"] == 15 and found[0]["subject"]["id"] == store.pk

        early = {"stores": [{"supply_point_id": store.pk, "runs_dry_on": (today + timedelta(days=30)).isoformat()}]}
        with (
            patch.object(planning, "_rule_items", return_value=[item]),
            patch.object(planning, "_forecast", return_value=early),
            patch(
                "connect_labs.supply_chain.stock.services.network._expected_inbound", return_value={store.pk: [order]}
            ),
        ):
            assert checks.run_checks(da, kinds=["delivery_after_stockout"]) == []

    def test_the_tender_start_by_hook_finds_nothing_until_the_column_exists(self, da):
        assert checks.run_checks(da, kinds=["tender_start_by"]) == []


class TestDeliverySuggestions:
    def test_a_facility_is_sent_a_few_days_of_what_its_workers_use_less_what_it_holds(self):
        item = SimpleNamespace(pk=1, name="A Product")
        stores = [
            {"supply_point_id": 1, "name": "PHC One", "kind": "facility", "workers": 3,
             "need": ["70"], "own_on_hand": "10", "runs_dry_on": None},
            {"supply_point_id": 2, "name": "PHC Two", "kind": "facility", "workers": 2,
             "need": ["70"], "own_on_hand": "500", "runs_dry_on": None},
            {"supply_point_id": 3, "name": "Warehouse", "kind": "central_store", "workers": 5,
             "need": ["700"], "own_on_hand": "0", "runs_dry_on": None},
        ]  # fmt: skip
        with patch.object(planning, "_forecast", return_value={"stores": stores, "unit": "sachet"}):
            out = planning.delivery_suggestions(PROGRAM, item, cover_days=7)
        by_name = {r["name"]: r for r in out["facilities"]}
        assert set(by_name) == {"PHC One", "PHC Two"}, "only shared facilities are sized"
        assert Decimal(by_name["PHC One"]["suggested"]) == 60  # 10/day x 7 days - 10 held
        assert Decimal(by_name["PHC Two"]["suggested"]) == 0  # already holds more than the cover


def test_the_suggestions_page_sizes_facilities_from_the_forecast(client, django_user_model, monkeypatch):
    from django.urls import reverse

    from connect_labs.supply_chain import form_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.partners import views as partner_views

    client.force_login(django_user_model.objects.create_user(username="pm2", password="x", email="pm2@dimagi.com"))

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "partners.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    monkeypatch.setattr(
        "connect_labs.supply_chain.stock.visit_views.rule_items", lambda op: [{"id": 1, "name": "A Product"}]
    )
    monkeypatch.setattr(
        partner_views.OperationBase,
        "op",
        lambda self, name, **payload: {
            "unit": "sachet",
            "facilities": [
                {"name": "PHC One", "workers": 3, "uses_per_day": "10.0", "holds": "10", "suggested": "60"},
            ],
        },
        raising=False,
    )
    page = client.get(reverse("supply_chain:partner_deliveries"))
    body = page.content.decode()
    assert page.status_code == 200 and "PHC One" in body and "60 sachet" in body
