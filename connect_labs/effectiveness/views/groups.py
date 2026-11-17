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
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.effectiveness import saved, scenario_groups
from connect_labs.effectiveness.views import scenario_page
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


def first_sentence(text: str, limit: int = 160) -> str:
    """The first sentence of a note, for a card: the rest is one click away."""
    text = " ".join((text or "").split())
    cut = text.find(". ")
    head = text if cut < 0 else text[: cut + 1]
    return head if len(head) <= limit else head[: limit - 1].rsplit(" ", 1)[0] + "…"


def sections(summary: str) -> list[dict]:
    """A group's summary as sections a person can open one at a time: ``## Heading`` starts one, ``- `` lines are a
    list,
    ``1. `` lines a numbered one, anything else a paragraph. A summary with no headings is one section."""
    out: list[dict] = []
    for raw in (summary or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            out.append({"title": line[3:].strip(), "blocks": []})
            continue
        if not line.strip():
            continue
        if not out:
            out.append({"title": "What we know", "blocks": []})
        blocks = out[-1]["blocks"]
        stripped = line.strip()
        kind = (
            "ul"
            if stripped.startswith("- ")
            else "ol" if stripped[:2].rstrip(".").isdigit() and ". " in stripped[:4] else "p"
        )
        text = stripped[2:].strip() if kind == "ul" else stripped.split(". ", 1)[1] if kind == "ol" else stripped
        if kind != "p" and blocks and blocks[-1]["kind"] == kind:
            blocks[-1]["items"].append(text)
        else:
            blocks.append({"kind": kind, "items": [text]})
    return out


#: How much of a bar's track the longest bar (or range) fills; the rest holds its value label.
BAR_SPACE = 0.78

#: The comparison's charts: one result each, which way is better, and whether it has a range to draw.
CHARTS = (
    (
        "deaths",
        "Deaths averted a year",
        "More is better. The line is the 90% range as mortality varies within its published intervals.",
        True,
    ),
    ("cost_per_death", "Cost per death averted", "Less is better.", False),
    ("multiple", "Multiple of GiveWell's benchmark", "More is better; the bar is 6x.", False),
)


def shortened(names: list[str], ids: list[int]) -> dict[int, str]:
    """The scenarios' names without what they all start with ("KC 1M · "), so a chart label says what differs."""
    prefix = ""
    if len(names) > 1:
        first, last = min(names), max(names)
        i = 0
        while i < min(len(first), len(last)) and first[i] == last[i]:
            i += 1
        prefix = first[:i]
        prefix = prefix[: prefix.rfind(" · ") + 3] if " · " in prefix else ""
    return {i: (n[len(prefix) :] if prefix else n) for i, n in zip(ids, names, strict=True)}


def charts(data: dict) -> list[dict]:
    """The scenarios as horizontal bars on one scale per result, best marked, with the range where there is one."""
    by_key = {r["key"]: r for r in data["attributes"]}
    short = shortened([s["name"] for s in data["scenarios"]], [s["id"] for s in data["scenarios"]])
    out = []
    for key, title, hint, ranged in CHARTS:
        unit = by_key[key]["unit"]
        vals = [(s, (s.get("headline") or {}).get(key)) for s in data["scenarios"]]
        shown = [v for _, v in vals if v is not None]
        if not shown:
            continue
        top = (
            max([*shown, *[(s["headline"].get("deaths_p95") or 0) for s, _ in vals if ranged and s.get("headline")]])
            or 1
        ) / BAR_SPACE  # the longest bar stops short of the edge, leaving room for its value
        rows = []
        for s, v in vals:
            h = s.get("headline") or {}
            lo, hi = (h.get("deaths_p05"), h.get("deaths_p95")) if ranged else (None, None)
            rows.append(
                {
                    "id": s["id"],
                    "name": s["name"],
                    "short": short[s["id"]],
                    "text": show(unit, v),
                    "width": 0 if v is None else v / top * 100,
                    "lo": None if lo is None else lo / top * 100,
                    "hi": None if hi is None else hi / top * 100,
                    "span": None if lo is None or hi is None else (hi - lo) / top * 100,
                    "range": None if lo is None or hi is None else f"{show(unit, lo)} to {show(unit, hi)}",
                    "label_at": max(0 if v is None else v, hi or 0) / top * 100 + 1.5,
                    "best": key in s["best"],
                    "reference": s["id"] == data["reference"],
                }
            )
        out.append({"key": key, "title": title, "hint": hint, "rows": rows})
    return out


def landing(group: dict) -> dict:
    """A group as its landing card: what it is, and each scenario with what it gave (from what is stored)."""
    rows = []
    for s in group["scenarios"]:
        h = s.get("headline") or {}
        rows.append(
            {
                "id": s["id"],
                "number": len(rows) + 1,
                "name": s["name"],
                "why": first_sentence(s["note"]),
                "open_path": s["open_path"],
                "deaths": show("count", h.get("deaths")) if h else None,
                "multiple": show("multiple", h.get("multiple")) if h else None,
            }
        )
    best = max((s["headline"]["deaths"] for s in group["scenarios"] if s.get("headline")), default=None)
    worst = min((s["headline"]["deaths"] for s in group["scenarios"] if s.get("headline")), default=None)
    return {
        **group,
        "rows": rows,
        "span": None if best is None else f"{show('count', worst)} to {show('count', best)} deaths averted a year",
    }


def cards(data: dict) -> list[dict]:
    """One card a scenario: its name, the one thing it changes, and its headline."""
    out = []
    for s in data["scenarios"]:
        h = s.get("headline") or {}
        out.append(
            {
                "id": s["id"],
                "name": s["name"],
                "open_path": s["open_path"],
                "why": first_sentence(s.get("note")),
                "note": s.get("note", ""),
                "changes": s["differs"],
                "changes_from": (s.get("differs_from") or {}).get("name"),
                "reference": s["id"] == data["reference"],
                "deaths": show("count", h.get("deaths")),
                "range": (
                    None
                    if h.get("deaths_p05") is None
                    else f"{show('count', h['deaths_p05'])} to {show('count', h['deaths_p95'])}"
                ),
                "multiple": show("multiple", h.get("multiple")),
                "cost": show("usd", h.get("cost")),
                "per_death": show("usd", h.get("cost_per_death")),
                "best": [r["label"] for r in data["attributes"] if r["key"] in s["best"] and r.get("headline")],
                "delta": s["delta"].get("deaths"),
                "moved": bool(s.get("drift")),
                "error": s.get("error"),
                "models_changed": s.get("models_changed"),
                "source": s.get("source"),
            }
        )
    return out


@CONTEXT_NOT_USED
class GroupView(ScenarioPageGate, View):
    def get(self, request, pk):
        try:
            group = scenario_groups.get(request.user, pk)
            ref = request.GET.get("ref")
            live = request.GET.get("live") == "1" or request.GET.get("format") == "json"
            data = scenario_groups.compare_group(request.user, group, reference=int(ref) if ref else None, live=live)
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
        sources = {s.get("source") for s in data["scenarios"]}
        return render(
            request,
            "effectiveness/group.html",
            {
                "group": data["group"],
                "data": data,
                "table": table(data),
                "scenarios": data["scenarios"],
                "cards": cards(data),
                "charts": charts(data),
                "sections": sections(data["group"]["summary"]),
                "live": live,
                "urls": {
                    "group": reverse("cea:group", args=[pk]),
                    "baseline": reverse("cea:group_baseline", args=[pk]),
                    "scenarios": reverse("cea:scenarios"),
                    "groups": reverse("cea:groups"),
                    "order": [s["id"] for s in data["scenarios"]],
                },
                "baselined": "baseline" in sources or None in sources,
                "canopy_panel": scenario_page.agent_panel(
                    request,
                    f"labs-cea://group/{pk}",
                    "cea_scenario_groups",
                    {"id": str(pk), **({"ref": ref} if ref else {})},
                ),
            },
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
        shown = scenario_groups.listing(request.user, headlines=True)
        return render(request, "effectiveness/groups.html", {"groups": [landing(g) for g in shown]})

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
