"""
Reusable view mixins for labs projects.

Provides patterns for async loading of heavy data operations.
"""

import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


class AsyncLoadingViewMixin:
    """
    Mixin for page views that load data asynchronously.

    Pattern:
    1. Initial page load returns quickly with a loading indicator
    2. Frontend makes AJAX call to data API endpoint
    3. Data API does the heavy lifting and returns JSON

    Usage:
        class MyMapView(AsyncLoadingViewMixin, LoginRequiredMixin, TemplateView):
            template_name = "my_app/map.html"
            data_api_url_name = "my_app:map_data"

            def get_initial_context(self):
                # Fast context for initial page load
                return {"title": "My Map"}

    Template should include JavaScript to fetch from data_api_url.
    """

    data_api_url_name: str = None  # Override in subclass

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Add data API URL for frontend AJAX call
        if self.data_api_url_name:
            from django.urls import reverse

            context["data_api_url"] = reverse(self.data_api_url_name)

        # Add initial context from subclass
        context.update(self.get_initial_context())

        return context

    def get_initial_context(self) -> dict:
        """
        Override to provide fast initial context.

        This should only include data that can be computed quickly,
        such as OAuth status checks or basic user info.

        Returns:
            Dict of context data for initial page render
        """
        return {}


class AsyncDataViewMixin:
    """
    Mixin for API views that process long-running data operations.

    Provides:
    - Consistent JSON error handling
    - Progress logging helpers
    - Common auth checks

    Usage:
        class MyDataView(AsyncDataViewMixin, LoginRequiredMixin, View):

            def get(self, request):
                # Auth check
                error = self.check_labs_auth(request)
                if error:
                    return self.json_error(error, status=401)

                try:
                    self.log_step(1, 3, "Fetching data...")
                    data = fetch_data()

                    self.log_step(2, 3, "Processing...")
                    result = process(data)

                    self.log_step(3, 3, "Building response...")
                    return self.json_success({
                        "data": result,
                        "count": len(result),
                    })

                except Exception as e:
                    logger.error(f"Failed: {e}", exc_info=True)
                    return self.json_error(str(e), status=500)
    """

    def check_labs_auth(self, request) -> str | None:
        """
        Check Labs OAuth authentication.

        Args:
            request: HttpRequest object

        Returns:
            Error message string if auth failed, None if OK
        """
        from django.utils import timezone

        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return "Labs OAuth not configured. Please log in again."

        expires_at = labs_oauth.get("expires_at", 0)
        if timezone.now().timestamp() >= expires_at:
            return "OAuth token expired. Please log in again."

        return None

    def check_commcare_auth(self, request) -> str | None:
        """
        Check CommCare HQ OAuth authentication (for apps that need it).

        Args:
            request: HttpRequest object

        Returns:
            Error message string if auth failed, None if OK
        """
        from django.utils import timezone

        commcare_oauth = request.session.get("commcare_oauth", {})
        if not commcare_oauth.get("access_token"):
            return "CommCare OAuth not configured. Please authorize CommCare access."

        expires_at = commcare_oauth.get("expires_at", 0)
        if timezone.now().timestamp() >= expires_at:
            return "CommCare OAuth token expired. Please re-authorize."

        return None

    def json_error(self, message: str, status: int = 400) -> JsonResponse:
        """
        Return JSON error response.

        Args:
            message: Error message to include
            status: HTTP status code (default 400)

        Returns:
            JsonResponse with error message
        """
        return JsonResponse({"error": message, "success": False}, status=status)

    def json_success(self, data: dict) -> JsonResponse:
        """
        Return JSON success response.

        Args:
            data: Dict of response data

        Returns:
            JsonResponse with success=True and data
        """
        return JsonResponse({"success": True, **data})

    def log_step(self, step: int, total: int, message: str) -> None:
        """
        Log a processing step for progress tracking.

        Args:
            step: Current step number (1-indexed)
            total: Total number of steps
            message: Description of what's happening
        """
        logger.info(f"[{self.__class__.__name__}] Step {step}/{total}: {message}")
