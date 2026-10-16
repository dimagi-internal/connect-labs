import pytest
from django.urls import resolve, reverse

# Use a minimal URL config that only includes solicitations,
# avoiding imports of unrelated apps with optional dependencies.
pytestmark = pytest.mark.urls("connect_labs.solicitations.tests.test_urls_conf")


class TestURLResolution:
    """Verify all solicitations URL patterns resolve correctly."""

    # -- Public views --

    def test_public_list_reverse(self):
        url = reverse("solicitations:public_list")
        assert url == "/solicitations/"

    def test_public_list_resolve(self):
        match = resolve("/solicitations/")
        assert match.url_name == "public_list"
        assert match.namespace == "solicitations"

    def test_public_detail_reverse(self):
        url = reverse("solicitations:public_detail", kwargs={"pk": 1})
        assert url == "/solicitations/1/"

    def test_public_detail_resolve(self):
        match = resolve("/solicitations/42/")
        assert match.url_name == "public_detail"
        assert match.kwargs == {"pk": 42}

    # -- Manager views --

    def test_manage_list_reverse(self):
        url = reverse("solicitations:manage_list")
        assert url == "/solicitations/manage/"

    def test_manage_list_resolve(self):
        match = resolve("/solicitations/manage/")
        assert match.url_name == "manage_list"

    def test_create_reverse(self):
        url = reverse("solicitations:create")
        assert url == "/solicitations/create/"

    def test_create_resolve(self):
        match = resolve("/solicitations/create/")
        assert match.url_name == "create"

    def test_edit_reverse(self):
        url = reverse("solicitations:edit", kwargs={"pk": 1})
        assert url == "/solicitations/1/edit/"

    def test_edit_resolve(self):
        match = resolve("/solicitations/5/edit/")
        assert match.url_name == "edit"
        assert match.kwargs == {"pk": 5}

    def test_responses_list_reverse(self):
        url = reverse("solicitations:responses_list", kwargs={"pk": 1})
        assert url == "/solicitations/1/responses/"

    def test_responses_list_resolve(self):
        match = resolve("/solicitations/7/responses/")
        assert match.url_name == "responses_list"
        assert match.kwargs == {"pk": 7}

    # -- Response views --

    def test_respond_reverse(self):
        url = reverse("solicitations:respond", kwargs={"pk": 1})
        assert url == "/solicitations/1/respond/"

    def test_respond_resolve(self):
        match = resolve("/solicitations/3/respond/")
        assert match.url_name == "respond"
        assert match.kwargs == {"pk": 3}

    def test_response_detail_reverse(self):
        url = reverse("solicitations:response_detail", kwargs={"pk": 1})
        assert url == "/solicitations/response/1/"

    def test_response_detail_resolve(self):
        match = resolve("/solicitations/response/10/")
        assert match.url_name == "response_detail"
        assert match.kwargs == {"pk": 10}

    # -- Review views --

    def test_review_reverse(self):
        url = reverse("solicitations:review", kwargs={"pk": 1})
        assert url == "/solicitations/response/1/review/"

    def test_review_resolve(self):
        match = resolve("/solicitations/response/8/review/")
        assert match.url_name == "review"
        assert match.kwargs == {"pk": 8}

    # -- Namespace verification --

    def test_all_urls_in_correct_namespace(self):
        """Every named URL in the app should live under the solicitations namespace."""
        url_names = [
            "public_list",
            "public_detail",
            "manage_list",
            "create",
            "edit",
            "responses_list",
            "respond",
            "response_detail",
            "review",
        ]
        for name in url_names:
            full_name = f"solicitations:{name}"
            # Should not raise NoReverseMatch
            kwargs = {}
            if name in (
                "public_detail",
                "edit",
                "responses_list",
                "respond",
                "response_detail",
                "review",
            ):
                kwargs = {"pk": 1}
            url = reverse(full_name, kwargs=kwargs)
            assert url.startswith("/solicitations/"), f"{full_name} resolved to {url}"

    def test_url_pattern_count(self):
        """Ensure we have exactly the expected number of URL patterns (the JSON API is gone: MCP is the API)."""
        from connect_labs.solicitations.urls import urlpatterns

        assert len(urlpatterns) == 14

    def test_org_pages_reverse(self):
        assert reverse("solicitations:org_page", kwargs={"org_slug": "acme"}) == "/solicitations/o/acme/"
        assert reverse("solicitations:org_manage", kwargs={"org_slug": "acme"}) == "/solicitations/o/acme/manage/"
        assert reverse("solicitations:past_detail", kwargs={"slug": "chc-2025"}) == "/solicitations/past/chc-2025/"
