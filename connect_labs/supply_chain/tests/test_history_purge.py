"""Purging a program wipes its history too: revisions and operation calls.

THIS REPOSITORY IS PUBLIC. Every id, name and figure below is invented.

Covers the history-purging half of `SupplyDataAccess.purge()`. See
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.5.
(This file also used to test migration 0033_history_backfill's one-time
backfill; that migration is applied in production, and its tests were removed
with the rest of the applied-migration tests on 2026-10-14.)
"""

from unittest.mock import patch

import pytest
from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.models import OperationCall, Revision
from connect_labs.supply_chain.models import SupplierProfile, Tender
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

OTHER_PROGRAM = 10996
SYNTHETIC_PROGRAM = 10995


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
