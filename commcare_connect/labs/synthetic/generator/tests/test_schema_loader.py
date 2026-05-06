from unittest.mock import MagicMock

from commcare_connect.labs.synthetic.generator.schema_loader import (
    FormSchema,
    load_form_schema,
    parse_form_schema_from_app_json,
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


def test_parse_form_schema_from_app_json_extracts_deliver_form():
    app_json = {
        "deliver_app": {
            "modules": [
                {
                    "case_type": "vendor",
                    "forms": [
                        {
                            "name": "Vendor Visit",
                            "questions": [
                                {"value": "/data/weight_kg", "type": "Decimal", "options": []},
                                {
                                    "value": "/data/kmc_status",
                                    "type": "Select",
                                    "options": [{"value": "active"}, {"value": "inactive"}],
                                },
                            ],
                        }
                    ],
                }
            ]
        },
        "learn_app": None,
    }
    schema = parse_form_schema_from_app_json(app_json, app_type="deliver")
    assert len(schema.questions) == 2
    assert schema.questions[0].json_path == "form.weight_kg"
    assert schema.questions[0].kind == "decimal"
    assert schema.questions[1].kind == "select"
    assert schema.questions[1].choices == ["active", "inactive"]


def test_parse_form_schema_from_app_json_handles_missing_app():
    assert parse_form_schema_from_app_json({"deliver_app": None}).questions == []
    assert parse_form_schema_from_app_json({}, app_type="deliver").questions == []
    assert parse_form_schema_from_app_json("not-a-dict").questions == []


def test_parse_form_schema_from_app_json_descends_into_groups():
    app_json = {
        "deliver_app": {
            "modules": [
                {
                    "forms": [
                        {
                            "name": "Form",
                            "questions": [
                                {
                                    "value": "/data/group",
                                    "type": "Group",
                                    "children": [
                                        {"value": "/data/group/inner_weight", "type": "Decimal"},
                                        {"value": "/data/group/inner_note", "type": "Text"},
                                    ],
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    }
    schema = parse_form_schema_from_app_json(app_json)
    paths = [q.json_path for q in schema.questions]
    assert paths == ["form.group.inner_weight", "form.group.inner_note"]
    assert schema.questions[0].kind == "decimal"
    assert schema.questions[1].kind == "text"


def test_parse_form_schema_from_app_json_picks_app_type():
    app_json = {
        "learn_app": {"modules": [{"forms": [{"questions": [{"value": "/data/learn_q", "type": "Text"}]}]}]},
        "deliver_app": {"modules": [{"forms": [{"questions": [{"value": "/data/deliver_q", "type": "Decimal"}]}]}]},
    }
    learn = parse_form_schema_from_app_json(app_json, app_type="learn")
    assert learn.questions[0].json_path == "form.learn_q"
    deliver = parse_form_schema_from_app_json(app_json, app_type="deliver")
    assert deliver.questions[0].json_path == "form.deliver_q"


def test_parse_form_schema_from_app_json_skips_questions_without_value():
    app_json = {
        "deliver_app": {
            "modules": [
                {
                    "forms": [
                        {
                            "questions": [
                                {"type": "Decimal"},
                                {"value": "/data/real", "type": "Decimal"},
                            ]
                        }
                    ]
                }
            ]
        }
    }
    schema = parse_form_schema_from_app_json(app_json)
    assert [q.json_path for q in schema.questions] == ["form.real"]
