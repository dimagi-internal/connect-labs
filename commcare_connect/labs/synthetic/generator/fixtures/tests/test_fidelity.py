"""Tests for fidelity.py — verifies that data generated from a profiled manifest
scores faithfully against that same manifest."""

import random

import pytest

from commcare_connect.labs.synthetic.generator.fixtures.engine import generate
from commcare_connect.labs.synthetic.generator.fixtures.fidelity import compare
from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest
from commcare_connect.labs.synthetic.generator.fixtures.profiler import profile
from commcare_connect.labs.synthetic.generator.fixtures.schema_loader import FormSchema, QuestionSpec


def _make_app_structure():
    """Minimal app_structure with two correlated numeric fields and one select."""
    return {
        "deliver_app": {
            "modules": [
                {
                    "forms": [
                        {
                            "questions": [
                                {"value": "/data/a", "type": "Decimal", "options": []},
                                {"value": "/data/b", "type": "Decimal", "options": []},
                                {
                                    "value": "/data/sex",
                                    "type": "Select",
                                    "options": [{"value": "m"}, {"value": "f"}],
                                },
                            ]
                        }
                    ]
                }
            ]
        }
    }


def _make_form_schema():
    return FormSchema(
        questions=[
            QuestionSpec(json_path="form.a", kind="decimal", choices=[]),
            QuestionSpec(json_path="form.b", kind="decimal", choices=[]),
            QuestionSpec(json_path="form.sex", kind="select", choices=["m", "f"]),
        ]
    )


def _make_opportunity_detail():
    return {
        "id": 9999,
        "name": "Fidelity Test Opp",
        "deliver_units": [{"id": 1, "name": "DU1"}],
        "payment_units": [{"id": 1, "name": "PU1", "deliver_units": [1]}],
    }


def _make_source_visits(n=800):
    """Build n deterministic visits with two strongly-correlated numeric fields
    and a categorical field — large enough for the profiler to detect correlation."""
    rng = random.Random(42)
    visits = []
    for i in range(n):
        day_offset = i % 28
        day = f"2026-05-{1 + day_offset:02d}"
        a = rng.gauss(10, 2)
        b = a * 0.9 + rng.gauss(0, 0.5)  # strongly correlated with a
        sex = "m" if rng.random() < 0.7 else "f"
        visits.append(
            {
                "username": f"flw_{i % 3}",
                "visit_date": day,
                "status": "approved",
                "flagged": False,
                "entity_id": f"ent_{i % 50}",
                "form_json": {
                    "form": {
                        "a": a,
                        "b": b,
                        "sex": sex,
                    }
                },
            }
        )
    return visits


@pytest.fixture
def _full_manifest_and_visits():
    """Profile a deterministic synthetic input → manifest, generate from it,
    return (manifest, generated_user_visits)."""
    source_visits = _make_source_visits(n=800)
    app_structure = _make_app_structure()

    manifest_yaml = profile(
        opportunity_id=9999,
        user_visits=source_visits,
        user_data=[],
        opportunity_detail={"name": "Fidelity Test Opp"},
        app_structure=app_structure,
    )
    manifest = Manifest.from_yaml(manifest_yaml)

    form_schema = _make_form_schema()
    opportunity_detail = _make_opportunity_detail()

    out = generate(
        manifest=manifest,
        opportunity_detail=opportunity_detail,
        form_schema=form_schema,
    )
    return manifest, out["user_visits"]


def test_fidelity_reports_low_divergence_for_self_generated(_full_manifest_and_visits):
    manifest, visits = _full_manifest_and_visits  # fixture: profile a synthetic set, generate it
    report = compare(manifest, visits)
    assert report["correlation_frobenius"] < 0.25
    assert report["fields"]  # non-empty per-field section


# --- source-vs-clone longitudinal fidelity (issue #713 #3) ---


def _series(entity, owner, day_weights):
    return [
        {"entity_id": entity, "username": owner, "visit_date": d, "form_json": {"form": {"weight": w}}}
        for d, w in day_weights
    ]


def test_compare_to_source_scores_an_identical_clone_near_perfect():
    from commcare_connect.labs.synthetic.generator.fixtures.fidelity import compare_to_source

    src = (
        _series("e1", "flwA", [("2026-01-01", 1200), ("2026-01-08", 1300), ("2026-01-15", 1400)])
        + _series("e2", "flwA", [("2026-01-02", 1100), ("2026-01-09", 1200)])
        + _series("e3", "flwB", [("2026-01-03", 1000), ("2026-01-10", 1100), ("2026-01-17", 1250)])
    )
    clone = [dict(v) for v in src]  # identical structure + values

    rep = compare_to_source(src, clone, numeric_paths={"form.weight"})

    assert rep["score"] >= 0.99
    assert rep["visits_per_case_tvd"] == 0.0
    assert rep["cases_per_flw_tvd"] == 0.0
    assert rep["fields"]["form.weight"]["out_of_range_rate"] == 0.0
    assert rep["fields"]["form.weight"]["wasserstein_norm"] == 0.0


def test_compare_to_source_flags_out_of_range_and_trajectory_mismatch():
    from commcare_connect.labs.synthetic.generator.fixtures.fidelity import compare_to_source

    src = _series("e1", "flwA", [("2026-01-01", 1200), ("2026-01-08", 1300), ("2026-01-15", 1400)])
    # clone: a value outside the source range, and a flat-then-spike (different slope shape)
    clone = _series("c1", "flwA", [("2026-01-01", 1300), ("2026-01-08", 1300), ("2026-01-15", 5000)])

    rep = compare_to_source(src, clone, numeric_paths={"form.weight"})

    assert rep["fields"]["form.weight"]["out_of_range_rate"] > 0.0  # 5000 is outside [1200, 1400]
    assert rep["trajectory"]["form.weight"]["slope_delta"] > 0.0
    assert rep["score"] < 0.99


def test_mirror_clone_round_trip_reproduces_structure_and_growth_curve():
    """End-to-end close mirror: profile(mirror=True) -> generate -> compare_to_source.
    The clone reproduces exact ratios, stays high-fidelity, and preserves the
    per-child growth curve (issue #713 acceptance)."""
    from commcare_connect.labs.synthetic.generator.fixtures.fidelity import compare_to_source

    source = []
    for e in range(6):
        owner = "asha" if e < 4 else "ben"  # 4 cases vs 2 cases
        for i, d in enumerate(["2026-01-05", "2026-01-12", "2026-01-19"]):
            source.append(
                {
                    "username": owner,
                    "visit_date": d,
                    "status": "approved",
                    "flagged": False,
                    "entity_id": f"ent_{e}",
                    "form_json": {"form": {"weight": 1200.0 + 110 * i + 7 * e}},  # rises with each visit
                }
            )
    app_structure = {
        "learn_app": None,
        "deliver_app": {
            "modules": [{"forms": [{"questions": [{"value": "/data/weight", "type": "Decimal", "options": []}]}]}]
        },
    }
    manifest = Manifest.from_yaml(
        profile(
            opportunity_id=10009,
            user_visits=source,
            user_data=[],
            opportunity_detail={"name": "KMC"},
            app_structure=app_structure,
            mirror=True,
        )
    )
    schema = FormSchema(questions=[QuestionSpec(json_path="form.weight", kind="decimal")])
    out = generate(manifest=manifest, opportunity_detail={"name": "KMC"}, form_schema=schema)

    rep = compare_to_source(source, out["user_visits"], numeric_paths={"form.weight"})
    assert rep["visits_per_case_tvd"] == 0.0  # exact visits/case
    assert rep["cases_per_flw_tvd"] == 0.0  # exact cases/FLW
    assert rep["score"] >= 0.9
    assert rep["fields"]["form.weight"]["out_of_range_rate"] == 0.0  # jitter stays in each case's band

    # every cloned case is a coherent rising series (the growth curve we couldn't plot before)
    by_entity = {}
    for v in out["user_visits"]:
        by_entity.setdefault(v["entity_id"], []).append(v)
    assert len(by_entity) == 6
    for vs in by_entity.values():
        weights = [w["form_json"]["form"]["weight"] for w in sorted(vs, key=lambda v: v["visit_date"])]
        assert weights == sorted(weights) and weights[-1] > weights[0]
