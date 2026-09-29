"""A column click on a long-open program report survives another tab moving the session.

The labs scope lives in the session, so every tab shares it. Workflow 6371 is owned
by program 10082; after an audit tab switched the session to opportunity 1978, the
report's next column click read the definition at opportunity scope and 404'd
"Workflow not found" -- mid-demo. The page already sends its owner as
owning_program_id; the endpoint now retries there, and reads the registry there too.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class _Def:
    pass


def _get(client, django_user_model, qs):
    user = django_user_model.objects.create_user(username="u", password="p")
    client.force_login(user)
    constructed = []

    class _WDA:
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            self.program_id = kwargs.get("program_id")

        def get_definition(self, definition_id):
            # Only the program-scoped read can see a program-owned definition.
            return _Def() if self.program_id == 10082 else None

        def close(self):
            pass

    registry_scopes = []

    def _resolve(definition, registry_access_factory):
        registry_access_factory()
        return {}, {"measures": []}, {}, {}, None, {"registry_id": 6369}

    with (
        patch("connect_labs.workflow.views.WorkflowDataAccess", _WDA),
        patch(
            "connect_labs.workflow.data_access.SemanticRegistryDataAccess",
            side_effect=lambda **kw: registry_scopes.append(kw),
        ),
        patch("connect_labs.semantic.workflow_binding.resolve_registry_for", _resolve),
        patch("connect_labs.semantic.explain.explain", return_value={"indicator": "SF_P1"}),
    ):
        resp = client.get(reverse("labs:workflow:api_indicator_definitions", args=[6371]) + qs)
    return resp, constructed, registry_scopes


def test_a_program_owned_definition_is_found_through_the_owner_hint(client, django_user_model):
    resp, constructed, registry_scopes = _get(
        client, django_user_model, "?owning_program_id=10082&indicators=SF_P1&scope=llo"
    )
    assert resp.status_code == 200, resp.content
    assert constructed[-1].get("program_id") == 10082
    assert registry_scopes[-1].get("program_id") == 10082


def test_without_a_hint_a_miss_is_still_a_404(client, django_user_model):
    resp, constructed, _ = _get(client, django_user_model, "?indicators=SF_P1")
    assert resp.status_code == 404
    assert len(constructed) == 1, "no hint, no retry"
