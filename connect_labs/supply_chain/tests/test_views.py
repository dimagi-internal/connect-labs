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
    body = response.content.decode()
    assert "No ration table" in body
    assert "Nobody outside can answer it" in body


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
