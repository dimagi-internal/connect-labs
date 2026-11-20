"""The KMC demo's photo cases land in the coaching situation each one is declared for.

The chain a demo needs: the cohort spec (cohorts/kmc.yaml) declares a showcase case,
the generator builds its visits, the KMC programme pipeline reads them at its own
paths, and the registry's case states say which coaching situation the baby is in as
of the report date. A case in no state has no Coach button, so each case is checked
here end to end, as of the demo report's date, rather than by reading the YAML.
"""

import datetime as dt
from pathlib import Path

import pytest
import yaml
from django.db import connection

from connect_labs.labs.synthetic.cohort import CohortSpec
from connect_labs.labs.synthetic.generator.fixtures.manifest import ImageConfig
from connect_labs.labs.synthetic.generator.fixtures.showcase import build_showcase_visits
from connect_labs.semantic import case_states as cs
from connect_labs.semantic.layer1 import visit_columns_sql
from connect_labs.semantic.model import resolve_model
from connect_labs.semantic.runtime import evaluate_with_cases
from connect_labs.semantic.tests.test_kmc_case_states import ABSENT_SIGNS, COLUMNS
from connect_labs.workflow.templates.kmc_programme_metrics import CASE_PROPERTIES_SCHEMA, WEIGHT_SERIES_SCHEMA

SPEC = "connect_labs/labs/synthetic/cohorts/kmc.yaml"
REGISTRY = Path(__file__).resolve().parents[3] / "semantic" / "registry" / "kmc"
OPP = 10130
# The opportunity's last day of data, as a fresh profile would give it; the report
# runs on the latest data, so it is dated that day too.
REPORT = dt.date(2026, 11, 18)

EXPECTED = {
    "KMC Demo — Steady Gain": "case_state_thriving",
    "KMC Demo — Faltering Growth": "case_state_faltering",
    "KMC Demo — Weighing to Check": "case_state_weight_check",
    "KMC Demo — Danger Sign Not Referred": "case_state_danger_unreferred",
}


def _get(form_json, path):
    node = form_json
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _row(visit):
    """One visit as the KMC programme pipeline reads it: each field's first present path."""
    row = {
        "visit_id": str(visit["id"]),
        "visit_date": visit["visit_date"],
        "opportunity_id": visit["opportunity_id"],
        "username": visit["username"],
        "entity_name": visit["entity_name"],
    }
    # The case properties and, from its own schema, the weight series.
    for field in CASE_PROPERTIES_SCHEMA["fields"] + WEIGHT_SERIES_SCHEMA["fields"]:
        paths = field.get("paths") or ([field["path"]] if field.get("path") else [])
        for path in paths:
            value = visit.get(path) if path == "entity_id" else _get(visit["form_json"], path)
            if value not in (None, "") and field["name"] not in row:
                row[field["name"]] = value
                break
    return row


@pytest.fixture
def demo_cases(db):
    spec = CohortSpec.from_yaml(Path(SPEC).read_text())
    config = ImageConfig(**spec.image_config)
    assert config.showcase_last_visit_on == "end"
    visits = build_showcase_visits(config, opportunity_id=OPP, start_date=dt.date(2026, 4, 27), end_date=REPORT)
    props = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    inds = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    cols = COLUMNS + [(c, "text") for c in ABSENT_SIGNS]
    names = [c for c, _ in cols]
    rows = [_row(v) for v in visits]
    with connection.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE kmc_sc_visits ({', '.join(f'{c} {t}' for c, t in cols)})")
        cur.executemany(
            f"INSERT INTO kmc_sc_visits ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})",
            [[r.get(n) for n in names] for r in rows],
        )
    model = resolve_model(props)
    visit_sql = f"SELECT x.*{visit_columns_sql(list(model.visit_columns))} FROM kmc_sc_visits x"
    _rows, cases, dropped = evaluate_with_cases(
        None,
        [OPP],
        scopes=["flw"],
        case_fields={**cs.case_fields(props), "case_name": "case_name"},
        visit_sql=visit_sql,
        registry_documents=(props, inds),
        as_of=f"DATE '{REPORT.isoformat()}'",
    )
    assert not dropped
    catalog = cs.catalog(props)
    by_name = {}
    for case in cases:
        state = cs.case_state(case, catalog)
        by_name[case["case_name"]] = (state["name"] if state else None, cs.facts(state, case) if state else None)
    yield by_name, visits
    with connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS kmc_sc_visits")


def test_each_coaching_case_is_in_its_situation_on_the_report_date(demo_cases):
    states, _ = demo_cases
    got = {name: states.get(name, (None, None))[0] for name in EXPECTED}
    assert got == EXPECTED, states


def test_the_faltering_case_says_skin_to_skin_fell(demo_cases):
    states, _ = demo_cases
    assert "Skin-to-skin fell from 12 h" in states["KMC Demo — Faltering Growth"][1]
    assert "to 9 h on 18 Nov" in states["KMC Demo — Faltering Growth"][1]


def test_the_danger_case_names_its_sign(demo_cases):
    states, _ = demo_cases
    assert "severe chest indrawing" in states["KMC Demo — Danger Sign Not Referred"][1]


def test_every_coaching_case_is_photographed_and_ends_on_the_latest_day(demo_cases):
    _, visits = demo_cases
    for name in EXPECTED:
        weighed = [v for v in visits if v["entity_name"] == name and v["images"]]
        assert weighed
        assert max(v["visit_date"][:10] for v in weighed) == REPORT.isoformat()


def test_the_clean_coaching_cases_share_one_worker(demo_cases):
    _, visits = demo_cases
    clean = set(EXPECTED) - {"KMC Demo — Weighing to Check"}
    assert {v["username"] for v in visits if v["entity_name"] in clean} == {"flw_001"}


def test_the_weighing_to_check_is_wrong_only_at_its_last_weighing(demo_cases):
    _, visits = demo_cases
    weighed = sorted(
        (v for v in visits if v["entity_name"] == "KMC Demo — Weighing to Check" and v["images"]),
        key=lambda v: v["visit_date"],
    )
    assert [v["showcase"]["outcome"] for v in weighed] == ["pass"] * (len(weighed) - 1) + ["fail_number"]
