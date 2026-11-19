"""The tracker's payload: the hand-edited content with the live figures laid over it.

``build`` is the only place the three sources meet, and it is what both the public page and the editor's preview
draw, so a preview is the page.

What is live:

* a public Connect model's base case and scenario range (``figures``), and how many clear the bar;
* a program's verified visits, when the content names the Connect delivery types that make it up and the timed job
  has counted them (``delivery``, from ``store.refresh_delivery``); and the total of all verified visits.

Everything else, countries and start dates included, is the hand-edited value: Connect's record of where and when
older programs ran is incomplete, so those would be wrong if read live.

Pure: no Django, no database.
"""

from __future__ import annotations

import copy


def _ce_link(program: dict, content: dict, explorer_url: str) -> str:
    if content.get("model_link") == "explorer" and explorer_url:
        return f"{explorer_url}?model={program['model_key']}"
    return program.get("sheet") or ""


def build(content: dict, model_figures: dict, delivery: dict | None = None, *, explorer_url: str = "") -> dict:
    """``content`` (already cleaned) as the page draws it.

    ``model_figures`` is ``figures.all_models()``; ``delivery`` is ``{"services": {slug: approved visits}, "total":
    n, "as_of": "YYYY-MM-DD"}`` or None when nothing has been counted yet, in which case every count is the
    hand-entered one and the page gives the content's own snapshot date.
    """
    services = (delivery or {}).get("services") or {}
    out = copy.deepcopy(content)
    above_bar = 0
    bar = None
    for p in out["programs"]:
        p["visits_live"] = False
        key = p.get("model_key")
        if key and key in model_figures:
            f = model_figures[key]
            p["ce"] = {"base": f["base"], "lo": f["lo"], "hi": f["hi"]}
            p["link"] = _ce_link(p, content, explorer_url)
            p["link_kind"] = "explorer" if content.get("model_link") == "explorer" and explorer_url else "sheet"
            bar = f["bar"]
            above_bar += 1 if f["clears_bar"] else 0
        wanted = (p.pop("connect", None) or {}).get("services") or []
        counted = [services[s] for s in wanted if s in services]
        if counted:
            p["visits"] = int(sum(counted))
            p["visits_live"] = True
        p.pop("sheet", None)
    if bar is None and model_figures:
        bar = next(iter(model_figures.values()))["bar"]
    live_total = (delivery or {}).get("total")
    out["stats"] = {
        "visits": int(live_total) if live_total else content["stats"]["visits"],
        "countries": content["stats"]["countries"],
        "organizations": content["stats"]["organizations"],
        "above_bar": above_bar,
    }
    out["bar"] = bar if bar is not None else 6.0
    out["as_of"] = (delivery or {}).get("as_of") or content["snapshot_date"]
    out["delivery_live"] = bool(delivery and delivery.get("as_of"))
    out.pop("model_link", None)
    return out
