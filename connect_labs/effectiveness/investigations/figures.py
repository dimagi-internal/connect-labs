"""A Connect model's headline for the tracker: its base case and the range across its published scenarios.

Read from ``explore``, the model explorer's own code, so the tracker shows the number the explorer shows. The range
is the lowest and highest multiple among the base case and the scenarios the model's builders wrote into its spec
(the "Scenarios" the explorer lists); it is not a sensitivity range.

Pure: no Django, no database.
"""

from __future__ import annotations

from functools import lru_cache

from connect_labs.effectiveness import engine, explore


@lru_cache(maxsize=8)
def model(key: str) -> dict:
    """``{"base", "lo", "hi", "bar", "clears_bar", "version"}`` for one model, multiples to one decimal place."""
    headline = explore.run(key)["headline"]
    base = headline["multiple"]
    multiples = [base] + [s["multiple"] for s in explore.scenarios(key)]
    return {
        "base": round(base, 1),
        "lo": round(min(multiples), 1),
        "hi": round(max(multiples), 1),
        "bar": headline["bar"],
        # Judged on the model's own figure, not the rounded one shown.
        "clears_bar": bool(headline["clears_bar"]),
        "version": str(engine.spec(key).get("version") or ""),
    }


def all_models() -> dict[str, dict]:
    return {key: model(key) for key in engine.MODELS}
