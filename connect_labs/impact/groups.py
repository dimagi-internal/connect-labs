"""The four inputs that decide a cost-effectiveness answer, and everything else.

Every model is built on the same four questions, so the explorer groups each model's inputs by them:

* **Baseline**: how big the problem is where the programme works (burden, mortality).
* **Counterfactual**: what would happen without the programme (coverage already reached, what others would fund).
* **Effectiveness**: how much the programme changes the outcome (effect sizes and the trial adjustments).
* **Cost**: what it costs, and what is delivered for it.

Anything not listed is "other assumptions": values published or fixed by GiveWell, and the model's judgement calls.
The lists are Labs' reading of each model, kept apart from the vendored specs so a refresh of the specs cannot lose
them; a key a refresh adds lands in "other assumptions" until someone places it. ``test_groups`` fails on a key the
spec no longer has.

Pure: no Django, no database.
"""

from __future__ import annotations

KEY_GROUPS = ("baseline", "counterfactual", "effectiveness", "cost")
OTHER = "other"
ORDER = (*KEY_GROUPS, OTHER)

TITLES = {
    "baseline": "Baseline",
    "counterfactual": "Counterfactual",
    "effectiveness": "Effectiveness",
    "cost": "Cost",
    OTHER: "Other assumptions",
}
HINTS = {
    "baseline": "How big the problem is",
    "counterfactual": "What happens without the programme",
    "effectiveness": "How much the programme changes it",
    "cost": "What it costs, and what is delivered",
    OTHER: "Published and fixed values, and judgement calls",
}

GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "chc": {
        "baseline": ("deaths", "scaling", "mort659", "worm", "rota_on", "rota_gain", "rota_share"),
        "counterfactual": ("ors", "vas", "dw_cov", "funging", "gov_share"),
        "effectiveness": (
            "arm",
            "arm_d2d",
            "arm_free",
            "ors_red",
            "episodes",
            "second",
            "va_effect",
            "va_diarr",
            "dw_itt",
            "dw_repl",
            "iv",
            "ev",
        ),
        "cost": ("cost", "campaigns", "va_on", "va_cost", "dw_on", "dw_cost"),
    },
    "kc": {
        "baseline": ("post_ref",),
        "counterfactual": (
            "funging",
            "gov_share",
            "lv_gov_value",
            "lv_other_value",
            "lv_p_gov",
            "lv_p_other",
            "lv_p_none",
            "lv_gov_part",
        ),
        "effectiveness": ("effect", "iv", "ev"),
        "cost": ("cost",),
    },
    "mbw": {
        "baseline": ("nmr", "imr"),
        "counterfactual": (
            "ebf",
            "ref_ebf",
            "funging",
            "gov_share",
            "lv_gov_value",
            "lv_other_value",
            "lv_p_gov",
            "lv_p_other",
            "lv_p_none",
            "lv_gov_part",
        ),
        "effectiveness": ("diarr_red", "neo_scale", "later", "gap_on", "iv", "ev"),
        "cost": ("cost",),
    },
}


def group_of(model: str, key: str) -> str:
    """The group an input belongs to; ``other`` when the model's list does not place it."""
    for group, keys in GROUPS.get(model, {}).items():
        if key in keys:
            return group
    return OTHER


def blocks() -> list[dict]:
    return [{"key": g, "title": TITLES[g], "hint": HINTS[g]} for g in ORDER]
