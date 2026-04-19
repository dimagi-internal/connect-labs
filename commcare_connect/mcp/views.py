from django.contrib.auth.models import AnonymousUser
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .transport import handle_request


@csrf_exempt
@require_http_methods(["POST"])
def mcp_endpoint(request):
    """MCP Streamable HTTP endpoint.

    Auth layer (Task C2) populates request.mcp_user. Until then, allow
    anonymous for smoke testing.
    """
    user = getattr(request, "mcp_user", None) or AnonymousUser()
    return handle_request(request, user)
