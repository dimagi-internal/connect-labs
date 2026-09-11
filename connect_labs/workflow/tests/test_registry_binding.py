"""A workflow can be bound to a registry that lives in ANOTHER scope, and says which it is.

Registries-as-records made indicator definitions editable without a deploy -- but
only for a workflow bound to a record it could READ. Reads are an exact scope match,
not hierarchical, and every KMC report is owned by an opportunity, so:

  * a registry created at the ORGANIZATION could not be read from an opportunity-
    owned workflow (`get_registry` sent the workflow's own scope);
  * "shared" registries were listable -- `list_registries` merges public records --
    but `create_registry` never set the record-level ACL, and `get_registry` never
    looked there, so none could be bound from outside its own scope;
  * templates never set `registry_source`, and `workflow_clone` dropped it.

The result, measured on 2026-09-11: every real KMC report computed from the on-disk
registry, while indicator fixes went to a record bound only to the synthetic
workflow. Nothing showed which registry a report used.

A binding now names the record AND the scope it lives in, the read happens there,
and every surface that shows a workflow or its figures states the binding.
Each link is tested separately, because any one of them dropping the scope
recreates the failure silently.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from connect_labs.semantic.workflow_binding import registry_binding

# ---------------------------------------------------------------------------
# The API client sends the home scope, and ONLY it.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheReadUsesTheHomeScope:
    def _client(self, **scope):
        from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient

        client = LabsRecordAPIClient.__new__(LabsRecordAPIClient)
        client.base_url = "https://connect.example"
        client.opportunity_id = scope.get("opportunity_id")
        client.program_id = scope.get("program_id")
        client.organization_id = scope.get("organization_id")
        response = MagicMock()
        response.json.return_value = [
            {"id": 19784, "experiment": "semantic", "type": "semantic_registry", "data": {}, "opportunity_id": None}
        ]
        client.http_client = MagicMock()
        client.http_client.get.return_value = response
        return client

    def _sent(self, client):
        return client.http_client.get.call_args.kwargs["params"]

    def test_an_organization_override_is_the_only_scope_sent(self):
        # The workflow's accessor is opportunity-scoped. If its opportunity leaked
        # alongside the override, the server would filter on the opportunity first
        # and find nothing -- the exact failure being fixed.
        client = self._client(opportunity_id=523)
        client.get_record_by_id(19784, experiment="semantic", type="semantic_registry", organization_id=179)
        params = self._sent(client)
        assert params["organization_id"] == 179
        assert "opportunity_id" not in params and "program_id" not in params

    def test_a_program_override_is_the_only_scope_sent(self):
        client = self._client(opportunity_id=523)
        client.get_record_by_id(19784, program_id=46)
        params = self._sent(client)
        assert params["program_id"] == 46 and "opportunity_id" not in params

    def test_no_override_keeps_the_clients_own_scope(self):
        client = self._client(opportunity_id=523)
        client.get_record_by_id(19784)
        assert self._sent(client)["opportunity_id"] == 523


# ---------------------------------------------------------------------------
# The accessor and the resolver pass the binding's scope through.
# ---------------------------------------------------------------------------


class TestTheResolverReadsWhereTheRecordLives:
    def _record(self):
        rec = MagicMock()
        rec.properties_doc = {"properties": [{"name": "p", "type": "int", "sql": "1"}]}
        rec.indicators_doc = {"measures": []}
        rec.deployment = {}
        return rec

    def test_the_home_scope_in_registry_source_reaches_get_registry(self):
        from connect_labs.semantic.runtime import resolve_registry

        access = MagicMock()
        access.get_registry.return_value = self._record()
        resolve_registry({"registry_id": 19784, "organization_id": 179}, access)
        access.get_registry.assert_called_once_with(19784, organization_id=179)

    def test_without_a_home_scope_the_read_is_unchanged(self):
        from connect_labs.semantic.runtime import resolve_registry

        access = MagicMock()
        access.get_registry.return_value = self._record()
        resolve_registry({"registry_id": 5500}, access)
        access.get_registry.assert_called_once_with(5500)

    def test_the_accessor_forwards_only_recognised_scope_keys(self):
        from connect_labs.workflow.data_access import SemanticRegistryDataAccess

        access = SemanticRegistryDataAccess.__new__(SemanticRegistryDataAccess)
        access.labs_api = MagicMock()
        access.get_registry(19784, organization_id=179, name="ignored", program_id=None)
        kwargs = access.labs_api.get_record_by_id.call_args.kwargs
        assert kwargs["organization_id"] == 179
        assert "name" not in kwargs and "program_id" not in kwargs


# ---------------------------------------------------------------------------
# Binding: validated at write time, in the record's home scope.
# ---------------------------------------------------------------------------


class TestBindingValidation:
    def _validate(self, value, monkeypatch, resolved=None, raises=None):
        from connect_labs.mcp.tools import workflows as wf
        from connect_labs.semantic import runtime

        seen = {}

        def fake_resolve(source, access):
            seen["source"] = source
            if raises:
                raise raises
            return resolved

        monkeypatch.setattr(runtime, "resolve_registry", fake_resolve)
        monkeypatch.setattr("connect_labs.workflow.data_access.SemanticRegistryDataAccess", lambda **kw: MagicMock())
        wf._validate_registry_source(value, MagicMock(access_token="t"), opportunity_id=523)
        return seen

    def test_an_org_owned_registry_binds_and_is_resolved_in_its_home_scope(self, monkeypatch):
        seen = self._validate({"registry_id": 19784, "organization_id": 179}, monkeypatch)
        assert seen["source"] == {"registry_id": 19784, "organization_id": 179}

    @pytest.mark.parametrize(
        "value,needle",
        [
            ({"name": "kmc", "organization_id": 179}, "only apply to a registry_id"),
            ({"registry_id": 1, "organization_id": 179, "program_id": 46}, "not several"),
            ({"registry_id": 1, "organization_id": "179"}, "must be an integer"),
            ({"registry_id": 1, "organization_id": True}, "must be an integer"),
            ({"registry_id": 1, "workspace": 3}, "Unknown registry_source keys"),
        ],
    )
    def test_a_malformed_binding_is_refused_by_name(self, value, needle, monkeypatch):
        from connect_labs.mcp.tool_registry import MCPToolError

        with pytest.raises(MCPToolError) as e:
            self._validate(value, monkeypatch)
        assert needle in str(e.value)

    def test_an_unreadable_binding_names_the_scope_it_was_read_in(self, monkeypatch):
        from connect_labs.labs.integrations.connect.api_client import LabsAPIError
        from connect_labs.mcp.tool_registry import MCPToolError

        with pytest.raises(MCPToolError) as e:
            self._validate(
                {"registry_id": 19784}, monkeypatch, raises=LabsAPIError("Failed to fetch record 19784: 404")
            )
        msg = str(e.value)
        assert "cannot be read in this workflow's scope" in msg
        assert "organization_id" in msg, "the message says how to bind an org-owned registry"


# ---------------------------------------------------------------------------
# Visibility: every surface states the binding.
# ---------------------------------------------------------------------------


class _Def:
    def __init__(self, source=None):
        self.registry_source = source


class TestTheBindingIsStated:
    def test_unbound_says_disk_and_what_that_means(self):
        b = registry_binding(_Def())
        assert b["source"] == "disk" and b["name"] == "kmc"
        assert "deploy" in b["note"] and "registry_source" in b["note"]

    def test_a_record_binding_carries_its_home_scope(self):
        b = registry_binding(_Def({"registry_id": 19784, "organization_id": 179}))
        assert b == {"source": "record", "registry_id": 19784, "organization_id": 179}

    def test_a_named_disk_registry_is_still_disk(self):
        assert registry_binding(_Def({"name": "kmc"}))["source"] == "disk"


# ---------------------------------------------------------------------------
# Carried through: create and clone keep the binding.
# ---------------------------------------------------------------------------


class TestTheBindingSurvivesCreationAndClone:
    def test_create_definition_persists_registry_source(self):
        from connect_labs.workflow.data_access import WorkflowDataAccess

        wda = WorkflowDataAccess.__new__(WorkflowDataAccess)
        wda.labs_api = MagicMock()
        created = MagicMock(id=1, experiment="workflow", type="workflow_definition", data={}, opportunity_id=523)
        created.program_id = None
        wda.labs_api.create_record.return_value = created
        wda.create_definition("x", "y", registry_source={"registry_id": 19784, "organization_id": 179})
        data = wda.labs_api.create_record.call_args.kwargs["data"]
        assert data["registry_source"] == {"registry_id": 19784, "organization_id": 179}

    @pytest.mark.django_db
    def test_workflow_clone_forwards_the_binding_and_the_snapshot_contract(self, monkeypatch):
        from connect_labs.mcp.tools import workflows as wf

        source_def = MagicMock()
        source_def.name, source_def.description = "JJ - KMC Programme Metrics", "d"
        source_def.data = {
            "config": {},
            "pipeline_sources": [],
            "opportunity_ids": [523],
            "registry_source": {"registry_id": 19784, "organization_id": 179},
            "snapshot_inputs": {"builder": "semantic_snapshot"},
        }
        src, dst = MagicMock(), MagicMock()
        src.get_definition.return_value = source_def
        src.get_render_code.return_value = None
        dst.create_definition.return_value = MagicMock(id=2)
        instances = iter([src, dst])
        # Patched where workflow_clone LOOKS IT UP. Patching the defining module left the
        # name bound in workflows.py untouched, and the first draft of this test reached
        # real Connect (a 401 on the fake token) -- a unit test must not touch the network.
        monkeypatch.setattr(wf, "WorkflowDataAccess", lambda **kw: next(instances), raising=False)
        monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", lambda **kw: next(instances))
        monkeypatch.setattr(wf, "require_connect_token", lambda u: "t", raising=False)
        monkeypatch.setattr("connect_labs.mcp.connect_token.require_connect_token", lambda u: "t")

        wf.workflow_clone(MagicMock(), source_workflow_id=19778, source_opportunity_id=523, target_opportunity_id=2166)

        kwargs = dst.create_definition.call_args.kwargs
        assert kwargs["registry_source"] == {"registry_id": 19784, "organization_id": 179}
        assert kwargs["snapshot_inputs"] == {"builder": "semantic_snapshot"}
