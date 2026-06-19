from django.http import HttpRequest, JsonResponse


def ping(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True})
