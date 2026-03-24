"""
End-to-end integration tests for the award flow.

Walks through the demo scenario: view responses list → view response
detail → award response → verify awarded status.

Uses Django's test client with mocked LabsRecordAPIClient.

Run:
    pytest commcare_connect/solicitations/tests/test_e2e_award_flow.py -v
"""
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from commcare_connect.solicitations.data_access import RESPONSE_TYPE, REVIEW_TYPE, SOLICITATION_TYPE
from commcare_connect.solicitations.models import ResponseRecord, ReviewRecord, SolicitationRecord
from commcare_connect.solicitations.views import AwardView, ResponseDetailView, ResponsesListView

# =========================================================================
# Helpers
# =========================================================================


def _make_solicitation(pk=1, title="Neonatal Care RFP", status="active"):
    return SolicitationRecord(
        {
            "id": pk,
            "experiment": "prog_42",
            "type": SOLICITATION_TYPE,
            "data": {
                "title": title,
                "description": "A test solicitation",
                "solicitation_type": "rfp",
                "status": status,
                "questions": [],
            },
            "opportunity_id": 0,
        }
    )


def _make_response(pk=10, solicitation_id=1, status="submitted", org_id="", reward_budget=None):
    data = {
        "solicitation_id": solicitation_id,
        "submitted_by_name": "Jane Doe",
        "submitted_by_email": "jane@example.org",
        "llo_entity_name": "Health Org",
        "status": status,
        "answers": {},
    }
    if org_id:
        data["org_id"] = org_id
    if reward_budget is not None:
        data["reward_budget"] = reward_budget
    return ResponseRecord(
        {
            "id": pk,
            "experiment": "prog_42",
            "type": RESPONSE_TYPE,
            "data": data,
            "opportunity_id": 0,
        }
    )


def _make_review(pk=20, response_id=10, recommendation="approved", score=85):
    return ReviewRecord(
        {
            "id": pk,
            "experiment": "prog_42",
            "type": REVIEW_TYPE,
            "data": {
                "response_id": response_id,
                "recommendation": recommendation,
                "score": score,
                "reviewer_username": "reviewer1",
                "notes": "Good proposal",
            },
            "opportunity_id": 0,
        }
    )


def _make_request(path="/", method="GET", data=None, user=None):
    factory = RequestFactory()
    if method == "POST":
        request = factory.post(path, data=data or {})
    else:
        request = factory.get(path)
    if user is None:
        user = MagicMock(is_authenticated=True, username="manager_user")
        user.id = 1
        user.email = "test@dimagi.com"
    request.user = user
    request.labs_context = {"program_id": 42}
    request.session = {"labs_oauth": {"access_token": "tok", "expires_at": 9999999999}}
    return request


# Patch context processors that need real settings (GTM, chat widget) to
# return plain dicts so base.html renders without errors.
_CONTEXT_PATCH = patch.multiple(
    "commcare_connect.web.context_processors",
    gtm_context=lambda request: {"GTM_VARS_JSON": {}},
    chat_widget_context=lambda request: {
        "chat_widget_enabled": False,
        "chatbot_id": "",
        "chatbot_embed_key": "",
    },
)


# =========================================================================
# Step 1: Responses List
# =========================================================================


class TestStep1ResponsesList:
    """Step 1: Manager views the list of responses to a solicitation."""

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_responses_list_renders(self, MockDA):
        """Responses list shows all responses with Award action."""
        solicitation = _make_solicitation(pk=1)
        responses = [
            _make_response(pk=10, status="submitted"),
            _make_response(pk=11, status="reviewed"),
        ]
        MockDA.return_value.get_solicitation_by_id.return_value = solicitation
        MockDA.return_value.get_responses_for_solicitation.return_value = responses
        MockDA.return_value.get_reviews_for_response.return_value = []

        request = _make_request("/solicitations/1/responses/")
        response = ResponsesListView.as_view()(request, pk=1)

        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Health Org" in content
        assert "Neonatal Care RFP" in content

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_responses_list_shows_awarded_badge(self, MockDA):
        """Responses that are awarded show the Awarded badge instead of Award link."""
        solicitation = _make_solicitation(pk=1)
        responses = [
            _make_response(pk=10, status="awarded", org_id="org_77", reward_budget=50000),
        ]
        MockDA.return_value.get_solicitation_by_id.return_value = solicitation
        MockDA.return_value.get_responses_for_solicitation.return_value = responses
        MockDA.return_value.get_reviews_for_response.return_value = []

        request = _make_request("/solicitations/1/responses/")
        response = ResponsesListView.as_view()(request, pk=1)

        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Awarded" in content


# =========================================================================
# Step 2: Response Detail
# =========================================================================


class TestStep2ResponseDetail:
    """Step 2: Manager views a response detail to see if it's award-worthy."""

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_detail_shows_award_button(self, MockDA):
        """Non-awarded response shows the Award button."""
        resp = _make_response(pk=10, status="submitted")
        solicitation = _make_solicitation(pk=1)
        MockDA.return_value.get_response_by_id.return_value = resp
        MockDA.return_value.get_solicitation_by_id.return_value = solicitation
        MockDA.return_value.get_reviews_for_response.return_value = []

        request = _make_request("/solicitations/response/10/")
        response = ResponseDetailView.as_view()(request, pk=10)

        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Award" in content
        assert "fa-trophy" in content

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_detail_shows_awarded_badge_when_awarded(self, MockDA):
        """Awarded response shows the Awarded badge instead of Award button."""
        resp = _make_response(pk=10, status="awarded")
        solicitation = _make_solicitation(pk=1)
        MockDA.return_value.get_response_by_id.return_value = resp
        MockDA.return_value.get_solicitation_by_id.return_value = solicitation
        MockDA.return_value.get_reviews_for_response.return_value = []

        request = _make_request("/solicitations/response/10/")
        response = ResponseDetailView.as_view()(request, pk=10)

        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Awarded" in content


# =========================================================================
# Step 3: Award Form
# =========================================================================


class TestStep3AwardForm:
    """Step 3: Manager opens the award form and submits it."""

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_award_form_renders(self, MockDA):
        """Award form shows org_id and budget fields."""
        resp = _make_response(pk=10)
        solicitation = _make_solicitation(pk=1)
        MockDA.return_value.get_response_by_id.return_value = resp
        MockDA.return_value.get_solicitation_by_id.return_value = solicitation

        request = _make_request("/solicitations/response/10/award/")
        response = AwardView.as_view()(request, pk=10)

        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Award Response" in content
        assert 'name="org_id"' in content
        assert 'name="reward_budget"' in content

    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_award_submit_redirects(self, MockDA):
        """POST with valid data awards the response and redirects."""
        _make_response(pk=10, solicitation_id=1)
        awarded_resp = _make_response(pk=10, status="awarded", org_id="org_77", reward_budget=50000)
        MockDA.return_value.award_response.return_value = awarded_resp
        MockDA.return_value.get_response_by_id.return_value = awarded_resp

        request = _make_request(
            "/solicitations/response/10/award/",
            method="POST",
            data={"org_id": "org_77", "reward_budget": "50000"},
        )
        response = AwardView.as_view()(request, pk=10)

        assert response.status_code == 302
        MockDA.return_value.award_response.assert_called_once_with(10, reward_budget=50000, org_id="org_77")


# =========================================================================
# Full Flow: Responses List → Detail → Award → Verify
# =========================================================================


class TestFullAwardFlow:
    """Walk through the complete award lifecycle."""

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_full_award_lifecycle(self, MockDA):
        """Full lifecycle: responses list → detail → award → verify awarded."""
        solicitation = _make_solicitation(pk=1)
        resp_submitted = _make_response(pk=10, status="submitted")
        resp_awarded = _make_response(pk=10, status="awarded", org_id="org_77", reward_budget=50000)

        # -- Step 1: View responses list --
        MockDA.return_value.get_solicitation_by_id.return_value = solicitation
        MockDA.return_value.get_responses_for_solicitation.return_value = [resp_submitted]
        MockDA.return_value.get_reviews_for_response.return_value = []

        request = _make_request("/solicitations/1/responses/")
        response = ResponsesListView.as_view()(request, pk=1)
        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Health Org" in content

        # -- Step 2: View response detail --
        MockDA.return_value.get_response_by_id.return_value = resp_submitted
        MockDA.return_value.get_solicitation_by_id.return_value = solicitation

        request = _make_request("/solicitations/response/10/")
        response = ResponseDetailView.as_view()(request, pk=10)
        assert response.status_code == 200
        response.render()
        assert "Award" in response.content.decode()

        # -- Step 3: Award the response --
        MockDA.return_value.award_response.return_value = resp_awarded
        MockDA.return_value.get_response_by_id.return_value = resp_awarded

        request = _make_request(
            "/solicitations/response/10/award/",
            method="POST",
            data={"org_id": "org_77", "reward_budget": "50000"},
        )
        response = AwardView.as_view()(request, pk=10)
        assert response.status_code == 302

        # -- Step 4: Verify awarded status on detail --
        MockDA.return_value.get_response_by_id.return_value = resp_awarded

        request = _make_request("/solicitations/response/10/")
        response = ResponseDetailView.as_view()(request, pk=10)
        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Awarded" in content

        # -- Step 5: Verify awarded status on responses list --
        MockDA.return_value.get_responses_for_solicitation.return_value = [resp_awarded]

        request = _make_request("/solicitations/1/responses/")
        response = ResponsesListView.as_view()(request, pk=1)
        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Awarded" in content
