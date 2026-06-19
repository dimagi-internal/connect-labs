"""
Render tests for the solicitations UI fixes surfaced by a DDD walkthrough.

Covers (priority order):
  1. Post-submission confirmation + suppression of the public respond/login
     re-invite for an actor who already responded.
  2. Real responding-org display name everywhere (no "Unknown Organization",
     no literal "this organization") — falls back to the submitter's name.
  3. Status vocabulary unified to "Accepting Responses".
  4. Scope-of-Work block collapses when it duplicates the Description.
  5. Responses list: first-class "Selected coverage" column + "Pending review".
  7. Create-success landing: published confirmation + share action.

Mocked-DA render tests (no DB), matching test_e2e_award_flow.py.
"""
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from commcare_connect.solicitations.data_access import RESPONSE_TYPE, SOLICITATION_TYPE
from commcare_connect.solicitations.models import ResponseRecord, SolicitationRecord
from commcare_connect.solicitations.views import AwardView, PublicSolicitationDetailView, ResponsesListView

_CONTEXT_PATCH = patch.multiple(
    "commcare_connect.web.context_processors",
    gtm_context=lambda request: {"GTM_VARS_JSON": {}},
    chat_widget_context=lambda request: {
        "chat_widget_enabled": False,
        "chatbot_id": "",
        "chatbot_embed_key": "",
    },
)


def _make_solicitation(pk=1, status="active", description="A test solicitation", scope_of_work=""):
    return SolicitationRecord(
        {
            "id": pk,
            "experiment": "prog_42",
            "type": SOLICITATION_TYPE,
            "data": {
                "title": "Neonatal Care RFP",
                "description": description,
                "scope_of_work": scope_of_work,
                "solicitation_type": "rfp",
                "status": status,
                "questions": [],
                "plans": [],
            },
            "opportunity_id": 0,
        }
    )


def _make_response(
    pk=10,
    solicitation_id=1,
    status="submitted",
    submitted_by_name="Jane Doe",
    submitted_by_email="jane@example.org",
    llo_entity_name="",
    org_name="",
    selected_plan_names=None,
):
    return ResponseRecord(
        {
            "id": pk,
            "experiment": "individual",
            "type": RESPONSE_TYPE,
            "data": {
                "solicitation_id": solicitation_id,
                "submitted_by_name": submitted_by_name,
                "submitted_by_email": submitted_by_email,
                "llo_entity_name": llo_entity_name,
                "org_name": org_name,
                "status": status,
                "selected_plan_names": selected_plan_names or [],
            },
            "opportunity_id": 0,
        }
    )


def _make_request(path="/", email="jane@example.org", username="jane"):
    request = RequestFactory().get(path)
    user = MagicMock(is_authenticated=True, username=username)
    user.id = 1
    user.email = email
    request.user = user
    request.labs_context = {"program_id": 42}
    request.session = {"labs_oauth": {"access_token": "tok", "expires_at": 9999999999}}
    return request


def _render(view_cls, request, **kwargs):
    response = view_cls.as_view()(request, **kwargs)
    assert response.status_code == 200
    response.render()
    return response.content.decode()


# -- Finding 1: confirmation + suppress re-invite ---------------------------


class TestSubmissionConfirmation:
    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_just_submitted_shows_confirmation_not_reinvite(self, MockDA):
        sol = _make_solicitation(pk=1)
        mine = _make_response(pk=10, status="submitted", selected_plan_names=["R6 — Attakar"])
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_responses_for_solicitation.return_value = [mine]

        request = _make_request("/solicitations/1/?submitted=1")
        content = _render(PublicSolicitationDetailView, request, pk=1)

        assert "Response submitted" in content
        assert "R6 — Attakar" in content
        # The public re-invite copy is gone for someone who already responded.
        assert "You will need to log in to continue" not in content
        assert "Respond to this Solicitation" not in content

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_already_responded_suppresses_invite(self, MockDA):
        sol = _make_solicitation(pk=1)
        mine = _make_response(pk=10, status="submitted")
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_responses_for_solicitation.return_value = [mine]

        request = _make_request("/solicitations/1/")  # no ?submitted
        content = _render(PublicSolicitationDetailView, request, pk=1)

        assert "already submitted a response" in content
        assert "Ready to respond?" not in content

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_non_responder_still_sees_respond_cta(self, MockDA):
        sol = _make_solicitation(pk=1)
        other = _make_response(pk=10, submitted_by_email="someone-else@example.org")
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_responses_for_solicitation.return_value = [other]

        request = _make_request("/solicitations/1/", email="newcomer@example.org", username="newcomer")
        content = _render(PublicSolicitationDetailView, request, pk=1)

        assert "Ready to respond?" in content
        assert "already submitted a response" not in content


# -- Finding 2: real org display name ---------------------------------------


class TestOrgDisplayName:
    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_responses_list_falls_back_to_submitter_name(self, MockDA):
        sol = _make_solicitation(pk=1)
        # No llo_entity_name, no org_name — must NOT say "Unknown Organization".
        resp = _make_response(pk=10, llo_entity_name="", org_name="", submitted_by_name="Amina Okafor")
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_responses_for_solicitation.return_value = [resp]
        MockDA.return_value.get_reviews_for_response.return_value = []

        content = _render(ResponsesListView, _make_request("/solicitations/1/responses/"), pk=1)

        assert "Unknown Organization" not in content
        assert "Amina Okafor" in content

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_award_page_never_says_this_organization(self, MockDA):
        sol = _make_solicitation(pk=1)
        resp = _make_response(pk=10, llo_entity_name="", org_name="", submitted_by_name="Amina Okafor")
        MockDA.return_value.get_response_by_id.return_value = resp
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_reviews_for_response.return_value = []

        content = _render(AwardView, _make_request("/solicitations/award/10/"), pk=10)

        assert "this organization" not in content
        assert "Amina Okafor" in content


# -- Finding 3 + 5: status vocab + responses list columns -------------------


class TestResponsesListPresentation:
    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_status_reads_accepting_responses(self, MockDA):
        sol = _make_solicitation(pk=1, status="active")
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_responses_for_solicitation.return_value = [_make_response(pk=10)]
        MockDA.return_value.get_reviews_for_response.return_value = []

        content = _render(ResponsesListView, _make_request("/solicitations/1/responses/"), pk=1)

        assert "Accepting Responses" in content

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_pending_review_label_for_unreviewed(self, MockDA):
        sol = _make_solicitation(pk=1)
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_responses_for_solicitation.return_value = [_make_response(pk=10)]
        MockDA.return_value.get_reviews_for_response.return_value = []

        content = _render(ResponsesListView, _make_request("/solicitations/1/responses/"), pk=1)

        assert "Pending review" in content

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_selected_coverage_is_a_column(self, MockDA):
        sol = _make_solicitation(pk=1)
        resp = _make_response(pk=10, selected_plan_names=["R6 — Attakar"])
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_responses_for_solicitation.return_value = [resp]
        MockDA.return_value.get_reviews_for_response.return_value = []

        content = _render(ResponsesListView, _make_request("/solicitations/1/responses/"), pk=1)

        # Header column exists and the plan name renders.
        assert "Selected coverage" in content
        assert "R6 — Attakar" in content


# -- Finding 4: collapse Description / Scope-of-Work duplication -------------


class TestScopeDeduplication:
    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_scope_hidden_when_duplicate_of_description(self, MockDA):
        sol = _make_solicitation(
            pk=1,
            description="Survey 5000 rooftops across Kaduna.",
            scope_of_work="Survey 5000 rooftops across Kaduna.",
        )
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_responses_for_solicitation.return_value = []

        content = _render(PublicSolicitationDetailView, _make_request("/solicitations/1/"), pk=1)
        # The rendered Scope heading (with its icon) is absent — the HTML comment
        # marker may remain, so assert on the visible heading markup specifically.
        assert "fa-list-check" not in content

    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_scope_shown_when_additive(self, MockDA):
        sol = _make_solicitation(
            pk=1,
            description="Survey 5000 rooftops across Kaduna.",
            scope_of_work="Deliverables: weekly GPS dumps, QA review, and a final coverage report.",
        )
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_responses_for_solicitation.return_value = []

        content = _render(PublicSolicitationDetailView, _make_request("/solicitations/1/"), pk=1)
        assert "fa-list-check" in content
        assert "weekly GPS dumps" in content


# -- Finding 7: create-success landing --------------------------------------


class TestCreateSuccessLanding:
    @_CONTEXT_PATCH
    @patch("commcare_connect.solicitations.views.SolicitationsDataAccess")
    def test_created_flag_shows_published_banner_and_share(self, MockDA):
        sol = _make_solicitation(pk=1)
        MockDA.return_value.get_solicitation_by_id.return_value = sol
        MockDA.return_value.get_responses_for_solicitation.return_value = []

        content = _render(ResponsesListView, _make_request("/solicitations/1/responses/?created=1"), pk=1)

        assert "Solicitation published" in content
        assert "Copy share link" in content
