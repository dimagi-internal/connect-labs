from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


@csrf_exempt
@require_http_methods(["POST"])
def mcp_endpoint(request):
    """MCP Streamable HTTP endpoint. Replaced by transport handler in Task B3."""
    return JsonResponse({"error": "not implemented"}, status=501)
