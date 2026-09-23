"""Alerts: a new check or a matching movement, emailed once.

THIS REPOSITORY IS PUBLIC. Every name and address here is invented.

The behaviours that matter most are about NOT sending: a check that stays true
for a month is one email, not eight thousand; a subscription made today does
not mail the programme's history; a filter narrows and never widens. And the
email itself states a fact and a link, and never advice (design doc section 22).
"""

from datetime import timedelta
from unittest.mock import patch

import jsonschema
import pytest
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain import checks
from connect_labs.supply_chain.alerts import service
from connect_labs.supply_chain.alerts.models import AlertNotice, AlertSubscription
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10501
OTHER_PROGRAM = 10502


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def other_da():
    return SupplyDataAccess(access_token="unused", program_id=OTHER_PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def sent():
    """Every email the alert run queued, captured instead of sent."""
    outbox = []

    def _capture(subject, message, recipient_list, html_message=None, from_email=None):
        outbox.append({"subject": subject, "body": message, "to": recipient_list, "html": html_message})
        return object()

    with (
        patch.object(service, "send_labs_email", side_effect=_capture),
        patch.object(service, "email_enabled", return_value=True),
    ):
        yield outbox


@pytest.fixture
def rutf(da):
    """A commodity with no ration table: `commodity_course_undefined` is true of it."""
    return op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet"})


def _set_course(da):
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "rutf",
            "course_definition": {"base_units_per_day": "2", "days_per_course": 75, "base_units_per_course": 150},
        },
    )


def _clear_course(da):
    op(da, "commodity_upsert", data={"slug": "rutf", "course_definition": {}})


def _worker(da, slug, username):
    return op(
        da,
        "supply_point_upsert",
        data={
            "slug": slug,
            "name": f"Worker {username}",
            "kind": "user_held",
            "connect_username": username,
            "source": "we_recorded",
        },
    )


def _subscribe(da, **data):
    data.setdefault("recipient_email", "stores@example.org")
    # None means "leave it out", the way a caller omitting a key would.
    data = {key: value for key, value in data.items() if value is not None}
    return op(da, "alert_subscription_create", data=data)


# ---- the operations ------------------------------------------------------


class TestSubscriptionOperations:
    def test_a_subscription_names_what_to_watch_and_who_hears(self, da):
        sub = _subscribe(da, check_kinds=["stock_below_minimum"], label="Low stock to EvAc")
        assert sub["check_kinds"] == ["stock_below_minimum"]
        assert sub["recipient_email"] == "stores@example.org"
        assert sub["cadence"] == "immediate"
        assert sub["active"] is True
        assert [s["id"] for s in op(da, "alert_subscription_list")] == [sub["id"]]

    def test_an_unknown_check_kind_is_refused_by_name(self, da):
        with pytest.raises(ValueError, match="stock_is_sad"):
            _subscribe(da, check_kinds=["stock_is_sad"])

    def test_check_kinds_are_read_from_the_live_list_not_a_copy(self, da):
        """Another change is adding checks. A subscription must accept a new
        kind the moment `run_checks` can emit it, with no edit here."""
        with patch.dict(checks.KIND_CATEGORIES, {"shipment_overdue": "threshold"}):
            sub = _subscribe(da, check_kinds=["shipment_overdue"])
        assert sub["check_kinds"] == ["shipment_overdue"]

    def test_an_unknown_movement_kind_is_refused_by_the_schema(self, da):
        with pytest.raises(jsonschema.ValidationError):
            _subscribe(da, movement_kinds=["teleport"])

    def test_a_subscription_that_watches_nothing_is_refused(self, da):
        with pytest.raises(ValueError, match="watch"):
            _subscribe(da)

    def test_it_goes_to_one_recipient_never_two_and_never_none(self, da, django_user_model):
        user = django_user_model.objects.create(username="amina", email="amina@example.org")
        with pytest.raises(ValueError, match="one recipient"):
            _subscribe(da, check_kinds=["stock_stockout"], recipient_user_id=user.pk)
        with pytest.raises(ValueError, match="one recipient"):
            op(da, "alert_subscription_create", data={"check_kinds": ["stock_stockout"]})

    def test_a_signed_in_member_may_subscribe_themselves_but_not_another_user(self, da, django_user_model):
        me = django_user_model.objects.create(username="amina", email="amina@example.org")
        colleague = django_user_model.objects.create(username="bola", email="bola@example.org")
        mine = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        mine.user = me
        sub = op(
            mine,
            "alert_subscription_create",
            data={"check_kinds": ["stock_stockout"], "recipient_user_id": me.pk},
        )
        assert sub["recipient_user_id"] == me.pk
        assert sub["created_by_id"] == me.pk
        with pytest.raises(ValueError, match="email address"):
            op(
                mine,
                "alert_subscription_create",
                data={"check_kinds": ["stock_stockout"], "recipient_user_id": colleague.pk},
            )

    def test_the_filters_must_be_this_programmes_own(self, da, other_da, rutf):
        elsewhere = _worker(other_da, "w-elsewhere", "elsewhere")
        with pytest.raises(ValueError, match="supply point"):
            _subscribe(da, check_kinds=["stock_stockout"], supply_point_id=elsewhere["id"])
        with pytest.raises(ValueError, match="commodity"):
            _subscribe(da, check_kinds=["stock_stockout"], commodity_slug="not-in-catalogue")
        sub = _subscribe(da, check_kinds=["stock_stockout"], commodity_slug="rutf")
        assert sub["commodity_slug"] == "rutf"

    def test_update_is_a_partial_change(self, da):
        sub = _subscribe(da, check_kinds=["stock_stockout"], label="x")
        updated = op(da, "alert_subscription_update", subscription_id=sub["id"], data={"cadence": "daily_digest"})
        assert updated["cadence"] == "daily_digest"
        assert updated["check_kinds"] == ["stock_stockout"]
        assert updated["label"] == "x"
        paused = op(da, "alert_subscription_update", subscription_id=sub["id"], data={"active": False})
        assert paused["active"] is False

    def test_another_programmes_subscription_cannot_be_read_changed_or_deleted(self, da, other_da):
        theirs = _subscribe(other_da, check_kinds=["stock_stockout"])
        assert op(da, "alert_subscription_list") == []
        with pytest.raises(ValueError, match="not found"):
            op(da, "alert_subscription_update", subscription_id=theirs["id"], data={"active": False})
        with pytest.raises(ValueError, match="not found"):
            op(da, "alert_subscription_delete", subscription_id=theirs["id"])
        assert AlertSubscription.objects.get(pk=theirs["id"]).active is True

    def test_delete_removes_it(self, da):
        sub = _subscribe(da, check_kinds=["stock_stockout"])
        op(da, "alert_subscription_delete", subscription_id=sub["id"])
        assert op(da, "alert_subscription_list") == []


# ---- what is sent, and what is not --------------------------------------


class TestNewChecksOnly:
    def test_a_new_check_is_sent_once(self, da, rutf, sent):
        _subscribe(da, check_kinds=["commodity_course_undefined"])
        service.run_alerts()
        assert len(sent) == 1
        service.run_alerts()
        service.run_alerts()
        assert len(sent) == 1, "a check that persists was re-sent"

    def test_a_check_that_clears_and_comes_back_is_new_again(self, da, rutf, sent):
        _subscribe(da, check_kinds=["commodity_course_undefined"])
        service.run_alerts()
        _set_course(da)
        service.run_alerts()
        assert len(sent) == 1, "clearing is not news"
        _clear_course(da)
        service.run_alerts()
        assert len(sent) == 2

    def test_only_the_kinds_asked_for(self, da, rutf, sent):
        _subscribe(da, check_kinds=["stock_stockout"])
        service.run_alerts()
        assert sent == []

    def test_a_supply_point_filter_narrows_to_that_point(self, da, sent):
        amina = _worker(da, "w-amina", "amina")
        _worker(da, "w-bola", "bola")
        _subscribe(da, check_kinds=["stock_never_reported"], supply_point_id=amina["id"])
        service.run_alerts()
        notices = list(AlertNotice.objects.all())
        assert [n.subject["id"] for n in notices] == [amina["id"]]

    def test_an_inactive_subscription_hears_nothing(self, da, rutf, sent):
        sub = _subscribe(da, check_kinds=["commodity_course_undefined"])
        op(da, "alert_subscription_update", subscription_id=sub["id"], data={"active": False})
        service.run_alerts()
        assert sent == []

    def test_each_subscription_keeps_its_own_memory(self, da, rutf, sent):
        _subscribe(da, check_kinds=["commodity_course_undefined"], recipient_email="one@example.org")
        service.run_alerts()
        _subscribe(da, check_kinds=["commodity_course_undefined"], recipient_email="two@example.org")
        service.run_alerts()
        assert [m["to"] for m in sent] == [["one@example.org"], ["two@example.org"]]


class TestMovements:
    @pytest.fixture
    def stores(self, da, rutf):
        a = op(
            da,
            "supply_point_upsert",
            data={"slug": "a", "name": "Store A", "kind": "central_store", "source": "we_recorded"},
        )
        b = op(
            da,
            "supply_point_upsert",
            data={"slug": "b", "name": "Store B", "kind": "facility", "source": "we_recorded"},
        )
        return a, b

    def _move(self, da, a, b, kind="transfer", quantity="10"):
        return op(
            da,
            "movement_record",
            data={
                "kind": kind,
                "occurred_on": "2026-09-20",
                "from_supply_point_id": a["id"],
                "to_supply_point_id": b["id"],
                "commodity_slug": "rutf",
                "quantity": quantity,
                "quantity_unit": "sachet",
                "source": "supplier_reported",
            },
        )

    def test_a_matching_movement_recorded_after_subscribing_is_sent(self, da, stores, sent):
        a, b = stores
        self._move(da, a, b)  # before: history, not news
        _subscribe(da, movement_kinds=["transfer"])
        service.run_alerts()
        assert sent == []
        self._move(da, a, b, quantity="25")
        service.run_alerts()
        assert len(sent) == 1
        assert "25" in sent[0]["body"]
        service.run_alerts()
        assert len(sent) == 1

    def test_a_movement_of_another_kind_or_elsewhere_is_not(self, da, stores, sent):
        a, b = stores
        c = op(
            da,
            "supply_point_upsert",
            data={"slug": "c", "name": "Store C", "kind": "facility", "source": "we_recorded"},
        )
        _subscribe(da, movement_kinds=["loss"])
        _subscribe(da, movement_kinds=["transfer"], supply_point_id=c["id"], recipient_email="c@example.org")
        self._move(da, a, b)
        service.run_alerts()
        assert sent == []


class TestDigest:
    def test_a_digest_holds_notices_until_a_day_has_passed_then_sends_one_email(self, da, rutf, sent):
        _worker(da, "w-amina", "amina")
        sub = _subscribe(
            da, check_kinds=["commodity_course_undefined", "stock_never_reported"], cadence="daily_digest"
        )
        service.run_alerts()
        assert sent == []
        assert AlertNotice.objects.filter(subscription_id=sub["id"], delivery="pending").count() == 2

        later = timezone.now() + timedelta(hours=25)
        service.run_alerts(now=later)
        assert len(sent) == 1
        assert "commodity_course_undefined" in sent[0]["body"] and "stock_never_reported" in sent[0]["body"]
        assert AlertNotice.objects.filter(subscription_id=sub["id"], delivery="queued").count() == 2


class TestTheEmail:
    def test_it_states_the_fact_and_links_to_the_record_and_advises_nothing(self, da, rutf, sent):
        _subscribe(da, check_kinds=["commodity_course_undefined"], label="Catalogue gaps")
        service.run_alerts()
        body = sent[0]["body"]
        assert "commodity_course_undefined" in body
        assert "RUTF" in body
        assert "https://labs.connect.dimagi.com/supply/catalogue/rutf/?program_id=10501" in body
        assert "Catalogue gaps" in sent[0]["subject"]
        for word in ("should", "recommend", "urgent", "priority", "please"):
            assert word not in body.lower(), f"the alert email advises: {word!r}"

    def test_when_outbound_email_is_off_the_log_says_so_rather_than_claiming_a_send(self, da, rutf):
        _subscribe(da, check_kinds=["commodity_course_undefined"])
        with (
            patch.object(service, "email_enabled", return_value=False),
            patch.object(service, "send_labs_email") as send,
        ):
            service.run_alerts()
        assert not send.called
        assert list(AlertNotice.objects.values_list("delivery", flat=True)) == ["email_disabled"]

    def test_a_labs_user_is_mailed_at_their_current_address(self, da, rutf, sent, django_user_model):
        user = django_user_model.objects.create(username="amina", email="old@example.org")
        _subscribe(da, check_kinds=["commodity_course_undefined"], recipient_email=None, recipient_user_id=user.pk)
        user.email = "new@example.org"
        user.save()
        service.run_alerts()
        assert sent[0]["to"] == ["new@example.org"]


class TestTheLog:
    def test_the_log_lists_what_went_out_for_this_programme_only(self, da, other_da, rutf, sent):
        _subscribe(da, check_kinds=["commodity_course_undefined"])
        op(other_da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF elsewhere"})
        _subscribe(other_da, check_kinds=["commodity_course_undefined"])
        service.run_alerts()
        log = op(da, "alert_log_list")
        assert len(log) == 1
        assert log[0]["subject_kind"] == "commodity_course_undefined"
        assert log[0]["delivery"] == "queued"
        assert log[0]["sent_to"] == "stores@example.org"


class TestTheTask:
    def test_the_beat_task_runs_the_alerts(self):
        from connect_labs.supply_chain import tasks

        with patch.object(service, "run_alerts", return_value={"subscriptions": 0}) as run:
            tasks.send_supply_alerts()
        assert run.called

    def test_the_task_is_scheduled(self):
        """Runs the migration's own function rather than trusting the seeded row:
        a TransactionTestCase elsewhere can flush the table, and a test that
        passes or fails on run order proves nothing."""
        import importlib

        from django_celery_beat.models import PeriodicTask

        importlib.import_module("connect_labs.supply_chain.migrations.0007_seed_alert_beat_task").create_periodic_task(
            None, None
        )
        task = PeriodicTask.objects.get(name="supply_chain_send_alerts")
        assert task.task == "connect_labs.supply_chain.tasks.send_supply_alerts"
        assert task.enabled
