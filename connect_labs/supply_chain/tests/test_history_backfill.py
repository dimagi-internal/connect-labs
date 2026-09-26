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
from unittest.mock import patch

import pytest
from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.alerts.models import AlertCheckState, AlertSubscription
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history import program
from connect_labs.supply_chain.history.context import capture_suspended
from connect_labs.supply_chain.history.models import OperationCall, Revision
from connect_labs.supply_chain.models import (
    Commodity,
    Contract,
    Shipment,
    ShipmentLine,
    Supplier,
    SupplierProfile,
    Tender,
)
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10997
OTHER_PROGRAM = 10996
SYNTHETIC_PROGRAM = 10995

backfill_module = importlib.import_module("connect_labs.supply_chain.migrations.0033_history_backfill")
backfill = backfill_module.backfill


def _tender_payload(label="Backfilled tender"):
    return {"data": {"label": label, "delivery_point": {"name": "Central store"}}}


@pytest.fixture
def registered_synthetic():
    """Register `SYNTHETIC_PROGRAM` as a labs-only scope. `purge()` refuses a
    program `scopes.is_synthetic` does not recognise -- see
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

    def test_rows_whose_program_cannot_be_resolved_are_counted_and_logged(self, monkeypatch, caplog):
        with capture_suspended():
            tender = Tender.objects.create(program_id=PROGRAM, label="Unresolvable")
        real = program.program_of

        def refuses_tenders(instance):
            if type(instance).__name__ == "Tender":
                raise LookupError("a relation the frozen registry cannot walk")
            return real(instance)

        monkeypatch.setattr(program, "program_of", refuses_tenders)
        with caplog.at_level("WARNING"):
            unscoped = backfill(django_apps, None)

        assert unscoped == {"Tender": 1}
        assert "1 row(s) fell back to program None" in caplog.text and "Tender=1" in caplog.text
        rev = Revision.objects.get(content_type=ContentType.objects.get_for_model(Tender), object_id=str(tender.pk))
        assert rev.program_id is None

    def test_an_unexpected_error_is_not_swallowed(self, monkeypatch):
        with capture_suspended():
            Tender.objects.create(program_id=PROGRAM, label="Broken")

        def breaks(instance):
            raise RuntimeError("a bug, not a missing relation")

        monkeypatch.setattr(program, "program_of", breaks)
        with pytest.raises(RuntimeError):
            backfill(django_apps, None)

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

    def test_a_model_with_no_created_at_is_stamped_with_its_own_event_time(self):
        """`AlertCheckState` has no `created_at`, but says when it was first
        reported -- that is when it came to exist, not this migration's run.
        """
        first_reported = timezone.now() - timedelta(days=3)
        with capture_suspended():
            subscription = AlertSubscription.objects.create(program_id=PROGRAM, recipient_email="ops@example.test")
            state = AlertCheckState.objects.create(
                subscription=subscription,
                check_key="stale_outreach",
                first_reported_at=first_reported,
                last_seen_at=timezone.now(),
            )
        assert not hasattr(state, "created_at")

        backfill(django_apps, None)

        rev = Revision.objects.get(
            content_type=ContentType.objects.get_for_model(AlertCheckState), object_id=str(state.pk), action="create"
        )
        assert rev.recorded_at == first_reported
        # `AlertCheckState` resolves its program by walking `subscription`,
        # which is a plain FK attribute a historical model instance carries
        # identically to the real one.
        assert rev.program_id == PROGRAM

    def test_a_line_row_is_stamped_with_its_parents_created_at(self):
        """A shipment line has no timestamp of its own. Stamped at run time, a
        rewind to before the deploy would strip lines off shipments that
        stay; it existed from when its shipment did."""
        scope = f"prog:{PROGRAM}"
        with capture_suspended():
            commodity = Commodity.objects.create(
                scope_key=scope, slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150
            )
            supplier = Supplier.objects.enrol(
                scope_key=scope, name="Northwind Foods", type="manufacturer", country="NG"
            )
            contract = Contract.objects.create(
                program_id=PROGRAM, supplier=supplier, commodity=commodity, buyer_of_record="programme_org"
            )
            shipment = Shipment.objects.create(contract=contract, reference="SHP-1")
            Shipment.objects.filter(pk=shipment.pk).update(created_at=timezone.now() - timedelta(days=40))
            shipment.refresh_from_db()
            line = ShipmentLine.objects.create(shipment=shipment, quantity=600, quantity_unit="carton")

        backfill(django_apps, None)

        rev = Revision.objects.get(
            content_type=ContentType.objects.get_for_model(ShipmentLine), object_id=str(line.pk), action="create"
        )
        assert rev.recorded_at == shipment.created_at
        assert rev.program_id == PROGRAM

    def test_a_many_to_many_link_row_is_backfilled_from_its_owner(self):
        """An invitation (a `Tender.invited_orgs` through row) gets a create
        revision, stamped with its tender's `created_at` and scoped to its program."""
        with capture_suspended():
            tender = Tender.objects.create(program_id=PROGRAM, label="Restricted tender")
            Tender.objects.filter(pk=tender.pk).update(created_at=timezone.now() - timedelta(days=10))
            tender.refresh_from_db()
            org = LabsOrg.objects.create(slug="northwind", name="Northwind Foods")
            tender.invited_orgs.add(org)
        through = Tender.invited_orgs.through
        link = through.objects.get(tender=tender, labsorg=org)

        backfill(django_apps, None)

        # By name, not `get_for_model`: a through model's content type is made
        # on first use and may not survive in the per-process cache.
        rev = Revision.objects.get(
            content_type__app_label="supply_chain",
            content_type__model=through._meta.model_name,
            object_id=str(link.pk),
            action="create",
        )
        assert rev.recorded_at == tender.created_at
        assert rev.program_id == PROGRAM
        assert rev.changes["labsorg_id"] == [None, org.pk]

    def test_a_row_with_no_resolvable_stamp_falls_back_to_the_run_time(self):
        run_time = timezone.now()

        class Orphan:
            created_at = None
            shipment = None

        assert backfill_module._stamp(Orphan(), "ShipmentLine", run_time) == run_time


class TestBackfillAvoidsNPlusOneOnViaChains:
    """`program.PATHS` resolves several models by walking a foreign key (or a
    chain of them); without `select_related`, each hop is one extra query
    PER ROW. These pin the migration's `SELECT_RELATED` map: it stays
    complete (every `_via`-resolved model has an entry) and it builds the
    right join for a single hop, a multi-hop chain, and a multi-alternative
    one.
    """

    def test_every_via_resolved_model_has_a_select_related_entry(self):
        # `_via(...)` always returns a closure literally named `resolve`;
        # `_direct` and `_scope` are named for themselves. That is the
        # cheapest reliable way to ask "does this model's PATHS entry walk a
        # foreign key" without re-deriving the chains from the closure itself
        # (see the migration module's own comment on why not).
        via_models = {name for name, resolve in program.PATHS.items() if resolve.__name__ == "resolve"}

        assert via_models == set(backfill_module.SELECT_RELATED.keys())

    def test_a_single_hop_chain_is_covered(self):
        from connect_labs.supply_chain.models import Quote

        queryset = backfill_module._queryset_for(Quote)

        assert queryset.query.select_related == {"tender": {}}

    def test_a_two_hop_chain_is_covered(self):
        from connect_labs.supply_chain.models import AwardApproval

        queryset = backfill_module._queryset_for(AwardApproval)

        assert queryset.query.select_related == {"award": {"tender": {}}}

    def test_a_multi_alternative_chain_covers_every_alternative(self):
        """`Receipt` tries `contract`, then `shipment`, then `supply_point`
        (program.py) -- `program_of` may follow any one of the three
        depending on which is set, so all three need pre-joining.
        """
        from connect_labs.supply_chain.models import Receipt

        queryset = backfill_module._queryset_for(Receipt)

        assert queryset.query.select_related == {
            "contract": {},
            "shipment": {"contract": {}},
            "supply_point": {},
        }

    def test_a_direct_or_scope_resolved_model_gets_no_select_related(self):
        queryset = backfill_module._queryset_for(Tender)

        assert queryset.query.select_related is False


@pytest.mark.usefixtures("registered_synthetic")
class TestPurgeWipesHistory:
    def test_purge_deletes_the_programs_revisions_and_calls_but_not_anothers(self):
        synthetic_access = SupplyDataAccess(program_id=SYNTHETIC_PROGRAM, caller=SYSTEM)
        other_access = SupplyDataAccess(program_id=OTHER_PROGRAM, caller=SYSTEM)

        call_operation("tender_create", synthetic_access, _tender_payload("Synthetic tender"), channel="command")
        call_operation("tender_create", other_access, _tender_payload("Someone else's tender"), channel="command")

        assert Revision.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
        assert OperationCall.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()

        synthetic_access.purge()

        assert not Revision.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
        assert not OperationCall.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
        # The other program's history is untouched.
        assert Revision.objects.filter(program_id=OTHER_PROGRAM).exists()
        assert OperationCall.objects.filter(program_id=OTHER_PROGRAM).exists()

    def test_purge_does_not_leave_delete_revisions_behind(self):
        """Every row purge() deletes runs under `capture_suspended()`, so the
        deletes themselves never produce `delete` Revisions that would then
        need cleaning up too -- there is nothing left at all for the
        program, not merely no `create` rows.
        """
        access = SupplyDataAccess(program_id=SYNTHETIC_PROGRAM, caller=SYSTEM)
        call_operation("tender_create", access, _tender_payload(), channel="command")

        access.purge()

        assert Revision.objects.filter(program_id=SYNTHETIC_PROGRAM).count() == 0

    def test_purge_clears_an_org_level_revision_written_by_its_own_call(self):
        """`supplier_create` writes a `SupplierProfile` -- organisation-level,
        shared across every program that buys from it (program.SKIPPED_MODELS)
        -- so that Revision's OWN `program_id` is None even though the call
        that wrote it was scoped to this program. Before the `call__program_id`
        fallback, purge deleted the program's OWN revisions, then hit
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

    def test_purge_leaves_another_programs_revision_on_a_shared_org_level_row(self):
        """The hard case: ONE shared `SupplierProfile` row carries a revision
        from program A's call AND a revision from program B's call (two
        programs buying from the same company). Purging A must take only
        A's revision and OperationCall off that shared row -- B's must
        survive, and so must the row itself.
        """
        from connect_labs.labs.synthetic.models import SyntheticOpportunity

        SyntheticOpportunity.objects.create(
            opportunity_id=OTHER_PROGRAM,
            program_id=OTHER_PROGRAM,
            labs_only=True,
            enabled=True,
            label="the other program",
            allowed_domains=["dimagi.com"],
        )
        access_a = SupplyDataAccess(program_id=SYNTHETIC_PROGRAM, caller=SYSTEM)
        access_b = SupplyDataAccess(program_id=OTHER_PROGRAM, caller=SYSTEM)

        call_operation(
            "supplier_create",
            access_a,
            {"data": {"name": "Northwind Foods", "type": "manufacturer", "country": "NG"}},
            channel="command",
        )
        call_operation(
            "supplier_create",
            access_b,
            # Same company, by exact name -- `find_or_mint_supplier_org`
            # resolves to the SAME organisation, and this fact (a blank
            # field A never gave) makes `fill_profile` write a SECOND
            # revision on that SAME profile, attributed to B's call.
            {"data": {"name": "Northwind Foods", "website": "https://northwind.example"}},
            channel="command",
        )
        profile_ct = ContentType.objects.get_for_model(SupplierProfile)
        b_revisions = Revision.objects.filter(content_type=profile_ct, call__program_id=OTHER_PROGRAM)
        assert b_revisions.exists()
        b_revision_ids = set(b_revisions.values_list("pk", flat=True))
        profile_id = b_revisions.first().object_id

        access_a.purge()

        assert not Revision.objects.filter(content_type=profile_ct, call__program_id=SYNTHETIC_PROGRAM).exists()
        assert not OperationCall.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
        # B's revisions on the shared row, and B's call, survive untouched.
        assert set(Revision.objects.filter(pk__in=b_revision_ids).values_list("pk", flat=True)) == b_revision_ids
        assert OperationCall.objects.filter(program_id=OTHER_PROGRAM).exists()
        # The shared row itself was never in scope for either purge.
        assert SupplierProfile.objects.filter(pk=profile_id).exists()

    def test_purge_is_atomic_a_failure_after_the_data_deletes_leaves_everything(self):
        """If the history deletes blow up after the data deletes have
        already run, the whole purge must roll back together -- not leave
        supply rows gone with their history still standing, or the reverse.
        """
        access = SupplyDataAccess(program_id=SYNTHETIC_PROGRAM, caller=SYSTEM)
        call_operation("tender_create", access, _tender_payload(), channel="command")
        assert Tender.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()

        with (
            patch.object(Revision.objects, "filter", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            access.purge()

        assert Tender.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
        assert Revision.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
        assert OperationCall.objects.filter(program_id=SYNTHETIC_PROGRAM).exists()
