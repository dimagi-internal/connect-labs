"""Validation cascade — port of R Data_Analysis.R reported-vs-validated logic.

The headline QC analytic: of the visits an FLW *reported*, how many survive a
sequence of integrity filters? Each filter is independent; a household is
`visit_validated` only if it reported a visit and fails none of them.

Rooftop's three pilot filters (CCC-CHC):
  1. older-services — any household member aged ≥ 8 who received Vit-A / ORS /
     MUAC (those services target under-5s, so this signals fabrication).
  2. confidence    — FLW's own confidence flag == "no_other_campaign".
  3. phone-use     — worker_used_phone == "no" (the app's phone wasn't used).

`apply_cascade` is the generic engine (compose any FilterRule list);
`validation_cascade` wires the three named rules and emits the per-ward
reported→validated drop report analysts know from the R output.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

REPORTED_VISIT_YES = "yes"
CONFIDENCE_FAIL = "no_other_campaign"
PHONE_FAIL = "no"
OLDER_SERVICE_MIN_AGE = 8


@dataclass(frozen=True)
class FilterRule:
    name: str
    description: str
    fails: Callable[[pd.DataFrame], pd.Series]  # returns boolean Series (True = fails the rule)
    severity: str = "drop"  # "drop" removes from validated set; "flag" only annotates


@dataclass
class CascadeResult:
    households: pd.DataFrame  # input + per-rule fail_* columns + visit_validated
    per_rule: dict = field(default_factory=dict)  # rule_name -> count failing (among reported)
    n_reported: int = 0
    n_validated: int = 0


def older_services_violations(members: pd.DataFrame, household_key: str = "household_id") -> set:
    """Household keys with a member aged >= 8 who received any of vita/ors/muac."""
    if members.empty:
        return set()
    age = pd.to_numeric(members.get("member_age"), errors="coerce")
    got = pd.Series(False, index=members.index)
    for svc in ("member_vita", "member_ors", "member_muac"):
        if svc in members.columns:
            got = got | members[svc].astype("object").fillna("").eq("yes")
    bad = members[(age >= OLDER_SERVICE_MIN_AGE) & got]
    return set(bad[household_key].tolist()) if household_key in bad.columns else set()


def rooftop_rules() -> list[FilterRule]:
    return [
        FilterRule(
            "older_services",
            "Household gave Vit-A/ORS/MUAC to a member aged 8+ (services target under-5s)",
            lambda df: df.get("fail_older_services", pd.Series(False, index=df.index)).astype(bool),
        ),
        FilterRule(
            "confidence",
            "FLW confidence flag == no_other_campaign",
            lambda df: df.get("confidence_in_vita_visit", pd.Series("", index=df.index))
            .astype("object")
            .fillna("")
            .eq(CONFIDENCE_FAIL),
        ),
        FilterRule(
            "phone",
            "Worker did not use the phone during the visit",
            lambda df: df.get("worker_used_phone", pd.Series("", index=df.index))
            .astype("object")
            .fillna("")
            .eq(PHONE_FAIL),
        ),
    ]


def apply_cascade(households: pd.DataFrame, rules: list[FilterRule]) -> CascadeResult:
    df = households.copy()
    reported = df.get("flw_visit", pd.Series("", index=df.index)).astype("object").fillna("").eq(REPORTED_VISIT_YES)
    df["reported_visit"] = reported

    failed_any = pd.Series(False, index=df.index)
    per_rule = {}
    for rule in rules:
        fails = rule.fails(df) & reported  # only meaningful among reported visits
        df[f"fail_{rule.name}"] = fails
        per_rule[rule.name] = int(fails.sum())
        if rule.severity == "drop":
            failed_any = failed_any | fails

    df["visit_validated"] = reported & ~failed_any
    return CascadeResult(
        households=df,
        per_rule=per_rule,
        n_reported=int(reported.sum()),
        n_validated=int(df["visit_validated"].sum()),
    )


def validation_cascade(households: pd.DataFrame) -> CascadeResult:
    """Convenience: apply the three named rooftop filters."""
    return apply_cascade(households, rooftop_rules())


def cascade_report_by(result: CascadeResult, group_col: str = "ward") -> pd.DataFrame:
    """Per-group reported→validated drop table (matches the R analyst output shape)."""
    df = result.households
    if group_col not in df.columns:
        df = df.assign(**{group_col: "ALL"})
    rows = []
    for grp, sub in df.groupby(group_col):
        reported = int(sub["reported_visit"].sum())
        validated = int(sub["visit_validated"].sum())
        n = len(sub)
        rows.append(
            {
                group_col: grp,
                "households": n,
                "reported": reported,
                "validated": validated,
                "reported_rate": round(100.0 * reported / n, 1) if n else 0.0,
                "validated_rate": round(100.0 * validated / n, 1) if n else 0.0,
                "drop_pp": round(100.0 * (reported - validated) / n, 1) if n else 0.0,
            }
        )
    return pd.DataFrame(rows)
