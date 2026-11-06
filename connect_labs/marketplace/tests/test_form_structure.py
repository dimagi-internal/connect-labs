"""A round's questions take their Google Form's structure (Forms API ``forms.get``).

The fixture follows the documented Forms API shape; every title is invented.
"""

from connect_labs.marketplace import form_structure as fs

FORM = {
    "formId": "edit-id-1",
    "responderUri": "https://docs.google.com/forms/d/e/PUBLIC123/viewform",
    "linkedSheetId": "sheet-1",
    "items": [
        {"itemId": "s1", "title": "Identity", "textItem": {}},
        {
            "itemId": "a",
            "title": "Organisation name",
            "questionItem": {"question": {"required": True, "textQuestion": {}}},
        },
        {
            "itemId": "b",
            "title": "Which describes you? ",
            "description": "Tick all that apply.",
            "questionItem": {
                "question": {
                    "choiceQuestion": {
                        "type": "CHECKBOX",
                        "options": [{"value": "NGO"}, {"value": "CBO"}, {"isOther": True}],
                    }
                }
            },
        },
        {"itemId": "p", "title": "Programmes", "description": "See the overview.", "pageBreakItem": {}},
        {
            "itemId": "g",
            "title": "Which programmes?",
            "questionGroupItem": {
                "grid": {"columns": {"type": "RADIO", "options": [{"value": "1st"}, {"value": "2nd"}]}},
                "questions": [{"rowQuestion": {"title": "Kangaroo care"}}, {"rowQuestion": {"title": "Lead"}}],
            },
        },
        {
            "itemId": "c",
            "title": "Budget",
            "questionItem": {
                "question": {
                    "required": True,
                    "choiceQuestion": {"type": "DROP_DOWN", "options": [{"value": "Small"}]},
                }
            },
        },
        {"itemId": "d", "title": "Why?", "questionItem": {"question": {"textQuestion": {"paragraph": True}}}},
        {"itemId": "i", "title": "A picture", "imageItem": {}},
    ],
}


def _questions():
    texts = [
        "Timestamp",
        "Organisation name",
        "Which describes you?",
        "Which programmes? [Kangaroo care]",
        "Which programmes? [Lead]",
        "Budget",
        "Why?",
    ]
    return [{"id": f"q{i}", "text": t, "column": i, "long": True} for i, t in enumerate(texts)]


def test_annotate_gives_each_sheet_column_its_form_question():
    qs = _questions()
    assert fs.annotate(qs, FORM) == 6
    stamp, name, kind, kc, lead, budget, why = qs
    assert "type" not in stamp
    assert name["type"] == "text" and name["required"] is True
    assert name["section"] == {"title": "Identity", "help": ""}
    assert kind["type"] == "checkboxes" and kind["options"] == ["NGO", "CBO", "Other"]
    assert kind["help"] == "Tick all that apply."
    assert kc["type"] == "multiple_choice" and kc["display"] == "radio" and kc["options"] == ["1st", "2nd"]
    assert kc["grid"]["row"] == "Kangaroo care" and lead["grid"]["id"] == kc["grid"]["id"] == "g"
    assert kc["section"] == {"title": "Programmes", "help": "See the overview."} and "section" not in lead
    assert budget["display"] == "dropdown"
    assert why["type"] == "textarea"


def test_annotate_replaces_a_stale_structure_and_skips_another_forms_questions():
    qs = _questions()
    qs[1].update(options=["stale"], forms=["fr"])
    qs[5].update(options=["stale"], forms=["en"])
    fs.annotate(qs, FORM, form_key="en")
    assert qs[1]["options"] == ["stale"] and "type" not in qs[1]
    assert qs[5]["options"] == ["Small"]


def test_find_matches_by_response_sheet_then_public_link():
    other = {"linkedSheetId": "x", "responderUri": "https://docs.google.com/forms/d/e/OTHER/viewform"}
    assert fs.find([other, FORM], {"response_spreadsheet_id": "sheet-1"}) is FORM
    entry = {
        "response_spreadsheet_id": "",
        "form_url": "https://docs.google.com/forms/d/e/PUBLIC123/viewform?usp=sf_link",
    }
    assert fs.find([other, FORM], entry) is FORM
    assert fs.find([other], {"response_spreadsheet_id": "sheet-1", "form_url": ""}) is None


def test_shared_forms_pages_through_drive_and_skips_an_unreadable_form():
    pages = {
        None: {"files": [{"id": "f1"}, {"id": "bad"}], "nextPageToken": "p2"},
        "p2": {"files": [{"id": "f2"}]},
    }

    def get(url, params):
        if url == fs.DRIVE_FILES:
            assert "application/vnd.google-apps.form" in params["q"]
            return pages[params.get("pageToken")]
        if url.endswith("/bad"):
            raise RuntimeError("403")
        return {"formId": url.rsplit("/", 1)[1]}

    assert [f["formId"] for f in fs.shared_forms(get)] == ["f1", "f2"]
