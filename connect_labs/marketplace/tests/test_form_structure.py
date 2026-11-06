"""A round's questions take their Google Form's structure, read from its public page.

``FORM`` is in the Forms API item shape `parse` produces; every title is invented.
"""

import json

import pytest

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


# A public form page, cut down: the array is in the shape Google serves today
# (item = [id, title, help, type, specs, ...]); every title is invented.
PAGE_DATA = [
    None,
    [
        "Form description",
        [
            [1, "Identity", None, 6, None],
            [2, "Organisation name", None, 0, [[20, None, 1]]],
            [
                3,
                "Which describes you?",
                "Tick all that apply.",
                4,
                [[30, [["NGO", None, None, None, 0], ["CBO", None, None, None, 0], ["", None, None, None, 1]], 0]],
            ],
            [4, "Programmes", "See the overview.", 8, None],
            [
                5,
                "Which programmes?",
                None,
                7,
                [
                    [50, [["1st"], ["2nd"]], 0, ["Kangaroo care"], None, None, None, None, None, None, None, [0]],
                    [51, [["1st"], ["2nd"]], 1, ["Lead"], None, None, None, None, None, None, None, [0]],
                ],
            ],
            [6, "Budget", None, 3, [[60, [["Small", None, None, None, 0]], 1]]],
            [7, "How sure?", None, 5, [[70, [["1"], ["2"], ["3"]], 0]]],
            [8, "Why?", None, 1, [[80, None, 0]]],
            [9, "A picture", None, 11, None],
        ],
        None,
        None,
        None,
        None,
        None,
        None,
        "Invented RFI",
    ],
]
PAGE = f"<html><script>var FB_PUBLIC_LOAD_DATA_ = {json.dumps(PAGE_DATA)};</script></html>"


def test_the_public_page_reads_as_the_same_structure_the_api_gives():
    form = fs.parse(PAGE)
    assert form["title"] == "Invented RFI"
    qs = _questions()
    qs.insert(6, {"id": "sure", "text": "How sure?", "column": 9})
    assert fs.annotate(qs, form) == 7
    name, kind, kc, lead, budget, sure, why = qs[1:]
    assert name["section"]["title"] == "Identity" and name["type"] == "text" and name["required"] is True
    assert kind["type"] == "checkboxes" and kind["options"] == ["NGO", "CBO", "Other"] and kind["required"] is False
    assert kind["help"] == "Tick all that apply."
    assert kc["grid"]["columns"] == ["1st", "2nd"] and kc["display"] == "radio" and kc["required"] is False
    assert kc["section"] == {"title": "Programmes", "help": "See the overview."}
    assert lead["required"] is True and lead["grid"]["id"] == kc["grid"]["id"]
    assert budget["display"] == "dropdown" and budget["required"] is True
    assert sure["options"] == ["1", "2", "3"]
    assert why["type"] == "textarea"


def test_a_checkbox_grid_is_ticked_per_row():
    data = json.loads(json.dumps(PAGE_DATA))
    data[1][1][4][4][0][11] = [1]
    form = fs.parse(f"FB_PUBLIC_LOAD_DATA_ = {json.dumps(data)};</script>")
    group = form["items"][4]["questionGroupItem"]
    assert group["grid"]["columns"]["type"] == "CHECKBOX"


def test_a_page_with_no_form_or_a_changed_layout_is_refused_with_a_reason():
    with pytest.raises(fs.FormUnreadable, match="no form"):
        fs.parse("<html>Sign in to continue</html>")
    with pytest.raises(fs.FormUnreadable, match="layout has changed"):
        fs.parse("FB_PUBLIC_LOAD_DATA_ = [null, 5];</script>")
    with pytest.raises(fs.FormUnreadable, match="no public form link"):
        fs.fetch("", get=lambda url: PAGE)


def test_fetch_reads_the_public_link():
    seen = []
    form = fs.fetch("https://docs.google.com/forms/d/e/X/viewform", get=lambda url: seen.append(url) or PAGE)
    assert seen == ["https://docs.google.com/forms/d/e/X/viewform"] and len(form["items"]) == 9
