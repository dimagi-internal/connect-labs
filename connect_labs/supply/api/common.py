"""Shared helpers for the supply JSON API."""
import json
from functools import wraps

from django.http import JsonResponse

from ..services.org_actions import ActionError


def json_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body)
    except ValueError:
        return {}


def handle_action_errors(view):
    """Turn service-layer rule violations into 400s with a readable message."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except ActionError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    return wrapped


def method_required(*methods):
    def deco(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                return JsonResponse({"error": "method not allowed"}, status=405)
            return view(request, *args, **kwargs)

        return wrapped

    return deco
