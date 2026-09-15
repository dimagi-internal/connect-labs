import json
from unittest.mock import patch

import pytest
from django.urls import reverse

from connect_labs.supply_chain.operations import agent_operations, all_operations

pytestmark = pytest.mark.django_db


def test_the_discovery_endpoint_lists_every_operation_a_caller_may_reach(client, django_user_model):
    """`agent_operations`, not `all_operations` — the same list the MCP server
    builds its tools from, so the two surfaces offer exactly the same set."""
    user = django_user_model.objects.create_user(username="sophie", password="x")
    client.force_login(user)
    response = client.get(reverse("supply_chain:api_operations"))
    assert response.status_code == 200
    listed = {row["name"] for row in response.json()["operations"]}
    assert listed == set(agent_operations())


def test_the_internal_operations_are_not_advertised(client, django_user_model):
    internal = {name for name, op in all_operations().items() if op.internal}
    assert internal, "if nothing is internal any more, this test is measuring nothing"

    user = django_user_model.objects.create_user(username="sophie", password="x")
    client.force_login(user)
    listed = {row["name"] for row in client.get(reverse("supply_chain:api_operations")).json()["operations"]}
    assert not (internal & listed)


@pytest.mark.parametrize("name", sorted(n for n, op in all_operations().items() if op.internal))
def test_an_internal_operation_cannot_be_posted_to(client, django_user_model, name):
    """The `internal=True` flag used to do half its job.

    It kept seeds, bulk imports and ingests off the MCP catalogue while leaving
    them POST-able here by any signed-in user — so `catalogue_seed` against
    somebody else's programme was one curl away. Parametrised over the registry
    rather than over a list, so an operation marked internal later is covered
    without anyone remembering this file exists.
    """
    user = django_user_model.objects.create_user(username="sophie", password="x")
    client.force_login(user)
    with patch("connect_labs.supply_chain.api_views.call_operation") as called:
        response = client.post(
            reverse("supply_chain:api_operation", args=[name]),
            data="{}",
            content_type="application/json",
        )
    assert response.status_code == 404
    assert not called.called, "refused before dispatch, not after"


def test_an_internal_operation_is_refused_the_same_way_an_unknown_name_is(client, django_user_model):
    """Same status, so probing tells a caller nothing about what exists."""
    user = django_user_model.objects.create_user(username="sophie", password="x")
    client.force_login(user)
    internal = next(name for name, op in all_operations().items() if op.internal)

    hidden = client.post(
        reverse("supply_chain:api_operation", args=[internal]), data="{}", content_type="application/json"
    )
    unknown = client.post(
        reverse("supply_chain:api_operation", args=["nope"]), data="{}", content_type="application/json"
    )
    assert hidden.status_code == unknown.status_code == 404


def test_an_internal_operation_still_runs_through_call_operation(django_user_model):
    """The flag hides it from REQUESTS, not from the registry.

    Its management command calls `call_operation` directly, and would lose
    schema validation and provenance stamping if the operation were deleted
    rather than hidden."""
    from connect_labs.supply_chain.operations import get_operation

    assert get_operation("catalogue_seed").internal
    assert get_operation("catalogue_seed").handler is not None


def test_posting_to_an_operation_calls_it(client, django_user_model):
    user = django_user_model.objects.create_user(username="sophie", password="x")
    client.force_login(user)
    with patch("connect_labs.supply_chain.api_views.call_operation", return_value=[]) as called:
        response = client.post(
            reverse("supply_chain:api_operation", args=["supplier_list"]),
            data=json.dumps({"search": "northwind"}),
            content_type="application/json",
        )
    assert response.status_code == 200
    assert called.call_args.args[0] == "supplier_list"


def test_an_unknown_operation_is_a_404(client, django_user_model):
    user = django_user_model.objects.create_user(username="sophie", password="x")
    client.force_login(user)
    response = client.post(
        reverse("supply_chain:api_operation", args=["nope"]),
        data="{}",
        content_type="application/json",
    )
    assert response.status_code == 404


def test_a_schema_violation_is_a_400_naming_the_problem(client, django_user_model):
    user = django_user_model.objects.create_user(username="sophie", password="x")
    client.force_login(user)
    response = client.post(
        reverse("supply_chain:api_operation", args=["supplier_create"]),
        data=json.dumps({"wrong": 1}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_anonymous_requests_are_rejected(client):
    response = client.get(reverse("supply_chain:api_operations"))
    assert response.status_code in (302, 401, 403)
