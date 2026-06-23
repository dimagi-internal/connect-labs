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

from commcare_connect.users.tests.factories import UserFactory


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
        from commcare_connect.workflow.templates import TEMPLATES

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
                patch("commcare_connect.workflow.views.WorkflowDataAccess") as MockWDA,
                patch("commcare_connect.workflow.views.create_from_template") as mock_create,
            ):
                mock_wda = MagicMock()
                MockWDA.return_value = mock_wda
                mock_create.return_value = (
                    MagicMock(id=1, name="T"),
                    MagicMock(),
                    None,
                )

                from commcare_connect.workflow.views import create_workflow_from_template_view

                create_workflow_from_template_view(request)

                # Verify opportunity_ids was passed through
                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs["opportunity_ids"] == [700, 825]
        finally:
            del TEMPLATES["__tv_multi__"]

    def test_rejects_opportunity_ids_outside_user_opportunities(self, dimagi_user, rf: RequestFactory):
        from commcare_connect.workflow.templates import TEMPLATES

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

            with patch("commcare_connect.workflow.views.create_from_template") as mock_create:
                from commcare_connect.workflow.views import create_workflow_from_template_view

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

        with patch("commcare_connect.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_definition.return_value = MagicMock(multi_opp=True)
            mock_wda.update_opportunity_ids.return_value = MagicMock(id=1)

            from commcare_connect.workflow.views import UpdateOpportunityIdsView

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

        with patch("commcare_connect.workflow.views.WorkflowDataAccess") as MockWDA:
            mock_wda = MagicMock()
            MockWDA.return_value = mock_wda
            mock_wda.get_definition.return_value = MagicMock(multi_opp=False)

            from commcare_connect.workflow.views import UpdateOpportunityIdsView

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

        from commcare_connect.workflow.views import UpdateOpportunityIdsView

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

        from commcare_connect.workflow.views import UpdateOpportunityIdsView

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

        from commcare_connect.workflow.views import UpdateOpportunityIdsView

        response = UpdateOpportunityIdsView.as_view()(request, definition_id=1)
        assert response.status_code == 400


class TestCompleteRunTemplateFallback:
    """complete_run_api recovers a missing config.templateType from the
    workflow name (same strict match template sync uses) and self-heals the
    definition record, instead of dead-ending the conclude with a 400."""

    TEMPLATE_KEY = "__tv_saved_runs__"

    def _records(self, definition_name):
        from commcare_connect.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord

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
        from commcare_connect.workflow.views import complete_run_api

        with patch("commcare_connect.workflow.views.WorkflowDataAccess") as MockWDA:
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

        from commcare_connect.workflow.templates import TEMPLATES

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

        from commcare_connect.workflow.data_access import WorkflowDefinitionRecord

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

        from commcare_connect.workflow.templates import TEMPLATES

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
        from commcare_connect.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord

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
        from commcare_connect.workflow.views import complete_run_api

        with patch("commcare_connect.workflow.views.WorkflowDataAccess") as MockWDA:
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

        from commcare_connect.workflow.data_access import PipelineCacheMiss

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
        from commcare_connect.workflow.views import WorkflowRunView

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
        with patch("commcare_connect.workflow.views.WorkflowDataAccess") as MockWDA:
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
        with patch("commcare_connect.workflow.views.WorkflowDataAccess") as MockWDA:
            MockWDA.return_value.get_definition.return_value = MagicMock(opportunity_id=1251)
            assert view._recover_opportunity_id(3962) == 1251

    def test_get_redirects_to_canonical_url_for_malformed_link(self, rf):
        """Whole flow: a malformed link 302s to a clean integer param so the
        middleware re-seeds context on the redirect — and the junk is gone."""
        from commcare_connect.workflow.views import WorkflowRunView

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

        from commcare_connect.workflow.views import TemplateView, WorkflowRunView

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
        with patch("commcare_connect.workflow.views.WorkflowDataAccess") as MockWDA:
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
        with patch("commcare_connect.workflow.views.WorkflowDataAccess") as MockWDA:
            MockWDA.return_value.get_definition.return_value = None
            context = view.get_context_data()
        assert context.get("malformed_opportunity_param") == "stacked bar chart"
        assert "unauthorized_opportunity_id" not in context
