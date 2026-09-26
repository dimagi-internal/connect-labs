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
