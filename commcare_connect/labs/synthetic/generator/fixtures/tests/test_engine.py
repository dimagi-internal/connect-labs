import datetime as dt
import json
from pathlib import Path

from commcare_connect.labs.synthetic.generator.fixtures.engine import generate
from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest
from commcare_connect.labs.synthetic.generator.fixtures.schema_loader import FormSchema, QuestionSpec

GOLDEN = Path(__file__).parent / "golden"


def _load_inputs():
    manifest = Manifest.from_yaml((GOLDEN / "manifest.yaml").read_text())
    detail = json.loads((GOLDEN / "opportunity_detail.json").read_text())
    schema_data = json.loads((GOLDEN / "form_schema.json").read_text())
    schema = FormSchema(questions=[QuestionSpec(**q) for q in schema_data["questions"]])
    return manifest, detail, schema


def test_generate_returns_all_endpoints():
    manifest, detail, schema = _load_inputs()
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    assert set(out.keys()) == {
        "opportunity",
        "user_visits",
        "user_data",
        "completed_works",
        "completed_module",
        "task_records",
        "app_structure",
    }


def test_generate_is_deterministic_under_seed():
    manifest, detail, schema = _load_inputs()
    a = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    b = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _mirror_inputs(pool, *, jitter_frac=0.0, weight_kind="int"):
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import (
        BeneficiaryCohort,
        FlwPersona,
        KpiSpec,
        LongitudinalSpec,
        MeanStddev,
        NormalDistribution,
        Timeline,
    )

    manifest = Manifest(
        opportunity_id=10007,
        opportunity_name="KMC Mirror",
        random_seed=42,
        timeline=Timeline(
            start_date=dt.date(2026, 1, 1),
            end_date=dt.date(2026, 3, 26),
            weeks=12,
            visit_cadence_per_week_per_flw=MeanStddev(mean=8, stddev=2),
        ),
        flw_personas=[
            FlwPersona(
                id="flw_001",
                archetype="steady",
                accuracy_distribution=MeanStddev(mean=0.9, stddev=0.05),
                completeness_distribution=MeanStddev(mean=0.95, stddev=0.03),
                flag_rate=0.05,
            )
        ],
        beneficiary_cohorts=[
            BeneficiaryCohort(
                id="primary",
                size=1,
                field_distributions={"form.weight": NormalDistribution(mean=1300, stddev=200, lo=900, hi=1600)},
                progression="improvement_curve",
                longitudinal=LongitudinalSpec(mode="mirror", jitter_frac=jitter_frac, transplant_pool=pool),
            )
        ],
        kpi_config=[KpiSpec(kpi="w", field_path="form.weight", aggregation="mean", threshold_underperform=1000)],
    )
    schema = FormSchema(questions=[QuestionSpec(json_path="form.weight", kind=weight_kind)])
    return manifest, {"name": "KMC Mirror"}, schema


def test_mirror_mode_replays_a_series_as_one_stable_rising_entity():
    pool = [
        {
            "owner": "flw_001",
            "start_date": "2026-01-01",
            "visits": [
                {"day": 0, "values": {"form.weight": 1200.0}},
                {"day": 7, "values": {"form.weight": 1300.0}},
                {"day": 14, "values": {"form.weight": 1400.0}},
            ],
        }
    ]
    manifest, detail, schema = _mirror_inputs(pool)  # jitter 0 -> exact replay

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    visits = sorted(out["user_visits"], key=lambda v: v["visit_date"])

    assert len(visits) == 3
    assert len({v["entity_id"] for v in visits}) == 1  # stable entity across its visits
    assert len({v["entity_name"] for v in visits}) == 1
    assert [v["visit_date"] for v in visits] == ["2026-01-01", "2026-01-08", "2026-01-15"]
    assert [v["username"] for v in visits] == ["flw_001", "flw_001", "flw_001"]
    weights = [v["form_json"]["form"]["weight"] for v in visits]
    assert weights == [1200, 1300, 1400]  # rises with age, exact (no jitter), int-cast to the schema kind


def test_mirror_jitter_keeps_values_inside_the_cases_own_range():
    pool = [
        {
            "owner": "flw_001",
            "start_date": "2026-01-01",
            "visits": [
                {"day": 0, "values": {"form.weight": 1200.0}},
                {"day": 7, "values": {"form.weight": 1300.0}},
                {"day": 14, "values": {"form.weight": 1400.0}},
            ],
        }
    ]
    manifest, detail, schema = _mirror_inputs(pool, jitter_frac=0.1, weight_kind="decimal")

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    weights = [v["form_json"]["form"]["weight"] for v in out["user_visits"]]

    assert all(1200.0 <= w <= 1400.0 for w in weights), weights  # never escapes this case's [min,max]


def test_mirror_reproduces_cases_per_flw_exactly():
    # flw_001 owns two cases, flw_002 owns one -> clone must reproduce that split.
    pool = [
        {"owner": "flw_001", "start_date": "2026-01-01", "visits": [{"day": 0, "values": {"form.weight": 1200.0}}]},
        {"owner": "flw_001", "start_date": "2026-01-02", "visits": [{"day": 0, "values": {"form.weight": 1250.0}}]},
        {"owner": "flw_002", "start_date": "2026-01-03", "visits": [{"day": 0, "values": {"form.weight": 1300.0}}]},
    ]
    manifest, detail, schema = _mirror_inputs(pool)
    # add the second persona the pool references
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import FlwPersona, MeanStddev

    manifest.flw_personas.append(
        FlwPersona(
            id="flw_002",
            archetype="steady",
            accuracy_distribution=MeanStddev(mean=0.9, stddev=0.05),
            completeness_distribution=MeanStddev(mean=0.95, stddev=0.03),
            flag_rate=0.05,
        )
    )

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    cases_per_flw = {}
    for v in out["user_visits"]:
        cases_per_flw.setdefault(v["username"], set()).add(v["entity_id"])

    assert {k: len(s) for k, s in cases_per_flw.items()} == {"flw_001": 2, "flw_002": 1}


def test_mirror_replays_date_leaves_as_stable_reconstructed_dates():
    # A constant per-child DOB is carried as a day-offset; the clone reconstructs
    # it as (first_visit + offset), identical across the child's visits, so the
    # age axis (visit_date - dob) is faithful instead of a randomly fabricated date.
    pool = [
        {
            "owner": "flw_001",
            "start_date": "2026-02-01",
            "visits": [
                {"day": 0, "values": {"form.weight": 1200.0}, "dates": {"form.dob": -10}},
                {"day": 7, "values": {"form.weight": 1300.0}, "dates": {"form.dob": -10}},
                {"day": 14, "values": {"form.weight": 1400.0}, "dates": {"form.dob": -10}},
            ],
        }
    ]
    manifest, detail, _schema = _mirror_inputs(pool)
    schema = FormSchema(
        questions=[
            QuestionSpec(json_path="form.weight", kind="int"),
            QuestionSpec(json_path="form.dob", kind="date"),
        ]
    )

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    visits = sorted(out["user_visits"], key=lambda v: v["visit_date"])

    # 2026-02-01 minus 10 days = 2026-01-22, the SAME reconstructed DOB every visit.
    assert {v["form_json"]["form"]["dob"] for v in visits} == {"2026-01-22"}
    ages = [(dt.date.fromisoformat(v["visit_date"]) - dt.date(2026, 1, 22)).days for v in visits]
    weights = [v["form_json"]["form"]["weight"] for v in visits]
    assert ages == sorted(ages) and ages[0] < ages[-1]  # age axis climbs
    assert weights == [1200, 1300, 1400]  # weight climbs with it


def test_mirror_round_trips_a_climbing_growth_curve():
    # Six children, each with a constant DOB + birthweight and a weight that climbs
    # with age. The faithful clone must keep corr(weight, age) strongly positive
    # AND each child's own series must rise — the issue's definition of done.
    import statistics

    pool = []
    for c in range(6):
        birthweight = 1000.0 + 25 * c  # one tight band (the issue validates per 250 g band)
        start = dt.date(2026, 3, 1) + dt.timedelta(days=c)  # staggered enrollment
        series = []
        for wk in range(6):
            day = wk * 7
            age_days = 3 + day  # born 3 days before the first visit
            series.append(
                {"day": day, "values": {"form.weight": birthweight + 18.0 * age_days}, "dates": {"form.dob": -3}}
            )
        pool.append({"owner": "flw_001", "start_date": start.isoformat(), "visits": series})

    manifest, detail, _schema = _mirror_inputs(pool)
    schema = FormSchema(
        questions=[
            QuestionSpec(json_path="form.weight", kind="decimal"),
            QuestionSpec(json_path="form.dob", kind="date"),
        ]
    )

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)

    rows: list[tuple[int, float]] = []
    by_entity: dict[str, list[tuple[int, float]]] = {}
    for v in out["user_visits"]:
        dob = dt.date.fromisoformat(v["form_json"]["form"]["dob"])
        age = (dt.date.fromisoformat(v["visit_date"]) - dob).days
        w = v["form_json"]["form"]["weight"]
        rows.append((age, w))
        by_entity.setdefault(v["entity_id"], []).append((age, w))

    ages = [a for a, _ in rows]
    ws = [w for _, w in rows]
    cov = sum((a - statistics.mean(ages)) * (w - statistics.mean(ws)) for a, w in rows) / len(rows)
    corr = cov / (statistics.pstdev(ages) * statistics.pstdev(ws))
    assert corr > 0.8, corr  # weight climbs with age across the population

    assert len(by_entity) == 6
    for series in by_entity.values():
        series.sort()
        series_weights = [w for _, w in series]
        assert series_weights == sorted(series_weights) and series_weights[-1] > series_weights[0]


def test_mirror_applies_seeded_field_outlier_and_duplicate_anomalies():
    # Faithful replay must still honor seeded QA anomalies, so a curated mirror
    # clone keeps the signal audits/evals are meant to find (no regression when
    # mirror became the clone default in #734).
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import Anomaly

    pool = [
        {
            "owner": "flw_001",
            "start_date": "2026-01-01",
            "visits": [
                {"day": 0, "values": {"form.weight": 1200.0}},  # week 1 -> targeted
                {"day": 7, "values": {"form.weight": 1300.0}},  # week 2 -> untouched
            ],
        }
    ]
    manifest, detail, schema = _mirror_inputs(pool)  # cohort weight ~ N(1300, 200), timeline starts 2026-01-01
    manifest.anomalies = [
        Anomaly(id="o1", type="field_outlier", flw_ids=["flw_001"], field_path="form.weight", week=1),
        Anomaly(id="d1", type="duplicate_submission", flw_ids=["flw_001"], week=1),
    ]

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    visits = out["user_visits"]

    # The duplicate_submission seeds exactly one extra flagged "already visited" copy.
    dups = [v for v in visits if v.get("flag_reason") == "Beneficiary already visited this week"]
    assert len(dups) == 1

    # The week-1 visit's weight is a >= 4-sigma outlier (mean 1300, std 200 -> outside [1100, 1500]),
    # while the untouched week-2 visit keeps its faithful transplanted value.
    by_date = {}
    for v in visits:
        if not v.get("flagged") or v.get("flag_reason") != "Beneficiary already visited this week":
            by_date.setdefault(v["visit_date"], v["form_json"]["form"]["weight"])
    assert by_date["2026-01-08"] == 1300  # faithful, untouched
    w0 = by_date["2026-01-01"]
    assert w0 < 600 or w0 > 2000, w0  # the seeded outlier landed on the week-1 visit


def test_synthetic_trajectory_gives_each_entity_a_rising_stable_series():
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import (
        BeneficiaryCohort,
        FlwPersona,
        KpiSpec,
        LongitudinalSpec,
        MeanStddev,
        NormalDistribution,
        Timeline,
        TrajectoryParams,
    )

    manifest = Manifest(
        opportunity_id=10008,
        opportunity_name="KMC Synthetic",
        random_seed=7,
        timeline=Timeline(
            start_date=dt.date(2026, 1, 1),
            end_date=dt.date(2026, 3, 26),
            weeks=12,
            visit_cadence_per_week_per_flw=MeanStddev(mean=20, stddev=2),
        ),
        flw_personas=[
            FlwPersona(
                id="flw_001",
                archetype="steady",
                accuracy_distribution=MeanStddev(mean=0.9, stddev=0.05),
                completeness_distribution=MeanStddev(mean=0.95, stddev=0.03),
                flag_rate=0.0,
            )
        ],
        beneficiary_cohorts=[
            BeneficiaryCohort(
                id="primary",
                size=4,  # small cohort + many visits -> entities get repeat visits
                field_distributions={"form.weight": NormalDistribution(mean=1500, stddev=400, lo=900, hi=4000)},
                progression="improvement_curve",
                longitudinal=LongitudinalSpec(
                    mode="synthetic",
                    fields={
                        "form.weight": TrajectoryParams(
                            model="trajectory",
                            intercept=MeanStddev(mean=1200, stddev=80),
                            slope=MeanStddev(mean=20, stddev=2),  # ~20 g/day
                            residual_std=2.0,  # << weekly gain, so the series rises monotonically
                            x_axis="day",
                        )
                    },
                ),
            )
        ],
        kpi_config=[KpiSpec(kpi="w", field_path="form.weight", aggregation="mean", threshold_underperform=1000)],
    )
    schema = FormSchema(questions=[QuestionSpec(json_path="form.weight", kind="decimal")])

    out = generate(manifest=manifest, opportunity_detail={"name": "KMC Synthetic"}, form_schema=schema)

    by_entity = {}
    for v in out["user_visits"]:
        by_entity.setdefault(v["entity_id"], []).append(v)

    multi = [vs for vs in by_entity.values() if len(vs) >= 3]
    assert multi, "expected entities with repeat visits"
    for vs in multi:
        # Weight rises with the child's AGE (day offset). Collapse any same-day
        # visits (identical age) since within-day order isn't trajectory-meaningful.
        weight_by_day = {}
        for v in vs:
            weight_by_day[v["visit_date"]] = v["form_json"]["form"]["weight"]
        weights = [weight_by_day[d] for d in sorted(weight_by_day)]
        assert weights == sorted(weights), weights  # monotonic across the child's visit days
        assert weights[-1] > weights[0]


def test_generate_visits_carry_required_fields():
    manifest, detail, schema = _load_inputs()
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    visits = out["user_visits"]
    assert len(visits) > 0
    v = visits[0]
    for key in (
        "id",
        "username",
        "visit_date",
        "status",
        "form_json",
        "deliver_unit_id",
        "opportunity_id",
    ):
        assert key in v, f"missing key {key} in visit"


def test_generate_user_data_matches_personas():
    manifest, detail, schema = _load_inputs()
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    usernames = {u["username"] for u in out["user_data"]}
    assert usernames == {"asha", "ravi"}


def test_generate_with_image_config():
    """Visits whose form_json has a MUAC field should have images assigned."""
    base_yaml = (GOLDEN / "manifest.yaml").read_text()
    # Add a MUAC field distribution so visits get a muac value in form_json,
    # and add image_config so assign_visit_images is called.
    muac_patch = "image_config:\n" "  probability: 1.0\n" "  stock_image_count: 5\n"
    # Add muac field distribution to the cohort.
    muac_field = "      'form.case.update.soliciter_muac_cm': { distribution: normal, mean: 13.5, stddev: 0.3 }\n"
    patched = base_yaml + muac_patch
    patched = patched.replace(
        "      'form.weight_kg': { distribution: normal, mean: 12.4, stddev: 0.5 }\n",
        "      'form.weight_kg': { distribution: normal, mean: 12.4, stddev: 0.5 }\n" + muac_field,
    )
    manifest = Manifest.from_yaml(patched)
    detail = json.loads((GOLDEN / "opportunity_detail.json").read_text())
    schema_data = json.loads((GOLDEN / "form_schema.json").read_text())
    schema = FormSchema(questions=[QuestionSpec(**q) for q in schema_data["questions"]])

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    visits_with_images = [v for v in out["user_visits"] if v["images"]]
    assert len(visits_with_images) > 0, "expected at least one visit with images"
    for v in visits_with_images:
        assert len(v["images"]) == 1
        blob_id = v["images"][0]["blob_id"]
        assert blob_id.startswith("synth-muac-"), f"unexpected blob_id: {blob_id}"


def test_generate_produces_task_records():
    """A manifest with one task should produce one task_record entry."""
    base_yaml = (GOLDEN / "manifest.yaml").read_text()
    task_yaml = (
        "tasks:\n"
        "  - flw_id: asha\n"
        "    title: Follow up on low-weight child\n"
        "    priority: high\n"
        "    status: pending\n"
        "    created_week: 1\n"
    )
    patched = base_yaml + task_yaml
    manifest = Manifest.from_yaml(patched)
    detail = json.loads((GOLDEN / "opportunity_detail.json").read_text())
    schema_data = json.loads((GOLDEN / "form_schema.json").read_text())
    schema = FormSchema(questions=[QuestionSpec(**q) for q in schema_data["questions"]])

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    assert "task_records" in out
    assert len(out["task_records"]) == 1
    record = out["task_records"][0]
    assert record["title"] == "Follow up on low-weight child"
    assert record["assigned_to"] == "asha"


def test_generate_flags_visits_when_anomaly_scheduled(tmp_path):
    """An anomaly scheduled at a specific week should produce flagged visits."""
    base_yaml = (GOLDEN / "manifest.yaml").read_text()
    # Insert an anomaly targeting Asha during week 2 on form.weight_kg.
    anomaly_yaml = (
        "anomalies:\n"
        "  - id: weight_outlier\n"
        "    type: field_outlier\n"
        "    flw_ids: [asha]\n"
        "    field_path: form.weight_kg\n"
        "    week: 2\n"
    )
    patched = base_yaml.replace("anomalies: []", anomaly_yaml)
    manifest = Manifest.from_yaml(patched)
    detail = json.loads((GOLDEN / "opportunity_detail.json").read_text())
    schema_data = json.loads((GOLDEN / "form_schema.json").read_text())
    schema = FormSchema(questions=[QuestionSpec(**q) for q in schema_data["questions"]])

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    flagged = [v for v in out["user_visits"] if v["flagged"]]
    assert len(flagged) > 0, "expected at least one flagged visit from anomaly"
    # Asha's week-2 visits should be the flagged ones.
    asha_flagged = [v for v in flagged if v["username"] == "asha"]
    assert len(asha_flagged) > 0
    for v in asha_flagged:
        assert v["status"] in {"pending", "rejected"}
        assert v["flag_reason"]


def _in_week2(visit_date: str) -> bool:
    """Golden timeline starts 2026-02-01, so week 2 spans 02-08..02-14 (1-based)."""
    d = dt.date.fromisoformat(visit_date)
    return dt.date(2026, 2, 8) <= d <= dt.date(2026, 2, 14)


def _patched(anomaly_yaml: str):
    base_yaml = (GOLDEN / "manifest.yaml").read_text()
    manifest = Manifest.from_yaml(base_yaml.replace("anomalies: []", anomaly_yaml))
    detail = json.loads((GOLDEN / "opportunity_detail.json").read_text())
    schema_data = json.loads((GOLDEN / "form_schema.json").read_text())
    schema = FormSchema(questions=[QuestionSpec(**q) for q in schema_data["questions"]])
    base = Manifest.from_yaml(base_yaml)
    return manifest, base, detail, schema


def test_generate_missing_visits_drops_targeted_flw_week():
    """A missing_visits anomaly removes the targeted FLW's visits for that week,
    leaving a detectable coverage gap; other FLWs and other weeks are untouched."""
    anomaly_yaml = (
        "anomalies:\n" "  - id: asha_gap\n" "    type: missing_visits\n" "    flw_ids: [asha]\n" "    week: 2\n"
    )
    manifest, base, detail, schema = _patched(anomaly_yaml)

    base_out = generate(manifest=base, opportunity_detail=detail, form_schema=schema)
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)

    base_asha_w2 = [v for v in base_out["user_visits"] if v["username"] == "asha" and _in_week2(v["visit_date"])]
    assert len(base_asha_w2) > 0, "baseline should have asha week-2 visits to remove"

    asha_w2 = [v for v in out["user_visits"] if v["username"] == "asha" and _in_week2(v["visit_date"])]
    assert asha_w2 == [], "missing_visits should drop all of asha's week-2 visits"

    asha_other = [v for v in out["user_visits"] if v["username"] == "asha" and not _in_week2(v["visit_date"])]
    assert len(asha_other) > 0, "asha's other weeks must remain"

    ravi_w2 = [v for v in out["user_visits"] if v["username"] == "ravi" and _in_week2(v["visit_date"])]
    assert len(ravi_w2) > 0, "an untargeted FLW must keep its week-2 visits"


def test_generate_duplicate_submission_creates_near_identical_pair():
    """A duplicate_submission anomaly emits a second visit for the same beneficiary
    with identical form_json on the same date but a distinct id."""
    anomaly_yaml = (
        "anomalies:\n" "  - id: asha_dup\n" "    type: duplicate_submission\n" "    flw_ids: [asha]\n" "    week: 2\n"
    )
    manifest, base, detail, schema = _patched(anomaly_yaml)

    base_out = generate(manifest=base, opportunity_detail=detail, form_schema=schema)
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    assert len(out["user_visits"]) > len(base_out["user_visits"]), "duplicate should add a visit"

    by_entity: dict[str, list] = {}
    for v in out["user_visits"]:
        if v["username"] == "asha":
            by_entity.setdefault(v["entity_id"], []).append(v)
    dup_groups = [vs for vs in by_entity.values() if len(vs) >= 2]
    assert dup_groups, "expected a duplicated submission sharing one entity_id"

    pair = dup_groups[0]
    assert pair[0]["form_json"] == pair[1]["form_json"], "duplicate must carry identical form_json"
    assert pair[0]["visit_date"] == pair[1]["visit_date"], "duplicate must share the visit date"
    assert pair[0]["id"] != pair[1]["id"], "duplicate must have a distinct visit id"


def test_duplicate_submission_flags_only_the_duplicate_not_the_original():
    """asha is a rockstar (flag_rate 0): without the anomaly every visit is approved.
    The dedup event must flag the injected copy, not the genuine original."""
    anomaly_yaml = (
        "anomalies:\n" "  - id: asha_dup\n" "    type: duplicate_submission\n" "    flw_ids: [asha]\n" "    week: 2\n"
    )
    manifest, base, detail, schema = _patched(anomaly_yaml)
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)

    asha = [v for v in out["user_visits"] if v["username"] == "asha"]
    flagged = [v for v in asha if v["flagged"]]
    approved = [v for v in asha if v["status"] == "approved" and not v["flagged"]]
    assert flagged, "the duplicate should be flagged"
    approved_entities = {v["entity_id"] for v in approved}
    for v in flagged:
        assert v["entity_id"] in approved_entities, "a flagged dup must shadow an un-flagged original (same entity)"


def test_generate_without_geography_leaves_location_empty():
    """The default manifest (no geography) keeps visit location blank."""
    manifest, detail, schema = _load_inputs()
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    assert all(v["location"] == "" for v in out["user_visits"])


def test_generate_with_geography_places_visits_in_polygon():
    """A geography block scatters visit GPS inside the polygon, one fixed household
    point per beneficiary, formatted as a CommCare packed 'lat lon alt acc' string."""
    from shapely.geometry import Point, shape

    base_yaml = (GOLDEN / "manifest.yaml").read_text()
    # A small square around Madobi (Kano): lon 8.30-8.40, lat 11.78-11.88.
    geo_yaml = (
        "geography:\n"
        "  settlements: 4\n"
        "  settlement_spread_km: 0.8\n"
        "  polygon:\n"
        "    type: Polygon\n"
        "    coordinates:\n"
        "      - - [8.30, 11.78]\n"
        "        - [8.40, 11.78]\n"
        "        - [8.40, 11.88]\n"
        "        - [8.30, 11.88]\n"
        "        - [8.30, 11.78]\n"
    )
    manifest = Manifest.from_yaml(base_yaml + geo_yaml)
    detail = json.loads((GOLDEN / "opportunity_detail.json").read_text())
    schema_data = json.loads((GOLDEN / "form_schema.json").read_text())
    schema = FormSchema(questions=[QuestionSpec(**q) for q in schema_data["questions"]])

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    visits = out["user_visits"]
    assert len(visits) > 0

    poly = shape(manifest.geography.polygon)
    seen_points = set()
    for v in visits:
        assert v["location"], "geography visit should carry a non-empty location"
        parts = v["location"].split()
        assert len(parts) == 4, f"packed location should be 'lat lon alt acc', got {v['location']!r}"
        lat, lon = float(parts[0]), float(parts[1])
        assert poly.contains(Point(lon, lat)), f"visit GPS {lon},{lat} fell outside the polygon"
        seen_points.add((v["entity_name"], lon, lat))

    # Repeat visits to the same beneficiary stack at the same household point.
    by_name = {}
    for name, lon, lat in seen_points:
        by_name.setdefault(name, set()).add((round(lon, 6), round(lat, 6)))
    assert all(len(pts) == 1 for pts in by_name.values()), "each beneficiary should have one fixed household location"

    # Determinism: same seed → identical locations.
    out2 = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    assert [v["location"] for v in out2["user_visits"]] == [v["location"] for v in visits]


def test_curated_profile_seeds_materialize_as_qa_signals_end_to_end():
    """Composition guard (the part mocked unit tests miss): profile(curate=True)'s
    seeded anomalies must survive the manifest round-trip and produce real QA signals
    when generated — a flagged visit (outlier/dedup) and a duplicated-entity pair."""
    from commcare_connect.labs.synthetic.generator.fixtures.profiler import profile
    from commcare_connect.labs.synthetic.generator.fixtures.schema_loader import parse_form_schema_from_app_json

    visits = []
    for i in range(120):
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
    app_structure = {
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
    manifest = Manifest.from_yaml(
        profile(
            opportunity_id=874,
            user_visits=visits,
            user_data=[],
            opportunity_detail={"name": "KMC X"},
            app_structure=app_structure,
            curate=True,
        )
    )
    assert manifest.anomalies, "curated manifest should carry seeded anomalies"
    schema = parse_form_schema_from_app_json(app_structure, app_type="deliver")
    out = generate(manifest=manifest, opportunity_detail={"name": "KMC X"}, form_schema=schema)

    gen = out["user_visits"]
    assert any(v["flagged"] for v in gen), "seeded outlier/dedup anomalies should flag at least one visit"

    by_entity: dict[str, int] = {}
    for v in gen:
        by_entity[v["entity_id"]] = by_entity.get(v["entity_id"], 0) + 1
    assert any(c >= 2 for c in by_entity.values()), "the seeded duplicate_submission should yield a shared-entity pair"


def test_generate_threads_app_structure_and_hour_distribution():
    # reuse the golden manifest/detail/schema loaders already used by test_engine
    manifest, detail, schema = _load_inputs()
    app_structure = {"learn_app": None, "deliver_app": {"modules": []}}
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema, app_structure=app_structure)
    assert out["app_structure"] == app_structure
    assert "user_visits" in out


def test_geography_gps_lands_where_the_service_delivery_pipeline_reads_it():
    """Integration guard: the service-delivery GPS pipeline reads device location
    from form_json.metadata.location, NOT the top-level `location` field. A geography
    visit must mirror its GPS into metadata.location, or the map overlay shows
    0% with-GPS (the exact bug that shipped opp 10007 with 333 visits and no points).
    """
    from commcare_connect.microplans.service_delivery.points import _parse_packed_location

    base_yaml = (GOLDEN / "manifest.yaml").read_text()
    geo_yaml = (
        "geography:\n"
        "  settlements: 4\n"
        "  settlement_spread_km: 0.8\n"
        "  polygon:\n"
        "    type: Polygon\n"
        "    coordinates:\n"
        "      - - [8.30, 11.78]\n"
        "        - [8.40, 11.78]\n"
        "        - [8.40, 11.88]\n"
        "        - [8.30, 11.88]\n"
        "        - [8.30, 11.78]\n"
    )
    manifest = Manifest.from_yaml(base_yaml + geo_yaml)
    detail = json.loads((GOLDEN / "opportunity_detail.json").read_text())
    schema_data = json.loads((GOLDEN / "form_schema.json").read_text())
    schema = FormSchema(questions=[QuestionSpec(**q) for q in schema_data["questions"]])

    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    visits = out["user_visits"]
    assert visits

    for v in visits:
        meta_loc = v["form_json"].get("metadata", {}).get("location")
        assert meta_loc, "GPS must be mirrored into form_json.metadata.location for the SD pipeline"
        assert meta_loc == v["location"], "metadata.location must match the top-level packed location"
        # The pipeline's own parser must read it as a valid (lon, lat).
        parsed = _parse_packed_location(meta_loc)
        assert parsed is not None, f"SD pipeline could not parse {meta_loc!r}"
        lon, lat = parsed
        assert 8.30 <= lon <= 8.40 and 11.78 <= lat <= 11.88
