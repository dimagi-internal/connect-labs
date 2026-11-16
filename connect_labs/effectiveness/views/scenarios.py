"""Saved scenarios over HTTP: what the scenario panel on Market size and the workspace calls.

``GET`` lists the viewer's own and the shared ones, ``POST`` saves one (``{name, params, note, shared, overwrite}``),
and a scenario's own address reads and deletes it. All the rules are ``effectiveness.saved``; this only carries them.
"""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.views import View

from connect_labs.effectiveness import saved
from connect_labs.effectiveness.views.scenario_page import ScenarioPageGate


def _fail(exc: saved.SavedError) -> JsonResponse:
    return JsonResponse({"error": exc.message, **exc.extra}, status=exc.status)


def _body(request) -> dict:
    try:
        got = json.loads(request.body or b"{}")
    except ValueError:
        raise saved.SavedError("That request was not JSON.") from None
    if not isinstance(got, dict):
        raise saved.SavedError("That request was not a JSON object.")
    return got


class ScenarioListView(ScenarioPageGate, View):
    def get(self, request):
        return JsonResponse(saved.listing(request.user))

    def post(self, request):
        try:
            body = _body(request)
            record, created = saved.save(
                request.user,
                body.get("name"),
                body.get("params"),
                note=body.get("note", ""),
                shared=body.get("shared", False),
                overwrite=body.get("overwrite", False),
            )
        except saved.SavedError as exc:
            return _fail(exc)
        return JsonResponse(
            {**saved.as_dict(record, request.user), "created": created}, status=201 if created else 200
        )


class ScenarioDetailView(ScenarioPageGate, View):
    def get(self, request, pk):
        try:
            return JsonResponse(saved.as_dict(saved.get(request.user, pk), request.user))
        except saved.SavedError as exc:
            return _fail(exc)

    def delete(self, request, pk):
        try:
            saved.delete(request.user, pk)
        except saved.SavedError as exc:
            return _fail(exc)
        return JsonResponse({"deleted": pk})
