"""Per-record timelines: what changed on a tender or an order, who told us, and on what evidence.

THIS REPOSITORY IS PUBLIC. Every id, name, address and figure below is invented.

History is built the realistic way -- through `call_operation`, dated and
attributed with `seed_overrides` on a synthetic program (see
test_history_rewind.py) -- and read back through `timeline_for_tender` /
`timeline_for_contract` and through the two detail pages. Design doc
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.4, §4.3.
"""

import datetime

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.history.labels import actor_label
from connect_labs.supply_chain.history.models import OperationCall
from connect_labs.supply_chain.history.timeline import (
    contract_scope_revisions,
    tender_scope_revisions,
    timeline_for_contract,
    timeline_for_tender,
)
from connect_labs.supply_chain.operations import call_operation

PROGRAM = 20997
# Measured, plus a small margin: a query per line would blow straight past these.
QUERY_BOUND_CONTRACT = (
    21  # measured 18: payments are found by their order too, an advance having no invoice; what we owe on it, one more
)
QUERY_BOUND_TENDER = 27  # measured 23, what we owe on the round included; +1 the asked Incoterm (DDD 003 b7)


def _at(month, day, hour=9):
    return datetime.datetime(2026, month, day, hour, 0, tzinfo=datetime.UTC)


AUG_3, AUG_20, AUG_28 = _at(8, 3), _at(8, 20), _at(8, 28)
EMAIL = "Hi Sophie, the RUTF consignment will now arrive on 5 September. Regards, Northwind dispatch"


@pytest.fixture
def registered_synthetic():
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    return SyntheticOpportunity.objects.create(
        opportunity_id=PROGRAM,
        program_id=PROGRAM,
        labs_only=True,
        enabled=True,
        label="history timeline tests",
        allowed_domains=["dimagi.com"],
    )


@pytest.fixture
def da(registered_synthetic):
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def sophie(django_user_model):
    return django_user_model.objects.create_user(
        username="sophie", email="sophie@example.org", name="Sophie Bello", password="x"
    )


@pytest.fixture
def ace(django_user_model):
    return django_user_model.objects.create_user(username="ace", email="ace@dimagi-ai.com", password="x")


def op(da, name, when, *, channel="command", actor=None, source=None, **payload):
    """One recorded write, dated `when`, through `channel`, by `actor`."""
    if source is not None:
        payload["source"] = source
    with seed_overrides(PROGRAM, actor=actor, recorded_at=when):
        return call_operation(name, da, payload, channel=channel)


@pytest.fixture
def base(da):
    op(
        da,
        "commodity_upsert",
        AUG_3,
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"},
    )
    supplier = op(da, "supplier_create", AUG_3, data={"name": "Northwind Foods"})
    us = op(da, "org_upsert", AUG_3, data={"slug": "timeline-us", "name": "The program"})
    tender = op(
        da,
        "tender_create",
        AUG_3,
        data={
            "label": "Tender Harmattan",
            "delivery_point": {"city": "Kano"},
            "response_deadline": "2026-09-30",
            "lines": [{"commodity_slug": "rutf", "quantity": "600", "quantity_unit": "carton"}],
        },
    )
    return {"supplier": supplier, "us": us, "tender": tender}


@pytest.fixture
def order(da, base, sophie, ace):
    """An order whose shipment an agent recorded from an email, and Sophie then moved."""
    contract = op(
        da,
        "contract_create",
        AUG_3,
        data={
            "supplier_id": base["supplier"]["id"],
            "commodity_slug": "rutf",
            "buyer_of_record": "programme_org",
            "buyer_org_id": base["us"]["id"],
            "reference": "PO-HARMATTAN",
            "quantity": "600",
            "quantity_unit": "carton",
            "status": "placed",
            "source": "we_recorded",
        },
    )
    shipment = op(
        da,
        "shipment_record",
        AUG_20,
        channel="mcp",
        actor=ace,
        source={"ref": "<msg-4411@northwind.example>", "excerpt": EMAIL},
        data={
            "contract_id": contract["id"],
            "reference": "SH-1",
            "expected_on": "2026-09-05",
            "source": "supplier_reported",
        },
    )
    op(
        da,
        "shipment_update",
        AUG_28,
        channel="web",
        actor=sophie,
        shipment_id=shipment["id"],
        data={"contract_id": contract["id"], "expected_on": "2026-09-19", "source": "supplier_reported"},
    )
    return {"contract": contract, "shipment": shipment}


def _quote(da, tender_id, supplier_id, when, **kwargs):
    return op(
        da,
        "quote_record",
        when,
        data={
            "tender_id": tender_id,
            "commodity_slug": "rutf",
            "supplier_id": supplier_id,
            "as_quoted_amount": "42.50",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "600",
            "quantity_basis_unit": "carton",
            "received_on": "2026-08-20",
        },
        **kwargs,
    )


@pytest.mark.django_db
class TestContractTimeline:
    def test_newest_first_with_the_eta_change_on_top(self, order, sophie):
        entries = timeline_for_contract(order["contract"]["id"], program_id=PROGRAM)

        assert entries[0].sentence == "ETA 5 Sep → 19 Sep"
        assert entries[0].actor == "Sophie Bello"
        assert entries[0].is_ai is False
        assert [e.when for e in entries] == sorted((e.when for e in entries), reverse=True)

    def test_the_agent_create_carries_its_label_and_the_email(self, order):
        entries = timeline_for_contract(order["contract"]["id"], program_id=PROGRAM)
        created = next(e for e in entries if e.sentence.startswith("Shipment recorded"))

        assert created.actor == "ACE (agent)"
        assert created.is_ai is True
        assert created.excerpt == EMAIL
        assert created.source_ref == "<msg-4411@northwind.example>"
        assert "ETA 5 Sep" in created.sentence

    def test_the_order_itself_is_on_its_own_timeline(self, order):
        sentences = [e.sentence for e in timeline_for_contract(order["contract"]["id"], program_id=PROGRAM)]
        assert sentences[-1].startswith("Order recorded")
        assert "PO-HARMATTAN" in sentences[-1]

    def test_until_leaves_out_what_happened_after_that_day(self, order):
        entries = timeline_for_contract(order["contract"]["id"], program_id=PROGRAM, until=datetime.date(2026, 8, 27))

        assert "ETA 5 Sep → 19 Sep" not in [e.sentence for e in entries]
        assert any(e.sentence.startswith("Shipment recorded") for e in entries)

    def test_until_includes_the_whole_of_that_day(self, order):
        entries = timeline_for_contract(order["contract"]["id"], program_id=PROGRAM, until=datetime.date(2026, 8, 28))
        assert entries[0].sentence == "ETA 5 Sep → 19 Sep"

    def test_another_orders_shipments_are_not_on_this_timeline(self, da, order, base):
        other = op(
            da,
            "contract_create",
            AUG_3,
            data={
                "supplier_id": base["supplier"]["id"],
                "commodity_slug": "rutf",
                "buyer_of_record": "programme_org",
                "buyer_org_id": base["us"]["id"],
                "reference": "PO-OTHER",
                "source": "we_recorded",
            },
        )
        op(
            da,
            "shipment_record",
            AUG_20,
            data={"contract_id": other["id"], "reference": "SH-OTHER", "source": "we_recorded"},
        )
        sentences = " ".join(e.sentence for e in timeline_for_contract(order["contract"]["id"], program_id=PROGRAM))
        assert "SH-OTHER" not in sentences
        assert "PO-OTHER" not in sentences

    def test_line_rows_fold_into_the_shipment_they_arrived_with(self, da, order):
        op(
            da,
            "shipment_record",
            AUG_20,
            data={
                "contract_id": order["contract"]["id"],
                "reference": "SH-2",
                "expected_on": "2026-09-12",
                "source": "we_recorded",
                "lines": [
                    {"batch": "B1", "quantity": "300", "quantity_unit": "carton"},
                    {"batch": "B2", "quantity": "1", "quantity_unit": "carton"},
                ],
            },
        )
        sentences = [e.sentence for e in timeline_for_contract(order["contract"]["id"], program_id=PROGRAM)]

        assert "Shipment recorded: SH-2, ETA 12 Sep — 300 cartons, batch B1; 1 carton, batch B2" in sentences
        assert not [s for s in sentences if s.startswith("Shipment line")]

    def test_a_line_added_by_a_later_call_keeps_its_own_line(self, da, order):
        from connect_labs.supply_chain.models import ShipmentLine

        with seed_overrides(PROGRAM, recorded_at=AUG_28):
            ShipmentLine.objects.create(
                shipment_id=order["shipment"]["id"], batch="B9", quantity="5", quantity_unit="carton"
            )
        sentences = [e.sentence for e in timeline_for_contract(order["contract"]["id"], program_id=PROGRAM)]
        assert "Shipment line recorded: 5 cartons, batch B9" in sentences

    def test_a_save_lines_save_again_handler_is_one_line(self, da, order):
        """Shipment saved, its lines, then the shipment again: one call, one shipment line."""
        from django.contrib.contenttypes.models import ContentType

        from connect_labs.supply_chain.history.models import Revision
        from connect_labs.supply_chain.models import Shipment, ShipmentLine

        call = OperationCall.objects.create(operation="shipment_record", channel="web", program_id=PROGRAM)
        shipment_type = ContentType.objects.get_for_model(Shipment)
        common = dict(call=call, program_id=PROGRAM, recorded_at=AUG_28)
        Revision.objects.create(
            content_type=shipment_type,
            object_id="888888",
            action="create",
            changes={"contract_id": [None, order["contract"]["id"]], "reference": [None, "SH-9"]},
            **common,
        )
        Revision.objects.create(
            content_type=ContentType.objects.get_for_model(ShipmentLine),
            object_id="777777",
            action="create",
            changes={"shipment_id": [None, 888888], "quantity": [None, "40"], "quantity_unit": [None, "carton"]},
            **common,
        )
        Revision.objects.create(
            content_type=shipment_type,
            object_id="888888",
            action="update",
            changes={"expected_on": [None, "2026-10-02"]},
            **common,
        )
        sentences = [e.sentence for e in timeline_for_contract(order["contract"]["id"], program_id=PROGRAM)]

        assert sentences[0] == "Shipment recorded: SH-9, ETA 2 Oct — 40 cartons"
        assert not [s for s in sentences if s.startswith("ETA") and "2 Oct" in s]

    def test_free_text_reads_verbatim_and_codes_read_as_words(self, da, order):
        op(
            da,
            "contract_update",
            AUG_28,
            contract_id=order["contract"]["id"],
            data={"reference": "PO_7", "status": "part_received"},
        )
        top = timeline_for_contract(order["contract"]["id"], program_id=PROGRAM)[0].sentence
        assert "Reference PO-HARMATTAN → PO_7" in top
        assert "Status placed → part received" in top

    def test_the_whole_timeline_is_a_bounded_number_of_queries(self, da, order, base, django_assert_max_num_queries):
        for n in range(3):
            op(
                da,
                "shipment_record",
                AUG_20,
                data={"contract_id": order["contract"]["id"], "reference": f"SH-B{n}", "source": "we_recorded"},
            )
        with django_assert_max_num_queries(QUERY_BOUND_CONTRACT):
            entries = timeline_for_contract(order["contract"]["id"], program_id=PROGRAM)
        assert len(entries) >= 5

    def test_program_is_required(self, order):
        with pytest.raises(TypeError):
            timeline_for_contract(order["contract"]["id"])
        with pytest.raises(TypeError):
            contract_scope_revisions(order["contract"]["id"])

    def test_the_scope_is_one_query_set_not_one_per_child(self, order, django_assert_max_num_queries):
        with django_assert_max_num_queries(20):
            revisions = list(contract_scope_revisions(order["contract"]["id"], program_id=PROGRAM))
        assert revisions


@pytest.mark.django_db
class TestTenderTimeline:
    def test_a_quote_reads_as_its_price_basis_and_pack(self, da, base):
        _quote(da, base["tender"]["id"], base["supplier"]["id"], AUG_20)
        entries = timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)

        quote = next(e for e in entries if e.sentence.startswith("Quote recorded"))
        assert quote.sentence.startswith("Quote recorded: USD 42.50 per carton (basis not specified)")
        assert "Northwind Foods" in quote.sentence

    def test_an_ai_entered_quote_offers_correct_and_void(self, da, base, sophie):
        quote = _quote(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, channel="mcp", actor=sophie)
        entry = next(
            e for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM) if e.sentence.startswith("Quote")
        )

        assert entry.actor == "via AI · Sophie"
        assert entry.correct_url == reverse("supply_chain:procurement_quote_correct", args=[quote["id"]])
        assert entry.void_url == reverse("supply_chain:procurement_quote_void", args=[quote["id"]])

    def test_a_quote_typed_in_on_the_web_offers_neither(self, da, base, sophie):
        _quote(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, channel="web", actor=sophie)
        entry = next(
            e for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM) if e.sentence.startswith("Quote")
        )

        assert entry.correct_url is None
        assert entry.void_url is None

    def test_a_voided_quote_offers_neither(self, da, base, sophie):
        quote = _quote(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, channel="mcp", actor=sophie)
        op(da, "quote_void", AUG_28, quote_id=quote["id"], reason="duplicate of an earlier email")
        entries = timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)

        assert entries[0].sentence == "Voided: duplicate of an earlier email"
        assert entries[0].subject == "Quote · Northwind Foods"
        assert all(e.correct_url is None and e.void_url is None for e in entries)

    def test_as_of_mode_never_offers_correct_or_void(self, da, base, sophie):
        _quote(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, channel="mcp", actor=sophie)
        entries = timeline_for_tender(base["tender"]["id"], program_id=PROGRAM, until=datetime.date(2026, 8, 30))
        assert all(e.correct_url is None for e in entries)

    def test_a_deleted_outreach_still_appears(self, da, base):
        outreach = op(
            da,
            "outreach_log",
            AUG_3,
            data={"tender_id": base["tender"]["id"], "supplier_id": base["supplier"]["id"], "sent_on": "2026-08-03"},
        )
        op(da, "outreach_delete", AUG_20, outreach_id=outreach["id"], reason="never actually sent")
        sentences = [e.sentence for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)]

        assert sentences[0] == "Outreach removed: Northwind Foods"
        assert any(s.startswith("Outreach recorded: Northwind Foods") for s in sentences)

    @pytest.mark.parametrize(
        "response_kind, expected",
        [
            ("quote", "Replied with a quote"),
            ("declined", "Declined to quote"),
            ("needs_info", "Replied asking for more information"),
            ("no_reply", "Marked as no reply"),
        ],
    )
    def test_a_reply_reads_as_a_program_manager_would_say_it(self, da, base, response_kind, expected):
        """Not "Responded; Response kind: quote" -- one clause, in plain words."""
        outreach = op(
            da,
            "outreach_log",
            AUG_3,
            data={"tender_id": base["tender"]["id"], "supplier_id": base["supplier"]["id"], "sent_on": "2026-08-03"},
        )
        op(
            da,
            "outreach_update",
            AUG_20,
            outreach_id=outreach["id"],
            data={"responded": True, "response_kind": response_kind},
        )
        sentences = [e.sentence for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)]

        assert expected in sentences
        assert not any("Response kind" in s for s in sentences)
        assert not any(s == "Responded" for s in sentences)

    def test_responded_with_no_response_kind_still_reads_as_responded(self, da, base):
        outreach = op(
            da,
            "outreach_log",
            AUG_3,
            data={"tender_id": base["tender"]["id"], "supplier_id": base["supplier"]["id"], "sent_on": "2026-08-03"},
        )
        op(da, "outreach_update", AUG_20, outreach_id=outreach["id"], data={"responded": True})
        sentences = [e.sentence for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)]

        assert "Responded" in sentences

    def test_a_status_change_names_both_states(self, da, base):
        op(da, "tender_open", AUG_20, tender_id=base["tender"]["id"])
        entries = timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)
        assert any(e.sentence.startswith("Status draft → open") for e in entries)

    def test_another_tenders_quotes_are_not_on_this_timeline(self, da, base):
        other = op(
            da,
            "tender_create",
            AUG_3,
            data={"label": "Tender Other", "lines": [{"commodity_slug": "rutf", "quantity": "1"}]},
        )
        _quote(da, other["id"], base["supplier"]["id"], AUG_20)
        assert not [
            e for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM) if e.sentence.startswith("Quote")
        ]

    def test_an_invitation_and_its_withdrawal_name_the_organisation(self, da, base):
        from connect_labs.supply_chain.models import Supplier

        org_id = Supplier.objects.get(pk=base["supplier"]["id"]).org_id
        op(da, "tender_invite_org", AUG_20, tender_id=base["tender"]["id"], org_id=org_id)
        op(da, "tender_uninvite_org", AUG_28, tender_id=base["tender"]["id"], org_id=org_id)
        sentences = [e.sentence for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)]

        assert sentences[:2] == ["Invitation to Northwind Foods withdrawn", "Invited Northwind Foods"]

    def test_one_calls_create_and_update_of_one_record_read_as_one_line(self, da, base):
        """The handler's second save is bookkeeping to a reader, not a second event."""
        from django.contrib.contenttypes.models import ContentType

        from connect_labs.supply_chain.history.models import Revision
        from connect_labs.supply_chain.models import Tender

        call = OperationCall.objects.create(operation="tender_create", channel="web", program_id=PROGRAM)
        common = dict(
            call=call,
            program_id=PROGRAM,
            content_type=ContentType.objects.get_for_model(Tender),
            object_id="999999",
            recorded_at=AUG_20,
        )
        Revision.objects.create(action="create", changes={"label": [None, "Draft name"]}, **common)
        Revision.objects.create(action="update", changes={"label": ["Draft name", "Tender Sahel"]}, **common)
        from connect_labs.supply_chain.history.timeline import _timeline

        entries = _timeline(Revision.objects.filter(object_id="999999").order_by("-recorded_at", "-id"), None)
        assert [e.sentence for e in entries] == ["Tender recorded: Tender Sahel"]

    def test_the_whole_timeline_is_a_bounded_number_of_queries(self, da, base, django_assert_max_num_queries):
        for name in ("Harmattan Mills", "Sahel Nutrition", "Lakeside Foods"):
            supplier = op(da, "supplier_create", AUG_3, data={"name": name})
            _quote(da, base["tender"]["id"], supplier["id"], AUG_20, channel="mcp")
        with django_assert_max_num_queries(QUERY_BOUND_TENDER):
            entries = timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)
        assert len([e for e in entries if e.sentence.startswith("Quote recorded")]) == 3

    def test_scope_revisions_are_this_tenders_only(self, da, base):
        _quote(da, base["tender"]["id"], base["supplier"]["id"], AUG_20)
        revisions = tender_scope_revisions(base["tender"]["id"], program_id=PROGRAM)
        assert {r.program_id for r in revisions} == {PROGRAM}
        assert {r.content_type.model for r in revisions} >= {"tender", "quote"}


PACK_EMAIL = "Each carton holds 150 sachets."

_COMPARABLE = dict(
    freight_basis="included",
    duties_basis="included",
    pack_spec_source="stated_on_quote",
    base_per_pack_stated=150,
    base_unit_grams_stated=92,
)


def _quote_with(da, tender_id, supplier_id, when, extra, **kwargs):
    data = {
        "tender_id": tender_id,
        "commodity_slug": "rutf",
        "supplier_id": supplier_id,
        "as_quoted_amount": "42.50",
        "as_quoted_unit": "per_pack",
        "quantity_basis": "600",
        "quantity_basis_unit": "carton",
        "received_on": "2026-08-20",
        **extra,
    }
    return op(da, "quote_record", when, data=data, **kwargs)


def _correct_pack(da, quote, ace):
    return op(
        da,
        "quote_correct",
        AUG_28,
        channel="mcp",
        actor=ace,
        source={"ref": "<msg-pack@northwind.example>", "excerpt": PACK_EMAIL},
        quote_id=quote["id"],
        data={"pack_spec_source": "stated_on_quote", "base_per_pack_stated": 150},
        reason="the supplier stated the pack",
    )


@pytest.mark.django_db
class TestTenderTimelineReachesTheOrder:
    def test_the_order_placed_from_the_tender_and_its_shipments_are_on_it(self, da, base, ace):
        contract = op(
            da,
            "contract_create",
            AUG_20,
            data={
                "tender_id": base["tender"]["id"],
                "supplier_id": base["supplier"]["id"],
                "commodity_slug": "rutf",
                "buyer_of_record": "programme_org",
                "buyer_org_id": base["us"]["id"],
                "reference": "PO-FROM-TENDER",
                "quantity": "600",
                "quantity_unit": "carton",
                "source": "we_recorded",
            },
        )
        op(
            da,
            "shipment_record",
            AUG_28,
            channel="mcp",
            actor=ace,
            data={
                "contract_id": contract["id"],
                "reference": "SH-7",
                "expected_on": "2026-09-05",
                "source": "supplier_reported",
            },
        )
        op(
            da,
            "contract_create",
            AUG_28,
            data={
                "supplier_id": base["supplier"]["id"],
                "commodity_slug": "rutf",
                "buyer_of_record": "programme_org",
                "buyer_org_id": base["us"]["id"],
                "reference": "PO-ELSEWHERE",
                "quantity": "1",
                "quantity_unit": "carton",
                "source": "we_recorded",
            },
        )
        sentences = [e.sentence for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)]

        assert sentences[0].startswith("Shipment recorded: SH-7")
        assert any(s.startswith("Order recorded: PO-FROM-TENDER") for s in sentences)
        assert not any("PO-ELSEWHERE" in s for s in sentences)
        assert len(sentences) == len(set(sentences))
        assert any(s.startswith("Tender recorded") for s in sentences)


@pytest.mark.django_db
class TestLineHooks:
    def test_each_line_names_the_fields_it_changed(self, order):
        entries = timeline_for_contract(order["contract"]["id"], program_id=PROGRAM)
        assert entries[0].sentence == "ETA 5 Sep → 19 Sep"
        assert entries[0].fields == ("expected_on",)

    def test_a_correction_is_one_line_naming_what_changed(self, da, base, ace):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {}, channel="mcp", actor=ace)
        corrected = _correct_pack(da, quote, ace)
        entries = timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)

        lines = [e for e in entries if e.excerpt == PACK_EMAIL]
        assert len(lines) == 1
        line = lines[0]
        assert line.sentence == "Quote corrected: sachets per carton 150 (was not stated)"
        assert line.subject == "Northwind Foods"
        assert "base_per_pack_stated" in line.fields
        assert line.actor == "ACE (agent)" and line.is_ai
        assert line.correct_url == reverse("supply_chain:procurement_quote_correct", args=[corrected["id"]])
        assert not any("Replaced by a corrected version" in e.sentence for e in entries)

    def test_an_award_says_why(self, da, base, sophie):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE)
        op(
            da,
            "award_create",
            AUG_28,
            channel="web",
            actor=sophie,
            tender_id=base["tender"]["id"],
            quote_id=quote["id"],
            rationale="lowest delivered cost of the comparable offers",
            decided_on="2026-08-28",
        )
        sentences = [e.sentence for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)]
        assert (
            "Award recorded: Northwind Foods, decided 28 Aug — why: lowest delivered cost of the comparable offers"
            in sentences
        )


@pytest.mark.django_db
class TestActorLabel:
    def _call(self, **fields):
        return OperationCall(operation="x", **fields)

    def test_no_call_is_the_system(self):
        assert actor_label(None) == "System"

    def test_the_agent_account(self, ace):
        assert actor_label(self._call(actor=ace, actor_is_agent=True, channel="mcp")) == "ACE (agent)"

    def test_another_agent_account_is_named(self, django_user_model):
        bot = django_user_model.objects.create_user(username="bot", email="bot@example.org", name="Reorder bot")
        assert actor_label(self._call(actor=bot, actor_is_agent=True, channel="api")) == "Reorder bot (agent)"

    def test_a_person_through_an_ai(self, sophie):
        assert actor_label(self._call(actor=sophie, channel="mcp")) == "via AI · Sophie"
        assert actor_label(self._call(actor=sophie, channel="api")) == "via AI · Sophie"

    def test_a_person_on_the_web(self, sophie, django_user_model):
        assert actor_label(self._call(actor=sophie, channel="web")) == "Sophie Bello"
        nameless = django_user_model.objects.create_user(username="kwame", password="x")
        assert actor_label(self._call(actor=nameless, channel="web")) == "kwame"

    def test_a_command_is_an_import(self):
        assert actor_label(self._call(channel="command")) == "Imported"

    def test_an_unknown_channel_is_the_system(self):
        assert actor_label(self._call(channel="replay")) == "System"


class _ProgramContextMiddleware:
    """Stands in for LabsContextMiddleware (left out of the test settings)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.labs_context = {"program_id": PROGRAM} if request.user.is_authenticated else {}
        return self.get_response(request)


@pytest.fixture
def client_in_program(client, monkeypatch, settings, da, sophie):
    """Signed in, in PROGRAM; `_access` stubbed as in test_history_as_of.py (api_views
    included: as-of builds the pages' access from there before it rewinds)."""
    from connect_labs.supply_chain import api_views, form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    settings.MIDDLEWARE = [*settings.MIDDLEWARE, f"{__name__}._ProgramContextMiddleware"]
    for module in ("api_views", "form_views", "views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", lambda request: da)
    client.force_login(sophie)
    return client


@pytest.mark.django_db
class TestPages:
    def test_the_order_page_shows_the_eta_change_and_the_ai_pill(self, client_in_program, order):
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[order["contract"]["id"]])).content
        body = body.decode()

        assert "data-timeline" in body
        # Since batch 8 the new value is bold, and the AI marker is a glyph read out as "AI".
        assert 'ETA 5 Sep → <strong class="font-semibold">19 Sep</strong>' in body
        assert 'aria-label="AI"' in body
        assert "AI assistant" in body  # since DDD 003 batch 7; the agent is in the tooltip
        assert "AI</span><span>via AI" not in body
        assert "<blockquote" in body and "Northwind dispatch" in body

    def test_the_tender_page_offers_correct_live_and_not_as_of(self, client_in_program, da, base, ace):
        quote = _quote(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, channel="mcp", actor=ace)
        url = reverse("supply_chain:procurement_tender_detail", args=[base["tender"]["id"]])
        correct = reverse("supply_chain:procurement_quote_correct", args=[quote["id"]])

        live = client_in_program.get(url).content.decode()
        assert "data-timeline" in live
        assert "AI assistant" in live
        assert "Quote · Northwind Foods · recorded: USD 42.50 per carton" in live
        assert correct in live

        past = client_in_program.get(url, {"as_of": "2026-08-25"})
        assert past.status_code == 200
        body = past.content.decode()
        assert "Quote · Northwind Foods · recorded: USD 42.50 per carton" in body
        assert correct not in body

    def test_a_via_ai_pill_does_not_say_ai_twice(self, client_in_program, da, base, sophie):
        _quote(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, channel="mcp", actor=sophie)
        url = reverse("supply_chain:procurement_tender_detail", args=[base["tender"]["id"]])
        body = client_in_program.get(url).content.decode()

        assert "<span>via AI · Sophie</span>" in body
        assert ">AI</span><span>via AI" not in body

    def test_the_order_page_hooks_find_the_eta_line_and_open_its_source_from_the_badge(self, client_in_program, order):
        import re

        body = client_in_program.get(reverse("supply_chain:order_detail", args=[order["contract"]["id"]])).content
        body = body.decode()

        assert 'data-testid="timeline"' in body
        assert re.search(r'data-testid="revision-line" data-fields="expected_on"', body)
        # The agent's shipment email: one email event (unanswered round 1004 b3), its badge a
        # label in the event's head, its own "Source email" toggle opening the excerpt.
        timeline = body.split('data-testid="timeline"', 1)[1]
        event = re.findall(r'<li data-testid="email-event".*?</ol>\s*</li>', timeline, re.S)[0]
        assert 'data-testid="revision-line"' in event
        assert re.search(r'<span data-testid="actor-badge" data-ai ', event) and "ACE" in event
        details = re.search(r"<details.*?</details>", event, re.S).group(0)
        assert re.search(r'<summary data-testid="source-toggle"', details)
        assert re.search(r'<blockquote data-testid="source-excerpt"[^>]*>' + re.escape(EMAIL), details)
        assert "actor-badge" not in details
        # Sophie's web edit has no excerpt: a badge, and no toggle.
        assert timeline.count('data-testid="actor-badge"') > timeline.count('data-testid="source-toggle"')

    def test_the_comparison_marks_each_quote_and_the_ai_entered_ones(self, client_in_program, da, base, ace, sophie):
        import re

        typed = _quote_with(
            da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE, channel="web", actor=sophie
        )
        other = op(da, "supplier_create", AUG_3, data={"name": "Sahel Nutrition"})
        by_ai = _quote_with(da, base["tender"]["id"], other["id"], AUG_20, {}, channel="mcp", actor=ace)
        url = reverse("supply_chain:procurement_comparison", args=[base["tender"]["id"]])
        body = client_in_program.get(url, {"commodity": "rutf"}).content.decode()

        # Each quote's price carries its source: one typed by a person, one recorded through the AI.
        heads = re.findall(r'data-quote-id="(\d+)" data-testid="grid-quote"', body)
        price = "".join(re.findall(r'<td [^>]*data-fact="price"[^>]*>.*?</td>', body, re.S))
        sources = re.findall(r'data-src="(\w+)"', price)
        assert dict(zip(map(int, heads), sources)) == {typed["id"]: "person", by_ai["id"]: "ai"}

    def test_a_value_corrected_over_mcp_is_marked_ai_on_the_comparison(self, client_in_program, da, base, ace, sophie):
        typed = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {}, channel="web", actor=sophie)
        corrected = _correct_pack(da, typed, ace)
        url = reverse("supply_chain:procurement_comparison", args=[base["tender"]["id"]])
        body = client_in_program.get(url, {"commodity": "rutf"}).content.decode()

        import re

        assert f'data-quote-id="{corrected["id"]}"' in body
        # Marked value by value: the pack the AI corrected is the AI's, the price Sophie
        # typed and the correction carried over is still hers.
        price = "".join(re.findall(r'<td [^>]*data-fact="price"[^>]*>.*?</td>', body, re.S))
        pack = "".join(re.findall(r'<td [^>]*data-fact="pack"[^>]*>.*?</td>', body, re.S))
        assert re.findall(r'data-src="(\w+)"', pack) == ["ai"]
        assert re.findall(r'data-src="(\w+)"', price) == ["person"]

    def test_the_order_page_as_of_leaves_out_the_later_change(self, client_in_program, order):
        import re

        url = reverse("supply_chain:order_detail", args=[order["contract"]["id"]])
        body = client_in_program.get(url, {"as_of": "2026-08-25"}).content.decode()
        assert "ETA 5 Sep → 19 Sep" not in body
        # Under its email event the record's kind is set apart: "<b>Shipment</b> · SH-1 · recorded".
        assert "Shipment · SH-1 · recorded" in " ".join(re.sub(r"<[^>]+>", " ", body).split())
