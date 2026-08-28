"""
Tests for labs context management.
"""
import pytest
from django.test import RequestFactory

from connect_labs.labs.context import (
    LabsContextMiddleware,
    add_context_to_url,
    extract_context_from_url,
    get_context_url_params,
    try_auto_select_context,
    validate_context_access,
)
from connect_labs.users.models import User


@pytest.mark.django_db
class TestContextExtraction:
    """Test context extraction from URLs and sessions."""

    def test_extract_context_from_url(self):
        """Test extracting context parameters from URL."""
        factory = RequestFactory()
        request = factory.get("/tasks/?opportunity_id=123&program_id=456")

        context = extract_context_from_url(request)

        assert context["opportunity_id"] == 123
        assert context["program_id"] == 456

    def test_extract_context_from_url_with_org_slug(self):
        """Test extracting organization as string slug."""
        factory = RequestFactory()
        request = factory.get("/solicitations/?organization_id=dimagi")

        context = extract_context_from_url(request)

        assert context["organization_id"] == "dimagi"

    def test_add_context_to_url(self):
        """Test adding context parameters to a URL."""
        url = "/tasks/"
        context = {"opportunity_id": 123, "program_id": 456}

        result = add_context_to_url(url, context)

        assert "opportunity_id=123" in result
        assert "program_id=456" in result

    def test_get_context_url_params(self):
        """Test getting context as query string."""
        context = {"opportunity_id": 123, "program_id": 456}

        result = get_context_url_params(context)

        assert "opportunity_id=123" in result
        assert "program_id=456" in result


@pytest.mark.django_db
class TestContextValidation:
    """Test context access validation."""

    def test_validate_context_access_with_valid_opportunity(self):
        """Test validation succeeds with valid opportunity."""
        factory = RequestFactory()
        request = factory.get("/")

        # Create Django User and set up session with org data
        user = User.objects.create(username="testuser", email="test@example.com")
        request.user = user
        request.session = {
            "labs_oauth": {"organization_data": {"opportunities": [{"id": 123, "name": "Test Opportunity"}]}}
        }

        context = {"opportunity_id": 123}
        validated = validate_context_access(request, context)

        assert validated["opportunity_id"] == 123
        assert "opportunity" in validated
        assert validated["opportunity"]["name"] == "Test Opportunity"

    def test_validate_context_access_with_invalid_opportunity(self):
        """Test validation fails with invalid opportunity."""
        factory = RequestFactory()
        request = factory.get("/")

        user = User.objects.create(username="testuser2", email="test2@example.com")
        request.user = user
        request.session = {
            "labs_oauth": {"organization_data": {"opportunities": [{"id": 123, "name": "Test Opportunity"}]}}
        }

        context = {"opportunity_id": 999}
        validated = validate_context_access(request, context)

        # Unknown opportunity IDs are passed through for API-level validation
        # (handles managed opps not in cached OAuth data)
        assert validated["opportunity_id"] == 999
        assert "opportunity" not in validated

    def test_validate_context_access_passes_through_with_empty_org_data(self):
        """opportunity_id / program_id pass through even when cached OAuth org_data is empty.

        A session whose ``organization_data`` came back empty (e.g. the Connect
        org-list API flaked at login, stored as ``{}`` — see oauth_views.py) must
        still be able to apply an opportunity/program context from a deep link.
        Returning ``{}`` here would make LabsContextMiddleware treat the param as
        "no access" and strip it from the URL, defeating opportunity-scoped deep
        links (e.g. headless walkthrough renders). Downstream LabsRecord API calls
        enforce real access, so passthrough is safe.
        """
        factory = RequestFactory()
        request = factory.get("/")

        user = User.objects.create(username="testuser_empty", email="empty@example.com")
        request.user = user
        # organization_data stored empty — the API-failed-at-login case.
        request.session = {"labs_oauth": {"organization_data": {}}}

        context = {"opportunity_id": 2018, "program_id": 77}
        validated = validate_context_access(request, context)

        assert validated["opportunity_id"] == 2018
        assert validated["program_id"] == 77
        # No org/opportunity objects to resolve without cached data — IDs only.
        assert "opportunity" not in validated
        assert "program" not in validated


@pytest.mark.django_db
class TestAutoSelection:
    """Test auto-selection logic."""

    def test_auto_select_single_opportunity(self):
        """Test auto-selects when user has exactly one opportunity."""
        factory = RequestFactory()
        request = factory.get("/")

        org_data = {
            "opportunities": [{"id": 123, "name": "Only Opportunity"}],
            "programs": [],
            "organizations": [],
        }
        user = User.objects.create(username="testuser3", email="test3@example.com")
        request.user = user
        request.session = {"labs_oauth": {"organization_data": org_data}}

        result = try_auto_select_context(request)

        assert result is not None
        assert result["opportunity_id"] == 123

    def test_no_auto_select_multiple_opportunities(self):
        """Test doesn't auto-select when user has multiple opportunities."""
        factory = RequestFactory()
        request = factory.get("/")

        org_data = {
            "opportunities": [{"id": 123, "name": "Opportunity 1"}, {"id": 456, "name": "Opportunity 2"}],
            "programs": [],
            "organizations": [],
        }
        user = User.objects.create(username="testuser4", email="test4@example.com")
        request.user = user
        request.session = {"labs_oauth": {"organization_data": org_data}}

        result = try_auto_select_context(request)

        assert result is None

    def test_auto_select_single_program(self):
        """Test auto-selects program when user has exactly one program and no opportunities."""
        factory = RequestFactory()
        request = factory.get("/")

        org_data = {
            "opportunities": [],
            "programs": [{"id": 789, "name": "Only Program"}],
            "organizations": [],
        }
        user = User.objects.create(username="testuser5", email="test5@example.com")
        request.user = user
        request.session = {"labs_oauth": {"organization_data": org_data}}

        result = try_auto_select_context(request)

        assert result is not None
        assert result["program_id"] == 789


@pytest.mark.django_db
class TestContextRedirect:
    """The session->URL context redirect, and the sub-resources it must skip.

    These pin a COST, not just a behaviour: the property that regressed is
    "how many HTTP requests does one image tile take", and a test that only
    asserted the final context would have stayed green through the doubling.
    """

    def _request(self, path, user):
        factory = RequestFactory()
        request = factory.get(path)
        request.user = user
        request.session = {"labs_context": {"opportunity_id": 1488}}
        return request

    def _user(self, db):
        return User.objects.create_user(username="ctx-redirect", password="x")

    def test_page_path_still_redirects_to_carry_context(self, db):
        """A navigable page keeps the redirect — that is what makes its URL shareable."""
        request = self._request("/audit/16722/bulk/", self._user(db))
        response = LabsContextMiddleware(lambda r: None).process_request(request)

        assert response is not None, "a page under /audit/ must still be decorated"
        assert response.status_code == 302
        assert "opportunity_id=1488" in response["Location"]

    def test_image_subresource_is_not_redirected(self, db):
        """An image tile is fetched, never shared, and already names its opportunity.

        Redirecting it doubles the request count of the highest-volume endpoint
        on the site: 1,888 redirects to serve 1,839 images over three hours of
        real traffic (2026-08-25). One request in, one request out.
        """
        request = self._request("/audit/image/1488/7d2e7121-b191-46c8-bf55-231e75f83a0f/", self._user(db))
        response = LabsContextMiddleware(lambda r: None).process_request(request)

        assert response is None, "image tiles must be served, not redirected"

    def test_skipping_the_redirect_still_applies_session_context(self, db):
        """The context still lands — only the round trip is dropped.

        This is the half that makes the exemption safe, and the half a
        "does it redirect?" test cannot see.
        """
        request = self._request("/audit/image/1488/7d2e7121-b191-46c8-bf55-231e75f83a0f/", self._user(db))
        LabsContextMiddleware(lambda r: None).process_request(request)

        assert getattr(request, "labs_context", None) is not None
