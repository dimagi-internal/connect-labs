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
