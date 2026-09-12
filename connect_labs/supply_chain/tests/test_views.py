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
        "questions": [{"key": "pack_spec", "question": "How many sachets are in one carton?"}],
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

    with patch("connect_labs.supply_chain.procurement.views.call_operation", side_effect=_dispatch):
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

    with patch("connect_labs.supply_chain.procurement.views.call_operation", side_effect=_dispatch):
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

    with patch("connect_labs.supply_chain.procurement.views.call_operation", side_effect=_dispatch):
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
    """
    called = set()
    for path in (
        "connect_labs/supply_chain/views.py",
        "connect_labs/supply_chain/procurement/views.py",
    ):
        tree = ast.parse(pathlib.Path(path).read_text())
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
