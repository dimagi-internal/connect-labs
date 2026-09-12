import json
from unittest.mock import patch

import pytest
from django.urls import reverse

from connect_labs.supply_chain.operations import all_operations

pytestmark = pytest.mark.django_db


def test_the_discovery_endpoint_lists_every_operation(client, django_user_model):
    user = django_user_model.objects.create_user(username="sophie", password="x")
    client.force_login(user)
    response = client.get(reverse("supply_chain:api_operations"))
    assert response.status_code == 200
    listed = {row["name"] for row in response.json()["operations"]}
    assert listed == set(all_operations())


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
