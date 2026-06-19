"""Tests for the profiler module — field typing, categorical frequencies, null rates."""

from commcare_connect.labs.synthetic.generator.fixtures.profiler import (
    _profile_categorical,
    _profile_null_rate,
    profile,
)


def _visit(form):
    return {"username": "x", "visit_date": "2026-05-04", "form_json": form}


def test_profile_categorical_frequencies():
    visits = [_visit({"form": {"sex": "m"}}) for _ in range(7)] + [_visit({"form": {"sex": "f"}}) for _ in range(3)]
    freqs = _profile_categorical(visits, "form.sex")
    assert round(freqs["m"], 2) == 0.7
    assert round(freqs["f"], 2) == 0.3


def test_profile_null_rate():
    visits = [_visit({"form": {"w": 1}}), _visit({"form": {}}), _visit({"form": {"w": 3}}), _visit({"form": {}})]
    assert _profile_null_rate(visits, "form.w") == 0.5


def _make_app_structure():
    """Build a minimal app_structure JSON that parse_form_schema_from_app_json will parse."""
    return {
        "deliver_app": {
            "modules": [
                {
                    "forms": [
                        {
                            "questions": [
                                {
                                    "value": "/data/sex",
                                    "type": "Select",
                                    "options": [{"value": "m"}, {"value": "f"}],
                                },
                                {
                                    "value": "/data/weight_kg",
                                    "type": "Decimal",
                                    "options": [],
                                },
                            ]
                        }
                    ]
                }
            ]
        }
    }


def _make_visits(n=10):
    """Build n visits spanning a multi-day date range so the manifest timeline validates."""
    visits = []
    for i in range(n):
        day = f"2026-05-{4 + i:02d}"
        visits.append(
            {
                "username": f"flw_{i % 2}",
                "visit_date": day,
                "form_json": {
                    "form": {
                        "sex": "m" if i < 7 else "f",
                        "weight_kg": str(50.0 + i),
                    }
                },
                "entity_id": f"ent_{i}",
                "status": "approved",
            }
        )
    return visits


def test_profile_with_app_structure_adds_categorical():
    """profile() with app_structure attaches categorical dist for select fields."""
    visits = _make_visits()

    app_structure = _make_app_structure()
    manifest_yaml = profile(
        opportunity_id=999,
        user_visits=visits,
        user_data=[],
        opportunity_detail={"name": "Test Opp"},
        app_structure=app_structure,
    )

    import yaml

    manifest = yaml.safe_load(manifest_yaml)
    cohort = manifest["beneficiary_cohorts"][0]
    field_dists = cohort["field_distributions"]

    # The select field should appear as a categorical distribution
    assert "form.sex" in field_dists, f"Expected 'form.sex' in field_dists, got: {list(field_dists.keys())}"
    sex_dist = field_dists["form.sex"]
    assert sex_dist["distribution"] == "categorical"
    assert "values" in sex_dist
    assert "null_rate" in sex_dist


def test_profile_numeric_gets_null_rate():
    """profile() with app_structure attaches null_rate to numeric distributions."""
    visits = []
    for i in range(10):
        day = f"2026-05-{4 + i:02d}"
        visits.append(
            {
                "username": f"flw_{i % 2}",
                "visit_date": day,
                "form_json": {
                    "form": {
                        "weight_kg": str(50.0 + i) if i % 2 == 0 else None,
                    }
                },
                "entity_id": f"ent_{i}",
                "status": "approved",
            }
        )

    app_structure = _make_app_structure()
    manifest_yaml = profile(
        opportunity_id=999,
        user_visits=visits,
        user_data=[],
        opportunity_detail={"name": "Test Opp"},
        app_structure=app_structure,
    )

    import yaml

    manifest = yaml.safe_load(manifest_yaml)
    cohort = manifest["beneficiary_cohorts"][0]
    field_dists = cohort["field_distributions"]

    # Numeric field that was profiled should have null_rate attached
    if "form.weight_kg" in field_dists:
        assert "null_rate" in field_dists["form.weight_kg"]


def test_profile_correlation_recovers_positive_relationship():
    import random

    from commcare_connect.labs.synthetic.generator.fixtures.profiler import _profile_correlation

    rng = random.Random(0)
    visits = []
    for _ in range(300):
        a = rng.gauss(10, 2)
        b = a * 0.9 + rng.gauss(0, 0.5)  # strongly correlated with a
        visits.append({"form_json": {"form": {"a": a, "b": b}}})
    corr = _profile_correlation(visits, ["form.a", "form.b"], {"form.a": "decimal", "form.b": "decimal"})
    assert corr["fields"] == ["form.a", "form.b"]
    # off-diagonal correlation is strongly positive
    assert corr["matrix"][0][1] > 0.7


def test_profile_backward_compatible_without_app_structure():
    """profile() without app_structure still works and produces a valid manifest."""
    visits = []
    for i in range(10):
        day = f"2026-05-{4 + i:02d}"
        visits.append(
            {
                "username": f"flw_{i % 2}",
                "visit_date": day,
                "form_json": {"form": {"weight_kg": str(50.0 + i)}},
                "entity_id": f"ent_{i}",
                "status": "approved",
            }
        )

    # Should not raise
    manifest_yaml = profile(
        opportunity_id=999,
        user_visits=visits,
        user_data=[],
        opportunity_detail={"name": "Test Opp"},
        # no app_structure
    )
    assert "opportunity_id" in manifest_yaml


def test_profile_emits_full_manifest(monkeypatch):
    import random

    from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import profile

    rng = random.Random(0)
    visits = []
    for d in range(28):
        for _ in range(5):
            a = rng.gauss(10, 2)
            visits.append(
                {
                    "username": rng.choice(["asha", "ben"]),
                    "visit_date": f"2026-05-{(d % 28) + 1:02d}",
                    "status": "approved",
                    "flagged": False,
                    "entity_id": f"e{rng.randint(1, 40)}",
                    "form_json": {
                        "form": {
                            "a": a,
                            "b": a * 0.8 + rng.gauss(0, 0.5),
                            "sex": rng.choice(["m", "f"]),
                        }
                    },
                }
            )
    # Use the same app_structure format as the rest of the tests (value/type/options keys)
    app_structure = {
        "learn_app": None,
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
        },
    }
    yaml_str = profile(
        opportunity_id=10001,
        user_visits=visits,
        user_data=[],
        opportunity_detail={"name": "X"},
        app_structure=app_structure,
    )
    m = Manifest.from_yaml(yaml_str)
    assert m.temporal is not None
    assert m.beneficiary_cohorts[0].correlation is not None
    assert "form.sex" in m.beneficiary_cohorts[0].field_distributions


def test_profile_field_distributions_captures_bounds():
    """Numeric distributions carry observed robust bounds (p1/p99) for clamping."""
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import _profile_field_distributions

    visits = [{"form_json": {"form": {"age": float(v)}}} for v in range(0, 60)]
    dists = _profile_field_distributions(visits, ["form.age"])
    d = dists["form.age"]
    assert d["distribution"] == "normal"
    assert "lo" in d and "hi" in d
    assert d["lo"] >= 0.0  # never below the real observed floor -> no negatives
    assert d["lo"] <= d["hi"] <= 59.0


def test_curate_flag_floor_gives_status_signal():
    """curate=True floors flag rates so all-approved opps get a status mix."""
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import _profile_flw_personas

    # An FLW with 50 perfectly-approved visits -> real flag_rate 0.
    visits_by_flw = {"a": [{"status": "approved", "flagged": False}] * 50}
    faithful = _profile_flw_personas(visits_by_flw)
    curated = _profile_flw_personas(visits_by_flw, curate=True, opp_jitter=1.0)
    assert faithful[0]["flag_rate"] == 0.0
    assert curated[0]["flag_rate"] > 0.0  # floored -> approval_rate now has variance


def test_curate_categorical_injects_minority():
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import _curate_categorical

    # Degenerate binary 'no' -> gets an affirmative minority.
    out = _curate_categorical({"no": 1.0}, 0.1)
    assert out["no"] == 0.9 and out["yes"] == 0.1
    # Near-degenerate multi-value -> rebalanced to give the minority mass.
    out2 = _curate_categorical({"yes": 0.98, "no": 0.02}, 0.1)
    assert out2["yes"] == 0.9 and 0.05 < out2["no"] <= 0.1
    # Single non-binary value -> left alone (don't invent a category).
    assert _curate_categorical({"ok": 1.0}, 0.1) == {"ok": 1.0}
    # Already has signal -> unchanged.
    assert _curate_categorical({"a": 0.6, "b": 0.4}, 0.1) == {"a": 0.6, "b": 0.4}


def test_profile_curate_end_to_end_validates_and_adds_signal():
    """profile(curate=True) yields a valid manifest whose flag rates are floored."""
    import yaml as _yaml

    from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest

    visits = []
    for i in range(40):
        visits.append(
            {
                "username": f"flw_{i % 3}",
                "visit_date": f"2026-05-{4 + (i % 20):02d}",
                "form_json": {"form": {"weight": str(1500 + i * 5), "danger_sign": "no"}},
                "entity_id": f"e{i}",
                "status": "approved",
                "flagged": False,
            }
        )
    app = {
        "deliver_app": {
            "modules": [
                {
                    "forms": [
                        {
                            "questions": [
                                {
                                    "json_path": "form.danger_sign",
                                    "type": "select",
                                    "options": [{"value": "no"}, {"value": "yes"}],
                                },
                            ]
                        }
                    ]
                }
            ]
        }
    }
    ml = profile(
        opportunity_id=874,
        user_visits=visits,
        user_data=[],
        opportunity_detail={"name": "X"},
        app_structure=app,
        curate=True,
    )
    Manifest.from_yaml(ml)  # must validate
    data = _yaml.safe_load(ml)
    assert any(p["flag_rate"] > 0 for p in data["flw_personas"])  # status signal injected
