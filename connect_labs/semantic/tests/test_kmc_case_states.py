"""The KMC registry's four case states, run on Postgres over visits shaped like the
synthetic demo cases of opportunities 10042 and 10016 (2026-10-09)."""

from pathlib import Path

import pytest
import yaml
from django.db import connection

from connect_labs.semantic import case_states as cs
from connect_labs.semantic.layer1 import visit_columns_sql
from connect_labs.semantic.model import resolve_model
from connect_labs.semantic.runtime import evaluate_with_cases

REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "kmc"
REG, FOLLOW = "Child Registration Form", "Record Visit Details"

COLUMNS = [
    ("baby_case_id", "text"),
    ("entity_name", "text"),
    ("visit_id", "text"),
    ("visit_date", "timestamp"),
    ("opportunity_id", "int"),
    ("username", "text"),
    ("weight_g", "double precision"),
    ("form_names", "text"),
    ("enrollment_weight_g", "double precision"),
    ("birth_weight_g", "double precision"),
    ("referral_answer", "text"),
    ("kmc_hours_total", "double precision"),
    ("ds_pus", "text"),
    ("ds_jaundice", "text"),
    ("dsl_fever", "text"),
    ("reg_date", "timestamp"),
    ("gestational_age_wks", "double precision"),
    ("days_discharge_to_reg", "double precision"),
    ("hospital_discharge_date", "timestamp"),
    ("kmc_hours_mean", "double precision"),
    ("death_visits", "text"),
    ("danger_visits", "text"),
    ("referral_visits", "text"),
    ("self_referral_visits", "text"),
    ("ebf_visits", "text"),
]
# The danger-sign fields the registry reads that this fixture leaves out entirely.
ABSENT_SIGNS = (
    "ds_convulsions",
    "ds_lethargic",
    "ds_poor_feeding",
    "ds_chest_indrawing",
    "ds_bluish",
    "ds_noisy_breathing",
    "dsl_hypothermia",
    "dsl_hypoxia",
    "dsl_fast_breathing",
    "dsl_slow_breathing",
    "dsl_low_heart_rate",
)


def V(
    case,
    name,
    day,
    *,
    weight=None,
    reg_weight=None,
    form=FOLLOW,
    hours=None,
    referred=None,
    pus=None,
    fever=None,
    user="flw_001",
):
    return {
        "baby_case_id": case,
        "entity_name": name,
        "visit_id": f"{case}-{day}-{form[:3]}",
        "visit_date": f"{day} 10:00",
        "opportunity_id": 10042,
        "username": user,
        "weight_g": weight,
        "form_names": form,
        "enrollment_weight_g": reg_weight,
        "birth_weight_g": 1250.0,
        "referral_answer": referred,
        "kmc_hours_total": hours,
        "ds_pus": pus,
        "dsl_fever": fever,
        "reg_date": f"{day} 00:00" if form == REG else None,
    }


VISITS = [
    # Steady gain (thriving): 1,350 -> 2,285 g over three weeks.
    V("sg", "KMC Demo — Steady Gain", "2026-05-17", reg_weight=1350, form=REG),
    V("sg", "KMC Demo — Steady Gain", "2026-05-18", weight=1350, referred="no"),
    V("sg", "KMC Demo — Steady Gain", "2026-05-25", weight=1635, hours=20, referred="no"),
    V("sg", "KMC Demo — Steady Gain", "2026-06-01", weight=1915, hours=40, referred="no"),
    V("sg", "KMC Demo — Steady Gain", "2026-06-08", weight=2285, hours=40, referred="no"),
    # Transcription error (weight check): +495 g in one day.
    V("te", "KMC Demo — Transcription Error", "2026-05-17", reg_weight=1415, form=REG, user="flw_007"),
    V("te", "KMC Demo — Transcription Error", "2026-05-17", weight=1415, user="flw_007"),
    V("te", "KMC Demo — Transcription Error", "2026-05-18", weight=1910.25, referred="no", user="flw_007"),
    V("te", "KMC Demo — Transcription Error", "2026-05-25", weight=2504.25, hours=22, user="flw_007"),
    # Faltering (Beneficiary 694 of 10016): enrolment 2,000 g, then 2,200, 2,200; hours 16 -> 8.
    V("fa", "Beneficiary 694", "2026-05-19", reg_weight=2000, form=REG, user="flw_005"),
    V("fa", "Beneficiary 694", "2026-06-02", weight=2200, hours=16, user="flw_005"),
    V("fa", "Beneficiary 694", "2026-06-09", weight=2200, hours=8, user="flw_005"),
    # Danger sign, not referred: pus on 5 Jun, referred: no. Believable weights.
    V("dg", "Beneficiary 410", "2026-05-21", reg_weight=1500, form=REG, referred="yes", user="flw_015"),
    V("dg", "Beneficiary 410", "2026-05-28", weight=1600, referred="no", user="flw_015"),
    V("dg", "Beneficiary 410", "2026-06-05", weight=1700, pus="yes", fever="OK", referred="no", user="flw_015"),
    # 250 g is no baby's weight: a weight check, never a danger-sign case.
    V("bw", "Beneficiary 411", "2026-06-01", weight=250, pus="yes", referred="no", user="flw_015"),
]


@pytest.fixture
def evaluated(db):
    props = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    inds = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    names = [c for c, _ in COLUMNS]
    with connection.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE kmc_cs_visits ({', '.join(f'{c} {t}' for c, t in COLUMNS)})")
        cur.executemany(
            f"INSERT INTO kmc_cs_visits ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})",
            [[v.get(n) for n in names] for v in VISITS],
        )
        for col in ABSENT_SIGNS:
            cur.execute(f"ALTER TABLE kmc_cs_visits ADD COLUMN {col} text")
    model = resolve_model(props)
    visit_sql = f"SELECT x.*{visit_columns_sql(list(model.visit_columns))} FROM kmc_cs_visits x"

    def run(as_of="2026-06-10"):
        fields = {**cs.case_fields(props), "case_name": "case_name"}
        rows, cases, dropped = evaluate_with_cases(
            None,
            [10042],
            scopes=["flw"],
            case_fields=fields,
            visit_sql=visit_sql,
            registry_documents=(props, inds),
            as_of=f"DATE '{as_of}'",
        )
        assert not dropped
        return {c["entity_id"]: c for c in cases}, rows

    yield props, run
    with connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS kmc_cs_visits")


def _state(props, case):
    st = cs.case_state(case, cs.catalog(props))
    return (st["name"], cs.facts(st, case)) if st else (None, None)


def test_each_demo_case_is_in_its_case_state_with_its_facts(evaluated):
    props, run = evaluated
    cases, _rows = run()
    assert _state(props, cases["sg"]) == (
        "case_state_thriving",
        "Weight rose from 1,350 g on 18 May to 2,285 g on 8 Jun, about 25 g/kg/day on average; "
        "healthy growth is 15-20 g/kg/day.",
    )
    assert _state(props, cases["te"]) == (
        "case_state_weight_check",
        "Weight changed by +495 g in 1 day between 17 May and 18 May, about 298 g/kg/day; the programme "
        "counts a change outside -20 to 45 g/kg/day as impossible.",
    )
    assert _state(props, cases["fa"]) == (
        "case_state_faltering",
        "Weight went from 2,000 g on 19 May to 2,200 g on 9 Jun, about 4.8 g/kg/day; healthy growth is "
        "15-20 g/kg/day. Skin-to-skin fell from 16 h on 2 Jun to 8 h on 9 Jun.",
    )
    assert _state(props, cases["dg"]) == (
        "case_state_danger_unreferred",
        "On 5 Jun 2026 the visit recorded fever, pus in the eyes, skin or belly button, "
        "and the baby was not referred.",
    )
    assert _state(props, cases["bw"])[0] == "case_state_weight_check"
    assert cases["sg"]["case_name"] == "KMC Demo — Steady Gain"


def test_the_states_are_exclusive_and_counted_per_worker(evaluated):
    props, run = evaluated
    cases, rows = run()
    names = [s["name"] for s in cs.catalog(props)]
    for c in cases.values():
        assert sum(bool(c[n]) for n in names) <= 1, c["entity_id"]
    by_worker = {r["username"]: r for r in rows}
    assert by_worker["flw_015"]["count_case_state_danger_unreferred"] == 1
    assert by_worker["flw_015"]["count_case_state_weight_check"] == 1
    assert by_worker["flw_001"]["count_case_state_thriving"] == 1


def test_a_state_is_as_of_the_run(evaluated):
    """Visits after the report date do not exist for it, and a state's evidence must
    be recent (30 days) as of that date."""
    props, run = evaluated
    early, _ = run("2026-05-20")
    # As of 20 May the faltering baby has only its enrolment weighing, and the steady
    # gain baby only two readings a day apart: neither is in a state yet.
    assert _state(props, early["fa"])[0] is None
    assert _state(props, early["sg"])[0] is None
    late, _ = run("2026-08-30")
    assert _state(props, late["sg"])[0] is None  # last weighed 8 Jun: no longer recent
    assert _state(props, late["te"])[0] is None
