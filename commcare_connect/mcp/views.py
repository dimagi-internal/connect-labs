from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .auth import authenticate_request
from .transport import handle_request


@csrf_exempt
@require_http_methods(["POST"])
def mcp_endpoint(request):
    """MCP Streamable HTTP endpoint. PAT-authenticated."""
    user, error_response = authenticate_request(request)
    if error_response is not None:
        return error_response
    return handle_request(request, user)
