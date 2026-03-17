import pytest
from django.urls import resolve, reverse

# Use a minimal URL config that only includes solicitations_new,
# avoiding imports of unrelated apps with optional dependencies.
pytestmark = pytest.mark.urls("commcare_connect.solicitations_new.tests.test_urls_conf")


class TestURLResolution:
    """Verify all solicitations_new URL patterns resolve correctly."""

    # -- Public views --

    def test_public_list_reverse(self):
        url = reverse("solicitations_new:public_list")
        assert url == "/solicitations_new/"

    def test_public_list_resolve(self):
        match = resolve("/solicitations_new/")
        assert match.url_name == "public_list"
        assert match.namespace == "solicitations_new"

    def test_public_detail_reverse(self):
        url = reverse("solicitations_new:public_detail", kwargs={"pk": 1})
        assert url == "/solicitations_new/1/"

    def test_public_detail_resolve(self):
        match = resolve("/solicitations_new/42/")
        assert match.url_name == "public_detail"
        assert match.kwargs == {"pk": 42}

    # -- Manager views --

    def test_manage_list_reverse(self):
        url = reverse("solicitations_new:manage_list")
        assert url == "/solicitations_new/manage/"

    def test_manage_list_resolve(self):
        match = resolve("/solicitations_new/manage/")
        assert match.url_name == "manage_list"

    def test_create_reverse(self):
        url = reverse("solicitations_new:create")
        assert url == "/solicitations_new/create/"

    def test_create_resolve(self):
        match = resolve("/solicitations_new/create/")
        assert match.url_name == "create"

    def test_edit_reverse(self):
        url = reverse("solicitations_new:edit", kwargs={"pk": 1})
        assert url == "/solicitations_new/1/edit/"

    def test_edit_resolve(self):
        match = resolve("/solicitations_new/5/edit/")
        assert match.url_name == "edit"
        assert match.kwargs == {"pk": 5}

    def test_responses_list_reverse(self):
        url = reverse("solicitations_new:responses_list", kwargs={"pk": 1})
        assert url == "/solicitations_new/1/responses/"

    def test_responses_list_resolve(self):
        match = resolve("/solicitations_new/7/responses/")
        assert match.url_name == "responses_list"
        assert match.kwargs == {"pk": 7}

    # -- Response views --

    def test_respond_reverse(self):
        url = reverse("solicitations_new:respond", kwargs={"pk": 1})
        assert url == "/solicitations_new/1/respond/"

    def test_respond_resolve(self):
        match = resolve("/solicitations_new/3/respond/")
        assert match.url_name == "respond"
        assert match.kwargs == {"pk": 3}

    def test_response_detail_reverse(self):
        url = reverse("solicitations_new:response_detail", kwargs={"pk": 1})
        assert url == "/solicitations_new/response/1/"

    def test_response_detail_resolve(self):
        match = resolve("/solicitations_new/response/10/")
        assert match.url_name == "response_detail"
        assert match.kwargs == {"pk": 10}

    # -- Review views --

    def test_review_reverse(self):
        url = reverse("solicitations_new:review", kwargs={"pk": 1})
        assert url == "/solicitations_new/response/1/review/"

    def test_review_resolve(self):
        match = resolve("/solicitations_new/response/8/review/")
        assert match.url_name == "review"
        assert match.kwargs == {"pk": 8}

    # -- JSON API endpoints --

    def test_api_solicitations_list_reverse(self):
        url = reverse("solicitations_new:api_solicitations_list")
        assert url == "/solicitations_new/api/solicitations/"

    def test_api_solicitations_list_resolve(self):
        match = resolve("/solicitations_new/api/solicitations/")
        assert match.url_name == "api_solicitations_list"

    def test_api_solicitation_detail_reverse(self):
        url = reverse("solicitations_new:api_solicitation_detail", kwargs={"pk": 1})
        assert url == "/solicitations_new/api/solicitations/1/"

    def test_api_solicitation_detail_resolve(self):
        match = resolve("/solicitations_new/api/solicitations/99/")
        assert match.url_name == "api_solicitation_detail"
        assert match.kwargs == {"pk": 99}

    def test_api_responses_list_reverse(self):
        url = reverse("solicitations_new:api_responses_list")
        assert url == "/solicitations_new/api/responses/"

    def test_api_responses_list_resolve(self):
        match = resolve("/solicitations_new/api/responses/")
        assert match.url_name == "api_responses_list"

    def test_api_response_detail_reverse(self):
        url = reverse("solicitations_new:api_response_detail", kwargs={"pk": 1})
        assert url == "/solicitations_new/api/responses/1/"

    def test_api_response_detail_resolve(self):
        match = resolve("/solicitations_new/api/responses/50/")
        assert match.url_name == "api_response_detail"
        assert match.kwargs == {"pk": 50}

    def test_api_reviews_create_reverse(self):
        url = reverse("solicitations_new:api_reviews_create")
        assert url == "/solicitations_new/api/reviews/"

    def test_api_reviews_create_resolve(self):
        match = resolve("/solicitations_new/api/reviews/")
        assert match.url_name == "api_reviews_create"

    def test_api_review_detail_reverse(self):
        url = reverse("solicitations_new:api_review_detail", kwargs={"pk": 1})
        assert url == "/solicitations_new/api/reviews/1/"

    def test_api_review_detail_resolve(self):
        match = resolve("/solicitations_new/api/reviews/20/")
        assert match.url_name == "api_review_detail"
        assert match.kwargs == {"pk": 20}

    # -- Namespace verification --

    def test_all_urls_in_correct_namespace(self):
        """Every named URL in the app should live under the solicitations_new namespace."""
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
            "api_solicitations_list",
            "api_solicitation_detail",
            "api_responses_list",
            "api_response_detail",
            "api_reviews_create",
            "api_review_detail",
        ]
        for name in url_names:
            full_name = "solicitations_new:{}".format(name)
            # Should not raise NoReverseMatch
            kwargs = {}
            if name in (
                "public_detail",
                "edit",
                "responses_list",
                "respond",
                "response_detail",
                "review",
                "api_solicitation_detail",
                "api_response_detail",
                "api_review_detail",
            ):
                kwargs = {"pk": 1}
            url = reverse(full_name, kwargs=kwargs)
            assert url.startswith("/solicitations_new/"), f"{full_name} resolved to {url}"

    def test_url_pattern_count(self):
        """Ensure we have exactly the expected number of URL patterns (16 total)."""
        from commcare_connect.solicitations_new.urls import urlpatterns

        assert len(urlpatterns) == 16
