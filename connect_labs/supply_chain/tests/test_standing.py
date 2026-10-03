"""Where every tender and order in a program stands: the first thing on /supply/.

THIS REPOSITORY IS PUBLIC. Every id, name, address and figure below is invented.

History is built through `call_operation`, dated and attributed with
`seed_overrides` on a synthetic program, as in test_history_timeline.py. Each
test passes `today` explicitly, so the stale rules read the same whatever day
the suite runs. Design doc
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §4.1, §4.2.
"""

import datetime
import re

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.standing import standing_rows

PROGRAM = 20996
OTHER_PROGRAM = 20995
TODAY = datetime.date(2026, 9, 10)


def _at(month, day, hour=9):
    return datetime.datetime(2026, month, day, hour, 0, tzinfo=datetime.UTC)


AUG_3, AUG_20, AUG_28, SEP_1 = _at(8, 3), _at(8, 20), _at(8, 28), _at(9, 1)


def _synthetic(program_id):
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    return SyntheticOpportunity.objects.create(
        opportunity_id=program_id,
        program_id=program_id,
        labs_only=True,
        enabled=True,
        label="standing tests",
        allowed_domains=["dimagi.com"],
    )


@pytest.fixture
def da(db):
    _synthetic(PROGRAM)
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def sophie(django_user_model):
    return django_user_model.objects.create_user(
        username="sophie", email="sophie@example.org", name="Sophie Bello", password="x"
    )


@pytest.fixture
def ace(django_user_model):
    return django_user_model.objects.create_user(username="ace", email="ace@dimagi-ai.com", password="x")


def op(da, name, when, *, channel="command", actor=None, program=PROGRAM, **payload):
    with seed_overrides(program, actor=actor, recorded_at=when):
        return call_operation(name, da, payload, channel=channel)


@pytest.fixture
def base(da):
    op(
        da,
        "commodity_upsert",
        AUG_3,
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"},
    )
    suppliers = [
        op(da, "supplier_create", AUG_3, data={"name": name})
        for name in ("Northwind Foods", "Baobab Nutrition", "Sahel Pastes", "Kaduna Mills")
    ]
    us = op(da, "org_upsert", AUG_3, data={"slug": "standing-us", "name": "The program"})
    partner = op(da, "org_upsert", AUG_3, data={"slug": "standing-partner", "name": "Kano Health Partners"})
    store = op(
        da,
        "supply_point_upsert",
        AUG_3,
        data={"slug": "kano-store", "name": "Kano store", "kind": "central_store", "source": "we_recorded"},
    )
    return {"suppliers": suppliers, "us": us, "partner": partner, "store": store}


def _tender(da, label, when, status="open"):
    tender = op(
        da,
        "tender_create",
        when,
        data={
            "label": label,
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "rutf", "quantity": "600", "quantity_unit": "carton"}],
        },
    )
    if status != "draft":
        op(da, "tender_update", when, tender_id=tender["id"], data={"status": status})
    return tender


def _outreach(da, tender, supplier, sent_on, responded=False, when=AUG_20):
    return op(
        da,
        "outreach_log",
        when,
        data={
            "tender_id": tender["id"],
            "supplier_id": supplier["id"],
            "sent_on": sent_on.isoformat(),
            "responded": responded,
            **({"response_kind": "quote"} if responded else {}),
        },
    )


def _quote(da, tender, supplier, when=AUG_28, **extra):
    return op(
        da,
        "quote_record",
        when,
        data={
            "tender_id": tender["id"],
            "commodity_slug": "rutf",
            "supplier_id": supplier["id"],
            "as_quoted_amount": "42.50",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "600",
            "quantity_basis_unit": "carton",
            "received_on": "2026-08-28",
            **extra,
        },
    )


def _order(da, base, reference, when=AUG_3, buyer="us", tender=None):
    return op(
        da,
        "contract_create",
        when,
        data={
            "supplier_id": base["suppliers"][0]["id"],
            "commodity_slug": "rutf",
            "buyer_of_record": "programme_org" if buyer == "us" else "partner_org",
            "buyer_org_id": base[buyer]["id"],
            "reference": reference,
            "quantity": "600",
            "quantity_unit": "carton",
            "status": "placed",
            "source": "we_recorded",
            **({"tender_id": tender["id"]} if tender else {}),
        },
    )


def _ship(da, contract, expected_on, when=AUG_20, **extra):
    return op(
        da,
        "shipment_record",
        when,
        data={
            "contract_id": contract["id"],
            "reference": "SH-1",
            "status": "dispatched",
            "dispatched_on": "2026-08-20",
            "expected_on": expected_on.isoformat(),
            "source": "supplier_reported",
            **extra,
        },
    )


def _receive(da, base, contract, shipment, when=SEP_1):
    return op(
        da,
        "receipt_record",
        when,
        data={
            "contract_id": contract["id"],
            "shipment_id": shipment["id"],
            "supply_point_id": base["store"]["id"],
            "received_on": "2026-09-01",
            "source": "we_recorded",
            "lines": [{"quantity_accepted": "600", "quantity_unit": "carton"}],
        },
    )


def _invoice(da, contract, when=SEP_1):
    return op(
        da,
        "invoice_record",
        when,
        data={"contract_id": contract["id"], "amount": "25500.00", "source": "supplier_reported"},
    )


def _pay(da, invoice, when=SEP_1, amount="25500.00"):
    return op(
        da,
        "payment_record",
        when,
        data={"invoice_id": invoice["id"], "paid_on": "2026-09-01", "amount": amount, "source": "we_recorded"},
    )


_COMPARABLE = dict(
    freight_basis="included",
    duties_basis="included",
    pack_spec_source="stated_on_quote",
    base_per_pack_stated=150,
    base_unit_grams_stated=92,
)


# The pack, stated: what is left to block a quote is its freight and duties.
_PACK = dict(pack_spec_source="stated_on_quote", base_per_pack_stated=150, base_unit_grams_stated=92)


def _award(da, tender, quote, when=SEP_1):
    return op(
        da,
        "award_create",
        when,
        tender_id=tender["id"],
        quote_id=quote["id"],
        rationale="the only offer we could compare",
        decided_on="2026-09-01",
    )


def _row(rows, title_start):
    return next(r for r in rows if r.title.startswith(title_start))


@pytest.mark.django_db
class TestTender:
    def test_an_open_tender_collects_quotes_and_names_each_silent_supplier_on_suppliers(self, da, base):
        tender = _tender(da, "Round 1", AUG_3)
        sent = datetime.date(2026, 8, 21)  # 20 days before TODAY
        for supplier in base["suppliers"][:3]:
            _outreach(da, tender, supplier, sent, responded=True)
        _outreach(da, tender, base["suppliers"][3], sent)

        row = _row(standing_rows(PROGRAM, TODAY), "Round 1")

        assert row.kind == "tender"
        assert row.stage_index == 1
        assert row.stage == "Collecting quotes · 3 of 4 answered"
        assert row.bars == ["done", "now", "todo", "todo", "todo", "todo"]
        assert [m.text for m in row.theirs] == ["Kaduna Mills: reply (silent 20 days)"]
        assert [m.rule for m in row.theirs] == ["no reply"]
        assert row.ours == []
        assert row.whose == "suppliers"
        assert row.next_move.text == "Kaduna Mills: reply (silent 20 days)"
        assert row.url == reverse("supply_chain:procurement_tender_detail", args=[tender["id"]])

    def test_a_quote_counts_as_a_reply_even_when_the_outreach_was_not_marked(self, da, base):
        tender = _tender(da, "Round 1", AUG_3)
        _outreach(da, tender, base["suppliers"][0], datetime.date(2026, 8, 1))
        _quote(da, tender, base["suppliers"][0], **_COMPARABLE)

        row = _row(standing_rows(PROGRAM, TODAY), "Round 1")
        assert row.theirs == []
        assert row.whose == ""

    def test_quote_gaps_are_not_moves(self, da, base):
        tender = _tender(da, "Round 1", AUG_3)
        _outreach(da, tender, base["suppliers"][0], datetime.date(2026, 9, 1), responded=True)
        _quote(da, tender, base["suppliers"][0], **_PACK)  # freight and duties not stated

        row = _row(standing_rows(PROGRAM, TODAY), "Round 1")
        assert (row.ours, row.theirs) == ([], [])

    def test_a_closed_tender_is_comparing_and_no_longer_chases_replies(self, da, base):
        tender = _tender(da, "Round 1", AUG_3)
        _outreach(da, tender, base["suppliers"][0], datetime.date(2026, 8, 1))
        assert _row(standing_rows(PROGRAM, TODAY), "Round 1").theirs

        op(da, "tender_update", SEP_1, tender_id=tender["id"], data={"status": "closed"})
        row = _row(standing_rows(PROGRAM, TODAY), "Round 1")
        assert row.stage_index == 2 and row.stage.startswith("Comparing")
        assert row.theirs == []

    def test_an_awarded_tender_is_awarding_to_its_supplier(self, da, base):
        tender = _tender(da, "Round 1", AUG_3)
        _award(da, tender, _quote(da, tender, base["suppliers"][1], **_COMPARABLE))
        row = _row(standing_rows(PROGRAM, TODAY), "Round 1")
        assert row.stage_index == 3
        assert row.stage == "Awarding · to Baobab Nutrition"

    def test_once_ordered_the_tender_is_its_order_row(self, da, base):
        tender = _tender(da, "Round 1", AUG_3)
        _award(da, tender, _quote(da, tender, base["suppliers"][1], **_COMPARABLE))
        _order(da, base, "PO-1", tender=tender)
        _order(da, base, "PO-LOOSE")

        rows = standing_rows(PROGRAM, TODAY)
        assert [r.kind for r in rows if r.title.startswith("Round 1")] == []
        assert _row(rows, "PO-1").tender_id == tender["id"]
        assert _row(rows, "PO-1").origin == "Round 1"
        assert _row(rows, "PO-LOOSE").tender_id is None

    def test_another_programs_tender_is_not_listed(self, da, base):
        _tender(da, "Round 1", AUG_3)
        _synthetic(OTHER_PROGRAM)
        other = SupplyDataAccess(program_id=OTHER_PROGRAM, caller=SYSTEM)
        op(
            other,
            "commodity_upsert",
            AUG_3,
            program=OTHER_PROGRAM,
            data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"},
        )
        op(
            other,
            "tender_create",
            AUG_3,
            program=OTHER_PROGRAM,
            data={
                "label": "Elsewhere",
                "lines": [{"commodity_slug": "rutf", "quantity": "1", "quantity_unit": "carton"}],
            },
        )

        titles = [r.title for r in standing_rows(PROGRAM, TODAY)]
        assert titles == ["Round 1"]


@pytest.mark.django_db
class TestOrder:
    def test_goods_on_the_road_are_delivering_in_transit(self, da, base):
        contract = _order(da, base, "PO-1")
        _ship(da, contract, datetime.date(2026, 9, 5))

        row = _row(standing_rows(PROGRAM, TODAY), "PO-1")

        assert row.kind == "order"
        assert row.stage_index == 5
        assert row.stage == "Delivering · in transit"
        assert (row.ours, row.theirs) == ([], [])
        assert row.url == reverse("supply_chain:order_detail", args=[contract["id"]])

    def test_an_order_paid_in_advance_with_goods_on_the_road_reads_paid_in_transit(self, da, base):
        contract = _order(da, base, "PO-1")
        _ship(da, contract, datetime.date(2026, 9, 5))
        _pay(da, _invoice(da, contract))
        assert _row(standing_rows(PROGRAM, TODAY), "PO-1").stage == "Delivering · paid, in transit"

    def test_a_delivered_and_paid_order_with_nothing_owed_leaves_the_list(self, da, base):
        contract = _order(da, base, "PO-1")
        shipment = _ship(da, contract, datetime.date(2026, 8, 30))
        _receive(da, base, contract, shipment)
        _pay(da, _invoice(da, contract))
        assert [r for r in standing_rows(PROGRAM, TODAY) if r.title.startswith("PO-1")] == []

    def test_a_half_paid_order_is_part_paid(self, da, base):
        contract = _order(da, base, "PO-1")
        shipment = _ship(da, contract, datetime.date(2026, 8, 30))
        _receive(da, base, contract, shipment)
        _pay(da, _invoice(da, contract), amount="12750.00")
        row = _row(standing_rows(PROGRAM, TODAY), "PO-1")
        assert row.stage == "Delivered · part paid"
        assert row.stage_index == 6

    def test_a_paid_order_nothing_arrived_for_stays_paid(self, da, base):
        contract = _order(da, base, "PO-1")
        _pay(da, _invoice(da, contract))
        assert _row(standing_rows(PROGRAM, TODAY), "PO-1").stage == "Delivering · paid"

    def test_a_placed_order_is_delivering_placed(self, da, base):
        _order(da, base, "PO-1")
        assert _row(standing_rows(PROGRAM, TODAY), "PO-1").stage == "Delivering · placed"

    def test_the_buyer_is_named_only_when_it_is_not_the_programs_own_org(self, da, base):
        _order(da, base, "PO-OURS")
        _order(da, base, "PO-PARTNER", buyer="partner")

        rows = standing_rows(PROGRAM, TODAY, own_org_id=base["us"]["id"])
        assert _row(rows, "PO-OURS").buyer == ""
        assert _row(rows, "PO-PARTNER").buyer == "Kano Health Partners"


@pytest.mark.django_db
class TestLastChangeAndOrder:
    def test_the_last_change_is_the_newest_write_under_the_record_with_who_made_it(self, da, base, ace):
        contract = _order(da, base, "PO-1")
        op(
            da,
            "shipment_record",
            AUG_28,
            channel="mcp",
            actor=ace,
            data={"contract_id": contract["id"], "reference": "SH-9", "source": "supplier_reported"},
        )

        row = _row(standing_rows(PROGRAM, TODAY), "PO-1")
        assert row.last_change_at == AUG_28
        assert row.last_change_by == "ACE (agent)"
        assert row.last_change_is_ai is True

    def test_a_person_on_the_web_is_not_ai(self, da, base, sophie):
        tender = _tender(da, "Round 1", AUG_3)
        op(
            da,
            "tender_update",
            SEP_1,
            channel="web",
            actor=sophie,
            tender_id=tender["id"],
            data={"notes_to_supplier": "x"},
        )
        row = _row(standing_rows(PROGRAM, TODAY), "Round 1")
        assert (row.last_change_at, row.last_change_by, row.last_change_is_ai) == (SEP_1, "Sophie Bello", False)

    def test_rows_are_ordered_by_last_change(self, da, base):
        older = _order(da, base, "PO-OLD", when=AUG_3)
        _ship(da, older, datetime.date(2026, 9, 5), when=AUG_20)
        _tender(da, "Round 2", SEP_1)

        rows = standing_rows(PROGRAM, TODAY)
        assert [r.title.split(" ")[0] for r in rows] == ["Round", "PO-OLD"]

    def test_until_cuts_the_last_change_at_that_day(self, da, base, ace):
        contract = _order(da, base, "PO-1")
        _ship(da, contract, datetime.date(2026, 9, 5), when=AUG_28)
        row = _row(standing_rows(PROGRAM, TODAY, until=datetime.date(2026, 8, 25)), "PO-1")
        assert row.last_change_at == AUG_3

    def test_queries_stay_bounded_per_row(self, da, base, django_assert_max_num_queries):
        for n in range(3):
            _tender(da, f"Round {n}", AUG_3)
            _order(da, base, f"PO-{n}")
        with django_assert_max_num_queries(110):
            standing_rows(PROGRAM, TODAY)


class _ProgramContextMiddleware:
    """Stands in for LabsContextMiddleware (left out of the test settings)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.labs_context = {"program_id": PROGRAM} if request.user.is_authenticated else {}
        return self.get_response(request)


@pytest.fixture
def client_in_program(client, monkeypatch, settings, da, sophie):
    from connect_labs.supply_chain import views  # noqa: F401  -- bind before patching

    settings.MIDDLEWARE = [*settings.MIDDLEWARE, f"{__name__}._ProgramContextMiddleware"]
    monkeypatch.setattr("connect_labs.supply_chain.views._access", lambda request: da)
    client.force_login(sophie)
    session = client.session
    session["labs_oauth"] = {"organization_data": {"programs": [{"id": PROGRAM, "name": "Connect-RUTF"}]}}
    session.save()
    return client


@pytest.mark.django_db
class TestHomePage:
    def test_the_banner_names_the_program_and_its_buyer_of_record(self, client_in_program, base, monkeypatch):
        from connect_labs.labs.models import LabsOrg

        us = LabsOrg.objects.get(pk=base["us"]["id"])
        monkeypatch.setattr("connect_labs.supply_chain.identity.resolve_org", lambda access: us)
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()

        # Before any order, the organisation acting in the program, in its role.
        line = re.search(r'data-testid="supply-program-line"[^>]*>(.*?)</p>', body).group(1)
        assert line == "Connect-RUTF · The program, buyer of record"
        # Said once: the overview's own heading gives way to it.
        assert '<h2 class="text-lg font-semibold text-gray-900 mb-3">' not in body

    def test_the_heading_falls_back_to_the_program_id(self, client_in_program, base, monkeypatch):
        monkeypatch.setattr("connect_labs.supply_chain.views.resolve_org", lambda access: None)
        session = client_in_program.session
        session["labs_oauth"] = {}
        session.save()
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()
        assert re.search(rf'data-testid="program-heading">Program {PROGRAM}</div>', body)

    def test_the_table_lists_each_tender_and_order_above_the_chain(self, client_in_program, da, base, ace):
        tender = _tender(da, "Round 1", AUG_3)
        _outreach(da, tender, base["suppliers"][0], datetime.date(2026, 8, 1))
        contract = _order(da, base, "PO-1", buyer="partner")
        op(
            da,
            "shipment_record",
            AUG_28,
            channel="mcp",
            actor=ace,
            data={
                "contract_id": contract["id"],
                "reference": "SH-1",
                "status": "dispatched",
                "expected_on": "2026-09-05",
                "source": "supplier_reported",
            },
        )
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()

        standing = body.index('id="supply-standing"')
        assert standing < body.index("The chain")
        table = body[standing : body.index("</table>", standing)]
        assert reverse("supply_chain:procurement_tender_detail", args=[tender["id"]]) in table
        assert reverse("supply_chain:order_detail", args=[contract["id"]]) in table
        assert "0 of 1 answered" in table
        assert "Northwind Foods: reply (silent " in table
        assert "In transit" in table
        assert "Kano Health Partners" in table
        assert "data-ai" in table and "AI assistant" in table

    def test_as_of_before_round_2_existed_leaves_it_out(self, client_in_program, da, base):
        _tender(da, "Round 1", AUG_3)
        _tender(da, "Round 2", AUG_28)
        url = reverse("supply_chain:home")

        live = client_in_program.get(url).content.decode()
        past = client_in_program.get(url, {"as_of": "2026-08-25"}).content.decode()

        assert "Round 2" in live
        assert "Round 1" in past
        assert "Round 2" not in past


@pytest.mark.django_db
class TestHomePageHooks:
    """Stable handles a walkthrough (or a person's test) can find each thing by."""

    def test_rows_badges_and_moves_carry_test_ids(self, client_in_program, da, base, ace):
        tender = _tender(da, "Round 1", AUG_3)
        _outreach(da, tender, base["suppliers"][0], datetime.date(2026, 8, 1))
        _tender(da, "Round 2", AUG_3)
        contract = _order(da, base, "PO-LOOSE")
        op(
            da,
            "shipment_record",
            AUG_28,
            channel="mcp",
            actor=ace,
            data={"contract_id": contract["id"], "reference": "SH-1", "source": "supplier_reported"},
        )
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()

        assert body.count('data-testid="overview-row"') == 3
        assert body.count(f'data-tender-id="{tender["id"]}"') == 1
        assert 'data-testid="ai-badge"' in body
        assert body.count('data-testid="whose"') == 1
        assert 'data-testid="standing-our-moves"' in body

    def test_as_of_an_earlier_day_a_delivered_and_paid_order_is_in_transit(self, client_in_program, da, base):
        contract = _order(da, base, "PO-1")
        shipment = _ship(da, contract, datetime.date(2026, 9, 5))
        _receive(da, base, contract, shipment)
        _pay(da, _invoice(da, contract))
        url = reverse("supply_chain:home")

        live = client_in_program.get(url).content.decode()
        past = client_in_program.get(url, {"as_of": "2026-08-25"}).content.decode()

        assert "PO-1" not in live.split('id="supply-standing"')[1].split("</table>")[0]
        assert "Delivering · in transit" in past
        assert 'data-testid="as-of-banner"' in past and 'data-testid="as-of-control"' in past
