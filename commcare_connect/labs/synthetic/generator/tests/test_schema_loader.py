from unittest.mock import MagicMock

from commcare_connect.labs.synthetic.generator.schema_loader import (
    FormSchema,
    QuestionSpec,
    load_form_schema,
)


def test_load_form_schema_collects_question_specs():
    """Schema loader returns one QuestionSpec per leaf question with a JSON path."""
    fake_hq_response = {
        "forms": [
            {
                "name": "Visit",
                "questions": [
                    {"value": "/data/weight_kg", "type": "Decimal", "options": []},
                    {
                        "value": "/data/kmc_status",
                        "type": "Select",
                        "options": [
                            {"value": "active"},
                            {"value": "inactive"},
                        ],
                    },
                ],
            }
        ]
    }
    api = MagicMock()
    api.get_form_json_paths.return_value = fake_hq_response

    schema = load_form_schema(api, app_id="app-123", form_xmlns="form-456")

    assert isinstance(schema, FormSchema)
    assert len(schema.questions) == 2
    weight = schema.questions[0]
    assert weight.json_path == "form.weight_kg"
    assert weight.kind == "decimal"
    assert weight.choices == []
    status = schema.questions[1]
    assert status.choices == ["active", "inactive"]
    assert status.kind == "select"


def test_load_form_schema_handles_empty_response():
    api = MagicMock()
    api.get_form_json_paths.return_value = {"forms": []}
    schema = load_form_schema(api, app_id="x", form_xmlns="y")
    assert schema.questions == []
