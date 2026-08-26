"""Input-availability and credibility gates -- when a number must NOT be shown.

Two separate reasons an indicator is withheld, and neither is a band:

  input availability  the scope never records the input, so the honest answer is
                      n/a rather than 0. A worker who logged no danger signs has
                      not achieved a 0% danger-sign rate.
  credibility         the workbook's Targets & settings say an LLO does not record
                      this credibly, so the figure exists but must not be published.

Both were invisible at programme level -- every input exists somewhere across 11
opportunities -- and only appeared on a drill: 268 of 5,302 per-FLW checks
disagreed with the dashboard until the input gate was ported, all on C20/C21.
"""

from __future__ import annotations

from typing import Any

# Indicator -> the derived inputs it needs present in scope.
IND_INPUTS: dict[str, list[str]] = {
    "C07": ["weights"],
    "C08": ["weights"],
    "C09": ["weights"],
    "C10": ["weights"],
    "C11": ["weights"],
    "C12": ["weights"],
    "C13": ["weights"],
    "C31": ["weights"],
    "C16": ["days_discharge_to_reg"],
    "C17": ["days_discharge_to_reg"],
    "C19": ["referred"],
    "C20": ["ever_danger_sign"],
    "C21": ["self_referral_count"],
    "C23": ["kmc_hours_mean"],
    "C28": ["birth_weight_g", "enrollment_weight_g"],
}

MORTALITY_CREDIBLE = {"PIPN": True, "EHA": True}
COMPLETION_CREDIBLE = {"GHI": False}


# derived-property name -> the pipeline column APP_ASKS is keyed on
ASKS_AS = {
    "referred": "referral_visits",
    "ever_danger_sign": "danger_visits",
    "self_referral_count": "self_referral_visits",
}

# Which opportunities' apps ASK for each field. Not every KMC app collects every
# question, and an app that never asks must read "not in app" rather than
# "recorded nothing" -- they are different facts about the programme.
#
# This was very nearly wrong: reading the first two opportunities suggested every
# field was true everywhere, and generalising from that produced the right VALUES
# with the wrong REASON on 369 per-FLW cells. 14 of the 22 opportunities have at
# least one field their app does not ask.
APP_ASKS: dict[str, dict[str, bool]] = {
    "10013": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": True,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "10014": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": True,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "10015": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": True,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "10016": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "10017": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "10018": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "10019": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "10020": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": False,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": False,
        "weights": True,
    },
    "10021": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": False,
        "weights": True,
    },
    "10022": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": False,
        "weights": True,
    },
    "10042": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": True,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "1234": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "1236": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "1487": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": True,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "1488": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": True,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "1739": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": True,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "1790": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": True,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "523": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": False,
        "weights": True,
    },
    "524": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": False,
        "weights": True,
    },
    "675": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": False,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": False,
        "weights": True,
    },
    "874": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
    "938": {
        "birth_weight_g": True,
        "danger_visits": True,
        "days_discharge_to_reg": False,
        "discharge_visits": True,
        "enrollment_weight_g": True,
        "kmc_hours_mean": True,
        "referral_visits": True,
        "reg_date": True,
        "self_referral_visits": True,
        "weights": True,
    },
}


def any_asks(field: str, opportunity_ids) -> bool:
    """True if ANY opportunity in scope asks for the field.

    Unknown opportunity, or unknown field on a known opportunity, counts as
    asking -- the same fail-open choice the render makes.
    """
    col = ASKS_AS.get(field, field)
    if not opportunity_ids:
        return True
    for o in opportunity_ids:
        m = APP_ASKS.get(str(o))
        if m is None or m.get(col) is None or m.get(col):
            return True
    return False


def input_state(indicator: str, row: dict[str, Any], opportunity_ids=None) -> str:
    """'ok' | 'notinapp' | 'unrecorded'.

    Two distinct reasons a number is withheld, and the distinction is real: an app
    that never asks the question ("not in app") is a different fact from an app
    that asks and recorded nothing ("unrecorded"). Both render as n/a, so the
    values agree either way -- but reporting the wrong reason misdescribes the
    programme.
    """
    for field in IND_INPUTS.get(indicator, []):
        if not any_asks(field, opportunity_ids):
            return "notinapp"
        gate = row.get(f"anyrec_{field}")
        if gate is not None and int(gate) == 0:
            return "unrecorded"
    return "ok"


def credible_for(indicator: str, llo: str | None) -> bool:
    """Programme scope (llo=None) is never gated: it pools credible recorders."""
    if indicator == "C14":
        return llo is None or bool(MORTALITY_CREDIBLE.get(llo))
    if indicator in ("C18", "C22"):
        return llo is None or COMPLETION_CREDIBLE.get(llo) is not False
    return True
