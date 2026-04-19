"""Unit tests for workflow views.

Uses Django's RequestFactory to construct bare requests and invokes view
functions / class-based-view dispatchers directly. External dependencies
like WorkflowDataAccess are mocked. Because RequestFactory does not run
middleware, middleware-dependent behaviour (CSRF, session, etc.) is
simulated by attaching the required attributes to the request in each test.
"""

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

            with patch("commcare_connect.workflow.views.WorkflowDataAccess") as MockWDA, patch(
                "commcare_connect.workflow.views.create_from_template"
            ) as mock_create:
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
