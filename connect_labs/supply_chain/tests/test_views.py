import ast
import pathlib
from unittest.mock import patch

import jsonschema
import pytest
from django.urls import reverse

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
