"""The sourcing lifecycle, driven through the browser.

THIS REPOSITORY IS PUBLIC. Every supplier and figure here is invented.

These go through the real operations against a real database rather than
patching `call_operation`, because the thing most likely to be wrong is the
join between the two: a form that validates happily and then hands the
operation a shape its schema refuses, or a Decimal that arrives as a float.
Mocking the operation would assert that the form posts, which was never in
doubt.
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Commodity, Outreach, Quote, Round, Supplier

pytestmark = pytest.mark.django_db

PROGRAM = 10501
SCOPE = f"prog:{PROGRAM}"


@pytest.fixture
def user(client, django_user_model):
    account = django_user_model.objects.create_user(username="sophie", password="x")
    client.force_login(account)
    return account


@pytest.fixture
def scoped(client, user, monkeypatch):
    """A signed-in caller with a programme selected.

    Patches the modules that CALL these names, never `api_views` where they
    are defined -- and imports every one of them first. Both matter, and the
    first version of this fixture got the second wrong: patching the source
    module before `form_views` had been imported meant that importing it (as
    monkeypatch itself does, to reach the attribute) ran
    `from api_views import has_program_context` against the already-patched
    module. monkeypatch then recorded the PATCH as the original value and
    faithfully restored it, so the stub leaked into every later test in the
    file and a genuinely unscoped request looked scoped.
    """
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def rutf():
    return Commodity.objects.create(
        scope_key=SCOPE, slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150
    )


@pytest.fixture
def supplier():
    return Supplier.objects.enrol(scope_key=SCOPE, name="Northwind Foods", type="manufacturer", country="NG")


@pytest.fixture
def a_round(rutf):
    return Round.objects.create(
        program_id=PROGRAM,
        label="Round 1",
        lines=[{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
        delivery_point={"name": "Central store"},
    )


class TestCreatingARound:
    def test_the_form_renders_with_its_commodity_lines(self, scoped, rutf):
        response = scoped.get(reverse("supply_chain:procurement_round_create"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "What this round is asking for" in body
        assert "lines-TOTAL_FORMS" in body, "the formset management form must be on the page"
        assert "rutf" in body, "the commodity picker must offer this programme's catalogue"

    def test_a_new_round_opens_on_exactly_one_blank_commodity_row(self, scoped, rutf):
        """A formset renders `max(initial, min_num) + extra` rows.

        With min_num=1 and extra=1 that is two blank rows, one of them
        required and one not, with nothing on screen to say which -- which is
        what shipped and what the browser showed. This asserts the count
        rather than the factory arguments, so it stays true however the
        arithmetic is spelled.
        """
        body = scoped.get(reverse("supply_chain:procurement_round_create")).content.decode()
        rows = set(re.findall(r'name="lines-(\d+)-commodity_slug"', body))
        assert rows == {"0"}, f"one blank row, got {sorted(rows)}"

    def test_a_round_is_created_with_its_lines_and_delivery_point(self, scoped, rutf):
        response = scoped.post(
            reverse("supply_chain:procurement_round_create"),
            {
                "label": "Round 1 — RUTF",
                "delivery_name": "Central store",
                "delivery_city": "Kano",
                "delivery_country": "Nigeria",
                "shelf_life_months_minimum": "18",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "1",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-commodity_slug": "rutf",
                "lines-0-quantity": "500",
                "lines-0-quantity_unit": "carton",
            },
        )
        created = Round.objects.get(label="Round 1 — RUTF")
        assert response.status_code == 302
        assert response.url == reverse("supply_chain:procurement_round_detail", args=[created.pk])
        assert created.program_id == PROGRAM
        assert created.status == "draft", "a new round opens in draft, not open"
        assert created.lines == [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}]
        assert created.delivery_point["city"] == "Kano"
        assert created.shelf_life_months_minimum == 18

    def test_a_quantity_survives_as_an_exact_string_not_a_float(self, scoped, rutf):
        """Money and quantity are strings on the wire precisely so nothing
        rounds them. A form layer that parsed them into floats would undo that
        at the last step before the boundary."""
        scoped.post(
            reverse("supply_chain:procurement_round_create"),
            {
                "label": "Precision",
                "delivery_name": "Store",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "1",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-commodity_slug": "rutf",
                "lines-0-quantity": "1234.5678",
                "lines-0-quantity_unit": "carton",
            },
        )
        stored = Round.objects.get(label="Precision").lines[0]["quantity"]
        assert stored == "1234.5678"
        assert Decimal(stored) == Decimal("1234.5678")

    def test_a_round_with_no_commodity_is_refused_and_keeps_what_was_typed(self, scoped, rutf):
        response = scoped.post(
            reverse("supply_chain:procurement_round_create"),
            {
                "label": "Empty round",
                "delivery_name": "Store",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "1",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-commodity_slug": "",
                "lines-0-quantity": "",
                "lines-0-quantity_unit": "",
            },
        )
        assert response.status_code == 200, "a refusal re-renders rather than redirecting"
        assert not Round.objects.filter(label="Empty round").exists()
        assert "Empty round" in response.content.decode(), "what was typed must survive the refusal"


class TestOpeningAndClosing:
    def test_open_is_a_post_only_action(self, scoped, a_round):
        assert scoped.get(reverse("supply_chain:procurement_round_open", args=[a_round.pk])).status_code == 405

    def test_opening_a_round_lets_it_take_quotes(self, scoped, a_round):
        response = scoped.post(reverse("supply_chain:procurement_round_open", args=[a_round.pk]))
        a_round.refresh_from_db()
        assert response.status_code == 302
        assert a_round.status == "open"

    def test_a_round_with_no_delivery_point_is_refused_on_its_own_page(self, scoped, rutf):
        """The operation's refusal is an instruction, so it belongs back where
        the button was rather than on an error page."""
        naked = Round.objects.create(program_id=PROGRAM, label="No delivery point", lines=[], delivery_point={})
        response = scoped.post(reverse("supply_chain:procurement_round_open", args=[naked.pk]), follow=True)
        naked.refresh_from_db()
        assert naked.status == "draft"
        assert any("delivery" in str(m).lower() for m in response.context["messages"])

    def test_closing_a_round_stops_further_quotes(self, scoped, a_round):
        a_round.status = "open"
        a_round.save()
        scoped.post(reverse("supply_chain:procurement_round_close", args=[a_round.pk]))
        a_round.refresh_from_db()
        assert a_round.status == "closed"


class TestInvitations:
    def test_the_supplier_picker_offers_only_this_scope(self, scoped, a_round, supplier):
        """An unscoped ModelChoiceField would list every supplier in the
        database, which is a cross-programme leak wearing a dropdown."""
        Supplier.objects.enrol(scope_key="prog:99999", name="Somebody Else Ltd")
        body = scoped.get(reverse("supply_chain:procurement_outreach_log", args=[a_round.pk])).content.decode()
        assert "Northwind Foods" in body
        assert "Somebody Else Ltd" not in body

    def test_recording_an_invitation(self, scoped, a_round, supplier):
        response = scoped.post(
            reverse("supply_chain:procurement_outreach_log", args=[a_round.pk]),
            {"supplier": str(supplier.pk), "channel": "manual", "sent_on": "2026-04-28", "notes": "emailed sales@"},
        )
        logged = Outreach.objects.get(round=a_round, supplier=supplier)
        assert response.status_code == 302
        assert logged.sent_on == date(2026, 4, 28)
        assert logged.responded is False

    def test_a_field_the_form_does_not_declare_cannot_be_smuggled_in(self, scoped, a_round, supplier):
        """`round_id` comes from the URL and is not a form field, so Django
        drops a posted one before it reaches the payload at all.

        Worth stating precisely: an earlier version of this test claimed to
        prove that fixed values WIN over posted ones, and it did not --
        reversing that precedence in `form_valid` left it green, because the
        posted key never survived `cleaned_data` to contend in the first
        place. The precedence itself is pinned in TestThePayloadBoundary.
        """
        other = Round.objects.create(program_id=PROGRAM, label="Other", lines=[], delivery_point={"name": "x"})
        scoped.post(
            reverse("supply_chain:procurement_outreach_log", args=[a_round.pk]),
            {"supplier": str(supplier.pk), "channel": "manual", "round_id": str(other.pk)},
        )
        assert Outreach.objects.filter(round=a_round).count() == 1
        assert not Outreach.objects.filter(round=other).exists()

    def test_recording_a_reply(self, scoped, a_round, supplier):
        invitation = Outreach.objects.create(round=a_round, supplier=supplier, sent_on=date(2026, 4, 28))
        scoped.post(
            reverse("supply_chain:procurement_outreach_reply", args=[invitation.pk]),
            {"responded": "on", "response_kind": "quote", "last_reminder_on": "2026-05-02"},
        )
        invitation.refresh_from_db()
        assert invitation.responded is True
        assert invitation.response_kind == "quote"

    def test_deleting_an_invitation_needs_a_reason(self, scoped, a_round, supplier):
        invitation = Outreach.objects.create(round=a_round, supplier=supplier)
        refused = scoped.post(
            reverse("supply_chain:procurement_outreach_delete", args=[invitation.pk]), {"reason": ""}
        )
        assert refused.status_code == 200
        assert Outreach.objects.filter(pk=invitation.pk).exists()

        scoped.post(
            reverse("supply_chain:procurement_outreach_delete", args=[invitation.pk]),
            {"reason": "recorded against the wrong supplier"},
        )
        assert not Outreach.objects.filter(pk=invitation.pk).exists()


class TestVoidingAQuote:
    @pytest.fixture
    def quote(self, a_round, rutf, supplier):
        return Quote.objects.create(
            round=a_round,
            commodity=rutf,
            supplier=supplier,
            as_quoted_amount=Decimal("52.42"),
            as_quoted_unit="per_pack",
        )

    def test_voiding_needs_a_reason(self, scoped, quote):
        response = scoped.post(reverse("supply_chain:procurement_quote_void", args=[quote.pk]), {"reason": ""})
        quote.refresh_from_db()
        assert response.status_code == 200
        assert quote.voided is False

    def test_a_voided_quote_stays_readable(self, scoped, quote):
        """Voiding records that a quote should not be compared. It does not
        erase that the supplier said it."""
        scoped.post(
            reverse("supply_chain:procurement_quote_void", args=[quote.pk]), {"reason": "duplicate of quote 1"}
        )
        quote.refresh_from_db()
        assert quote.voided is True
        assert quote.void_reason == "duplicate of quote 1"
        assert quote.as_quoted_amount == Decimal("52.42")


class TestTheScreensRefuseWithoutAProgramme:
    def test_every_write_screen_asks_for_a_programme_rather_than_raising(self, client, user, a_round):
        """`labs_context = {}` is a normal state. Each of these calls
        programme-scoped operations, so an unguarded one raises deep in
        SupplyDataAccess instead of saying to pick a programme."""
        for name, args in (
            ("procurement_round_create", []),
            ("procurement_outreach_log", [a_round.pk]),
            ("procurement_quote_void", [1]),
        ):
            response = client.get(reverse(f"supply_chain:{name}", args=args))
            assert response.status_code == 200, name
            assert "No programme selected" in response.content.decode(), name


class TestTheScreensAreReachable:
    """A screen nothing links to is a screen nobody finds. These also catch a
    malformed `{% url %}` in the pages that link to them, which is a 500
    rather than a missing link."""

    def test_a_searchable_picker_actually_loads_the_library(self, scoped, a_round, supplier):
        """`data-tomselect` alone does nothing.

        The bundle is not global -- seven templates each pull it in
        themselves -- so the attribute without the script is a plain <select>
        that merely claims to be searchable. This failed on the deployed site
        while every other test here passed, because nothing tied the marker to
        the code that reads it.
        """
        body = scoped.get(reverse("supply_chain:procurement_outreach_log", args=[a_round.pk])).content.decode()
        assert "data-tomselect" in body, "the supplier picker asks to be searchable"
        assert "tomselect-bundle.js" in body, "...and the page must load what reads that"
        assert "tomselect.css" in body

    def test_the_board_offers_a_new_round(self, scoped):
        body = scoped.get(reverse("supply_chain:procurement_round_board")).content.decode()
        assert reverse("supply_chain:procurement_round_create") in body

    def test_a_draft_round_offers_opening_and_an_open_one_offers_closing(self, scoped, a_round):
        draft = scoped.get(reverse("supply_chain:procurement_round_detail", args=[a_round.pk])).content.decode()
        assert reverse("supply_chain:procurement_round_open", args=[a_round.pk]) in draft
        assert reverse("supply_chain:procurement_round_close", args=[a_round.pk]) not in draft

        a_round.status = "open"
        a_round.save()
        opened = scoped.get(reverse("supply_chain:procurement_round_detail", args=[a_round.pk])).content.decode()
        assert reverse("supply_chain:procurement_round_close", args=[a_round.pk]) in opened
        assert reverse("supply_chain:procurement_round_open", args=[a_round.pk]) not in opened

    def test_opening_and_closing_are_posts_not_links(self, scoped, a_round):
        """A link that mutates is one prefetch away from doing it unasked."""
        body = scoped.get(reverse("supply_chain:procurement_round_detail", args=[a_round.pk])).content.decode()
        open_url = reverse("supply_chain:procurement_round_open", args=[a_round.pk])
        assert f'action="{open_url}"' in body, "must be a form action"
        assert f'href="{open_url}"' not in body, "must not be a link"

    def test_an_invitation_row_offers_reply_and_delete(self, scoped, a_round, supplier):
        invitation = Outreach.objects.create(round=a_round, supplier=supplier, sent_on=date(2026, 4, 28))
        body = scoped.get(reverse("supply_chain:procurement_round_detail", args=[a_round.pk])).content.decode()
        assert reverse("supply_chain:procurement_outreach_log", args=[a_round.pk]) in body
        assert reverse("supply_chain:procurement_outreach_reply", args=[invitation.pk]) in body
        assert reverse("supply_chain:procurement_outreach_delete", args=[invitation.pk]) in body

    def test_a_standing_quote_offers_voiding_and_a_voided_one_does_not(self, scoped, a_round, rutf, supplier):
        quote = Quote.objects.create(
            round=a_round,
            commodity=rutf,
            supplier=supplier,
            as_quoted_amount=Decimal("52.42"),
            as_quoted_unit="per_pack",
        )
        void_url = reverse("supply_chain:procurement_quote_void", args=[quote.pk])
        standing = scoped.get(reverse("supply_chain:procurement_quote_detail", args=[quote.pk])).content.decode()
        assert void_url in standing

        quote.voided = True
        quote.save()
        voided = scoped.get(reverse("supply_chain:procurement_quote_detail", args=[quote.pk])).content.decode()
        assert void_url not in voided, "a voided quote cannot be voided again"


class TestRecordingAQuote:
    """The screen that moved off its hand-rolled template onto this layer.

    Its old tests covered the three things the hand-rolled version could get
    wrong (a 500 on a typo, losing what was typed, injecting a submitted value
    into JS) and none of the things the new one can: scoped pickers, the
    price-basis rule, and the payload's shape. Mutation testing found that gap
    by removing each guard and watching nothing go red.
    """

    def _post(self, **overrides):
        payload = {
            "round": "",
            "supplier": "",
            "commodity": "",
            "item": "",
            "as_quoted_amount": "52.42",
            "as_quoted_unit": "per_pack",
            "as_quoted_currency": "usd",
            "quantity_basis": "500",
            "quantity_basis_unit": "carton",
            "pack_spec_source": "stated_on_quote",
            "base_per_pack_stated": "150",
            "base_unit_grams_stated": "92",
            "freight_basis": "not_specified",
            "freight_amount": "",
            "duties_basis": "not_specified",
            "duties_amount": "",
            "shelf_life_months_stated": "",
            "lead_time_days": "",
            "incoterm": "",
            "received_on": "",
        }
        payload.update(overrides)
        return payload

    def test_a_quote_is_recorded_as_stated(self, scoped, a_round, supplier, rutf):
        response = scoped.post(
            reverse("supply_chain:procurement_quote_entry"),
            self._post(round=a_round.pk, supplier=supplier.pk, commodity=rutf.pk),
        )
        assert response.status_code == 302

        made = Quote.objects.get(round=a_round, supplier=supplier)
        assert made.as_quoted_amount == Decimal("52.42")
        assert made.as_quoted_unit == "per_pack", "recorded as stated, not normalised on entry"
        assert made.as_quoted_currency == "USD"

    def test_the_pickers_offer_only_this_programme(self, scoped, a_round, supplier, rutf):
        Supplier.objects.enrol(scope_key="prog:99999", name="A supplier in another program")
        Commodity.objects.create(scope_key="prog:99999", slug="theirs", name="A product in another programme")
        Round.objects.create(program_id=99999, label="A round in another programme", lines=[], delivery_point={})

        body = scoped.get(reverse("supply_chain:procurement_quote_entry")).content.decode()
        assert "Northwind Foods" in body
        for foreign in (
            "A supplier in another programme",
            "A product in another programme",
            "A round in another programme",
        ):
            assert foreign not in body, foreign

    def test_a_price_with_no_basis_is_refused(self, scoped, a_round, supplier, rutf):
        """Without it a price compares with nothing, and comparing is what the
        whole round is for."""
        response = scoped.post(
            reverse("supply_chain:procurement_quote_entry"),
            self._post(round=a_round.pk, supplier=supplier.pk, commodity=rutf.pk, as_quoted_unit=""),
        )
        assert response.status_code == 200
        assert "as_quoted_unit" in response.context["form"].errors
        assert not Quote.objects.filter(round=a_round).exists()

    def test_a_trade_item_that_is_not_a_version_of_the_product_is_refused(self, scoped, a_round, supplier, rutf):
        from connect_labs.supply_chain.models import Item

        other = Commodity.objects.create(scope_key=SCOPE, slug="rusf", name="RUSF")
        wrong = Item.objects.create(scope_key=SCOPE, sku="X", name="Some RUSF", commodity=other)

        response = scoped.post(
            reverse("supply_chain:procurement_quote_entry"),
            self._post(round=a_round.pk, supplier=supplier.pk, commodity=rutf.pk, item=wrong.pk),
        )
        assert response.status_code == 200
        assert "item" in response.context["form"].errors

    def test_arriving_from_a_round_preselects_it(self, scoped, a_round):
        body = scoped.get(f"{reverse('supply_chain:procurement_quote_entry')}?round={a_round.pk}").content.decode()
        chosen = re.search(r'<option value="(\d+)"\s+selected', body)
        assert chosen and chosen.group(1) == str(a_round.pk)

    def test_a_nonsense_round_parameter_does_not_raise(self, scoped):
        """A URL is somebody else's input, including a bookmarked one."""
        assert scoped.get(f"{reverse('supply_chain:procurement_quote_entry')}?round=notanumber").status_code == 200

    def test_the_price_crosses_as_an_exact_string_and_the_product_by_slug(self, scoped, a_round, supplier, rutf):
        """Invisible to a round-trip: 52.42 rounds back through a float, and a
        payload carrying `commodity_id` instead of `commodity_slug` reaches the
        same column anyway."""
        from connect_labs.labs.access.scopes import SYSTEM
        from connect_labs.supply_chain.data_access import SupplyDataAccess
        from connect_labs.supply_chain.forms import QuoteForm

        access = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        form = QuoteForm(self._post(round=a_round.pk, supplier=supplier.pk, commodity=rutf.pk), access=access)
        assert form.is_valid(), form.errors

        payload = form.payload()
        assert payload["as_quoted_amount"] == "52.42"
        assert not isinstance(payload["as_quoted_amount"], float)
        assert payload["commodity_slug"] == "rutf"
        assert "commodity_id" not in payload


class TestThePayloadBoundary:
    """`to_payload` and the fixed/field precedence, tested directly.

    Both are load-bearing and neither is reachable from the screens in this
    change: no sourcing form has a money field, and no sourcing form declares
    a field that collides with a fixed one. Mutating them left the whole
    browser-level suite green, so they are pinned here rather than assumed --
    the quote, contract and invoice screens that come next depend on both.
    """

    def test_a_decimal_crosses_as_an_exact_string_never_a_float(self):
        from connect_labs.supply_chain.forms import to_payload

        out = to_payload({"amount": Decimal("52.42"), "rate": Decimal("0.0001")})
        assert out == {"amount": "52.42", "rate": "0.0001"}
        assert isinstance(out["amount"], str), "money is string-only on the wire; a float has already rounded"
        assert Decimal(out["amount"]) == Decimal("52.42")

    def test_a_date_crosses_as_iso_and_a_model_as_its_id(self, supplier):
        from connect_labs.supply_chain.forms import to_payload

        out = to_payload({"sent_on": date(2026, 4, 28), "supplier": supplier})
        assert out == {"sent_on": "2026-04-28", "supplier_id": supplier.pk}

    def test_unanswered_fields_are_dropped_rather_than_sent_empty(self):
        from connect_labs.supply_chain.forms import to_payload

        out = to_payload({"notes": "", "channel": None, "responded": False, "kept": "x"})
        assert out == {"responded": False, "kept": "x"}, "False is an answer; '' and None are not"

    def test_what_the_url_fixes_beats_what_the_form_collected(self):
        """The guarantee a screen relies on when it fixes a value the form
        also happens to carry."""
        from connect_labs.supply_chain.form_views import OperationFormView
        from connect_labs.supply_chain.operations import get_operation

        view = OperationFormView()
        view.operation = "outreach_log"
        view.kwargs = {}
        view.fixed = lambda **kw: {"data": {"round_id": 999}}

        class _Form:
            def payload(self):
                return {"round_id": 1, "supplier_id": 7}

        captured = {}
        view.op = lambda name, **payload: captured.update(payload) or {"round_id": 999}
        view.redirect_to = lambda result: "/"
        assert get_operation("outreach_log")  # the operation really exists
        view.form_valid(_Form())
        assert captured["data"]["round_id"] == 999, "the URL's value must win over a posted one"
