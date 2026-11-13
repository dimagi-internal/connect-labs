"""The cost-effectiveness explorer's model view: run a Connect model, change its inputs, test sensitivity.

``ModelExplorerView`` draws the page; ``ModelRunView`` is the JSON the page calls on every change. Both are
thin: the arithmetic is ``engine`` and ``explore``, the same code the delivered ranking uses, so a number
here is a number there. Inputs travel as ``i_<key>`` query parameters, so any state of the explorer is a
link someone can send.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.effectiveness import engine, explore, groups, valuation
from connect_labs.labs.chrome import NOT_USED, page_chrome

CONTEXT_NOT_USED = method_decorator(page_chrome(labs_context=NOT_USED), name="dispatch")
DEFAULT_MODEL = "chc"


def _request_state(request) -> tuple[str, dict]:
    model = request.GET.get("model", DEFAULT_MODEL)
    raw = {k[2:]: v for k, v in request.GET.items() if k.startswith("i_")}
    return model, raw


def _included(request) -> list[str] | None:
    """``?s=a,b`` switches sensitivity on for those inputs only; no ``s`` means the model's default set."""
    if "s" not in request.GET:
        return None
    return [k for k in request.GET["s"].split(",") if k]


def _floats(raw: str) -> list[float]:
    try:
        return [float(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise explore.ExploreError("A grid's values are comma-separated numbers.") from None


@CONTEXT_NOT_USED
class ModelExplorerView(LoginRequiredMixin, View):
    def get(self, request):
        model, raw = _request_state(request)
        error = None
        if model not in engine.MODELS:
            model, error = DEFAULT_MODEL, f"Unknown model {model!r}; showing {DEFAULT_MODEL}."
        try:
            overrides = explore.clean_overrides(model, raw)
            run = explore.run(model, overrides)
        except explore.ExploreError as exc:
            overrides, error = {}, exc.message
            run = explore.run(model)
        meta = explore.models()
        data = {
            "model": model,
            "models": {k: {**meta[k], "key": k} for k in engine.MODELS},
            "error": error,
            "inputs": explore.inputs(model),
            "blocks": groups.blocks(),
            "overrides": overrides,
            "run": run,
            "sensitivity": explore.sensitivity(model, overrides, _included(request)),
            "scenarios": explore.scenarios(model),
            "places": explore.places(model),
            "bar": valuation.bar(),
            "spec": {"version": engine.spec(model).get("version"), **engine.source()},
            "run_url": request.path + "api/run/",
        }
        return render(request, "effectiveness/models.html", {"data": data})


@CONTEXT_NOT_USED
class ModelRunView(LoginRequiredMixin, View):
    """``?model=chc&i_ors=0.4&s=ors,cost`` -> the run and its sensitivity over the inputs named in ``s``.

    ``&grid_x=cost&grid_xs=1.4,1.8&grid_y=ors&grid_ys=0.3,0.5`` adds a two-input grid.
    """

    def get(self, request):
        model, raw = _request_state(request)
        try:
            overrides = explore.clean_overrides(model, raw)
            out = {
                "run": explore.run(model, overrides),
                "sensitivity": explore.sensitivity(model, overrides, _included(request)),
            }
            if request.GET.get("grid_x"):
                out["grid"] = explore.grid(
                    model,
                    request.GET["grid_x"],
                    _floats(request.GET.get("grid_xs", "")),
                    request.GET.get("grid_y", ""),
                    _floats(request.GET.get("grid_ys", "")),
                    overrides,
                )
        except explore.ExploreError as exc:
            return JsonResponse({"error": exc.message}, status=400)
        return JsonResponse(out)
