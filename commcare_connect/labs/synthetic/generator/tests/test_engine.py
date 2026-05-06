import json
from pathlib import Path

from commcare_connect.labs.synthetic.generator.engine import generate
from commcare_connect.labs.synthetic.generator.manifest import Manifest
from commcare_connect.labs.synthetic.generator.schema_loader import FormSchema, QuestionSpec

GOLDEN = Path(__file__).parent / "golden"


def _load_inputs():
    manifest = Manifest.from_yaml((GOLDEN / "manifest.yaml").read_text())
    detail = json.loads((GOLDEN / "opportunity_detail.json").read_text())
    schema_data = json.loads((GOLDEN / "form_schema.json").read_text())
    schema = FormSchema(questions=[QuestionSpec(**q) for q in schema_data["questions"]])
    return manifest, detail, schema


def test_generate_returns_all_five_endpoints():
    manifest, detail, schema = _load_inputs()
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    assert set(out.keys()) == {
        "opportunity",
        "user_visits",
        "user_data",
        "completed_works",
        "completed_module",
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
