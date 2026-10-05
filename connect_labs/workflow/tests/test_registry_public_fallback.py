"""A registry bound in an org scope stays readable once its owner makes it public (#2216).

Workflow 19778 bound registry 19784 as `{registry_id: 19784, organization_id: 179}`.
The record was later made public, but the org-scoped read still went first and
Connect answered it with a 404 for every viewer who is not a member of org 179. The
404 escaped `get_registry` as a LabsAPIError, through `resolve_registry`, and reached
the indicator-definition popover as "An internal error occurred".

Two fixes, tested separately:

  * the accessor retries the same id as a PUBLIC record when the scoped read misses;
  * a registry that still cannot be read is reported by id, in words, as a 404.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from connect_labs.labs.integrations.connect.api_client import LabsAPIError


def _access(scoped=None, scoped_error=None, public=None):
    from connect_labs.workflow.data_access import SemanticRegistryDataAccess

    access = SemanticRegistryDataAccess.__new__(SemanticRegistryDataAccess)
    access.labs_api = MagicMock()
    if scoped_error is not None:
        access.labs_api.get_record_by_id.side_effect = scoped_error
    else:
        access.labs_api.get_record_by_id.return_value = scoped
    access.labs_api.get_public_record_by_id.return_value = public
    return access


class TestTheAccessorFallsBackToPublic:
    def test_an_org_scoped_404_is_retried_as_a_public_record(self):
        record = object()
        access = _access(scoped_error=LabsAPIError("not found", status_code=404), public=record)
        assert access.get_registry(19784, organization_id=179) is record
        args, kwargs = access.labs_api.get_public_record_by_id.call_args
        assert args == (19784,)
        assert "organization_id" not in kwargs, "the public read carries no scope"

    def test_an_empty_scoped_answer_is_retried_as_public_too(self):
        record = object()
        access = _access(scoped=None, public=record)
        assert access.get_registry(19784, opportunity_id=523) is record

    def test_a_scoped_hit_never_reads_the_public_listing(self):
        record = object()
        access = _access(scoped=record)
        assert access.get_registry(19784, organization_id=179) is record
        access.labs_api.get_public_record_by_id.assert_not_called()

    def test_a_server_error_is_not_mistaken_for_absence(self):
        access = _access(scoped_error=LabsAPIError("boom", status_code=502))
        with pytest.raises(LabsAPIError):
            access.get_registry(19784, organization_id=179)
        access.labs_api.get_public_record_by_id.assert_not_called()


class TestTheResolverNamesTheRegistry:
    def test_an_unreadable_registry_is_named_in_words(self):
        from connect_labs.semantic.runtime import RegistryNotFound, resolve_registry

        access = MagicMock()
        access.get_registry.return_value = None
        with pytest.raises(RegistryNotFound) as exc:
            resolve_registry({"registry_id": 19784, "organization_id": 179}, access)
        assert "The indicator definitions (registry 19784) could not be found or are not visible to you" in str(
            exc.value
        )
        assert exc.value.registry_id == 19784

    def test_a_404_from_connect_becomes_the_same_readable_error(self):
        from connect_labs.semantic.runtime import RegistryNotFound, resolve_registry

        access = MagicMock()
        access.get_registry.side_effect = LabsAPIError("nope", status_code=404)
        with pytest.raises(RegistryNotFound):
            resolve_registry({"registry_id": 19784, "organization_id": 179}, access)


@pytest.mark.django_db
class TestTheExplainEndpoint:
    def _get(self, client, django_user_model, resolve):
        user = django_user_model.objects.create_user(username="u", password="p")
        client.force_login(user)

        class _Def:
            registry_source = {"registry_id": 19784, "organization_id": 179}

        class _WDA:
            def __init__(self, **kwargs):
                self.program_id = kwargs.get("program_id")

            def get_definition(self, definition_id):
                return _Def()

            def close(self):
                pass

        with (
            patch("connect_labs.workflow.views.WorkflowDataAccess", _WDA),
            patch("connect_labs.semantic.workflow_binding.resolve_registry_for", resolve),
        ):
            return client.get(reverse("labs:workflow:api_indicator_definitions", args=[19778]) + "?indicators=x")

    def test_an_unreadable_registry_is_a_readable_404_not_an_internal_error(self, client, django_user_model):
        from connect_labs.semantic.runtime import RegistryNotFound

        def resolve(definition, registry_access_factory):
            raise RegistryNotFound(19784)

        resp = self._get(client, django_user_model, resolve)
        assert resp.status_code == 404
        body = resp.json()
        assert body["registry_id"] == 19784
        assert body["error"].startswith(
            "The indicator definitions (registry 19784) could not be found or are not visible to you"
        )
        assert "internal error" not in body["error"].lower()

    def test_a_connect_failure_names_the_registry_too(self, client, django_user_model):
        def resolve(definition, registry_access_factory):
            raise LabsAPIError("upstream", status_code=503)

        resp = self._get(client, django_user_model, resolve)
        assert resp.status_code == 502
        assert "registry 19784" in resp.json()["error"]
