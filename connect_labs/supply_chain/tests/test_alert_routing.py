# flake8: noqa: F811
"""Alert routing and wording: small FLW count gaps go to the partner's supervisor, and a failed alert is on the chase list.

THIS REPOSITORY IS PUBLIC. Invented names and addresses.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.supply_chain.alerts import service
from connect_labs.supply_chain.alerts.labels import WATCH_LABELS, watch_label
from connect_labs.supply_chain.alerts.models import AlertNotice, AlertSubscription
from connect_labs.supply_chain.portfolio.chase import chase_items
from connect_labs.supply_chain.tests.test_partner_alerts import PROGRAM, _subscribe, da, op, sent, world  # noqa: F401

pytestmark = pytest.mark.django_db


def _gap(point, variance, label="gap"):
    return {
        "kind": "stock_variance",
        "category": "conflict",
        "audience": "internal",
        "subject": {"type": "supply_point", "id": point.pk, "label": label},
        "facts": {"variance": str(variance), "unit": "sachet", "reconcilable": True},
        "since": None,
    }


def _kinds(subscription):
    return list(AlertNotice.objects.filter(subscription=subscription).values_list("subject_kind", flat=True))


def test_a_small_fl_w_gap_goes_to_the_partner_and_not_the_summary(da, world):
    a, _b, (_, amina), _ = world
    partner = AlertSubscription.objects.get(
        pk=_subscribe(da, check_kinds=["stock_variance"], partner_org_id=a.pk)["id"]
    )
    summary = AlertSubscription.objects.get(
        pk=_subscribe(da, check_kinds=["stock_variance"], cadence="daily_digest")["id"]
    )
    now = timezone.now()
    assert service.detect_checks(partner, [_gap(amina, -8)], now) == 1
    assert service.detect_checks(summary, [_gap(amina, -8)], now) == 0  # the partner's alert carries it


def test_a_big_gap_still_reaches_the_summary(da, world):
    a, _b, (_, amina), _ = world
    _subscribe(da, check_kinds=["stock_variance"], partner_org_id=a.pk)
    summary = AlertSubscription.objects.get(
        pk=_subscribe(da, check_kinds=["stock_variance"], cadence="daily_digest")["id"]
    )
    assert service.detect_checks(summary, [_gap(amina, -40)], timezone.now()) == 1


def test_a_small_gap_stays_in_the_summary_when_no_partner_alert_carries_it(da, world):
    _a, _b, (_, amina), _ = world
    summary = AlertSubscription.objects.get(
        pk=_subscribe(da, check_kinds=["stock_variance"], cadence="daily_digest")["id"]
    )
    assert service.detect_checks(summary, [_gap(amina, -8)], timezone.now()) == 1  # otherwise nobody would hear


def test_the_partner_setup_gives_partners_count_gaps_and_delivery_timing_and_the_summary_not_the_duplicate(da, world):
    a, _b, _, _ = world
    result = op(
        da,
        "alert_partner_setup",
        partners=[{"org_id": a.pk, "emails": ["lead@example.org"]}],
        summary_emails=["us@example.org"],
    )
    by_label = {s["label"]: s for s in result["created"]}
    partner_kinds = by_label["Alert Partner A: stock alerts"]["check_kinds"]
    assert "stock_variance" in partner_kinds and "delivery_after_stockout" in partner_kinds
    summary_kinds = by_label["Summary across partners"]["check_kinds"]
    assert summary_kinds.count("stock_variance") == 1 and "tender_start_by" in summary_kinds


def test_what_is_watched_has_plain_names_and_every_check_kind_resolves():
    from connect_labs.supply_chain.checks import KIND_CATEGORIES

    assert watch_label("stock_variance") == "A count differs from the records"
    assert watch_label("stock_negative") == "More was given out than was ever received"
    assert all(watch_label(kind) for kind in KIND_CATEGORIES)
    assert not [k for k in WATCH_LABELS if k not in KIND_CATEGORIES], "a label for a check that no longer exists"


def _failed(da, world, delivery, note=""):
    a, *_ = world
    sub = AlertSubscription.objects.get(
        pk=_subscribe(da, check_kinds=["stock_never_reported"], partner_org_id=a.pk, label="Partner A stock")["id"]
    )
    notice = AlertNotice.objects.create(
        subscription=sub,
        program_id=PROGRAM,
        kind="check",
        subject_kind="stock_never_reported",
        detected_at=timezone.now() - timedelta(days=3),
        delivery=delivery,
        delivery_note=note,
    )
    return sub, notice


def test_an_alert_that_failed_to_send_is_a_chase_list_item(da, world):
    _failed(da, world, "no_connection", "Your Open Chat Studio connection has expired.")
    [item] = service.failed_alert_items(PROGRAM, "Demo")
    assert item["source"] == "alert_failure" and item["audience"] == "internal"
    assert item["what"] == "An alert could not be sent" and "reconnect Open Chat Studio" in item["ask"]
    assert item["days"] == 3 and item["about"] == "Partner A stock" and "/supply/alerts/" in item["href"]
    items = chase_items(
        program_id=PROGRAM, program_name="Demo", checks=[], consignments=[], orders=[], suppliers={}, places={},
        check_labels={}, alert_failures=[item],
    )  # fmt: skip
    assert [i["what"] for i in items] == ["An alert could not be sent"]
    from connect_labs.supply_chain.portfolio.chase import grouped

    [section] = grouped(items)  # the Overview draws it under "Ours to answer"
    assert section["audience"] == "internal" and section["items"][0]["ask"] == item["ask"]


def test_an_alert_that_works_again_is_not_on_the_chase_list(da, world):
    sub, _ = _failed(da, world, "refused", "not agreed to messages")
    AlertNotice.objects.create(
        subscription=sub, program_id=PROGRAM, kind="check", subject_kind="stock_never_reported",
        detected_at=timezone.now(), delivery="queued", sent_at=timezone.now(),
    )  # fmt: skip
    assert service.failed_alert_items(PROGRAM) == []


def test_the_alerts_page_uses_plain_names_and_says_connect_message(da, world, client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.alerts import views as alert_views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access

    _subscribe(da, check_kinds=["stock_variance", "stock_negative"], label="Plain names")
    client.force_login(django_user_model.objects.create_user(username="sophie2", password="x"))

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "alerts.views"):
        monkeypatch.setattr(
            f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True, raising=False
        )
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped, raising=False)
    html = client.get(reverse("supply_chain:alerts")).content.decode()
    assert "A count differs from the records" in html and "More was given out than was ever received" in html
    assert "Ledger and count disagree" not in html
    assert "as a Connect message" in html and "An email when" not in html
    form = client.get(reverse("supply_chain:alert_create")).content.decode()
    assert "A place runs out before its next delivery" in form
