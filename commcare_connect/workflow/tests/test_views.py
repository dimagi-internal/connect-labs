"""Integration tests for workflow views.

Mocks WorkflowDataAccess and other external dependencies; uses Django test client.
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
