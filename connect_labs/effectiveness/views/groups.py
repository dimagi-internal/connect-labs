"""Scenario groups over HTTP: a page that sets a group's scenarios side by side, and the JSON behind it.

``GET groups/`` lists the groups, ``GET groups/<id>/`` is the comparison page (``?format=json`` is the same answer
for an agent, ``?ref=<scenario id>`` changes what the others are compared against), and a group is made, edited and
deleted with ``POST`` / ``PATCH`` / ``DELETE`` (a re-baseline is a ``POST`` to ``groups/<id>/baseline/``). All the
rules are
``effectiveness.scenario_groups``; this only carries them and lays the numbers out.
"""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.effectiveness import saved, scenario_groups
from connect_labs.effectiveness.views.scenario_page import ScenarioPageGate
from connect_labs.labs.chrome import NOT_USED, page_chrome

CONTEXT_NOT_USED = method_decorator(page_chrome(labs_context=NOT_USED), name="dispatch")


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


# ------------------------------------------------------------------------------------------------- laying it out


def show(unit: str, v) -> str:
    """A result as a person reads it."""
    if v is None:
        return "–"
    if unit == "usd":
        return f"${v / 1e6:,.1f}M" if abs(v) >= 1e6 else f"${v:,.0f}"
    if unit == "multiple":
        return f"{v:.1f}x"
    if unit == "share":
        return f"{v * 100:.0f}%"
    return f"{v / 1e6:,.2f}M" if abs(v) >= 1e6 else f"{v:,.0f}"


def table(data: dict) -> dict:
    """The comparison as rows of cells: what each scenario sets, then what it gave, the reference first."""
    scenarios = data["scenarios"]
    setting_keys = list(dict.fromkeys(k for s in scenarios for k in s["settings"]))
    setting_rows = []
    for k in setting_keys:
        values = [s["settings"].get(k, "–") for s in scenarios]
        setting_rows.append({"label": k, "cells": [{"text": v} for v in values], "varies": len(set(values)) > 1})
    result_rows = []
    for r in data["attributes"]:
        cells = []
        for s in scenarios:
            v = (s.get("headline") or {}).get(r["key"])
            d = s["delta"].get(r["key"])
            drift = (s.get("drift") or {}).get(r["key"])
            cells.append(
                {
                    "text": show(r["unit"], v),
                    "best": r["key"] in s["best"],
                    "delta": None if d is None else f"{d:+.1f}%",
                    "was": show(r["unit"], drift[0]) if drift else None,
                }
            )
        if any(c["text"] != "–" for c in cells):
            result_rows.append({**r, "label": r["label"], "cells": cells})
    return {"settings": setting_rows, "results": result_rows}


@CONTEXT_NOT_USED
class GroupView(ScenarioPageGate, View):
    def get(self, request, pk):
        try:
            group = scenario_groups.get(request.user, pk)
            ref = request.GET.get("ref")
            data = scenario_groups.compare_group(request.user, group, reference=int(ref) if ref else None)
        except (saved.SavedError, ValueError) as exc:
            if request.GET.get("format") == "json":
                return (
                    _fail(exc) if isinstance(exc, saved.SavedError) else JsonResponse({"error": str(exc)}, status=400)
                )
            return render(
                request, "effectiveness/group.html", {"error": getattr(exc, "message", str(exc))}, status=404
            )
        if request.GET.get("format") == "json":
            return JsonResponse(_jsonable(data))
        return render(
            request,
            "effectiveness/group.html",
            {"group": data["group"], "data": data, "table": table(data), "scenarios": data["scenarios"]},
        )

    def patch(self, request, pk):
        try:
            body = _body(request)
            group = scenario_groups.update(
                request.user,
                pk,
                name=body.get("name"),
                question=body.get("question"),
                summary=body.get("summary"),
                shared=body.get("shared"),
                scenario_ids=body.get("scenario_ids"),
            )
        except saved.SavedError as exc:
            return _fail(exc)
        return JsonResponse(scenario_groups.as_dict(group, request.user))

    def delete(self, request, pk):
        try:
            scenario_groups.delete(request.user, pk)
        except saved.SavedError as exc:
            return _fail(exc)
        return JsonResponse({"deleted": pk})


class GroupBaselineView(ScenarioPageGate, View):
    """Make what each scenario gives now the number later runs are compared with (the group's owner)."""

    def post(self, request, pk):
        try:
            group = scenario_groups.get(request.user, pk)
            data = scenario_groups.compare_group(request.user, group, rebaseline=True)
        except saved.SavedError as exc:
            return _fail(exc)
        return JsonResponse({"baselined": [s["id"] for s in data["scenarios"] if s.get("headline")]})


@CONTEXT_NOT_USED
class GroupListView(ScenarioPageGate, View):
    def get(self, request):
        groups = scenario_groups.listing(request.user)
        if request.GET.get("format") == "json":
            return JsonResponse({"groups": groups})
        return render(request, "effectiveness/groups.html", {"groups": groups})

    def post(self, request):
        try:
            body = _body(request)
            group, created = scenario_groups.save(
                request.user,
                body.get("name"),
                question=body.get("question"),
                summary=body.get("summary"),
                shared=body.get("shared"),
                overwrite=body.get("overwrite", False),
                scenario_ids=body.get("scenario_ids"),
            )
        except saved.SavedError as exc:
            return _fail(exc)
        return JsonResponse(
            {**scenario_groups.as_dict(group, request.user), "created": created}, status=201 if created else 200
        )


def _jsonable(data: dict) -> dict:
    """The comparison without the live ``Scenario`` objects (the table of settings stands for them)."""
    return {
        **data,
        "scenarios": [{k: v for k, v in s.items() if k != "scenario"} for s in data["scenarios"]],
    }
