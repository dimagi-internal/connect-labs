"""Answers in their question's own terms, and as the pages that read them show them."""

from connect_labs.solicitations.answers import (
    answer_blocks,
    answer_rows,
    answer_text,
    answer_view,
    split_checkboxes,
    typed_answer,
    typed_answers,
)

REACH = {
    "id": "reach",
    "text": "How do you reach households?",
    "type": "checkboxes",
    "options": ["Door to door", "Markets, fairs and churches", "Radio", "Other"],
}
YEARS = {
    "id": "years",
    "text": "Years operating",
    "type": "multiple_choice",
    "display": "radio",
    "options": ["Under 2", "2-5", "Over 5"],
}


def _grid_row(qid, row, multi=False):
    return {
        "id": qid,
        "text": f"Rank the barriers [{row}]",
        "type": "checkboxes" if multi else "multiple_choice",
        "display": "radio",
        "options": ["1st", "2nd", "3rd"],
        "grid": {"id": "g1", "title": "Rank the barriers", "columns": ["1st", "2nd", "3rd"], "row": row},
    }


class TestSplittingASheetCell:
    def test_an_option_with_a_comma_is_one_choice(self):
        assert split_checkboxes("Door to door, Markets, fairs and churches", REACH["options"]) == [
            "Door to door",
            "Markets, fairs and churches",
        ]

    def test_what_is_left_is_the_typed_other(self):
        assert split_checkboxes("Radio, WhatsApp groups, mostly", REACH["options"]) == [
            "Radio",
            "WhatsApp groups, mostly",
        ]

    def test_blank_is_nothing(self):
        assert split_checkboxes("  ", REACH["options"]) == []


class TestTypedAnswers:
    def test_a_checkboxes_cell_becomes_a_list_and_others_stay(self):
        stored = typed_answers([REACH, YEARS], {"reach": "Door to door, Radio", "years": "2-5", "x": "kept"})
        assert stored == {"reach": ["Door to door", "Radio"], "years": "2-5", "x": "kept"}

    def test_a_list_is_left_alone(self):
        assert typed_answer(REACH, ["Radio"]) == ["Radio"]

    def test_the_flat_text_keeps_its_length_either_way(self):
        # A review's quote offsets were taken on the sheet's ", " join; "; " is as long.
        raw = "Door to door, Radio"
        assert len(answer_text(typed_answer(REACH, raw))) == len(raw)


class TestAnswerView:
    def test_a_choice_lists_every_option_with_the_picks_marked(self):
        view = answer_view(REACH, ["Radio", "Fishing co-op"])
        assert [(c["label"], c["picked"]) for c in view["choices"]] == [
            ("Door to door", False),
            ("Markets, fairs and churches", False),
            ("Radio", True),
            ("Other: Fishing co-op", True),
        ]
        assert view["multi"] is True and view["text"] == "Radio; Fishing co-op"

    def test_an_unconverted_sheet_cell_reads_the_same(self):
        view = answer_view(REACH, "Door to door, Markets, fairs and churches")
        assert [c["label"] for c in view["choices"] if c["picked"]] == ["Door to door", "Markets, fairs and churches"]
        # The text is the stored text, which is what a review's quotes point into.
        assert view["text"] == "Door to door, Markets, fairs and churches"

    def test_a_dropdown_shows_only_what_was_picked(self):
        country = {"id": "c", "type": "multiple_choice", "display": "dropdown", "options": ["Ghana", "Kenya", "Togo"]}
        assert answer_view(country, "Kenya")["choices"] == [{"label": "Kenya", "picked": True}]

    def test_text_has_no_choices(self):
        assert answer_view({"id": "t", "type": "textarea"}, "Long answer")["choices"] is None


class TestAnswerBlocks:
    def test_sections_questions_and_a_grid_as_one_numbered_table(self):
        questions = [
            {**YEARS, "section": {"title": "About you", "help": ""}},
            _grid_row("g_a", "Cost"),
            _grid_row("g_b", "Distance"),
            REACH,
        ]
        stored = {"years": "Over 5", "g_a": "2nd", "g_b": "1st", "reach": ["Radio"]}
        blocks = answer_blocks(questions, stored)
        assert [b["kind"] for b in blocks] == ["section", "question", "grid", "question"]
        grid = blocks[2]
        assert grid["number"] == 2 and blocks[3]["number"] == 3
        assert [(r["label"], r["cells"]) for r in grid["rows"]] == [
            ("Cost", [False, True, False]),
            ("Distance", [True, False, False]),
        ]
        rows = answer_rows(blocks)
        assert [(r["id"], r["n"]) for r in rows] == [("years", 1), ("g_a", 2), ("g_b", 2), ("reach", 3)]
        assert rows[1]["question"] == "Rank the barriers [Cost]" and rows[1]["answer"] == "2nd"

    def test_a_one_per_column_grid_says_so(self):
        ranked = [_grid_row("g_a", "Cost"), _grid_row("g_b", "Distance")]
        for row in ranked:
            row["grid"]["one_per_column"] = True
        assert answer_blocks(ranked, {})[0]["one_per_column"] is True
        assert answer_blocks([_grid_row("g_a", "Cost")], {})[0]["one_per_column"] is False

    def test_a_multi_grid_row_ticks_several(self):
        blocks = answer_blocks([_grid_row("g_a", "Cost", multi=True)], {"g_a": "1st, 3rd"})
        assert blocks[0]["multi"] and blocks[0]["rows"][0]["cells"] == [True, False, True]

    def test_answered_only_carries_a_section_to_the_next_kept_question(self):
        questions = [{**YEARS, "section": {"title": "About you", "help": ""}}, REACH]
        blocks = answer_blocks(questions, {"reach": ["Radio"]}, answered_only=True)
        assert [b["kind"] for b in blocks] == ["section", "question"] and blocks[1]["id"] == "reach"

    def test_a_grid_is_scored_on_the_criteria_of_its_rows(self):
        crit = {"id": "c1", "name": "Insight"}
        blocks = answer_blocks(
            [_grid_row("g_a", "Cost"), _grid_row("g_b", "Distance")],
            {},
            extra=lambda q: {"criteria": [crit] if q["id"] == "g_b" else []},
        )
        assert blocks[0]["criteria"] == [crit]
