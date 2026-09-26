"""Write context and revision capture.

THIS REPOSITORY IS PUBLIC. Every id, name and figure below is invented.

Exercises real ORM saves and deletes -- inside and outside a write context --
against the `pre_save`/`post_save`/`pre_delete`/`post_delete` receivers wired
up in `history/capture.py`, and the `write_context`/`seed_overrides`/
`capture_suspended` contextvars in `history/context.py`.
"""

from decimal import Decimal

import pytest

from connect_labs.supply_chain.history.context import capture_suspended, seed_overrides, write_context
from connect_labs.supply_chain.history.models import OperationCall, Revision
from connect_labs.supply_chain.models import Commodity, Quote, Supplier, Tender

pytestmark = pytest.mark.django_db

PROGRAM = 10999
SCOPE = f"prog:{PROGRAM}"

# A program id registered as labs-only for `seed_overrides` tests.
SYNTHETIC_PROGRAM = 20999
REAL_PROGRAM = 263


def _tender(**overrides):
    fields = {"program_id": PROGRAM, "label": "Tender R2"}
    fields.update(overrides)
    return Tender.objects.create(**fields)


@pytest.fixture
def registered_synthetic():
    """Register `SYNTHETIC_PROGRAM` as a labs-only scope.

    `seed_overrides` gates on `scopes.is_synthetic`, which asks
    `labs/synthetic` whether a labs-only opportunity is registered under the
    program -- a bare id in the reserved range is not enough. See
    `test_data_access.py::registered_synthetic`, whose pattern this reuses.
    """
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    return SyntheticOpportunity.objects.create(
        opportunity_id=SYNTHETIC_PROGRAM,
        program_id=SYNTHETIC_PROGRAM,
        labs_only=True,
        enabled=True,
        label="history capture tests",
        allowed_domains=["dimagi.com"],
    )


def test_create_and_update_write_revisions_with_diff():
    call = OperationCall.objects.create(program_id=PROGRAM, operation="tender_create", channel="web")
    with write_context(call):
        tender = _tender(label="R2")
        tender.label = "Round 2"
        tender.save()
    revs = list(Revision.objects.filter(object_id=str(tender.pk)).order_by("id"))
    assert [r.action for r in revs] == ["create", "update"]
    assert revs[1].changes == {"label": ["R2", "Round 2"]}
    assert all(r.call_id == call.pk and r.program_id == PROGRAM for r in revs)
    assert "updated_at" not in revs[1].changes
    assert "created_at" not in revs[0].changes


def test_save_outside_any_operation_is_still_captured():
    tender = _tender(label="x")
    rev = Revision.objects.get(object_id=str(tender.pk))
    assert rev.call is None and rev.action == "create"


def test_noop_save_writes_nothing():
    tender = _tender(label="x")
    tender.save()
    assert Revision.objects.filter(object_id=str(tender.pk)).count() == 1


def test_cascade_delete_writes_child_revisions_with_program():
    """Deleting a tender cascades to its quotes; both get a `delete` revision.

    The quote resolves its program through `program.PATHS["Quote"]`, which
    walks the (still-live, at pre_delete time) tender -- so it must land on
    the same program the tender itself did.
    """
    commodity = Commodity.objects.create(
        scope_key=SCOPE, slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150
    )
    supplier = Supplier.objects.enrol(scope_key=SCOPE, name="Northwind Foods", type="manufacturer", country="NG")
    tender = _tender(label="Tender to delete")
    quote = Quote.objects.create(
        tender=tender, supplier=supplier, commodity=commodity, as_quoted_amount=Decimal("50.00")
    )
    quote_id = str(quote.pk)
    tender_id = tender.pk  # Django clears .pk on the instance once delete() returns.

    tender.delete()

    quote_deletes = Revision.objects.filter(object_id=quote_id, action="delete")
    assert quote_deletes.count() == 1
    rev = quote_deletes.get()
    assert rev.program_id == PROGRAM
    assert rev.changes["as_quoted_amount"] == "50.0000"
    assert rev.changes["tender_id"] == tender_id


def test_capture_suspended_writes_nothing():
    with capture_suspended():
        tender = _tender(label="x")
    assert not Revision.objects.filter(object_id=str(tender.pk)).exists()


def test_seed_overrides_refused_for_real_program():
    with pytest.raises(PermissionError):
        with seed_overrides(REAL_PROGRAM, channel="mcp"):
            pass


def test_seed_overrides_stamps_recorded_at_on_a_synthetic_program(registered_synthetic):
    import datetime

    backdated = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    with seed_overrides(SYNTHETIC_PROGRAM, actor=None, channel="command", recorded_at=backdated):
        tender = Tender.objects.create(program_id=SYNTHETIC_PROGRAM, label="Backdated tender")
    rev = Revision.objects.get(object_id=str(tender.pk))
    assert rev.recorded_at == backdated


class TestManyToManyLinkRows:
    """`add()` bulk-inserts link rows with no `post_save`, so `m2m_changed`
    records them; `remove()`/`clear()` go through a queryset delete whose
    per-row delete signals already record them -- exactly once each."""

    @pytest.fixture
    def orgs(self):
        from connect_labs.labs.models import LabsOrg

        return [LabsOrg.objects.create(slug=f"org-{n}", name=f"Org {n}") for n in (1, 2)]

    def _link_revs(self, action):
        return Revision.objects.filter(
            content_type__app_label="supply_chain", content_type__model="tender_invited_orgs", action=action
        )

    def test_add_writes_one_create_revision_per_new_link_with_program(self, orgs):
        tender = _tender()
        tender.invited_orgs.add(*orgs)
        tender.invited_orgs.add(orgs[0])  # already there: nothing new

        revs = self._link_revs("create")
        assert revs.count() == 2
        assert {r.changes["labsorg_id"][1] for r in revs} == {o.pk for o in orgs}
        assert all(r.program_id == PROGRAM and r.changes["tender_id"] == [None, tender.pk] for r in revs)

    def test_add_from_the_other_side_is_recorded_the_same_way(self, orgs):
        tender = _tender()
        orgs[0].tenders_invited_to.add(tender)

        rev = self._link_revs("create").get()
        assert rev.program_id == PROGRAM
        assert (rev.changes["tender_id"][1], rev.changes["labsorg_id"][1]) == (tender.pk, orgs[0].pk)

    def test_remove_and_clear_write_one_delete_revision_per_link(self, orgs):
        tender = _tender()
        tender.invited_orgs.add(*orgs)
        tender.invited_orgs.remove(orgs[0])
        assert self._link_revs("delete").count() == 1
        tender.invited_orgs.clear()

        revs = self._link_revs("delete")
        assert revs.count() == 2
        assert all(r.program_id == PROGRAM for r in revs)

    def test_a_cascade_records_the_link_rows_it_takes(self, orgs):
        """Deleting a tender, or an invited organisation, drops link rows the
        collector deletes without signals."""
        tender = _tender()
        tender.invited_orgs.add(*orgs)
        org_pks = {o.pk for o in orgs}  # delete() clears .pk on the instance
        orgs[1].delete()
        assert self._link_revs("delete").count() == 1
        tender.delete()

        revs = self._link_revs("delete")
        assert revs.count() == 2
        assert all(r.program_id == PROGRAM for r in revs)
        assert {r.changes["labsorg_id"] for r in revs} == org_pks

    def test_add_under_capture_suspended_writes_nothing(self, orgs):
        tender = _tender()
        with capture_suspended():
            tender.invited_orgs.add(*orgs)
        assert not self._link_revs("create").exists()


class TestReceiversAreConnectedPerSender:
    """Capture must not listen to models it does not track.

    A `pre_delete` receiver connected with no sender makes every model in the
    project look like it has delete listeners, and Django then refuses to
    fast-delete anything: a visit-cache purge or a retention sweep loads every
    row it deletes. Only supply models, and the non-supply models a supply
    link row points at, may have one.
    """

    def test_a_labs_model_outside_supply_can_still_be_fast_deleted(self):
        from django.db.models.deletion import Collector
        from django.db.models.signals import pre_delete

        from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

        assert not pre_delete.has_listeners(RawVisitCache)
        assert Collector(using="default").can_fast_delete(RawVisitCache.objects.all())

    def test_supply_models_and_link_targets_are_listened_to(self):
        from django.db.models.signals import m2m_changed, pre_delete

        from connect_labs.labs.models import LabsOrg

        assert pre_delete.has_listeners(Tender)
        assert pre_delete.has_listeners(LabsOrg)
        assert m2m_changed.has_listeners(Tender.invited_orgs.through)
        assert not pre_delete.has_listeners(OperationCall)
        assert not pre_delete.has_listeners(Revision)
