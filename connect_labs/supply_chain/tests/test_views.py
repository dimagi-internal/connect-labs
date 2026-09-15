import ast
import pathlib
from unittest.mock import patch

import jsonschema
import pytest
from django.urls import reverse

from connect_labs.supply_chain.operations import call_operation as real_call_operation

pytestmark = pytest.mark.django_db


@pytest.fixture
def sophie(client, django_user_model):
    user = django_user_model.objects.create_user(username="sophie", password="x")
    client.force_login(user)
    return user


def test_the_round_board_renders(client, sophie):
    with patch("connect_labs.supply_chain.procurement.views.call_operation", return_value=[]):
        response = client.get(reverse("supply_chain:procurement_round_board"))
    assert response.status_code == 200


def test_the_catalogue_shows_status_and_a_computed_spec_verdict(client, sophie):
    """Finding 15: the column headed 'Specification' actually rendered
    item.status -- mislabelled, and design doc section 12's 'spec verdict'
    content was entirely absent. Now there are two distinct columns.

    The page is the Catalogue now, with products and trade items as separate
    levels, but the verdict this pins is the same one.
    """
    items = [
        {
            "id": 1,
            "sku": "sku-1",
            "name": "Item One",
            "commodity_slug": "infant-scale",
            "spec_attributes": {"minimum_graduation_g": 10},
            "status": "active",
        }
    ]
    commodities = [
        {
            "slug": "infant-scale",
            "name": "Infant scale",
            "spec_requirements": [{"field": "minimum_graduation_g", "operator": "<=", "value": 20, "unit": "g"}],
        }
    ]

    def _dispatch(name, access, payload):
        if name == "item_list":
            return items
        if name == "commodity_list":
            return commodities
        raise AssertionError(name)

    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_dispatch):
        response = client.get(reverse("supply_chain:catalogue"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "Against the spec" in body
    assert "Meets all 1" in body
    # The two levels are distinct, and a trade item sits under its product.
    assert "Trade item" in body
    assert "Infant scale" in body


# --- Finding 3: '/supply/' and '/supply/procurement/' must not 500 with no
# programme selected. labs_context = {} is a normal state (labs/context.py)
# for a freshly-authenticated user with nothing auto-selectable -- exactly
# `sophie`, who has no organisations or programmes. Neither test below mocks
# call_operation: the point is that a REAL SupplyDataAccess is never even
# constructed on this path, let alone asked to read program_experiment with
# no program_id.


def test_the_domain_home_does_not_500_with_no_programme_selected(client, sophie):
    response = client.get(reverse("supply_chain:home"))
    assert response.status_code == 200
    assert "No programme selected" in response.content.decode()


def test_the_round_board_does_not_500_with_no_programme_selected(client, sophie):
    response = client.get(reverse("supply_chain:procurement_round_board"))
    assert response.status_code == 200
    assert "No programme selected" in response.content.decode()


def test_the_round_board_does_not_offer_record_a_quote_with_no_programme_selected(client, sophie):
    """Final review, item C: the board's own button (distinct from the
    persistent site nav's copy of the same link, which is out of this
    finding's scope) rendered ABOVE the has_program_context guard, so the
    no-programme board still put a one-click path to QuoteEntryView, which
    raised the same finding-3 ValueError. Marked by its icon, since the nav
    link (base.html) has the same text and href on every supply_chain page
    regardless of context."""
    response = client.get(reverse("supply_chain:procurement_round_board"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "fa-plus mr-1" not in body


def test_quote_entry_get_does_not_500_with_no_programme_selected(client, sophie):
    with patch("connect_labs.supply_chain.procurement.views.call_operation", return_value=[]):
        response = client.get(reverse("supply_chain:procurement_quote_entry"))
    assert response.status_code == 200
    assert "No programme selected" in response.content.decode()


def test_quote_entry_post_does_not_500_with_no_programme_selected(client, sophie):
    """The route must stay safe however it is reached -- a bookmark, a
    direct URL, or a raw POST -- not just the board's now-hidden button."""
    with patch("connect_labs.supply_chain.procurement.views.call_operation", return_value=[]) as mock_call:
        response = client.post(
            reverse("supply_chain:procurement_quote_entry"),
            {"as_quoted_amount": "52.42", "as_quoted_unit": "per_pack", "round_id": "1"},
        )
    assert response.status_code == 200
    assert "No programme selected" in response.content.decode()
    # No attempt was made to record the quote -- the guard returns before
    # any parsing/validation of the submitted fields.
    assert all(call.args[0] != "quote_record" for call in mock_call.call_args_list)


def test_the_comparison_page_shows_an_unconfirmed_reason_rather_than_a_number(client, sophie):
    # Shaped like the real round_compare operation's snapshot (comparison.py,
    # Comparison.to_snapshot()) — comparable/blocked/all_rows, not a flat
    # "rows" list. A mock describing a shape the real operation never
    # returns asserts nothing about the real contract.
    row = {
        "quote_id": 1,
        "supplier_id": 2,
        "supplier_name": "Harmattan Foods",
        "is_comparable": False,
        "figures": {"usd_per_base_unit": {"unconfirmed": ["pack spec not stated on the quote"]}},
        "compliance": [],
        "questions": [{"key": "pack_spec", "question": "How many sachets are in one carton?", "audience": "supplier"}],
    }
    snapshot = {
        "round_id": 1,
        "generated_at": "2026-09-11T00:00:00+00:00",
        "comparable_count": 0,
        "total_count": 1,
        "ranked_by": "landed_total_for_round_quantity",
        "provisional": True,
        "columns": [
            {
                "key": "usd_per_base_unit",
                "label": "USD per sachet",
                "rankable": False,
                "blocked_by": ["Harmattan Foods"],
            }
        ],
        "comparable": [],
        "blocked": [row],
        "all_rows": [row],
    }
    with patch("connect_labs.supply_chain.procurement.views.call_operation", return_value=snapshot):
        response = client.get(reverse("supply_chain:procurement_comparison", args=[1]) + "?commodity=rutf")
    body = response.content.decode()
    assert "pack spec not stated" in body
    assert "Harmattan Foods" in body


def test_the_comparison_page_uses_house_tailwind_not_bootstrap(client, sophie):
    """Finding 6: the centrepiece screen was rendering unstyled HTML because
    it used Bootstrap classes that do not exist in this Tailwind-only
    project's CSS. `.base-table` is the house table class. Needs at least one
    comparable row -- an all-blocked snapshot never renders the table at all."""
    comparable_row = {
        "quote_id": 5,
        "supplier_id": 1,
        "supplier_name": "Northwind Nutrition",
        "is_comparable": True,
        "figures": {"usd_per_base_unit": {"amount": "0.3333", "currency": "USD"}},
        "compliance": [],
        "questions": [],
    }
    blocked_row = {
        "quote_id": 1,
        "supplier_id": 2,
        "supplier_name": "Harmattan Foods",
        "is_comparable": False,
        "figures": {"usd_per_base_unit": {"unconfirmed": ["pack spec not stated on the quote"]}},
        "compliance": [],
        "questions": [{"key": "pack_spec", "question": "How many sachets are in one carton?", "audience": "supplier"}],
    }
    snapshot = {
        "round_id": 1,
        "generated_at": "2026-09-11T00:00:00+00:00",
        "comparable_count": 1,
        "total_count": 2,
        "ranked_by": "usd_per_base_unit",
        "provisional": True,
        "columns": [
            {
                "key": "usd_per_base_unit",
                "label": "USD per sachet",
                "rankable": True,
                "blocked_by": ["Harmattan Foods"],
            }
        ],
        "comparable": [comparable_row],
        "blocked": [blocked_row],
        "all_rows": [comparable_row, blocked_row],
    }
    with patch("connect_labs.supply_chain.procurement.views.call_operation", return_value=snapshot):
        response = client.get(reverse("supply_chain:procurement_comparison", args=[1]) + "?commodity=rutf")
    body = response.content.decode()
    for bootstrap_class in (
        "alert alert-warning",
        "btn btn-sm btn-primary",
        "form-control",
        "text-muted",
        "d-flex gap-1",
    ):
        assert bootstrap_class not in body
    assert "base-table" in body


def test_the_comparison_page_shows_outstanding_questions_for_a_comparable_row(client, sophie):
    """Finding 7: acceptance facts (shelf life, MOQ, lead time, validity)
    don't block a figure, so a fully-priced Comparable row can still be
    missing one -- that must not be swallowed just because the row is
    comparable. Also proves comparison.provisional (not a re-derived
    comparable_count < total_count) and ranked_by drive the page."""
    comparable_row = {
        "quote_id": 7,
        "supplier_id": 3,
        "supplier_name": "Northwind Nutrition",
        "is_comparable": True,
        "figures": {"usd_per_base_unit": {"amount": "0.3333", "currency": "USD"}},
        "compliance": [],
        "questions": [
            {
                "key": "shelf_life",
                "question": "What is the shelf life from date of manufacture?",
                "audience": "supplier",
            }
        ],
    }
    snapshot = {
        "round_id": 1,
        "generated_at": "2026-09-11T00:00:00+00:00",
        "comparable_count": 1,
        "total_count": 1,
        "ranked_by": "usd_per_base_unit",
        "provisional": False,
        "columns": [{"key": "usd_per_base_unit", "label": "USD per sachet", "rankable": True, "blocked_by": []}],
        "comparable": [comparable_row],
        "blocked": [],
        "all_rows": [comparable_row],
    }
    with patch("connect_labs.supply_chain.procurement.views.call_operation", return_value=snapshot):
        response = client.get(reverse("supply_chain:procurement_comparison", args=[1]) + "?commodity=rutf")
    body = response.content.decode()
    assert response.status_code == 200
    assert "Northwind Nutrition" in body
    assert "What is the shelf life from date of manufacture?" in body
    assert "Ranked by USD per sachet" in body
    # provisional is False -- the PROVISIONAL badge must not render.
    assert "PROVISIONAL" not in body


def test_award_post_calls_the_operation(client, sophie):
    with patch("connect_labs.supply_chain.procurement.views.call_operation") as mock_call:
        mock_call.return_value = {"id": 99, "round_id": 1, "quote_id": 5, "rationale": "cheapest defensible option"}
        response = client.post(
            reverse("supply_chain:procurement_comparison", args=[1]) + "?commodity=rutf",
            {"quote_id": "5", "rationale": "cheapest defensible option"},
        )
    assert response.status_code == 302
    assert response.url.startswith(reverse("supply_chain:procurement_comparison", args=[1]))
    mock_call.assert_called_once()
    name, access, payload = mock_call.call_args[0]
    assert name == "award_create"
    assert payload["round_id"] == 1
    assert payload["quote_id"] == 5
    assert payload["rationale"] == "cheapest defensible option"


def test_award_post_without_a_rationale_does_not_500(client, sophie):
    def _reject_missing_rationale(name, access, payload):
        if name == "award_create":
            raise jsonschema.ValidationError("'rationale' is a required property")
        return {"comparable": [], "blocked": [], "all_rows": [], "comparable_count": 0, "total_count": 0}

    with patch(
        "connect_labs.supply_chain.procurement.views.call_operation",
        side_effect=_reject_missing_rationale,
    ):
        response = client.post(
            reverse("supply_chain:procurement_comparison", args=[1]) + "?commodity=rutf",
            {"quote_id": "5", "rationale": ""},
        )
    assert response.status_code == 200
    assert "rationale" in response.content.decode().lower()


def test_quote_entry_post_with_a_malformed_amount_does_not_500(client, sophie):
    """A European decimal comma ("52,42" for "52.42") is an ordinary typo for
    someone transcribing a supplier's quote by hand, not a reason to 500.

    Dispatches "quote_record" to the REAL call_operation, so this exercises
    the actual MONEY_NONZERO pattern in operations.py rather than a guess at what it
    rejects — and confirms the rejection happens before anything is written
    (SupplyDataAccess.create_quote is never reached: jsonschema.validate
    raises first).
    """

    def _dispatch(name, access, payload):
        if name == "quote_record":
            return real_call_operation(name, access, payload)
        return []

    # has_program_context patched True: this test is about the malformed-
    # amount rejection, orthogonal to finding 3/C's no-programme guard, and
    # `sophie` carries no programme context by default.
    with (
        patch("connect_labs.supply_chain.procurement.views.call_operation", side_effect=_dispatch),
        patch("connect_labs.supply_chain.procurement.views.has_program_context", return_value=True),
    ):
        response = client.post(
            reverse("supply_chain:procurement_quote_entry"),
            {"as_quoted_amount": "52,42", "as_quoted_unit": "per_pack", "as_quoted_currency": "USD"},
        )
    assert response.status_code == 200
    body = response.content.decode()
    assert "52,42" in body


def test_quote_entry_post_preserves_entered_values_on_error(client, sophie):
    """Losing the form on a validation error means re-typing the whole quote."""

    def _dispatch(name, access, payload):
        if name == "quote_record":
            return real_call_operation(name, access, payload)
        if name == "commodity_list":
            return [{"id": 1, "slug": "rutf", "name": "RUTF"}]
        return []

    with (
        patch("connect_labs.supply_chain.procurement.views.call_operation", side_effect=_dispatch),
        patch("connect_labs.supply_chain.procurement.views.has_program_context", return_value=True),
    ):
        response = client.post(
            reverse("supply_chain:procurement_quote_entry"),
            {
                "as_quoted_amount": "52,42",
                "as_quoted_unit": "per_pack",
                "as_quoted_currency": "EUR",
                "commodity_slug": "rutf",
                "quantity_basis": "667",
            },
        )
    body = response.content.decode()
    assert response.status_code == 200
    assert 'value="52,42"' in body
    assert 'value="EUR"' in body
    assert 'value="667"' in body
    assert 'value="rutf"' in body and "selected" in body


def test_a_quoted_submitted_value_cannot_break_out_of_the_x_data_js_context(client, sophie):
    """The previously-submitted pack_spec_source used to be interpolated
    straight into an `x-data="quoteEntryForm('...')"` JS-string literal.
    Django HTML-escapes `'` to `&#x27;`, but the browser HTML-decodes an
    attribute value BEFORE Alpine evaluates x-data as JavaScript — so a
    submitted value containing a quote could break out of that string and
    inject arbitrary JS. Regression guard: the value must never again be
    interpolated into any JS expression at all, only carried in a data-*
    attribute (HTML-escaped, decoded back to an inert string by .dataset,
    never re-parsed as code).
    """
    payload = "not_stated');alert(document.cookie);//"

    def _dispatch(name, access, payload_):
        if name == "quote_record":
            return real_call_operation(name, access, payload_)
        return []

    with (
        patch("connect_labs.supply_chain.procurement.views.call_operation", side_effect=_dispatch),
        patch("connect_labs.supply_chain.procurement.views.has_program_context", return_value=True),
    ):
        response = client.post(
            reverse("supply_chain:procurement_quote_entry"),
            {"as_quoted_amount": "52,42", "pack_spec_source": payload},
        )
    assert response.status_code == 200
    body = response.content.decode()

    # The raw, unescaped payload must never appear verbatim anywhere in the
    # page — if it does, something HTML-escaped-but-JS-unsafe (or unescaped
    # entirely) let it through.
    assert payload not in body

    # x-data must carry zero interpolation — no call arguments at all — so
    # there is no JS-string context for a submitted value to land in, ever.
    assert 'x-data="quoteEntryForm()"' in body

    # The value only ever reaches the page via a data-* attribute, which is
    # HTML-escaped (single quote becomes &#x27;) and read back as an inert
    # string through $el.dataset — never evaluated as JavaScript.
    assert 'data-pack-spec-source="not_stated&#x27;);alert(document.cookie);//"' in body


def test_comparison_without_a_commodity_shows_a_chooser_instead_of_500ing(client, sophie):
    """A bookmark, browser-history entry, or shared link with no ?commodity=
    is a normal way to land here — it must not crash the schema-required
    commodity_slug straight into round_compare.
    """
    round_ = {
        "id": 1,
        "label": "Q3 RUTF round",
        "lines": [
            {"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"},
            {"commodity_slug": "amoxicillin", "quantity": "1000", "quantity_unit": "bottle"},
        ],
    }

    def _dispatch(name, access, payload):
        if name == "round_get":
            return round_
        raise AssertionError(f"round_compare must not be called with no commodity selected (got {name!r})")

    with patch("connect_labs.supply_chain.procurement.views.call_operation", side_effect=_dispatch):
        response = client.get(reverse("supply_chain:procurement_comparison", args=[1]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "rutf" in body
    assert "amoxicillin" in body


def test_comparison_without_a_commodity_defaults_when_the_round_has_one_line(client, sophie):
    round_ = {
        "id": 1,
        "label": "Q3 RUTF round",
        "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
    }
    snapshot = {
        "round_id": 1,
        "generated_at": "2026-09-11T00:00:00+00:00",
        "comparable_count": 0,
        "total_count": 0,
        "ranked_by": "usd_per_pack_normalized",
        "provisional": False,
        "columns": [],
        "comparable": [],
        "blocked": [],
        "all_rows": [],
    }
    calls = []

    def _dispatch(name, access, payload):
        calls.append((name, payload))
        if name == "round_get":
            return round_
        if name == "round_compare":
            return snapshot
        raise AssertionError(name)

    with patch("connect_labs.supply_chain.procurement.views.call_operation", side_effect=_dispatch):
        response = client.get(reverse("supply_chain:procurement_comparison", args=[1]))
    assert response.status_code == 200
    assert ("round_compare", {"round_id": 1, "commodity_slug": "rutf"}) in calls


def test_no_view_mutates_a_record_outside_an_operation():
    """The structural half of 'no capability without an operation'.

    A view that reaches for data_access directly could grow a capability the
    API and MCP surfaces do not have.

    Globbed rather than a hardcoded pair of paths (finding 16): a future
    sub-component's views.py (e.g. tracking/views.py) would otherwise sit
    silently outside this guard until someone remembered to add it here too.
    """
    called = set()
    view_files = sorted(pathlib.Path(".").glob("connect_labs/supply_chain/**/views.py"))
    assert view_files, "the glob found no views.py files -- check the pattern or the cwd pytest runs from"
    for path in view_files:
        tree = ast.parse(path.read_text())
        called |= {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
    forbidden = {
        name
        for name in called
        if name.startswith(("create_", "update_", "upsert_", "void_", "supersede_", "open_", "close_"))
    }
    assert not forbidden, f"a view mutates directly via {sorted(forbidden)}; call an operation"


def flat(response) -> str:
    """A response body with its whitespace collapsed.

    Django keeps a template's own newlines and indentation inside a sentence,
    so `"how many tablets a course is" in body` is false for markup that reads
    exactly that way on screen. Asserting on the rendered words means
    normalising first.
    """
    import re

    return re.sub(r"\s+", " ", response.content.decode())


def test_the_catalogue_says_a_product_with_no_ration_table_blocks_per_course_cost(client, sophie):
    """The gap is OURS and nobody outside can close it, so the page says so
    rather than leaving a blank where a figure would go."""
    commodities = [
        {
            "slug": "rutf",
            "name": "Ready-to-use therapeutic food",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
            "base_unit_grams": 92,
            "spec_requirements": [],
            "course_definition": {},
        }
    ]

    def _dispatch(name, access, payload):
        if name == "item_list":
            return []
        if name == "commodity_list":
            return commodities
        raise AssertionError(name)

    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_dispatch):
        response = client.get(reverse("supply_chain:catalogue"))
    body = flat(response)
    assert "No ration table set" in body
    # The full "nobody outside can answer it" explanation moved to the
    # product's own page: seven copies of it down one catalogue was the
    # permanent noise that teaches a reader to skip warnings. What the
    # catalogue must still say is that the gap blocks a figure, and that it is
    # ours to close.
    assert "cost per course and cost per child stay unconfirmed" in body
    assert "someone here says how many sachets a course is" in body


def test_the_catalogue_names_a_pack_disagreement_between_trade_items(client, sophie):
    """Two trade items under one product packed differently is the condition
    the whole trade-item layer exists for: invisible at product level, and it
    silently corrupts every per-base-unit comparison."""
    commodities = [
        {
            "slug": "rutf",
            "name": "RUTF",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
            "spec_requirements": [],
            "course_definition": {},
        }
    ]
    items = [
        {
            "id": 1,
            "sku": "a",
            "name": "A",
            "commodity_slug": "rutf",
            "base_per_pack": 150,
            "spec_attributes": {},
            "status": "active",
        },
        {
            "id": 2,
            "sku": "b",
            "name": "B",
            "commodity_slug": "rutf",
            "base_per_pack": 144,
            "spec_attributes": {},
            "status": "active",
        },
    ]

    def _dispatch(name, access, payload):
        if name == "item_list":
            return items
        if name == "commodity_list":
            return commodities
        raise AssertionError(name)

    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_dispatch):
        response = client.get(reverse("supply_chain:catalogue"))
    body = response.content.decode()
    # Substring stops before the template's line break rather than spanning it.
    assert "packed 144 and 150 to the" in body
    assert "is not one number" in body


def test_the_comparison_page_attributes_an_uncomputable_column_to_us_not_a_supplier(client, sophie):
    """Cost per course needs the commodity's ration table -- our treatment
    protocol, not anything a supplier states. It used to make every supplier
    read as BLOCKED on a gap they could not close. The page now says once, at
    the top, that the column is ours to close and is not part of the
    comparison, while the supplier stays comparable.

    Shaped like the real round_compare snapshot (Comparison.to_snapshot()); a
    mock describing a shape the operation never returns asserts nothing.
    """
    row = {
        "quote_id": 5,
        "supplier_id": 1,
        "supplier_name": "Harmattan Foods",
        "is_comparable": True,
        "figures": {
            "landed_total_for_round_quantity": {"amount": "100000.00", "currency": "USD"},
            "usd_per_course": {"unconfirmed": ["no course definition set for RUTF (sachets per course)"]},
        },
        "compliance": [],
        "questions": [],
    }
    snapshot = {
        "round_id": 1,
        "generated_at": "2026-09-12T00:00:00+00:00",
        "comparable_count": 1,
        "total_count": 1,
        "ranked_by": "landed_total_for_round_quantity",
        "provisional": False,
        "unavailable": {
            "usd_per_course": {
                "label": "USD per course",
                "reasons": ["no course definition set for RUTF (sachets per course)"],
            }
        },
        "columns": [
            {
                "key": "landed_total_for_round_quantity",
                "label": "Landed total (this round)",
                "rankable": True,
                "blocked_by": [],
            }
        ],
        "comparable": [row],
        "blocked": [],
        "all_rows": [row],
    }
    with patch("connect_labs.supply_chain.procurement.views.call_operation", return_value=snapshot):
        response = client.get(reverse("supply_chain:procurement_comparison", args=[1]) + "?commodity=rutf")
    body = response.content.decode()

    assert "Ours to close, not theirs" in body
    assert "no course definition set" in body
    # And it must NOT be dressed up as the supplier's problem.
    assert "PROVISIONAL" not in body
    assert "not given us enough to compare" not in body


def test_a_comparable_row_never_renders_an_unconfirmed_figure_as_a_blank(client, sophie):
    """Comparability no longer waits on figures only we can supply, so a
    COMPARABLE row can now hold an unconfirmed cell -- a state that was
    unreachable before. The comparable table rendered `cell.amount` for it
    unconditionally, producing an empty cell, which reads as "no cost per
    course" rather than "we have not set the ration table". A blank is the
    one thing the Unconfirmed type exists to prevent."""
    row = {
        "quote_id": 5,
        "supplier_id": 1,
        "supplier_name": "Harmattan Foods",
        "is_comparable": True,
        "figures": {
            "landed_total_for_round_quantity": {"amount": "100000.00", "currency": "USD"},
            "usd_per_course": {"unconfirmed": ["no course definition set for RUTF (sachets per course)"]},
        },
        "compliance": [],
        "questions": [],
    }
    snapshot = {
        "round_id": 1,
        "generated_at": "2026-09-12T00:00:00+00:00",
        "comparable_count": 1,
        "total_count": 1,
        "ranked_by": "landed_total_for_round_quantity",
        "provisional": False,
        "unavailable": {
            "usd_per_course": {"label": "USD per course", "reasons": ["no course definition set for RUTF"]}
        },
        "columns": [
            {
                "key": "landed_total_for_round_quantity",
                "label": "Landed total (this round)",
                "rankable": True,
                "blocked_by": [],
            },
            {"key": "usd_per_course", "label": "USD per course", "rankable": False, "blocked_by": []},
        ],
        "comparable": [row],
        "blocked": [],
        "all_rows": [row],
    }
    with patch("connect_labs.supply_chain.procurement.views.call_operation", return_value=snapshot):
        response = client.get(reverse("supply_chain:procurement_comparison", args=[1]) + "?commodity=rutf")
    body = response.content.decode()

    assert "Unconfirmed" in body, "the cell rendered blank instead of saying it is unconfirmed"
    # The confirmed figure on the same row still renders as a number.
    assert "100000" in body


def test_with_nothing_comparable_the_page_does_not_claim_a_provisional_ranking(client, sophie):
    """The first thing the real imported tracker shows is `0 of 2 comparable`,
    and the banner said "The ranking below is PROVISIONAL -- <suppliers> have
    not given us enough to compare, and could still beat it" directly above a
    section reading "Nothing is comparable yet."

    There is no ranking below and no leader to beat. PROVISIONAL qualifies a
    ranking, so with nothing ranked the badge asserts something that does not
    exist. The suppliers must still be named -- who has to answer is the
    useful half -- but the sentence has to be true.
    """
    blocked = {
        "quote_id": 2,
        "supplier_id": 2,
        "supplier_name": "EHA Clinics",
        "is_comparable": False,
        "figures": {"usd_per_pack_normalized": {"unconfirmed": ["pack spec not stated on the quote"]}},
        "compliance": [],
        "questions": [{"key": "pack_spec", "question": "How many sachets are in one carton?", "audience": "supplier"}],
    }
    snapshot = {
        "round_id": 1,
        "generated_at": "2026-09-12T00:00:00+00:00",
        "comparable_count": 0,
        "total_count": 2,
        "ranked_by": None,
        "provisional": True,
        "unavailable": {},
        "columns": [
            {
                "key": "usd_per_pack_normalized",
                "label": "USD per carton",
                "rankable": False,
                "blocked_by": ["EHA Clinics"],
            }
        ],
        "comparable": [],
        "blocked": [blocked, dict(blocked, quote_id=1, supplier_id=1, supplier_name="DABS")],
        "all_rows": [blocked],
    }
    with patch("connect_labs.supply_chain.procurement.views.call_operation", return_value=snapshot):
        response = client.get(reverse("supply_chain:procurement_comparison", args=[1]) + "?commodity=rutf")
    body = response.content.decode()

    assert "0 of 2 comparable" in body
    # The replacement sentence itself, not just the absence of the wrong one:
    # the count renders either way and the supplier names also appear in the
    # "Needs info" cards below, so without this the whole no-ranking branch
    # could be deleted and every other assertion here would still hold.
    assert "There is no ranking yet" in body
    # Who has to answer is still reported.
    assert "EHA Clinics" in body
    assert "DABS" in body
    # But nothing is ranked, so nothing can be provisionally ranked or beaten.
    assert "PROVISIONAL" not in body
    assert "could still beat it" not in body


def test_the_quote_page_asks_for_a_programme_rather_than_raising(client, sophie):
    """Every programme-scoped view in this app guards this; the quote page was
    added without it. Unguarded, `quote_get` reaches `_require_program` and
    raises ValueError -- a 500 on a page reached by an ordinary link, where
    the honest answer is "choose a programme".

    The operation must not be called at all: reaching it is the failure.
    """
    with patch("connect_labs.supply_chain.procurement.views.call_operation") as op:
        response = client.get(reverse("supply_chain:procurement_quote_detail", args=[1]))

    assert response.status_code == 200
    assert "No programme selected" in response.content.decode()
    assert not op.called, "a programme-scoped operation ran without a programme"


@pytest.mark.parametrize(
    "url_name,args",
    [
        ("supply_chain:order_detail", [9999]),
        ("supply_chain:procurement_round_detail", [9999]),
        ("supply_chain:procurement_comparison", [9999]),
        ("supply_chain:procurement_quote_detail", [9999]),
    ],
)
def test_a_detail_page_for_something_that_is_not_here_is_a_404(client, sophie, url_name, args):
    """Found by walking every page with a made-up id, after shipping a
    template that raised on a page I had never opened.

    Three of these four were wrong and in two different ways. The order and
    comparison pages tolerated a missing record as far as a DERIVATION, which
    then raised -- so a stale bookmark returned a 500 that named nothing. The
    round page answered 200 with "Round not found", which tells a browser, a
    link checker and an uptime monitor that the page is fine, and that is the
    one thing it is not.
    """
    with patch("connect_labs.supply_chain.views.has_program_context", return_value=True), patch(
        "connect_labs.supply_chain.procurement.views.has_program_context", return_value=True
    ), patch("connect_labs.supply_chain.views.call_operation", return_value=None), patch(
        "connect_labs.supply_chain.procurement.views.call_operation", return_value=None
    ):
        response = client.get(reverse(url_name, args=args))

    assert response.status_code == 404, f"{url_name} returned {response.status_code}"


# --- Click-through: a catalogue you can only read is a document, not a
# catalogue. These pin the destinations, because a link whose target 404s or
# 500s is worse than no link -- it looks like the data is missing.


PRODUCT = {
    "slug": "rutf",
    "name": "Ready-to-use therapeutic food",
    "category": "therapeutic_food",
    "base_unit": "sachet",
    "pack_unit": "carton",
    "base_per_pack": 150,
    "base_unit_grams": 92,
    "spec_requirements": [],
    "course_definition": {},
}
TRADE_ITEM = {
    "id": 7,
    "sku": "RUTF-NW-92",
    "name": "Northwind RUTF",
    "commodity_slug": "rutf",
    "manufacturer": "Northwind Foods",
    "base_per_pack": 150,
    "base_unit_grams": 92,
    "spec_attributes": {},
    "status": "active",
}
SUPPLIER = {"id": 1, "name": "Northwind Foods", "type": "manufacturer", "country": "NG", "status": "quoting"}
QUOTE = {
    "id": 10,
    "round_id": 5,
    "supplier_id": 1,
    "item_id": 7,
    "commodity_slug": "rutf",
    "as_quoted_amount": "0.46",
    "as_quoted_unit": "per_base_unit",
    "as_quoted_currency": "USD",
    "received_on": "2026-05-01",
    "voided": False,
    "superseded_by_quote_id": None,
}
CLAIM = {
    "supplier_id": 1,
    "supplier_name": "Northwind Foods",
    "supplier_country": "NG",
    "supplier_status": "quoting",
    "basis": "quoted",
    "last_heard": "2026-05-01",
    "evidence": [{"kind": "quoted", "detail": "USD 0.46 per base unit", "on": "2026-05-01", "quote_id": 10}],
}

_CATALOGUE_RESPONSES = {
    "commodity_list": [PRODUCT],
    "item_list": [TRADE_ITEM],
    "item_get": TRADE_ITEM,
    "supplier_list": [SUPPLIER],
    "supplier_get": SUPPLIER,
    "round_list": [{"id": 5, "label": "Round 1", "status": "open", "lines": [{"commodity_slug": "rutf"}]}],
    "quote_list": [QUOTE],
    "quote_get": {"quote": QUOTE, "figures": {}, "missing": []},
    "contract_list": [],
    "outreach_list": [],
    "award_list": [],
    "document_list": [],
    "shipment_list": [],
    "receipt_list": [],
    "invoice_list": [],
    "network_stock": {"summary": {}, "points": []},
    "commodity_supply_base": [CLAIM],
}


def _catalogue_dispatch(name, access, payload):
    if name not in _CATALOGUE_RESPONSES:
        raise AssertionError(f"unexpected operation {name}")
    return _CATALOGUE_RESPONSES[name]


# Everything below the reference tier needs a programme. A test that asserts
# "renders with no programme selected" while its stub happily answers
# `quote_list` proves nothing -- the view could be calling straight through to
# a real SupplyDataAccess and the stub would hide it. So the no-programme
# dispatcher refuses these by name.
PROGRAMME_SCOPED_OPS = frozenset(
    {
        "quote_list",
        "quote_get",
        "contract_list",
        "outreach_list",
        "award_list",
        "round_list",
        "document_list",
        "network_stock",
        "commodity_supply_base",
        "shipment_list",
        "receipt_list",
        "invoice_list",
    }
)


def _no_programme_dispatch(name, access, payload):
    if name in PROGRAMME_SCOPED_OPS:
        raise AssertionError(f"{name} was called with no programme selected")
    return _catalogue_dispatch(name, access, payload)


def _with_programme(monkeypatch):
    monkeypatch.setattr("connect_labs.supply_chain.views.has_program_context", lambda request: True)


def test_the_catalogue_links_to_each_product_and_trade_item(client, sophie):
    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_catalogue_dispatch):
        response = client.get(reverse("supply_chain:catalogue"))
    body = response.content.decode()
    assert reverse("supply_chain:product_detail", args=["rutf"]) in body
    assert reverse("supply_chain:item_detail", args=[7]) in body


def test_a_product_page_names_who_can_supply_it_and_on_what_evidence(client, sophie, monkeypatch):
    _with_programme(monkeypatch)
    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_catalogue_dispatch):
        response = client.get(reverse("supply_chain:product_detail", args=["rutf"]))
    body = response.content.decode()
    assert response.status_code == 200
    assert "Who can supply this" in body
    assert "Northwind Foods" in body
    # The evidence, not just the name: "quoted" and "under contract" are not
    # the same claim and the page must not flatten them.
    assert "Quoted" in body
    assert reverse("supply_chain:supplier_detail", args=[1]) in body


def test_a_product_that_is_not_in_the_catalogue_is_a_404_not_a_500(client, sophie, monkeypatch):
    _with_programme(monkeypatch)
    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_catalogue_dispatch):
        response = client.get(reverse("supply_chain:product_detail", args=["no-such-thing"]))
    assert response.status_code == 404


def test_the_product_page_renders_its_specification_with_no_programme_selected(client, sophie):
    """The specification is reference data and reads on its own; only the
    programme-scoped half is withheld."""
    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_no_programme_dispatch):
        response = client.get(reverse("supply_chain:product_detail", args=["rutf"]))
    body = response.content.decode()
    assert response.status_code == 200
    assert "What it must be" in body
    assert "Who can supply this" not in body


def test_a_trade_item_page_shows_its_pack_configuration_and_where_it_is(client, sophie, monkeypatch):
    _with_programme(monkeypatch)
    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_catalogue_dispatch):
        response = client.get(reverse("supply_chain:item_detail", args=[7]))
    body = response.content.decode()
    assert response.status_code == 200
    assert "How it is packed and identified" in body
    assert "Where it is now" in body
    assert reverse("supply_chain:product_detail", args=["rutf"]) in body


def test_a_trade_item_that_does_not_exist_is_a_404(client, sophie, monkeypatch):
    _with_programme(monkeypatch)

    def _dispatch(name, access, payload):
        if name == "item_get":
            return None
        return _catalogue_dispatch(name, access, payload)

    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_dispatch):
        response = client.get(reverse("supply_chain:item_detail", args=[999]))
    assert response.status_code == 404


def test_the_supplier_directory_links_to_each_supplier(client, sophie, monkeypatch):
    _with_programme(monkeypatch)
    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_catalogue_dispatch):
        response = client.get(reverse("supply_chain:suppliers"))
    body = response.content.decode()
    assert response.status_code == 200
    assert reverse("supply_chain:supplier_detail", args=[1]) in body


def test_a_supplier_page_gathers_the_history_that_was_spread_over_four_screens(client, sophie, monkeypatch):
    _with_programme(monkeypatch)
    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_catalogue_dispatch):
        response = client.get(reverse("supply_chain:supplier_detail", args=[1]))
    body = response.content.decode()
    assert response.status_code == 200
    for heading in ("What they supply", "What we asked them for", "What they quoted", "Orders with them"):
        assert heading in body
    assert reverse("supply_chain:procurement_quote_detail", args=[10]) in body


def test_a_supplier_that_is_not_on_file_is_a_404(client, sophie, monkeypatch):
    _with_programme(monkeypatch)

    def _dispatch(name, access, payload):
        if name == "supplier_get":
            return None
        return _catalogue_dispatch(name, access, payload)

    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_dispatch):
        response = client.get(reverse("supply_chain:supplier_detail", args=[999]))
    assert response.status_code == 404


def test_the_new_pages_do_not_500_with_no_programme_selected(client, sophie):
    """Same guard as finding 3, for the three routes added with the
    click-through. Each calls programme-scoped operations, so an unguarded
    one raises deep in SupplyDataAccess rather than saying to pick a
    programme."""
    with patch("connect_labs.supply_chain.views.call_operation", side_effect=_no_programme_dispatch):
        for url in (
            reverse("supply_chain:suppliers"),
            reverse("supply_chain:supplier_detail", args=[1]),
            reverse("supply_chain:item_detail", args=[7]),
            reverse("supply_chain:product_detail", args=["rutf"]),
        ):
            assert client.get(url).status_code == 200, url


def test_the_round_detail_page_links_each_supplier_it_names(client, sophie):
    """Both tables on it name a supplier, and neither was a link. Rendered
    here rather than trusted: `{% url %}` with a missing id is a 500, not a
    missing link, so an untested link is worse than none."""
    responses = {
        "round_get": {"id": 5, "label": "Round 1", "status": "open", "lines": [{"commodity_slug": "rutf"}]},
        "outreach_list": [{"id": 1, "round_id": 5, "supplier_id": 1, "sent_on": "2026-04-28", "responded": False}],
        "quote_list": [QUOTE],
        "supplier_list": [SUPPLIER],
    }
    with patch(
        "connect_labs.supply_chain.procurement.views.call_operation",
        side_effect=lambda name, access, payload: responses[name],
    ):
        response = client.get(reverse("supply_chain:procurement_round_detail", args=[5]))
    body = response.content.decode()
    assert response.status_code == 200
    assert reverse("supply_chain:supplier_detail", args=[1]) in body


def test_an_order_links_out_to_the_supplier_it_is_with(client, sophie, monkeypatch):
    _with_programme(monkeypatch)
    contract = {
        "id": 3,
        "supplier_id": 1,
        "commodity_slug": "rutf",
        "buyer_of_record": "partner_org",
        "buyer_org_id": 2,
        "status": "placed",
        "currency": "USD",
        "reference": "PO-114",
        "duty_relief_claimed": False,
        "source": "we_recorded",
        "witnessed": True,
    }
    responses = {
        "contract_get": contract,
        "contract_landed_cost": {"buyers": [], "landed_total": None},
        "contract_match": {"lines": [], "payable_now": None},
        "shipment_list": [],
        "receipt_list": [],
        "invoice_list": [],
        "document_list": [],
        "org_list": [{"id": 2, "name": "Dimagi", "slug": "dimagi"}],
        "supplier_list": [SUPPLIER],
    }
    with patch(
        "connect_labs.supply_chain.views.call_operation",
        side_effect=lambda name, access, payload: responses[name],
    ):
        response = client.get(reverse("supply_chain:order_detail", args=[3]))
    assert response.status_code == 200
    assert reverse("supply_chain:supplier_detail", args=[1]) in response.content.decode()


class TestReadingOrderAndDeadWarnings:
    """Polish found by looking at the deployed pages rather than by reasoning
    about them — which is the only way any of these four surfaced."""

    def test_a_withdrawn_quote_never_sits_above_the_one_that_replaced_it(self):
        from connect_labs.supply_chain.views import newest_standing_first

        quotes = [
            {"id": 6, "received_on": "2026-09-10", "voided": True, "superseded_by_quote_id": None},
            {"id": 3, "received_on": "2026-09-10", "voided": False, "superseded_by_quote_id": None},
            {"id": 1, "received_on": "2026-05-18", "voided": False, "superseded_by_quote_id": 2},
            {"id": 2, "received_on": "2026-05-18", "voided": False, "superseded_by_quote_id": None},
        ]
        assert [q["id"] for q in newest_standing_first(quotes)] == [3, 2, 1, 6]

    def test_equipment_is_not_warned_about_a_weight_and_an_expiry_it_cannot_have(self, client, sophie):
        board = {
            "slug": "height-board",
            "name": "Height and length measuring board, child",
            "category": "equipment",
            "base_unit": "board",
            "pack_unit": "unit",
            "base_per_pack": 1,
            "base_unit_grams": None,
            "shelf_life_months_minimum": None,
            "spec_requirements": [],
            "course_definition": {},
        }

        def _dispatch(name, access, payload):
            if name == "commodity_list":
                return [board]
            if name == "item_list":
                return []
            raise AssertionError(name)

        with patch("connect_labs.supply_chain.views.call_operation", side_effect=_dispatch):
            body = flat(client.get(reverse("supply_chain:catalogue")))
        assert "does not expire" in body
        assert "not applicable" in body
        # And no ration-table warning either, which the category rule already
        # handled -- pinned here so the two stay consistent.
        assert "No ration table set" not in body
        # "1 boards / unit" was the naive pluralisation.
        assert "1 board / unit" in body

    def test_a_ration_table_warning_uses_the_products_own_unit_noun(self, client, sophie):
        """It said "sachets per day" against a box of tablets."""
        tablets = {
            "slug": "amoxicillin-dt-250",
            "name": "Amoxicillin dispersible tablets, 250 mg",
            "category": "antibiotic",
            "base_unit": "tablet",
            "pack_unit": "box",
            "base_per_pack": 100,
            "shelf_life_months_minimum": 24,
            "spec_requirements": [],
            "course_definition": {},
        }

        def _dispatch(name, access, payload):
            if name == "commodity_list":
                return [tablets]
            if name == "item_list":
                return []
            raise AssertionError(name)

        with patch("connect_labs.supply_chain.views.call_operation", side_effect=_dispatch):
            body = flat(client.get(reverse("supply_chain:catalogue")))
        assert "how many tablets a course is" in body
        assert "sachets" not in body
