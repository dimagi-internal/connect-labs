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


def test_profile_covers_sparse_numeric_field_to_block_stub_leak():
    """A real numeric field with too few samples for a Normal is still modeled as a
    uniform over its observed range — so the engine never falls back to the
    randint(0,10) stub and leaks ~5 into a clinical column (issue #713 #4)."""
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import _profile_field_distributions

    visits = [{"form_json": {"form": {"muac": v}}} for v in (115.0, 122.0, 130.0)]
    d = _profile_field_distributions(visits, ["form.muac"])["form.muac"]
    assert d["distribution"] == "uniform"
    assert d["low"] == 115.0 and d["high"] == 130.0


def test_profile_models_near_constant_numeric_field_rather_than_dropping_it():
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import _profile_field_distributions

    visits = [{"form_json": {"form": {"dose": 1.0}}} for _ in range(20)]
    d = _profile_field_distributions(visits, ["form.dose"])["form.dose"]
    assert d["distribution"] == "uniform"  # zero variance -> degenerate uniform, not dropped
    assert d["low"] == d["high"] == 1.0


def test_profile_models_every_numeric_schema_field_even_when_sparsely_present():
    """A numeric schema field present in too few visits to be auto-discovered must
    still be modeled (so it can't slip through to the engine's randint(0,10) stub)."""
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import profile

    visits = []
    for d in range(28):
        for i in range(5):
            fj = {"form": {"a": 10.0 + i}}
            if d == 0:  # 'muac' present in 5/140 visits ~ 3.5% -> below the discovery threshold
                fj["form"]["muac"] = 118.0 + i
            visits.append(
                {
                    "username": "asha",
                    "visit_date": f"2026-05-{(d % 28) + 1:02d}",
                    "status": "approved",
                    "flagged": False,
                    "entity_id": f"e{d}",
                    "form_json": fj,
                }
            )
    app_structure = {
        "learn_app": None,
        "deliver_app": {
            "modules": [
                {
                    "forms": [
                        {
                            "questions": [
                                {"value": "/data/a", "type": "Decimal", "options": []},
                                {"value": "/data/muac", "type": "Decimal", "options": []},
                            ]
                        }
                    ]
                }
            ]
        },
    }
    yaml_str = profile(
        opportunity_id=10002,
        user_visits=visits,
        user_data=[],
        opportunity_detail={"name": "X"},
        app_structure=app_structure,
    )
    fd = Manifest.from_yaml(yaml_str).beneficiary_cohorts[0].field_distributions
    assert "form.muac" in fd  # sparse numeric schema field modeled, not left to the stub


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


def test_seed_anomalies_targets_real_flws_and_numeric_field():
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import _seed_anomalies

    personas = [
        {"id": "flw_001", "archetype": "rockstar"},
        {"id": "flw_002", "archetype": "struggling"},
        {"id": "flw_003", "archetype": "new_hire"},
    ]
    field_dists = {
        "form.birth_weight": {"distribution": "normal", "mean": 1800, "stddev": 300, "lo": 1000, "hi": 2500},
        "form.danger_sign": {"distribution": "categorical", "values": {"no": 0.9, "yes": 0.1}},
    }
    out = _seed_anomalies(personas, field_dists, weeks=4, opp_id=874, jitter=1.1)
    assert out, "curation should seed at least one anomaly"

    ids = {p["id"] for p in personas}
    for a in out:
        assert set(a["flw_ids"]) <= ids, "every anomaly must reference a real persona id"
        assert 1 <= a["week"] <= 4, "seeded week must fall inside the timeline"
    # field_outliers attach only to a numeric (normal) field, never a categorical.
    for a in out:
        if a["type"] == "field_outlier":
            assert a["field_path"] == "form.birth_weight"
    # The two newly-wired types are both represented in the seed set.
    types = {a["type"] for a in out}
    assert {"duplicate_submission", "missing_visits"} <= types
    assert len({a["id"] for a in out}) == len(out), "anomaly ids must be unique"


def test_seed_anomalies_handles_no_numeric_field():
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import _seed_anomalies

    personas = [{"id": "flw_001", "archetype": "steady"}]
    out = _seed_anomalies(
        personas, {"form.x": {"distribution": "categorical", "values": {"a": 1.0}}}, weeks=2, opp_id=1, jitter=1.0
    )
    # No normal field -> no field_outlier, but volume/duplicate signals still seed.
    assert out
    assert all(a["type"] != "field_outlier" for a in out)


def test_profile_curate_seeds_anomalies_only_when_curating():
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest

    visits = []
    for i in range(60):
        visits.append(
            {
                "username": f"flw_{i % 3}",
                "visit_date": f"2026-05-{4 + (i % 24):02d}",
                "form_json": {"form": {"birth_weight": str(1500 + (i % 30) * 10), "danger_sign": "no"}},
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
                                {"value": "/data/birth_weight", "type": "Decimal", "options": []},
                                {
                                    "value": "/data/danger_sign",
                                    "type": "Select",
                                    "options": [{"value": "no"}, {"value": "yes"}],
                                },
                            ]
                        }
                    ]
                }
            ]
        }
    }
    kwargs = dict(
        opportunity_id=874, user_visits=visits, user_data=[], opportunity_detail={"name": "X"}, app_structure=app
    )

    faithful = Manifest.from_yaml(profile(**kwargs, curate=False))
    curated = Manifest.from_yaml(profile(**kwargs, curate=True))

    assert faithful.anomalies == [], "faithful profiling must not invent anomalies"
    assert len(curated.anomalies) >= 1, "curation should seed QA anomalies to find"
    flw_ids = {p.id for p in curated.flw_personas}
    for a in curated.anomalies:
        assert set(a.flw_ids) <= flw_ids
    # A field_outlier must point at a field that actually has a distribution, so the
    # engine's _outlier path engages (a dangling path would be a silent no-op).
    dist_paths = set(curated.beneficiary_cohorts[0].field_distributions)
    for a in curated.anomalies:
        if a.type == "field_outlier":
            assert a.field_path in dist_paths


def test_profile_curate_anomalies_vary_by_opp():
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest

    def _visits():
        return [
            {
                "username": f"flw_{i % 3}",
                "visit_date": f"2026-05-{4 + (i % 24):02d}",
                "form_json": {"form": {"birth_weight": str(1500 + (i % 30) * 10)}},
                "entity_id": f"e{i}",
                "status": "approved",
                "flagged": False,
            }
            for i in range(60)
        ]

    app = {
        "deliver_app": {
            "modules": [
                {"forms": [{"questions": [{"value": "/data/birth_weight", "type": "Decimal", "options": []}]}]}
            ]
        }
    }
    cm1 = Manifest.from_yaml(
        profile(
            opportunity_id=874,
            user_visits=_visits(),
            user_data=[],
            opportunity_detail={"name": "X"},
            app_structure=app,
            curate=True,
        )
    )
    cm2 = Manifest.from_yaml(
        profile(
            opportunity_id=523,
            user_visits=_visits(),
            user_data=[],
            opportunity_detail={"name": "Y"},
            app_structure=app,
            curate=True,
        )
    )
    sig1 = sorted((a.type, a.flw_ids[0], a.week) for a in cm1.anomalies)
    sig2 = sorted((a.type, a.flw_ids[0], a.week) for a in cm2.anomalies)
    assert sig1 != sig2, "different opps should get distinct seeded anomaly sets"


def test_profile_repeat_groups_from_list_valued_data():
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import _profile_repeat_groups

    visits = []
    for i in range(30):
        n = (i % 3) + 1  # 1..3 instances
        visits.append(
            {
                "form_json": {
                    "form": {
                        "mother": "x",
                        "children": [{"weight": 1700 + j * 7 + i, "sex": "m" if j % 2 else "f"} for j in range(n)],
                    }
                }
            }
        )
    rgs = _profile_repeat_groups(visits)
    assert "form.children" in rgs
    rg = rgs["form.children"]
    assert set(rg["count"]) <= {1, 2, 3}
    assert abs(sum(rg["count"].values()) - 1.0) < 0.01
    assert rg["field_distributions"]["weight"]["distribution"] == "normal"
    assert rg["field_distributions"]["sex"]["distribution"] == "categorical"


def test_profile_ignores_list_of_scalars():
    """A list of plain scalars (e.g. a multi-select stored as a list) is not a repeat
    group — only lists of dicts are."""
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import _profile_repeat_groups

    visits = [{"form_json": {"form": {"tags": ["a", "b"]}}} for _ in range(10)]
    assert _profile_repeat_groups(visits) == {}


def test_profile_then_generate_reproduces_repeat_arrays_end_to_end():
    """Faithfulness guard: real repeat arrays in source data survive profile -> manifest
    -> generate as JSON arrays of sub-records, not single objects."""
    from commcare_connect.labs.synthetic.generator.fixtures.engine import generate
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest
    from commcare_connect.labs.synthetic.generator.fixtures.schema_loader import FormSchema, QuestionSpec

    visits = []
    for i in range(40):
        n = (i % 3) + 1
        day = f"2026-05-{4 + (i % 24):02d}"
        visits.append(
            {
                "username": f"flw_{i % 2}",
                "visit_date": day,
                "status": "approved",
                "flagged": False,
                "entity_id": f"e{i}",
                "form_json": {
                    "form": {
                        "mother_age": str(25 + (i % 10)),
                        "children": [{"weight": 1600 + j * 30 + i, "sex": "m" if j % 2 else "f"} for j in range(n)],
                    }
                },
            }
        )
    manifest = Manifest.from_yaml(
        profile(opportunity_id=10042, user_visits=visits, user_data=[], opportunity_detail={"name": "Repeat Opp"})
    )
    assert manifest.beneficiary_cohorts[0].repeat_groups, "profiler should capture the repeat group"

    schema = FormSchema(questions=[QuestionSpec("form.mother_age", "int")])
    out = generate(manifest=manifest, opportunity_detail={"name": "Repeat Opp"}, form_schema=schema)
    sampled = [v for v in out["user_visits"] if "children" in v["form_json"].get("form", {})]
    assert sampled, "generated visits should carry the children repeat"
    for v in sampled:
        kids = v["form_json"]["form"]["children"]
        assert isinstance(kids, list)
        for el in kids:
            assert isinstance(el, dict) and "weight" in el


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


def test_profile_mirror_emits_transplant_pool_with_persona_owners():
    """mirror=True makes the profiler emit a longitudinal transplant pool whose
    owners are persona ids (not raw source usernames) so the engine can replay
    the exact source structure (issue #713 #2)."""
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import profile

    # 'asha' runs the most visits (-> flw_001); each entity has a rising weight series.
    visits = []
    for e in range(6):
        owner = "asha" if e < 4 else "ben"
        for i, d in enumerate(["2026-01-01", "2026-01-08", "2026-01-15"]):
            visits.append(
                {
                    "username": owner,
                    "visit_date": d,
                    "status": "approved",
                    "flagged": False,
                    "entity_id": f"ent_{e}",
                    "form_json": {"form": {"weight": 1200.0 + 100 * i + e}},
                }
            )
    app_structure = {
        "learn_app": None,
        "deliver_app": {
            "modules": [{"forms": [{"questions": [{"value": "/data/weight", "type": "Decimal", "options": []}]}]}]
        },
    }

    m = Manifest.from_yaml(
        profile(
            opportunity_id=10003,
            user_visits=visits,
            user_data=[],
            opportunity_detail={"name": "KMC"},
            app_structure=app_structure,
            mirror=True,
        )
    )
    lng = m.beneficiary_cohorts[0].longitudinal
    assert lng is not None and lng.mode == "mirror"
    assert len(lng.transplant_pool) == 6  # one series per source entity
    persona_ids = {p.id for p in m.flw_personas}
    owners = {s["owner"] for s in lng.transplant_pool}
    assert owners <= persona_ids  # remapped to persona ids, not "asha"/"ben"


def test_profile_mirror_captures_date_fields_in_transplant_pool():
    """A date-typed schema field (e.g. child_dob) is carried in the mirror pool as a
    per-visit day-offset, so a clone reconstructs the age axis (visit_date - dob)
    instead of fabricating a random date — the #734 growth-curve fix."""
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import profile

    visits = []
    for e in range(3):
        for i, d in enumerate(["2026-02-01", "2026-02-08", "2026-02-15"]):
            visits.append(
                {
                    "username": "asha",
                    "visit_date": d,
                    "status": "approved",
                    "flagged": False,
                    "entity_id": f"ent_{e}",
                    "form_json": {"form": {"weight": 1200.0 + 100 * i, "dob": "2026-01-20"}},
                }
            )
    app_structure = {
        "learn_app": None,
        "deliver_app": {
            "modules": [
                {
                    "forms": [
                        {
                            "questions": [
                                {"value": "/data/weight", "type": "Decimal", "options": []},
                                {"value": "/data/dob", "type": "Date", "options": []},
                            ]
                        }
                    ]
                }
            ]
        },
    }

    m = Manifest.from_yaml(
        profile(
            opportunity_id=10009,
            user_visits=visits,
            user_data=[],
            opportunity_detail={"name": "KMC"},
            app_structure=app_structure,
            mirror=True,
        )
    )
    pool = m.beneficiary_cohorts[0].longitudinal.transplant_pool
    assert len(pool) == 3
    for series in pool:
        for v in series["visits"]:
            # 2026-01-20 is 12 days before the 2026-02-01 first visit.
            assert v["dates"]["form.dob"] == -12


def test_profile_without_mirror_emits_no_longitudinal_block():
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import profile

    visits = [
        {
            "username": "asha",
            "visit_date": f"2026-01-{d:02d}",
            "status": "approved",
            "entity_id": f"ent_{d}",
            "form_json": {"form": {"weight": 1200.0 + d}},
        }
        for d in range(1, 11)
    ]
    m = Manifest.from_yaml(
        profile(opportunity_id=10004, user_visits=visits, user_data=[], opportunity_detail={"name": "X"})
    )
    assert m.beneficiary_cohorts[0].longitudinal is None  # default unchanged
