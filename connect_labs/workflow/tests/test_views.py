"""Unit tests for workflow views.

Uses Django's RequestFactory to construct bare requests and invokes view
functions / class-based-view dispatchers directly. External dependencies
like WorkflowDataAccess are mocked. Because RequestFactory does not run
middleware, middleware-dependent behaviour (CSRF, session, etc.) is
simulated by attaching the required attributes to the request in each test.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.users.tests.factories import UserFactory


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


@pytest.fixture
def dimagi_user(db):
    user = UserFactory()
    user.email = "test@dimagi.com"
    user.save()
    return user


class TestCreateWorkflowOpportunityIds:
    def test_multi_opp_template_stores_opportunity_ids(self, dimagi_user, rf: RequestFactory):
        """POST /workflow/create/ with opportunity_ids=[...] for a multi_opp template."""
        from connect_labs.workflow.templates import TEMPLATES

        TEMPLATES["__tv_multi__"] = {
            "key": "__tv_multi__",
            "name": "T",
            "description": "d",
            "multi_opp": True,
            "definition": {"name": "T", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
        }

        try:
            request = rf.post(
                "/labs/workflow/create/",
                data={"template": "__tv_multi__", "opportunity_ids": ["700", "825"]},
            )
            request.user = dimagi_user
            request.labs_context = {
                "opportunity_id": 700,
                "opportunity_name": "Primary",
            }
            # get_org_data reads from request.session["labs_oauth"]["organization_data"]
            request.session = {
                "labs_oauth": {
                    "access_token": "t",
                    "organization_data": {
                        "opportunities": [
                            {"id": 700, "name": "A"},
                            {"id": 825, "name": "B"},
                            {"id": 912, "name": "C"},
                        ]
                    },
                },
            }

            # Hook Django messages framework
            from django.contrib.messages.storage.fallback import FallbackStorage

            setattr(request, "_messages", FallbackStorage(request))

            with (
                patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA,
                patch("connect_labs.workflow.views.create_from_template") as mock_create,
            ):
                mock_wda = MagicMock()
                MockWDA.return_value = mock_wda
                mock_create.return_value = (
                    MagicMock(id=1, name="T"),
                    MagicMock(),
                    None,
                )

                from connect_labs.workflow.views import create_workflow_from_template_view

                create_workflow_from_template_view(request)

                # Verify opportunity_ids was passed through
                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs["opportunity_ids"] == [700, 825]
        finally:
            del TEMPLATES["__tv_multi__"]

    def test_rejects_opportunity_ids_outside_user_opportunities(self, dimagi_user, rf: RequestFactory):
        from connect_labs.workflow.templates import TEMPLATES

        TEMPLATES["__tv_multi2__"] = {
            "key": "__tv_multi2__",
            "name": "T",
            "description": "d",
            "multi_opp": True,
            "definition": {"name": "T", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
        }
        try:
            request = rf.post(
                "/labs/workflow/create/",
                data={"template": "__tv_multi2__", "opportunity_ids": ["9999"]},
            )
            request.user = dimagi_user
            request.labs_context = {"opportunity_id": 700}
            # get_org_data reads from request.session["labs_oauth"]["organization_data"]
            request.session = {
                "labs_oauth": {
                    "access_token": "t",
                    "organization_data": {
                        "opportunities": [{"id": 700, "name": "A"}],
                    },
                },
            }

            # Hook Django messages framework
            from django.contrib.messages.storage.fallback import FallbackStorage

            setattr(request, "_messages", FallbackStorage(request))

            with patch("connect_labs.workflow.views.create_from_template") as mock_create:
                from connect_labs.workflow.views import create_workflow_from_template_view

                response = create_workflow_from_template_view(request)

                # Should NOT have created the workflow
                mock_create.assert_not_called()
                # Should redirect to list with error
                assert response.status_code in (302, 303)
        finally:
            del TEMPLATES["__tv_multi2__"]


class TestUpdateOpportunityIdsView:
    def test_updates_on_valid_payload(self, dimagi_user, rf: RequestFactory):
        import json

        request = rf.post(
            "/labs/workflow/api/1/opportunity-ids/",
            data=json.dumps({"opportunity_ids": [700, 825]}),
            content_type="application/json",
        )
        request.user = dimagi_user
        request.labs_context = {"opportunity_id": 700}
        request.session = {
            "labs_oauth": {
                "access_token": "t",
                "organization_data": {
                    "opportunities": [
                        {"id": 700, "name": "A"},
                        {"id": 825, "name": "B"},
                    ]
                },
            },
        }

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_definition.return_value = MagicMock(multi_opp=True)
            mock_wda.update_opportunity_ids.return_value = MagicMock(id=1)

            from connect_labs.workflow.views import UpdateOpportunityIdsView

            response = UpdateOpportunityIdsView.as_view()(request, definition_id=1)

            assert response.status_code == 200
            mock_wda.update_opportunity_ids.assert_called_once_with(1, [700, 825])

    def test_rejects_single_opp_workflow(self, dimagi_user, rf: RequestFactory):
        import json

        request = rf.post(
            "/labs/workflow/api/1/opportunity-ids/",
            data=json.dumps({"opportunity_ids": [700]}),
            content_type="application/json",
        )
        request.user = dimagi_user
        request.labs_context = {"opportunity_id": 700}
        request.session = {
            "labs_oauth": {
                "access_token": "t",
                "organization_data": {"opportunities": [{"id": 700, "name": "A"}]},
            },
        }

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_definition.return_value = MagicMock(multi_opp=False)

            from connect_labs.workflow.views import UpdateOpportunityIdsView

            response = UpdateOpportunityIdsView.as_view()(request, definition_id=1)

            assert response.status_code == 400
            mock_wda.update_opportunity_ids.assert_not_called()

    def test_rejects_empty_opportunity_ids(self, dimagi_user, rf: RequestFactory):
        import json

        request = rf.post(
            "/labs/workflow/api/1/opportunity-ids/",
            data=json.dumps({"opportunity_ids": []}),
            content_type="application/json",
        )
        request.user = dimagi_user
        request.labs_context = {"opportunity_id": 700}
        request.session = {
            "labs_oauth": {
                "access_token": "t",
                "organization_data": {"opportunities": [{"id": 700, "name": "A"}]},
            },
        }

        from connect_labs.workflow.views import UpdateOpportunityIdsView

        response = UpdateOpportunityIdsView.as_view()(request, definition_id=1)
        assert response.status_code == 400

    def test_rejects_unauthorized_opportunity(self, dimagi_user, rf: RequestFactory):
        import json

        request = rf.post(
            "/labs/workflow/api/1/opportunity-ids/",
            data=json.dumps({"opportunity_ids": [9999]}),
            content_type="application/json",
        )
        request.user = dimagi_user
        request.labs_context = {"opportunity_id": 700}
        request.session = {
            "labs_oauth": {
                "access_token": "t",
                "organization_data": {"opportunities": [{"id": 700, "name": "A"}]},
            },
        }

        from connect_labs.workflow.views import UpdateOpportunityIdsView

        response = UpdateOpportunityIdsView.as_view()(request, definition_id=1)
        assert response.status_code == 403

    def test_rejects_invalid_json(self, dimagi_user, rf: RequestFactory):
        request = rf.post(
            "/labs/workflow/api/1/opportunity-ids/",
            data="not-json",
            content_type="application/json",
        )
        request.user = dimagi_user
        request.labs_context = {"opportunity_id": 700}
        request.session = {
            "labs_oauth": {
                "access_token": "t",
                "organization_data": {"opportunities": [{"id": 700, "name": "A"}]},
            },
        }

        from connect_labs.workflow.views import UpdateOpportunityIdsView

        response = UpdateOpportunityIdsView.as_view()(request, definition_id=1)
        assert response.status_code == 400


class TestRenameRunView:
    """rename_run_api sets a run's display name (data.name) -- allowed
    regardless of run status, unlike the state-write path."""

    def test_renames_run_on_valid_payload(self, dimagi_user, rf: RequestFactory):
        import json

        request = rf.post(
            "/labs/workflow/api/run/9/rename/",
            data=json.dumps({"name": "  Week 30 Audit  "}),
            content_type="application/json",
        )
        request.user = dimagi_user

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_run = MagicMock()
            mock_wda.get_run.return_value = mock_run

            from connect_labs.workflow.views import rename_run_api

            response = rename_run_api(request, run_id=9)

            assert response.status_code == 200
            body = json.loads(response.content)
            assert body["success"] is True
            assert body["name"] == "Week 30 Audit"
            # Whitespace is trimmed before it's persisted.
            mock_wda.rename_run.assert_called_once_with(9, "Week 30 Audit", run=mock_run)

    def test_rejects_empty_name(self, dimagi_user, rf: RequestFactory):
        import json

        request = rf.post(
            "/labs/workflow/api/run/9/rename/",
            data=json.dumps({"name": "   "}),
            content_type="application/json",
        )
        request.user = dimagi_user

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda

            from connect_labs.workflow.views import rename_run_api

            response = rename_run_api(request, run_id=9)

            assert response.status_code == 400
            mock_wda.rename_run.assert_not_called()

    def test_returns_404_when_run_not_found(self, dimagi_user, rf: RequestFactory):
        import json

        request = rf.post(
            "/labs/workflow/api/run/9/rename/",
            data=json.dumps({"name": "New Name"}),
            content_type="application/json",
        )
        request.user = dimagi_user

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_run.return_value = None

            from connect_labs.workflow.views import rename_run_api

            response = rename_run_api(request, run_id=9)

            assert response.status_code == 404
            mock_wda.rename_run.assert_not_called()

    def test_rejects_invalid_json(self, dimagi_user, rf: RequestFactory):
        request = rf.post(
            "/labs/workflow/api/run/9/rename/",
            data="not-json",
            content_type="application/json",
        )
        request.user = dimagi_user

        from connect_labs.workflow.views import rename_run_api

        response = rename_run_api(request, run_id=9)
        assert response.status_code == 400


class TestUpdateAuditBatchConfigView:
    def _request(self, rf, dimagi_user, body):
        import json

        request = rf.post(
            "/labs/workflow/api/1/audit-batch-config/",
            data=json.dumps(body),
            content_type="application/json",
        )
        request.user = dimagi_user
        request.labs_context = {"opportunity_id": 700}
        request.session = {
            "labs_oauth": {
                "access_token": "t",
                "organization_data": {
                    "opportunities": [
                        {"id": 700, "name": "A"},
                        {"id": 825, "name": "B"},
                    ]
                },
            },
        }
        return request

    def test_updates_track_names_and_per_opp(self, dimagi_user, rf: RequestFactory):
        request = self._request(
            rf,
            dimagi_user,
            {
                "track_a_name": "MUAC",
                "track_b_name": "Other",
                "per_opp": {"700": {"muac_image_paths": ["a/muac_photo"], "rest_image_paths": ["a/other"]}},
            },
        )

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_definition.return_value = MagicMock(
                data={"name": "T", "config": {"audit_batch": {"track_a": {"tag": "muac"}, "per_opp": {}}}}
            )
            mock_wda.update_definition.return_value = MagicMock(
                data={
                    "config": {
                        "audit_batch": {
                            "track_a": {"tag": "muac", "name": "MUAC"},
                            "track_b": {"name": "Other"},
                            "per_opp": {
                                "700": {"muac_image_paths": ["a/muac_photo"], "rest_image_paths": ["a/other"]}
                            },
                        }
                    }
                }
            )

            from connect_labs.workflow.views import UpdateAuditBatchConfigView

            response = UpdateAuditBatchConfigView.as_view()(request, definition_id=1)

            assert response.status_code == 200
            saved_data = mock_wda.update_definition.call_args[0][1]
            audit_batch = saved_data["config"]["audit_batch"]
            assert audit_batch["track_a"]["name"] == "MUAC"
            assert audit_batch["track_a"]["tag"] == "muac"  # preserved, not clobbered
            assert audit_batch["track_b"]["name"] == "Other"
            assert audit_batch["per_opp"]["700"]["muac_image_paths"] == ["a/muac_photo"]

    def test_merges_per_opp_without_wiping_other_opps(self, dimagi_user, rf: RequestFactory):
        request = self._request(rf, dimagi_user, {"per_opp": {"700": {"muac_image_paths": ["a/new"]}}})

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_definition.return_value = MagicMock(
                data={
                    "config": {
                        "audit_batch": {
                            "per_opp": {
                                "700": {"muac_image_paths": ["a/old"]},
                                "825": {"muac_image_paths": ["b/old"]},
                            }
                        }
                    }
                }
            )
            mock_wda.update_definition.return_value = MagicMock(data={"config": {"audit_batch": {}}})

            from connect_labs.workflow.views import UpdateAuditBatchConfigView

            UpdateAuditBatchConfigView.as_view()(request, definition_id=1)

            saved_data = mock_wda.update_definition.call_args[0][1]
            per_opp = saved_data["config"]["audit_batch"]["per_opp"]
            assert per_opp["700"]["muac_image_paths"] == ["a/new"]
            assert per_opp["825"]["muac_image_paths"] == ["b/old"]  # untouched opp preserved

    def test_rejects_unauthorized_opportunity_in_per_opp(self, dimagi_user, rf: RequestFactory):
        request = self._request(rf, dimagi_user, {"per_opp": {"9999": {"muac_image_paths": ["x"]}}})

        from connect_labs.workflow.views import UpdateAuditBatchConfigView

        response = UpdateAuditBatchConfigView.as_view()(request, definition_id=1)
        assert response.status_code == 403

    def test_rejects_non_list_paths(self, dimagi_user, rf: RequestFactory):
        request = self._request(rf, dimagi_user, {"per_opp": {"700": {"muac_image_paths": "not-a-list"}}})

        from connect_labs.workflow.views import UpdateAuditBatchConfigView

        response = UpdateAuditBatchConfigView.as_view()(request, definition_id=1)
        assert response.status_code == 400

    def test_rejects_invalid_json(self, dimagi_user, rf: RequestFactory):
        request = rf.post(
            "/labs/workflow/api/1/audit-batch-config/",
            data="not-json",
            content_type="application/json",
        )
        request.user = dimagi_user
        request.labs_context = {"opportunity_id": 700}
        request.session = {"labs_oauth": {"access_token": "t", "organization_data": {"opportunities": []}}}

        from connect_labs.workflow.views import UpdateAuditBatchConfigView

        response = UpdateAuditBatchConfigView.as_view()(request, definition_id=1)
        assert response.status_code == 400

    def test_returns_404_when_definition_missing(self, dimagi_user, rf: RequestFactory):
        request = self._request(rf, dimagi_user, {"track_a_name": "MUAC"})

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_definition.return_value = None

            from connect_labs.workflow.views import UpdateAuditBatchConfigView

            response = UpdateAuditBatchConfigView.as_view()(request, definition_id=1)
            assert response.status_code == 404

    def test_accepts_valid_classifiers_and_round_trips(self, dimagi_user, rf: RequestFactory):
        import json

        request = self._request(
            rf,
            dimagi_user,
            {
                "per_opp": {
                    "700": {
                        "muac_image_paths": ["muac_group/muac_photo"],
                        "rest_image_paths": [],
                        "classifiers": {"muac_group/muac_photo": ["hyperzoom", "muac_mismatch"]},
                    }
                }
            },
        )

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_definition.return_value = MagicMock(data={"config": {"audit_batch": {"per_opp": {}}}})
            mock_wda.update_definition.return_value = MagicMock(
                data={
                    "config": {
                        "audit_batch": {
                            "per_opp": {
                                "700": {
                                    "muac_image_paths": ["muac_group/muac_photo"],
                                    "rest_image_paths": [],
                                    "classifiers": {"muac_group/muac_photo": ["hyperzoom", "muac_mismatch"]},
                                }
                            }
                        }
                    }
                }
            )

            from connect_labs.workflow.views import UpdateAuditBatchConfigView

            response = UpdateAuditBatchConfigView.as_view()(request, definition_id=1)

            assert response.status_code == 200
            payload = json.loads(response.content)
            assert payload["audit_batch"]["per_opp"]["700"]["classifiers"] == {
                "muac_group/muac_photo": ["hyperzoom", "muac_mismatch"]
            }

            saved_data = mock_wda.update_definition.call_args[0][1]
            saved_per_opp = saved_data["config"]["audit_batch"]["per_opp"]
            assert saved_per_opp["700"]["classifiers"] == {"muac_group/muac_photo": ["hyperzoom", "muac_mismatch"]}

    def test_rejects_classifiers_not_a_dict(self, dimagi_user, rf: RequestFactory):
        import json

        request = self._request(
            rf, dimagi_user, {"per_opp": {"700": {"muac_image_paths": [], "classifiers": ["hyperzoom"]}}}
        )

        from connect_labs.workflow.views import UpdateAuditBatchConfigView

        response = UpdateAuditBatchConfigView.as_view()(request, definition_id=1)
        assert response.status_code == 400
        assert "classifiers" in json.loads(response.content)["error"]

    def test_rejects_classifiers_value_not_a_list(self, dimagi_user, rf: RequestFactory):
        import json

        request = self._request(
            rf, dimagi_user, {"per_opp": {"700": {"muac_image_paths": [], "classifiers": {"path": "hyperzoom"}}}}
        )

        from connect_labs.workflow.views import UpdateAuditBatchConfigView

        response = UpdateAuditBatchConfigView.as_view()(request, definition_id=1)
        assert response.status_code == 400
        assert "classifiers" in json.loads(response.content)["error"]

    def test_rejects_unknown_classifier_key(self, dimagi_user, rf: RequestFactory):
        import json

        request = self._request(
            rf,
            dimagi_user,
            {"per_opp": {"700": {"muac_image_paths": [], "classifiers": {"path": ["not_a_real_classifier"]}}}},
        )

        from connect_labs.workflow.views import UpdateAuditBatchConfigView

        response = UpdateAuditBatchConfigView.as_view()(request, definition_id=1)
        assert response.status_code == 400
        assert "classifiers" in json.loads(response.content)["error"]


class TestCompleteRunTemplateFallback:
    """complete_run_api recovers a missing config.templateType from the
    workflow name (same strict match template sync uses) and self-heals the
    definition record, instead of dead-ending the conclude with a 400."""

    TEMPLATE_KEY = "__tv_saved_runs__"

    def _records(self, definition_name):
        from connect_labs.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord

        definition = WorkflowDefinitionRecord(
            {
                "id": 10,
                "experiment": "workflow",
                "type": "workflow_definition",
                "opportunity_id": 700,
                "data": {"name": definition_name, "config": {}, "statuses": []},
            }
        )
        run = WorkflowRunRecord(
            {
                "id": 55,
                "experiment": "workflow",
                "type": "workflow_run",
                "opportunity_id": 700,
                "data": {"definition_id": 10, "status": "in_progress", "state": {}},
            }
        )
        return definition, run

    def _request(self, rf, user):
        request = rf.post("/labs/workflow/api/run/55/complete/", data="{}", content_type="application/json")
        request.user = user
        request.labs_context = {"opportunity_id": 700}
        request.session = {"labs_oauth": {"access_token": "t", "organization_data": {"opportunities": []}}}
        return request

    def _call(self, rf, user, definition, run):
        from connect_labs.workflow.views import complete_run_api

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_run.return_value = run
            mock_wda.get_definition.return_value = definition
            mock_wda.get_cached_pipeline_data.return_value = {}
            mock_wda.get_workers.return_value = []
            completed = MagicMock()
            completed.status = "completed"
            completed.completed_at = "2026-06-11T00:00:00Z"
            completed.snapshot = {}
            mock_wda.complete_run.return_value = completed
            response = complete_run_api(request=self._request(rf, user), run_id=55)
        return response, mock_wda

    def test_name_match_completes_and_stamps_template_type(self, dimagi_user, rf: RequestFactory):
        import json as _json

        from connect_labs.workflow.templates import TEMPLATES

        TEMPLATES[self.TEMPLATE_KEY] = {
            "key": self.TEMPLATE_KEY,
            "name": "TV Saved Runs",
            "description": "d",
            "supports_saved_runs": True,
            "snapshot_inputs": {},
            "definition": {"name": "TV Saved Runs", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
        }
        try:
            definition, run = self._records("TV Saved Runs")
            response, mock_wda = self._call(rf, dimagi_user, definition, run)

            assert response.status_code == 200, response.content
            assert _json.loads(response.content)["success"] is True
            # Self-heal: the recovered key AND the template's manifest were
            # written back onto the definition — the instance owns its
            # completion contract from here on.
            stamped_data = mock_wda.update_definition.call_args.args[1]
            assert stamped_data["config"]["templateType"] == self.TEMPLATE_KEY
            assert stamped_data["snapshot_inputs"] == {}
        finally:
            TEMPLATES.pop(self.TEMPLATE_KEY, None)

    def test_no_name_match_returns_actionable_400(self, dimagi_user, rf: RequestFactory):
        import json as _json

        definition, run = self._records("Some Bespoke Workflow")
        response, mock_wda = self._call(rf, dimagi_user, definition, run)

        assert response.status_code == 400
        error = _json.loads(response.content)["error"]
        assert "config.templateType" in error
        assert "snapshot_inputs" in error
        mock_wda.update_definition.assert_not_called()
        mock_wda.complete_run.assert_not_called()

    def test_instance_snapshot_inputs_completes_without_any_template(self, dimagi_user, rf: RequestFactory):
        """A workflow with its own snapshot_inputs manifest completes with no
        templateType, no name match, no registry entry — the definition owns
        the contract."""
        import json as _json

        from connect_labs.workflow.data_access import WorkflowDefinitionRecord

        definition, run = self._records("Totally Bespoke Workflow")
        definition = WorkflowDefinitionRecord(
            {
                "id": 10,
                "experiment": "workflow",
                "type": "workflow_definition",
                "opportunity_id": 700,
                "data": {
                    "name": "Totally Bespoke Workflow",
                    "config": {},
                    "statuses": [],
                    "snapshot_inputs": {"workers": True, "state_keys": ["decisions"]},
                },
            }
        )
        response, mock_wda = self._call(rf, dimagi_user, definition, run)

        assert response.status_code == 200, response.content
        assert _json.loads(response.content)["success"] is True
        # No registry fallback happened, so nothing needed stamping.
        mock_wda.update_definition.assert_not_called()
        # The snapshot honors the instance manifest.
        snapshot = mock_wda.complete_run.call_args.args[1]
        assert snapshot["state"] == {}
        assert "pipelines" not in snapshot or snapshot["pipelines"] == {}

    def test_name_match_without_saved_runs_support_returns_400_and_no_stamp(self, dimagi_user, rf: RequestFactory):
        import json as _json

        from connect_labs.workflow.templates import TEMPLATES

        TEMPLATES[self.TEMPLATE_KEY] = {
            "key": self.TEMPLATE_KEY,
            "name": "TV Saved Runs",
            "description": "d",
            "definition": {"name": "TV Saved Runs", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
        }
        try:
            definition, run = self._records("TV Saved Runs")
            response, mock_wda = self._call(rf, dimagi_user, definition, run)

            assert response.status_code == 400
            assert "supports_saved_runs" in _json.loads(response.content)["error"]
            mock_wda.update_definition.assert_not_called()
        finally:
            TEMPLATES.pop(self.TEMPLATE_KEY, None)


class TestCompleteRunCacheOnlyPipelines:
    """Run completion must never execute pipelines — the snapshot freezes what
    the user was looking at, read from the processed cache the runner page
    populated, and only for the aliases the contract captures. A 102k-visit
    opp re-executed at conclude time took ~18 minutes and OOM-killed a worker
    before this contract existed."""

    def _records(self, snapshot_inputs):
        from connect_labs.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord

        definition = WorkflowDefinitionRecord(
            {
                "id": 10,
                "experiment": "workflow",
                "type": "workflow_definition",
                "opportunity_id": 700,
                "data": {
                    "name": "Cache Only WF",
                    "config": {},
                    "statuses": [],
                    "snapshot_inputs": snapshot_inputs,
                },
            }
        )
        run = WorkflowRunRecord(
            {
                "id": 55,
                "experiment": "workflow",
                "type": "workflow_run",
                "opportunity_id": 700,
                "data": {"definition_id": 10, "status": "in_progress", "state": {"decisions": {"a": 1}}},
            }
        )
        return definition, run

    def _request(self, rf, user):
        request = rf.post("/labs/workflow/api/run/55/complete/", data="{}", content_type="application/json")
        request.user = user
        request.labs_context = {"opportunity_id": 700}
        request.session = {"labs_oauth": {"access_token": "t", "organization_data": {"opportunities": []}}}
        return request

    def _call(self, rf, user, definition, run, cached_side_effect=None, cached_return=None):
        from connect_labs.workflow.views import complete_run_api

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_run.return_value = run
            mock_wda.get_definition.return_value = definition
            if cached_side_effect is not None:
                mock_wda.get_cached_pipeline_data.side_effect = cached_side_effect
            else:
                mock_wda.get_cached_pipeline_data.return_value = cached_return or {}
            mock_wda.get_workers.return_value = []
            completed = MagicMock()
            completed.status = "completed"
            completed.completed_at = "2026-06-11T00:00:00Z"
            completed.snapshot = {}
            mock_wda.complete_run.return_value = completed
            response = complete_run_api(request=self._request(rf, user), run_id=55)
        return response, mock_wda

    def test_empty_pipelines_manifest_skips_pipeline_fetch_entirely(self, dimagi_user, rf: RequestFactory):
        definition, run = self._records({"pipelines": [], "workers": True, "state_keys": ["decisions"]})
        response, mock_wda = self._call(rf, dimagi_user, definition, run)

        assert response.status_code == 200, response.content
        mock_wda.get_cached_pipeline_data.assert_not_called()
        mock_wda.get_pipeline_data.assert_not_called()
        snapshot = mock_wda.complete_run.call_args.args[1]
        assert snapshot["pipelines"] == {}
        assert snapshot["state"] == {"decisions": {"a": 1}}

    def test_manifest_aliases_scope_the_cached_read(self, dimagi_user, rf: RequestFactory):
        definition, run = self._records({"pipelines": ["visits"], "workers": True, "state_keys": []})
        response, mock_wda = self._call(
            rf, dimagi_user, definition, run, cached_return={"visits": {"rows": [], "metadata": {}}}
        )

        assert response.status_code == 200, response.content
        # period_start/period_end are threaded so opted-in pipelines can be
        # period-scoped (ace#764); this run carries no period, so both are None
        # and the read behaves exactly as the all-time cache read.
        mock_wda.get_cached_pipeline_data.assert_called_once_with(
            10, 700, aliases=["visits"], period_start=None, period_end=None
        )
        mock_wda.get_pipeline_data.assert_not_called()

    def test_cache_miss_returns_409_and_leaves_run_in_progress(self, dimagi_user, rf: RequestFactory):
        import json as _json

        from connect_labs.workflow.data_access import PipelineCacheMiss

        definition, run = self._records({"pipelines": ["visits"], "workers": True, "state_keys": []})
        response, mock_wda = self._call(
            rf, dimagi_user, definition, run, cached_side_effect=PipelineCacheMiss("visits", 700, "MBW Visits")
        )

        assert response.status_code == 409
        error = _json.loads(response.content)["error"]
        assert "Reload the run page" in error
        assert "MBW Visits" in error
        mock_wda.complete_run.assert_not_called()

    def test_oversize_snapshot_returns_400_and_leaves_run_in_progress(self, dimagi_user, rf: RequestFactory):
        import json as _json

        # ~6 MB of state captured by the manifest blows the 5 MB hard cap.
        definition, run = self._records({"pipelines": [], "workers": False, "state_keys": ["blob"]})
        run.data["state"] = {"blob": "x" * (6 * 1024 * 1024)}
        response, mock_wda = self._call(rf, dimagi_user, definition, run)

        assert response.status_code == 400
        error = _json.loads(response.content)["error"]
        assert "MB" in error and "snapshot_inputs" in error
        mock_wda.complete_run.assert_not_called()


class TestWorkflowRunOpportunityRecovery:
    """The run view recovers the workflow's opportunity when the labs context
    is empty — so a hand-edited / copy-pasted link (whose opportunity_id param
    the middleware dropped as non-integer) doesn't dead-end at the context
    picker. See WorkflowRunView.get / _recover_opportunity_id.

    DB-free: recovery reads the opp list off the session and (for synthetic-opp
    merging only) the user's view_synthetic_opps flag, so a lightweight fake
    user with that flag off exercises the real code without Postgres.
    """

    def _user(self):
        return SimpleNamespace(is_authenticated=True, view_synthetic_opps=False, username="jo")

    def _view(self, rf, *, url, labs_context, opportunities):
        from connect_labs.workflow.views import WorkflowRunView

        request = rf.get(url)
        request.user = self._user()
        request.labs_context = labs_context
        request.session = {
            "labs_oauth": {"access_token": "t", "organization_data": {"opportunities": opportunities}},
        }
        view = WorkflowRunView()
        view.setup(request, definition_id=3962)
        return view, request

    def test_salvages_leading_int_from_malformed_param(self, rf):
        """`opportunity_id=1251 stacked bar chart` → 1251 when accessible."""
        view, _ = self._view(
            rf,
            url="/labs/workflow/3962/run/?run_id=4259&opportunity_id=1251 stacked bar chart",
            labs_context={},
            opportunities=[{"id": 1251, "name": "Opp"}],
        )
        assert view._recover_opportunity_id(3962) == 1251

    def test_passes_through_leading_int_when_org_cache_empty(self, rf):
        """Empty OAuth cache → trust the salvaged id, let the API enforce access
        (mirrors labs.context.validate_context_access pass-through)."""
        view, _ = self._view(
            rf,
            url="/labs/workflow/3962/run/?opportunity_id=1251 junk",
            labs_context={},
            opportunities=[],
        )
        assert view._recover_opportunity_id(3962) == 1251

    def test_does_not_salvage_inaccessible_leading_int(self, rf):
        """A salvaged id the user can't access is not adopted from the URL; we
        fall through to the (private → None) definition lookup."""
        view, _ = self._view(
            rf,
            url="/labs/workflow/3962/run/?opportunity_id=9999 junk",
            labs_context={},
            opportunities=[{"id": 1251, "name": "Opp"}],
        )
        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            MockWDA.return_value.get_definition.return_value = None  # private/unreadable un-scoped
            assert view._recover_opportunity_id(3962) is None

    def test_recovers_from_public_definition_when_no_url_id(self, rf):
        """No opp in the URL at all → read the definition's own opportunity_id
        (works for public workflows, which the API returns un-scoped)."""
        view, _ = self._view(
            rf,
            url="/labs/workflow/3962/run/?run_id=4259",
            labs_context={},
            opportunities=[{"id": 1251, "name": "Opp"}],
        )
        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            MockWDA.return_value.get_definition.return_value = MagicMock(opportunity_id=1251)
            assert view._recover_opportunity_id(3962) == 1251

    def test_get_redirects_to_canonical_url_for_malformed_link(self, rf):
        """Whole flow: a malformed link 302s to a clean integer param so the
        middleware re-seeds context on the redirect — and the junk is gone."""
        from connect_labs.workflow.views import WorkflowRunView

        request = rf.get("/labs/workflow/3962/run/?run_id=4259&opportunity_id=1251 stacked bar chart")
        request.user = self._user()
        request.labs_context = {}
        request.session = {
            "labs_oauth": {"access_token": "t", "organization_data": {"opportunities": [{"id": 1251}]}},
        }
        response = WorkflowRunView.as_view()(request, definition_id=3962)
        assert response.status_code == 302
        assert "opportunity_id=1251" in response.url
        assert "stacked" not in response.url
        assert "run_id=4259" in response.url

    def test_get_does_not_redirect_when_context_present(self, rf):
        """Normal path: a resolved labs context never triggers a recovery
        redirect — it falls through to the normal render."""
        from django.http import HttpResponse

        from connect_labs.workflow.views import TemplateView, WorkflowRunView

        request = rf.get("/labs/workflow/3962/run/?run_id=4259")
        request.user = self._user()
        request.labs_context = {"opportunity_id": 1251}
        request.session = {"labs_oauth": {"access_token": "t", "organization_data": {"opportunities": [{"id": 1251}]}}}
        sentinel = HttpResponse("rendered")
        with patch.object(TemplateView, "get", return_value=sentinel):
            response = WorkflowRunView.as_view()(request, definition_id=3962)
        assert response is sentinel  # fell through to super().get(), no redirect

    def test_context_names_unauthorized_opportunity(self, rf):
        """When the link names an opp the user can't access (recovery declined,
        no redirect), the no-context render explains it's an access problem and
        names the opportunity — not a generic 'pick an opportunity' prompt."""
        view, _ = self._view(
            rf,
            url="/labs/workflow/3962/run/?run_id=4259&opportunity_id=1251 stacked bar chart",
            labs_context={},  # opp 1251 absent → user isn't a member
            opportunities=[{"id": 700, "name": "Other"}],
        )
        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            MockWDA.return_value.get_definition.return_value = None
            context = view.get_context_data()
        assert context.get("unauthorized_opportunity_id") == "1251"
        assert "malformed_opportunity_param" not in context

    def test_context_flags_unparseable_param(self, rf):
        """A link with no parseable opp id surfaces the rejected raw value."""
        view, _ = self._view(
            rf,
            url="/labs/workflow/3962/run/?run_id=4259&opportunity_id=stacked bar chart",
            labs_context={},
            opportunities=[{"id": 700, "name": "Other"}],
        )
        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            MockWDA.return_value.get_definition.return_value = None
            context = view.get_context_data()
        assert context.get("malformed_opportunity_param") == "stacked bar chart"
        assert "unauthorized_opportunity_id" not in context

    def test_definition_404_renders_clean_access_message(self, rf):
        """Empty OAuth cache → get() passes the recovered opp through, the API
        404s, and the view shows a clean access message naming the opp instead
        of the raw wrapped error that leaks the internal /export/ URL."""
        from connect_labs.labs.integrations.connect.api_client import LabsAPIError
        from connect_labs.workflow.views import WorkflowRunView

        request = rf.get("/labs/workflow/3962/run/?run_id=4259&opportunity_id=1251")
        request.user = self._user()
        request.labs_context = {"opportunity_id": 1251}  # passed-through, unvalidated
        request.session = {"labs_oauth": {"access_token": "t", "organization_data": {"opportunities": []}}}
        view = WorkflowRunView()
        view.setup(request, definition_id=3962)
        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            MockWDA.return_value.get_definition.side_effect = LabsAPIError(
                "Failed to fetch record 3962: Client error '404 Not Found' for url "
                "'https://connect.dimagi.com/export/labs_record/?id=3962'",
                status_code=404,
            )
            context = view.get_context_data()
        err = context.get("error", "")
        assert "1251" in err and "access" in err.lower()
        assert "export/labs_record" not in err  # internal URL not leaked
        assert "404" not in err


class TestPipelineDataProgramOwnedFallback:
    """Program-owned multi-opp workflows have no single owning opportunity in
    the request context (no `opportunity_id` in labs_context or the URL).
    Both pipeline-data endpoints must fall back to the definition's own
    `opportunity_ids` list instead of bailing out — see the "no audit reports
    found" bug where a program-owned workflow silently never loaded data."""

    def test_stream_data_does_not_bail_when_only_program_id_present(self, dimagi_user, rf: RequestFactory):
        import json

        from connect_labs.workflow.views import PipelineDataStreamView

        request = rf.get("/labs/workflow/api/5181/pipeline-data/stream/")
        request.user = dimagi_user
        request.labs_context = {"program_id": 176}
        request.session = {"labs_oauth": {"access_token": "t"}}

        mock_definition = MagicMock(pipeline_sources=[], opportunity_ids=[1973, 1976, 1978, 1982])
        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            MockWDA.return_value.get_definition.return_value = mock_definition

            view = PipelineDataStreamView()
            view.kwargs = {"definition_id": 5181}
            events = [json.loads(chunk[len("data: ") :]) for chunk in view.stream_data(request)]

        assert not any(e.get("error") == "No opportunity selected" for e in events)
        assert any(e.get("message") == "No pipelines" for e in events)

    def test_get_pipeline_data_api_falls_back_to_definition_opportunity_ids(self, dimagi_user, rf: RequestFactory):
        import json

        from connect_labs.workflow.views import get_pipeline_data_api

        request = rf.get("/labs/workflow/api/5181/pipeline-data/")
        request.user = dimagi_user
        request.labs_context = {"program_id": 176}
        request.session = {"labs_oauth": {"access_token": "t"}}

        mock_definition = MagicMock(opportunity_ids=[1973, 1976, 1978, 1982])
        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_definition.return_value = mock_definition
            mock_wda.get_pipeline_data.return_value = {"audit_reports": {"rows": [], "metadata": {}}}

            response = get_pipeline_data_api(request, definition_id=5181)

        assert response.status_code == 200
        assert json.loads(response.content) == {"audit_reports": {"rows": [], "metadata": {}}}
        # Fell back to the first id in the definition's opportunity_ids, not a 400.
        mock_wda.get_pipeline_data.assert_called_once_with(5181, 1973)

    def test_get_pipeline_data_api_still_400s_with_no_context_at_all(self, dimagi_user, rf: RequestFactory):
        """Single-opp workflow with genuinely no context anywhere still 400s
        cleanly instead of crashing on int(None)."""
        from connect_labs.workflow.views import get_pipeline_data_api

        request = rf.get("/labs/workflow/api/42/pipeline-data/")
        request.user = dimagi_user
        request.labs_context = {}
        request.session = {"labs_oauth": {"access_token": "t"}}

        mock_definition = MagicMock(opportunity_ids=[])
        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            MockWDA.return_value.get_definition.return_value = mock_definition

            response = get_pipeline_data_api(request, definition_id=42)

        assert response.status_code == 400


class TestWorkflowRunViewProgramScopedInstance:
    """The JS `instance` prop (and its apiEndpoints) must reflect the RUN
    RECORD's own ownership, not the ambient request/session labs_context —
    which can carry a stale `opportunity_id` (e.g. left over from the workflow
    list page's or the run page's own per-opp background fetches) even while
    viewing a program-scoped run. Reproduced via a real browser session in two
    stages: (1) a program-owned Weekly Dual-Track Audit run's "Create Audits"
    button read a stale non-null instance.opportunity_id, sent it to startJob,
    and start_job_api's dispatch logic picked the opp-scoped branch instead of
    program-scoped — 404ing on get_run() and failing the whole batch with a
    generic "internal error"; (2) after fixing that, the job succeeded but a
    *separate* "Failed to update state" error appeared, because
    apiEndpoints.updateState/etc. carried no scope of their own and fell
    through to the same session, which by click time had been clobbered to a
    stale opportunity_id with program_id gone entirely."""

    def test_instance_opportunity_id_reflects_run_record_not_stale_session_context(
        self, dimagi_user, rf: RequestFactory
    ):
        from connect_labs.workflow.views import WorkflowRunView

        request = rf.get("/labs/workflow/6810/run/?run_id=6823&program_id=176")
        request.user = dimagi_user
        # Contaminated session context: program_id is correct, but a stale
        # opportunity_id lingers from earlier browsing (e.g. the workflow list
        # page's per-opp background fetches) — this must NOT leak into the
        # run's own instance.opportunity_id.
        request.labs_context = {"program_id": 176, "opportunity_id": 1976}
        request.session = {"labs_oauth": {"access_token": "t"}}

        mock_definition = MagicMock(
            data={},
            id=6810,
            multi_opp=True,
            opportunity_ids=[1973, 1976, 1978, 1982],
            name="Weekly Dual-Track Image Audit",
        )
        mock_run = MagicMock(
            id=6823,
            status="in_progress",
            state={},
            period_start="2026-07-17",
            period_end="2026-07-17",
            completed_at=None,
            snapshot=None,
            opportunity_id=None,  # the record's own truth: program-owned, no opp
            program_id=176,
        )

        with (
            patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA,
            patch("connect_labs.workflow.views.get_org_data", return_value={}),
        ):
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_definition.return_value = mock_definition
            mock_wda.get_render_code.return_value = None
            mock_wda.get_run.return_value = mock_run
            mock_wda.get_workers.return_value = []

            view = WorkflowRunView()
            view.request = request
            view.kwargs = {"definition_id": 6810}
            context = view.get_context_data()

        instance = context["workflow_data"]["instance"]
        assert instance["opportunity_id"] is None
        assert instance["program_id"] == 176

        # apiEndpoints must carry the run's own scope as an explicit query
        # param too — otherwise update_state_api etc. (which build
        # WorkflowDataAccess(request=request) from ambient labs_context alone)
        # fall through to whatever the session holds when the fetch actually
        # fires, which unrelated same-page background requests can have
        # clobbered by then. Reproduced live as "Failed to update state" on a
        # program-owned run right after its audit-creation job succeeded.
        endpoints = context["workflow_data"]["apiEndpoints"]
        assert endpoints["updateState"] == "/labs/workflow/api/run/6823/state/?program_id=176"
        assert endpoints["saveWorkerResult"] == "/labs/workflow/api/run/6823/worker-result/?program_id=176"
        assert endpoints["completeRun"] == "/labs/workflow/api/run/6823/complete/?program_id=176"
        assert endpoints["getSnapshot"] == "/labs/workflow/api/run/6823/snapshot/?program_id=176"
        assert endpoints["renameRun"] == "/labs/workflow/api/run/6823/rename/?program_id=176"


class TestResolvePipelineDefinitionCrossOpp:
    """A pipeline reused under a program-owned workflow (the recommended way
    to build one — see the program-owned workflow migration guide) is often
    owned by an opportunity OTHER than whichever one the caller picked as its
    single scope fallback (definition.opportunity_ids[0]). Before this fix,
    `pipeline_access.get_definition(pipeline_id)` only tried that one
    fallback opp, so every pipeline reported "not found" uniformly across
    every opportunity the workflow spans — reproduced end-to-end via a real
    browser session on workflow 5266 (Ward Progress Tracker, program 176)."""

    def test_retries_other_opps_when_first_scope_misses(self):
        from connect_labs.workflow.views import _resolve_pipeline_definition

        primary_access = MagicMock(opportunity_id=1973)
        primary_access.get_definition.return_value = None

        found_definition = MagicMock()
        with patch("connect_labs.workflow.views.PipelineDataAccess") as MockPDA:
            retry_access = MagicMock()
            retry_access.get_definition.side_effect = [None, found_definition]
            MockPDA.return_value = retry_access

            result = _resolve_pipeline_definition(
                primary_access,
                5126,
                opp_ids=[1973, 1976, 1982],
                request=MagicMock(),
                access_token="t",
            )

        assert result is found_definition
        # 1973 is skipped (already tried via primary_access); 1976 misses, 1982 hits.
        assert MockPDA.call_count == 2

    def test_returns_primary_result_without_retry_when_found(self):
        from connect_labs.workflow.views import _resolve_pipeline_definition

        primary_access = MagicMock(opportunity_id=1973)
        primary_access.get_definition.return_value = "found"

        with patch("connect_labs.workflow.views.PipelineDataAccess") as MockPDA:
            result = _resolve_pipeline_definition(
                primary_access, 5126, opp_ids=[1973, 1976], request=MagicMock(), access_token="t"
            )

        assert result == "found"
        MockPDA.assert_not_called()

    def test_returns_none_when_no_opp_owns_it(self):
        from connect_labs.workflow.views import _resolve_pipeline_definition

        primary_access = MagicMock(opportunity_id=1973)
        primary_access.get_definition.return_value = None

        with patch("connect_labs.workflow.views.PipelineDataAccess") as MockPDA:
            MockPDA.return_value.get_definition.return_value = None
            result = _resolve_pipeline_definition(
                primary_access, 5126, opp_ids=[1973, 1976, 1982], request=MagicMock(), access_token="t"
            )

        assert result is None

    def test_skips_retry_for_single_opp_workflow(self):
        """A regular single-opp workflow (opp_ids has 0 or 1 entries) never
        retries — a miss there is a real 404, not a cross-opp-ownership
        artifact, so no need to burn extra API calls."""
        from connect_labs.workflow.views import _resolve_pipeline_definition

        primary_access = MagicMock(opportunity_id=1973)
        primary_access.get_definition.return_value = None

        with patch("connect_labs.workflow.views.PipelineDataAccess") as MockPDA:
            result = _resolve_pipeline_definition(
                primary_access, 5126, opp_ids=[1973], request=MagicMock(), access_token="t"
            )

        assert result is None
        MockPDA.assert_not_called()

    def test_stream_data_resolves_pipeline_owned_by_non_first_opp(self, dimagi_user, rf: RequestFactory):
        """End-to-end: a program-owned workflow spanning [1973, 1976, 1978,
        1982] reuses a pipeline actually owned by 1982 (last in the list).
        The stream must resolve it instead of emitting "Pipeline not found"."""
        import json

        from connect_labs.workflow.views import PipelineDataStreamView

        request = rf.get("/labs/workflow/api/5266/pipeline-data/stream/")
        request.user = dimagi_user
        request.labs_context = {"program_id": 176}
        request.session = {"labs_oauth": {"access_token": "t"}}

        mock_definition = MagicMock(
            pipeline_sources=[{"pipeline_id": 5126, "alias": "work_areas"}],
            opportunity_ids=[1973, 1976, 1978, 1982],
        )
        found_pipeline_def = MagicMock(schema={"data_source": {"type": "cchq_cases"}})
        found_pipeline_def.name = "CHC Work Areas"

        with (
            patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA,
            patch("connect_labs.workflow.views.PipelineDataAccess") as MockPDA,
        ):
            MockWDA.return_value.get_definition.return_value = mock_definition

            # The primary (fallback-scoped) access misses; a later retry scoped
            # to 1982 is the one that actually owns pipeline 5126.
            primary_access = MagicMock(opportunity_id=1973)
            primary_access.get_definition.return_value = None
            miss_access = MagicMock()
            miss_access.get_definition.return_value = None
            hit_access = MagicMock()
            hit_access.get_definition.return_value = found_pipeline_def
            MockPDA.side_effect = [primary_access, miss_access, miss_access, hit_access]

            view = PipelineDataStreamView()
            view.kwargs = {"definition_id": 5266}
            events = [json.loads(chunk[len("data: ") :]) for chunk in view.stream_data(request)]

        assert not any("not found" in (e.get("message") or "") for e in events)


class TestReconcileGeneration:
    """reconcile_generation_api flips a program run's per-opp generation statuses
    from the authoritative per-opp run state (server-side, monotonic)."""

    def _request(self, rf, dimagi_user):
        request = rf.post("/labs/workflow/api/50/reconcile-generation/")
        request.user = dimagi_user
        request.labs_context = {"program_id": 176}
        request.session = {"labs_oauth": {"access_token": "t"}}
        return request

    def _patched(self, program_run, opp_run, sessions):
        def wda_factory(*args, **kwargs):
            m = MagicMock()
            # program-scoped (request=...) returns the program run; opp-scoped the opp run
            m.get_run.return_value = program_run if "request" in kwargs else opp_run
            return m

        wda = patch("connect_labs.workflow.views.WorkflowDataAccess", side_effect=wda_factory)
        ada = patch("connect_labs.audit.data_access.AuditDataAccess")
        return wda, ada, sessions

    def test_completed_with_audits_flips_to_ready(self, dimagi_user, rf: RequestFactory):
        import json

        program_run = SimpleNamespace(
            data={"state": {"generation": {"1973": {"opportunity_id": 1973, "run_id": 900, "status": "running"}}}},
            is_completed=False,
        )
        opp_run = SimpleNamespace(data={"state": {"active_job": {"status": "completed"}}})
        wda, ada, _ = self._patched(program_run, opp_run, [1, 2, 3])

        with wda, ada as MockADA:
            MockADA.return_value.get_sessions_by_workflow_run.return_value = [1, 2, 3]
            from connect_labs.workflow.views import reconcile_generation_api

            resp = reconcile_generation_api(self._request(rf, dimagi_user), run_id=50)

        assert resp.status_code == 200
        body = json.loads(resp.content)
        assert body["reconciled"] is True
        assert body["generation"]["1973"]["status"] == "ready"
        assert body["generation"]["1973"]["session_count"] == 3

    def test_completed_with_no_audits_flips_to_failed(self, dimagi_user, rf: RequestFactory):
        import json

        program_run = SimpleNamespace(
            data={"state": {"generation": {"1973": {"opportunity_id": 1973, "run_id": 900, "status": "running"}}}},
            is_completed=False,
        )
        opp_run = SimpleNamespace(data={"state": {"active_job": {"status": "completed"}}})
        wda, ada, _ = self._patched(program_run, opp_run, [])

        with wda, ada as MockADA:
            MockADA.return_value.get_sessions_by_workflow_run.return_value = []
            from connect_labs.workflow.views import reconcile_generation_api

            resp = reconcile_generation_api(self._request(rf, dimagi_user), run_id=50)

        body = json.loads(resp.content)
        assert body["generation"]["1973"]["status"] == "failed"

    def test_still_running_opp_is_left_untouched(self, dimagi_user, rf: RequestFactory):
        import json

        program_run = SimpleNamespace(
            data={"state": {"generation": {"1973": {"opportunity_id": 1973, "run_id": 900, "status": "running"}}}},
            is_completed=False,
        )
        opp_run = SimpleNamespace(data={"state": {"active_job": {"status": "running"}}})
        wda, ada, _ = self._patched(program_run, opp_run, [])

        with wda, ada:
            from connect_labs.workflow.views import reconcile_generation_api

            resp = reconcile_generation_api(self._request(rf, dimagi_user), run_id=50)

        body = json.loads(resp.content)
        assert body["reconciled"] is False
        assert body["generation"]["1973"]["status"] == "running"


class TestFlwAuditReportHistoryApi:
    """flw_audit_trend_dashboard reads this endpoint to chart
    flw_weekly_audit_report's saved weekly snapshots across opportunities."""

    def _request(self, rf, user, definition_id=None):
        params = {"definition_id": str(definition_id)} if definition_id is not None else {}
        request = rf.get("/labs/workflow/api/flw-audit-report-history/", params)
        request.user = user
        request.labs_context = {"program_id": 176}
        request.session = {"labs_oauth": {"access_token": "t"}}
        return request

    def _run(self, run_id, opportunity_id, report=None, completed=True):
        data = {"status": "completed" if completed else "in_progress"}
        if report is not None:
            data["snapshot"] = {"state": {"flw_audit_report": report}}
        return SimpleNamespace(id=run_id, opportunity_id=opportunity_id, data=data, is_completed=completed)

    def test_requires_definition_id(self, dimagi_user, rf: RequestFactory):
        import json

        from connect_labs.workflow.views import flw_audit_report_history_api

        resp = flw_audit_report_history_api(self._request(rf, dimagi_user))

        assert resp.status_code == 400
        assert "definition_id" in json.loads(resp.content)["error"]

    def test_returns_one_entry_per_completed_run_with_a_report(self, dimagi_user, rf: RequestFactory):
        import json

        report_1973 = {"period_start": "2026-07-06", "period_end": "2026-07-12", "flws": [{"username": "alice"}]}
        report_1982 = {"period_start": "2026-07-06", "period_end": "2026-07-12", "flws": [{"username": "bob"}]}
        runs = [
            self._run(1, 1973, report=report_1973),
            self._run(2, 1982, report=report_1982),
            self._run(3, 1976, report=None),  # completed but no snapshot yet (shouldn't happen, but tolerate)
            self._run(4, 1978, report={"period_start": "2026-07-06", "flws": []}, completed=False),  # in_progress
        ]

        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            mock_wda.list_runs.return_value = runs
            MockWDA.return_value = mock_wda

            from connect_labs.workflow.views import flw_audit_report_history_api

            resp = flw_audit_report_history_api(self._request(rf, dimagi_user, definition_id=6621))

        assert resp.status_code == 200
        body = json.loads(resp.content)
        mock_wda.list_runs.assert_called_once_with(definition_id=6621)
        assert {w["opportunity_id"] for w in body["weeks"]} == {1973, 1982}
        week_1973 = next(w for w in body["weeks"] if w["opportunity_id"] == 1973)
        assert week_1973["flws"] == [{"username": "alice"}]

    def test_invalid_definition_id_returns_400(self, dimagi_user, rf: RequestFactory):
        import json

        from connect_labs.workflow.views import flw_audit_report_history_api

        resp = flw_audit_report_history_api(self._request(rf, dimagi_user, definition_id="not-a-number"))

        assert resp.status_code == 400
        assert "integer" in json.loads(resp.content)["error"]
