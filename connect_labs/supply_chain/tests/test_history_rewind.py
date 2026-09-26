"""The rewind engine: a program's records as they stood at a past instant.

THIS REPOSITORY IS PUBLIC. Every id, name and figure below is invented.

History is built the realistic way -- through `call_operation`, dated with
`seed_overrides` on a synthetic program -- and each test rewinds inside a
`transaction.atomic()` block it then rolls back, asserting both the rewound
state inside the block and the untouched live state after it. See
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.5.
"""

import datetime
from decimal import Decimal

import pytest
from django.db import transaction

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.history.rewind import _coerce, rewind
from connect_labs.supply_chain.models import Award, Quote, Tender
from connect_labs.supply_chain.operations import call_operation

PROGRAM = 20995
OTHER_PROGRAM = 10995


def _at(month, day=1):
    return datetime.datetime(2026, month, day, 9, 0, tzinfo=datetime.UTC)


T0, T1, T2 = _at(1), _at(2), _at(3)
BETWEEN_T0_T1 = _at(1, 15)
BETWEEN_T1_T2 = _at(2, 15)


@pytest.fixture
def registered_synthetic():
    """Register `PROGRAM` as labs-only so `seed_overrides` accepts it (see test_history_capture.py)."""
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    return SyntheticOpportunity.objects.create(
        opportunity_id=PROGRAM,
        program_id=PROGRAM,
        labs_only=True,
        enabled=True,
        label="history rewind tests",
        allowed_domains=["dimagi.com"],
    )


@pytest.fixture
def da(registered_synthetic):
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


def op(da, name, when, **payload):
    with seed_overrides(PROGRAM, channel="command", recorded_at=when):
        return call_operation(name, da, payload)


def at(when):
    """Date a direct ORM write, for shapes no operation produces (a delete)."""
    return seed_overrides(PROGRAM, channel="command", recorded_at=when)


def _quote(da, tender_id, supplier_id, when, amount="50.00"):
    return op(
        da,
        "quote_record",
        when,
        data={
            "tender_id": tender_id,
            "commodity_slug": "rutf",
            "supplier_id": supplier_id,
            "as_quoted_amount": amount,
            "as_quoted_unit": "per_pack",
            "quantity_basis": "600",
            "quantity_basis_unit": "carton",
            "validity_until": "2026-06-30",
            "received_on": "2026-01-01",
            "stated_spec": {"energy_kcal_per_100g": 545, "notes": ["peanut based"]},
        },
    )


@pytest.fixture
def world(da):
    """At T0: a commodity, a supplier, a tender and one quote on it."""
    op(
        da,
        "commodity_upsert",
        T0,
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"},
    )
    supplier = op(da, "supplier_create", T0, data={"name": "Northwind Foods"})
    tender = op(
        da,
        "tender_create",
        T0,
        data={
            "label": "Tender R1",
            "delivery_point": {"city": "Kano"},
            "response_deadline": "2026-03-31",
            "lines": [{"commodity_slug": "rutf", "quantity": "600", "quantity_unit": "carton"}],
        },
    )
    quote = _quote(da, tender["id"], supplier["id"], T0)
    return {"supplier": supplier, "tender": tender, "quote": quote}


def rewound(as_of, check, program_id=PROGRAM):
    """Rewind inside a rolled-back block, run `check` there, and return rewind's count."""
    with transaction.atomic():
        undone = rewind(program_id, as_of)
        check()
        transaction.set_rollback(True)
    return undone


@pytest.mark.django_db
class TestRewind:
    def test_a_field_updated_after_the_instant_shows_its_old_value(self, da, world):
        tender_id = world["tender"]["id"]
        op(da, "tender_update", T1, tender_id=tender_id, data={"label": "Tender R2"})
        op(da, "tender_update", T2, tender_id=tender_id, data={"label": "Tender R3"})

        def label_is(expected):
            return lambda: _assert_equal(Tender.objects.get(pk=tender_id).label, expected)

        assert rewound(BETWEEN_T0_T1, label_is("Tender R1")) == 2
        assert rewound(BETWEEN_T1_T2, label_is("Tender R2")) == 1
        assert Tender.objects.get(pk=tender_id).label == "Tender R3"

    def test_a_row_created_after_the_instant_is_gone_with_its_children(self, da, world):
        later = op(da, "tender_create", T1, data={"label": "Tender late", "delivery_point": {"city": "Kano"}})
        late_quote = _quote(da, later["id"], world["supplier"]["id"], T1, amount="61.00")
        # An award PROTECTs its quote, so undoing oldest-first would refuse.
        award = op(da, "award_create", T2, tender_id=later["id"], quote_id=late_quote["id"], rationale="cheapest")

        def gone():
            assert not Tender.objects.filter(pk=later["id"]).exists()
            assert not Quote.objects.filter(pk=late_quote["id"]).exists()
            assert not Award.objects.filter(pk=award["id"]).exists()
            assert Tender.objects.filter(pk=world["tender"]["id"]).exists()

        rewound(BETWEEN_T0_T1, gone)
        assert Tender.objects.filter(pk=later["id"]).exists()
        assert Quote.objects.filter(pk=late_quote["id"]).exists()
        assert Award.objects.filter(pk=award["id"]).exists()

    def test_a_row_deleted_after_the_instant_is_back_with_its_types(self, da, world):
        quote_id = world["quote"]["id"]
        before = Quote.objects.get(pk=quote_id)
        with at(T1):
            Quote.objects.get(pk=quote_id).delete()

        def back():
            quote = Quote.objects.get(pk=quote_id)
            assert quote.as_quoted_amount == Decimal("50.00") and isinstance(quote.as_quoted_amount, Decimal)
            assert quote.quantity_basis == Decimal("600") and isinstance(quote.quantity_basis, Decimal)
            assert quote.validity_until == datetime.date(2026, 6, 30)
            assert quote.received_on == datetime.date(2026, 1, 1)
            assert quote.stated_spec == {"energy_kcal_per_100g": 545, "notes": ["peanut based"]}
            assert (quote.tender_id, quote.supplier_id, quote.commodity_id) == (
                before.tender_id,
                before.supplier_id,
                before.commodity_id,
            )
            assert quote.superseded_by_id is None and quote.version == 1
            # Capture leaves the timestamps out; they come back from the row's own history.
            assert quote.created_at == T0 and quote.updated_at == T0

        rewound(BETWEEN_T0_T1, back)
        assert not Quote.objects.filter(pk=quote_id).exists()

    def test_a_superseded_quote_is_live_again(self, da, world):
        a_id = world["quote"]["id"]
        b = op(da, "quote_correct", T1, quote_id=a_id, data={"as_quoted_amount": "48.00"}, reason="typo")
        assert Quote.objects.get(pk=a_id).superseded_by_id == b["id"]

        def live_again():
            assert Quote.objects.get(pk=a_id).superseded_by_id is None
            assert not Quote.objects.filter(pk=b["id"]).exists()

        rewound(BETWEEN_T0_T1, live_again)
        assert Quote.objects.get(pk=a_id).superseded_by_id == b["id"]

    def test_a_cascaded_tender_delete_brings_back_the_tender_and_its_quotes(self, da, world):
        tender_id, quote_id = world["tender"]["id"], world["quote"]["id"]
        with at(T1):
            Tender.objects.get(pk=tender_id).delete()

        def back():
            tender = Tender.objects.get(pk=tender_id)
            assert tender.label == "Tender R1"
            assert tender.response_deadline == datetime.date(2026, 3, 31)
            assert tender.lines == [{"commodity_slug": "rutf", "quantity": "600", "quantity_unit": "carton"}]
            assert list(tender.quotes.values_list("pk", flat=True)) == [quote_id]

        rewound(BETWEEN_T0_T1, back)
        assert not Tender.objects.filter(pk=tender_id).exists()

    def test_rewinding_writes_no_revisions(self, da, world):
        op(da, "tender_update", T1, tender_id=world["tender"]["id"], data={"label": "Tender R2"})
        with at(T2):
            Tender.objects.get(pk=world["tender"]["id"]).delete()
        before = Revision.objects.count()

        rewound(BETWEEN_T0_T1, lambda: _assert_equal(Revision.objects.count(), before))

    def test_another_programs_rows_are_untouched(self, da, world):
        other = SupplyDataAccess(program_id=OTHER_PROGRAM, caller=SYSTEM)
        theirs = call_operation(
            "tender_create", other, {"data": {"label": "Theirs", "delivery_point": {"city": "Jos"}}}
        )
        call_operation("tender_update", other, {"tender_id": theirs["id"], "data": {"label": "Theirs v2"}})
        op(da, "tender_update", T1, tender_id=world["tender"]["id"], data={"label": "Tender R2"})

        def only_ours():
            assert Tender.objects.get(pk=theirs["id"]).label == "Theirs v2"
            assert Tender.objects.get(pk=world["tender"]["id"]).label == "Tender R1"

        assert rewound(BETWEEN_T0_T1, only_ours) == 1

    def test_a_revision_whose_model_or_row_is_gone_is_harmless(self, da, world):
        from django.contrib.contenttypes.models import ContentType

        removed = ContentType.objects.create(app_label="supply_chain", model="retiredthing")
        Revision.objects.create(
            program_id=PROGRAM,
            content_type=removed,
            object_id="1",
            action="update",
            changes={"x": [1, 2]},
            recorded_at=T1,
        )
        Revision.objects.create(
            program_id=PROGRAM,
            content_type=ContentType.objects.get_for_model(Tender),
            object_id="987654",
            action="update",
            changes={"label": ["Old", "New"]},
            recorded_at=T1,
        )

        # The retired model is skipped; the update to a missing row touches nothing.
        assert rewound(BETWEEN_T0_T1, lambda: _assert_equal(Tender.objects.filter(pk=987654).exists(), False)) == 1

    def test_nothing_after_the_instant_undoes_nothing(self, da, world):
        assert rewound(T2, lambda: None) == 0


def test_stored_json_values_come_back_as_their_field_types():
    """The DB would coerce a string on write anyway; this pins the in-memory
    types, which is what a revision's value means to anything else reading it."""
    amount = _coerce(Quote, "as_quoted_amount", "50.0000")
    assert amount == Decimal("50.0000") and isinstance(amount, Decimal)
    assert _coerce(Quote, "validity_until", "2026-06-30") == datetime.date(2026, 6, 30)
    assert _coerce(Tender, "opened_at", "2026-02-01T09:00:00Z") == T1
    # A foreign key's attname resolves to the FK field, whose to_python is its target pk's.
    assert _coerce(Quote, "tender_id", "7") == 7
    # A JSONField value round-trips unchanged -- a JSON string is not parsed again.
    assert _coerce(Quote, "stated_spec", {"notes": ["a"]}) == {"notes": ["a"]}
    assert _coerce(Quote, "stated_spec", '{"not": "parsed"}') == '{"not": "parsed"}'
    assert _coerce(Quote, "superseded_by_id", None) is None


@pytest.mark.django_db(transaction=True)
def test_rewind_outside_a_transaction_is_refused():
    with pytest.raises(RuntimeError):
        rewind(PROGRAM, T0)


def _assert_equal(actual, expected):
    assert actual == expected
