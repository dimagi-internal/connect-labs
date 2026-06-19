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
