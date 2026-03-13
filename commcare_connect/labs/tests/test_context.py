"""
Tests for labs context management.
"""
import pytest
from django.test import RequestFactory

from commcare_connect.labs.context import (
    add_context_to_url,
    extract_context_from_url,
    get_context_url_params,
    get_org_data,
    try_auto_select_context,
    validate_context_access,
)
from commcare_connect.users.models import User


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
        request.session = {"labs_oauth": {"organization_data": {"opportunities": [{"id": 123, "name": "Test Opportunity"}]}}}

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
        request.session = {"labs_oauth": {"organization_data": {"opportunities": [{"id": 123, "name": "Test Opportunity"}]}}}

        context = {"opportunity_id": 999}
        validated = validate_context_access(request, context)

        # Unknown opportunity IDs are passed through for API-level validation
        # (handles managed opps not in cached OAuth data)
        assert validated["opportunity_id"] == 999
        assert "opportunity" not in validated


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
