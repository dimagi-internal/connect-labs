"""
E2E: Full solicitation lifecycle test with three personas.

Persona 1 (Author/Sarah): Creates solicitation via MCP with evaluation criteria
Persona 2 (Respondent/Amina): Responds via web UI, sees criteria alongside questions
Persona 3 (Reviewer/James): Reviews response via web UI, scores against criteria, awards

All personas use jjackson+test account (profile: test-user).
"""
import concurrent.futures
import os
import sys
import time

import pytest

pytestmark = pytest.mark.e2e

SCREENSHOTS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "screenshots", "e2e", "solicitation_lifecycle"
)

# Add MCP tools to path — 4 levels up from e2e/ to project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "tools", "commcare_mcp"))


def _screenshot(page, name):
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    page.screenshot(path=os.path.join(SCREENSHOTS_DIR, f"{name}.png"), full_page=True)


class TestSolicitationLifecycle:
    """Full lifecycle: Author creates -> Respondent responds -> Reviewer scores -> Award."""

    def test_persona_1_author_creates_solicitation(self, auth_page, live_server_url, org_id):
        """
        PERSONA 1: Sarah Chen, Dimagi Program Manager
        Creates a solicitation via MCP tools with evaluation criteria.
        """
        from solicitation_tools import create_solicitation

        timestamp = int(time.time())
        data = {
            "title": f"E2E: Community Health Worker Training Program {timestamp}",
            "description": "Seeking qualified organizations to design and deliver CHW training programs focused on integrated maternal and child health service delivery in rural communities.",
            "solicitation_type": "rfp",
            "status": "active",
            "is_public": True,
            "scope_of_work": "Train 200+ CHWs in integrated RMNCH service delivery. Develop mobile-based learning modules. Establish mentorship program.",
            "application_deadline": "2026-06-30",
            "expected_start_date": "2026-09-01",
            "expected_end_date": "2028-08-31",
            "estimated_scale": "200+ CHWs across 50 facilities",
            "contact_email": "programs@dimagi.com",
            "questions": [
                {
                    "id": "q_1",
                    "text": "Describe your organization's experience training community health workers.",
                    "type": "textarea",
                    "required": True,
                    "options": [],
                },
                {
                    "id": "q_2",
                    "text": "What is your proposed training methodology?",
                    "type": "textarea",
                    "required": True,
                    "options": [],
                },
                {
                    "id": "q_3",
                    "text": "Which regions do you propose to work in?",
                    "type": "textarea",
                    "required": True,
                    "options": [],
                },
                {
                    "id": "q_4",
                    "text": "What is your proposed budget range?",
                    "type": "multiple_choice",
                    "required": True,
                    "options": ["Under $250K", "$250K-$500K", "$500K-$1M", "Over $1M"],
                },
            ],
            "evaluation_criteria": [
                {
                    "id": "ec_1",
                    "name": "Organizational Experience",
                    "weight": 30,
                    "description": "Track record of CHW training programs in similar contexts",
                    "scoring_guide": "Strong: 3+ years with measurable outcomes. Weak: no direct experience.",
                    "linked_questions": ["q_1"],
                },
                {
                    "id": "ec_2",
                    "name": "Technical Approach",
                    "weight": 35,
                    "description": "Quality and innovation of proposed training methodology",
                    "scoring_guide": "Strong: blended learning with digital tools. Weak: lecture-only.",
                    "linked_questions": ["q_2"],
                },
                {
                    "id": "ec_3",
                    "name": "Geographic Fit",
                    "weight": 15,
                    "description": "Presence and relationships in proposed regions",
                    "scoring_guide": "Strong: existing operations and MoH relationships. Weak: no presence.",
                    "linked_questions": ["q_3"],
                },
                {
                    "id": "ec_4",
                    "name": "Value for Money",
                    "weight": 20,
                    "description": "Cost-effectiveness and budget appropriateness",
                    "scoring_guide": "Strong: competitive with clear justification. Weak: significantly off market.",
                    "linked_questions": ["q_4"],
                },
            ],
        }

        # Run async MCP call in a separate thread (Playwright's sync API uses the event loop)
        import asyncio as _asyncio

        def _run():
            return _asyncio.run(create_solicitation(organization_id=org_id, data=data))

        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = pool.submit(_run).result(timeout=30)
        assert result.get("id"), f"Failed to create solicitation: {result}"
        solicitation_id = result["id"]

        self.__class__._solicitation_id = solicitation_id
        self.__class__._timestamp = timestamp

        page = auth_page
        page.goto(f"{live_server_url}/solicitations/{solicitation_id}/")
        page.wait_for_load_state("networkidle")
        _screenshot(page, "01_author_solicitation_created")

        assert page.get_by_text("Community Health Worker Training").first.is_visible()
        assert page.get_by_text("Evaluation Criteria").first.is_visible()
        assert page.get_by_text("Organizational Experience").first.is_visible()

        page.get_by_text("Evaluation Criteria").first.scroll_into_view_if_needed()
        _screenshot(page, "02_author_evaluation_criteria")

    def test_persona_2_respondent_submits_response(self, auth_page, live_server_url):
        """
        PERSONA 2: Amina Okafor, LLO Program Director in Nigeria
        Responds to the solicitation via web UI.
        """
        if not hasattr(self.__class__, "_solicitation_id"):
            pytest.skip("Previous test did not set solicitation_id")

        page = auth_page
        page.set_default_timeout(30_000)
        solicitation_id = self.__class__._solicitation_id

        page.goto(f"{live_server_url}/solicitations/{solicitation_id}/respond/")
        page.wait_for_load_state("networkidle")
        _screenshot(page, "03_respondent_form_view")

        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()

        response = page.request.post(
            f"{live_server_url}/solicitations/{solicitation_id}/respond/",
            form={
                "csrfmiddlewaretoken": csrf_token,
                "question_q_1": "Health Bridge Nigeria has trained over 500 CHWs across 3 states over 5 years, achieving 40% increase in facility deliveries and 65% improvement in postnatal visits.",
                "question_q_2": "Blended learning: 2-week classroom intensive, 3 months supervised field practice with weekly mobile micro-learning modules. Each CHW paired with an experienced mentor.",
                "question_q_3": "Kano, Kaduna, and Niger states — existing relationships with State PHCDAs and 12 operational LGAs.",
                "question_q_4": "$250K-$500K",
                "submit": "true",
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        page.goto(f"{live_server_url}/solicitations/{solicitation_id}/responses/")
        page.wait_for_load_state("networkidle")
        _screenshot(page, "04_respondent_responses_list")

        view_link = page.locator("a:has-text('View')").first
        if view_link.count() > 0:
            view_link.click()
            page.wait_for_load_state("networkidle")
            _screenshot(page, "05_respondent_response_detail")
            self.__class__._response_url = page.url

    def test_persona_3_reviewer_scores_response(self, auth_page, live_server_url):
        """
        PERSONA 3: James Mwangi, Dimagi Technical Advisor
        Reviews and scores the response against evaluation criteria.
        """
        if not hasattr(self.__class__, "_solicitation_id"):
            pytest.skip("Previous test did not set solicitation_id")

        page = auth_page
        page.set_default_timeout(30_000)
        solicitation_id = self.__class__._solicitation_id

        page.goto(f"{live_server_url}/solicitations/{solicitation_id}/responses/")
        page.wait_for_load_state("networkidle")

        review_link = page.locator("a:has-text('Review')").first
        assert review_link.count() > 0, "No Review link found"
        review_link.click()
        page.wait_for_load_state("networkidle")
        _screenshot(page, "06_reviewer_review_form")

        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()

        form_data = {
            "csrfmiddlewaretoken": csrf_token,
            "score": "82",
            "recommendation": "approved",
            "notes": "Strong candidate with proven CHW training experience in northern Nigeria. Blended learning approach is well-designed. Budget is competitive.",
            "tags": "experienced, nigeria, blended-learning",
        }

        # Add per-criteria scores if available
        criteria_fields = page.locator("input[name^='criteria_score_']")
        if criteria_fields.count() > 0:
            form_data["criteria_score_ec_1"] = "9"
            form_data["criteria_score_ec_2"] = "8"
            form_data["criteria_score_ec_3"] = "9"
            form_data["criteria_score_ec_4"] = "7"

        response = page.request.post(page.url, form=form_data, timeout=60_000)
        assert response.ok or response.status == 302

        page.goto(f"{live_server_url}/solicitations/{solicitation_id}/responses/")
        page.wait_for_load_state("networkidle")
        _screenshot(page, "07_reviewer_responses_after_review")

        view_link = page.locator("a:has-text('View')").first
        if view_link.count() > 0:
            view_link.click()
            page.wait_for_load_state("networkidle")
            _screenshot(page, "08_reviewer_response_with_review")

    def test_persona_3_reviewer_awards_response(self, auth_page, live_server_url, org_id):
        """
        PERSONA 3 continued: Awards the response after positive review.
        """
        if not hasattr(self.__class__, "_solicitation_id"):
            pytest.skip("Previous test did not set solicitation_id")

        page = auth_page
        page.set_default_timeout(30_000)
        solicitation_id = self.__class__._solicitation_id

        page.goto(f"{live_server_url}/solicitations/{solicitation_id}/responses/")
        page.wait_for_load_state("networkidle")

        award_link = page.locator("a:has-text('Award')").first
        if award_link.count() > 0:
            award_link.click()
            page.wait_for_load_state("networkidle")
            _screenshot(page, "09_reviewer_award_form")

            csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()
            response = page.request.post(
                page.url,
                form={
                    "csrfmiddlewaretoken": csrf_token,
                    "org_id": org_id,
                    "reward_budget": "350000",
                },
                timeout=60_000,
            )
            assert response.ok or response.status == 302

            page.goto(f"{live_server_url}/solicitations/{solicitation_id}/responses/")
            page.wait_for_load_state("networkidle")
            _screenshot(page, "10_final_awarded_state")
