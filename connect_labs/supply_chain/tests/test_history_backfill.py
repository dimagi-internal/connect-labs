"""Backfill create revisions for rows that predate history, and purge wipes it.

THIS REPOSITORY IS PUBLIC. Every id, name and figure below is invented.

Exercises the migration's `backfill(apps, schema_editor)` function directly
against a real database (the pattern `test_alerts.py` already uses for
`0012_seed_alert_beat_task`), and the history-purging half of
`SupplyDataAccess.purge()`. See
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.5.
"""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.alerts.models import AlertCheckState, AlertSubscription
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.context import capture_suspended
from connect_labs.supply_chain.history.models import OperationCall, Revision
from connect_labs.supply_chain.models import Commodity, Tender
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10997
OTHER_PROGRAM = 10996
SYNTHETIC_PROGRAM = 10995

backfill = importlib.import_module("connect_labs.supply_chain.migrations.0033_history_backfill").backfill


def _tender_payload(label="Backfilled tender"):
    return {"data": {"label": label, "delivery_point": {"name": "Central store"}}}


@pytest.fixture
def registered_synthetic():
    """Register `SYNTHETIC_PROGRAM` as a labs-only scope. `purge()` refuses a
    programme `scopes.is_synthetic` does not recognise -- see
    `test_data_access.py::registered_synthetic`, whose pattern this reuses.
    """
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    return SyntheticOpportunity.objects.create(
        opportunity_id=SYNTHETIC_PROGRAM,
        program_id=SYNTHETIC_PROGRAM,
        labs_only=True,
        enabled=True,
        label="history backfill tests",
        allowed_domains=["dimagi.com"],
    )


class TestBackfill:
    def test_one_create_revision_per_pre_existing_row_stamped_with_created_at(self):
        with capture_suspended():
            tender = Tender.objects.create(program_id=PROGRAM, label="Pre-existing tender")
            commodity = Commodity.objects.create(
                scope_key=f"prog:{PROGRAM}",
                slug="rutf",
                name="RUTF",
                base_unit="sachet",
                pack_unit="carton",
                base_per_pack=150,
            )
        assert not Revision.objects.exists(), "capture_suspended must have written nothing yet"

        backfill(django_apps, None)

        tender_rev = Revision.objects.get(
            content_type=ContentType.objects.get_for_model(Tender), object_id=str(tender.pk), action="create"
        )
        assert tender_rev.recorded_at == tender.created_at
        assert tender_rev.program_id == PROGRAM
        assert tender_rev.call is None
        assert tender_rev.changes["label"] == [None, "Pre-existing tender"]
        assert "created_at" not in tender_rev.changes

        commodity_rev = Revision.objects.get(
            content_type=ContentType.objects.get_for_model(Commodity), object_id=str(commodity.pk), action="create"
        )
        assert commodity_rev.recorded_at == commodity.created_at
        # Commodity resolves its program through `scope_key` ("prog:<id>"),
        # not a program_id column -- proof `program_of` runs on the
        # historical instance the same way it runs on a live one.
        assert commodity_rev.program_id == PROGRAM

    def test_rerunning_the_backfill_adds_no_revisions(self):
        with capture_suspended():
            Tender.objects.create(program_id=PROGRAM, label="Once")
        backfill(django_apps, None)
        after_first_run = Revision.objects.count()
        assert after_first_run > 0

        backfill(django_apps, None)

        assert Revision.objects.count() == after_first_run

    def test_a_row_that_already_has_a_create_revision_is_left_alone(self):
        """A row created AFTER 0032_history shipped already has a real create
        revision (recorded_at = whenever it actually happened, call set).
        The backfill must not overwrite it with a second, migration-time one.
        """
        tender_ct = ContentType.objects.get_for_model(Tender)
        tender = Tender.objects.create(program_id=PROGRAM, label="Already tracked")
        real_rev = Revision.objects.get(content_type=tender_ct, object_id=str(tender.pk), action="create")

        backfill(django_apps, None)

        revs = list(Revision.objects.filter(content_type=tender_ct, object_id=str(tender.pk), action="create"))
        assert len(revs) == 1
        assert revs[0].pk == real_rev.pk

    def test_a_model_with_no_created_at_is_stamped_with_the_migration_run_time(self):
        """`AlertCheckState` has no `created_at` -- the backfill must fall
        back to a stamp taken while it runs, not crash or leave `recorded_at`
        unset.
        """
        with capture_suspended():
            subscription = AlertSubscription.objects.create(program_id=PROGRAM, recipient_email="ops@example.test")
            state = AlertCheckState.objects.create(
                subscription=subscription,
                check_key="stale_outreach",
                first_reported_at=timezone.now(),
                last_seen_at=timezone.now(),
            )
        assert not hasattr(state, "created_at")

        before = timezone.now()
        backfill(django_apps, None)
        after = timezone.now()

        rev = Revision.objects.get(
            content_type=ContentType.objects.get_for_model(AlertCheckState), object_id=str(state.pk), action="create"
        )
        assert before - timedelta(seconds=1) <= rev.recorded_at <= after + timedelta(seconds=1)
        # `AlertCheckState` resolves its program by walking `subscription`,
        # which is a plain FK attribute a historical model instance carries
        # identically to the real one.
        assert rev.program_id == PROGRAM


@pytest.mark.usefixtures("registered_synthetic")
class TestPurgeWipesHistory:
    def test_purge_deletes_the_programmes_revisions_and_calls_but_not_anothers(self):
        synthetic_access = SupplyDataAccess(program_id=SYNTHETIC_PROGRAM, caller=SYSTEM)
        other_access = SupplyDataAccess(program_id=OTHER_PROGRAM, caller=SYSTEM)

        call_operation("tender_create", synthetic_access, _tender_payload("Synthetic tender"), channel="command")
        call_operation("tender_create", other_access, _tender_payload("Someone else's tender"), channel="command")

        assert Revision.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
        assert OperationCall.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()

        synthetic_access.purge()

        assert not Revision.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
        assert not OperationCall.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
        # The other programme's history is untouched.
        assert Revision.objects.filter(program_id=OTHER_PROGRAM).exists()
        assert OperationCall.objects.filter(program_id=OTHER_PROGRAM).exists()

    def test_purge_does_not_leave_delete_revisions_behind(self):
        """Every row purge() deletes runs under `capture_suspended()`, so the
        deletes themselves never produce `delete` Revisions that would then
        need cleaning up too -- there is nothing left at all for the
        programme, not merely no `create` rows.
        """
        access = SupplyDataAccess(program_id=SYNTHETIC_PROGRAM, caller=SYSTEM)
        call_operation("tender_create", access, _tender_payload(), channel="command")

        access.purge()

        assert Revision.objects.filter(program_id=SYNTHETIC_PROGRAM).count() == 0

    def test_purge_clears_an_org_level_revision_written_by_its_own_call(self):
        """`supplier_create` writes a `SupplierProfile` -- organisation-level,
        shared across every program that buys from it (program.SKIPPED_MODELS)
        -- so that Revision's OWN `program_id` is None even though the call
        that wrote it was scoped to this programme. Before the `call__program_id`
        fallback, purge deleted the programme's OWN revisions, then hit
        ProtectedError deleting its OperationCalls: the SupplierProfile
        revision still pointed at one of them.
        """
        access = SupplyDataAccess(program_id=SYNTHETIC_PROGRAM, caller=SYSTEM)
        call_operation(
            "supplier_create",
            access,
            {"data": {"name": "Northwind Foods", "type": "manufacturer", "country": "NG"}},
            channel="command",
        )
        profile_ct = ContentType.objects.get_for_model(django_apps.get_model("supply_chain", "SupplierProfile"))
        assert Revision.objects.filter(content_type=profile_ct, program_id__isnull=True).exists()

        access.purge()

        assert not Revision.objects.filter(content_type=profile_ct, call__program_id=SYNTHETIC_PROGRAM).exists()
        assert not OperationCall.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
