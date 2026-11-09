"""A stored answer as the question asked it.

A question carries its type and options (`validation`; a round's questions get
them from their Google Form, `marketplace.form_structure`). An answer is stored
in that question's own terms: text for a text question, the chosen option for
``multiple_choice``, and the list of chosen options for ``checkboxes`` -- the
shape the respond form saves (`forms.ResponseForm.get_responses_dict`).

The pages that show an answer read it through `answer_blocks`, so a choice
reads as the options with the picked ones ticked and a grid ("rank these 1st,
2nd, 3rd") as the table it was, on the form and on every page a reviewer reads.
`answer_text` is the one flat form of an answer: what a review quotes and what
an agent reads.
"""

from __future__ import annotations

# Google Forms names an "Other" choice nothing; the answer holds what was typed.
OTHER = "Other"

# A list longer than this (a dropdown of countries) shows only what was picked.
_LISTED_OPTIONS = 12


def answer_text(value) -> str:
    """A stored answer as text: a checkboxes answer is its chosen options, joined."""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value if v not in (None, ""))
    return "" if value is None else str(value)


def split_checkboxes(text: str, options) -> list[str]:
    """A response sheet's checkbox cell ("A, B, what they typed") as its choices.

    Google Sheets joins the ticked options with ", ", and an option can itself
    contain a comma, so the cell is split by matching the options (longest
    first) rather than on the comma. Google lists the choices in the form's
    order with "Other" last, so whatever is left once no option matches is the
    respondent's own "Other" text, kept whole.
    """
    known = sorted({str(o) for o in options or [] if o and o != OTHER}, key=len, reverse=True)
    picked: list[str] = []
    rest = (text or "").strip()
    while rest:
        for option in known:
            if rest == option or rest.startswith(option + ", "):
                picked.append(option)
                rest = rest[len(option) + 2 :].strip()
                break
        else:
            picked.append(rest)
            break
    return picked


def typed_answer(question: dict, value):
    """An answer in its question's own terms: a checkboxes answer as a list."""
    if question.get("type") != "checkboxes" or isinstance(value, (list, tuple)):
        return value
    return split_checkboxes(str(value or ""), question.get("options"))


def chosen(question: dict, value) -> list[str]:
    """What a choice answer picked, as a list (empty when nothing was)."""
    value = typed_answer(question, value)
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v not in (None, "")]
    text = "" if value is None else str(value).strip()
    return [text] if text else []


def answer_view(question: dict, value) -> dict:
    """What a page needs to show one answer.

    ``text`` is `answer_text` of the value as stored, so a review's quote
    offsets (`evidence.answers_of`) land on the same characters the page shows.
    A choice question also gets ``choices``: its options with the picked ones
    marked, plus what was typed for "Other". A long list shows only the picks.
    """
    is_choice = question.get("type") in ("multiple_choice", "checkboxes")
    picked = chosen(question, value) if is_choice else []
    view = {
        "text": answer_text(value),
        "picked": picked,
        "choices": None,
        "multi": question.get("type") == "checkboxes",
    }
    options = [str(o) for o in question.get("options") or []]
    if not is_choice or not options:
        return view
    typed = [p for p in picked if p not in options]
    listed = len(options) <= _LISTED_OPTIONS and question.get("display") != "dropdown"
    choices = [{"label": o, "picked": o in picked} for o in options if o != OTHER and (listed or o in picked)]
    choices += [{"label": f"{OTHER}: {t}", "picked": True} for t in typed]
    if listed and OTHER in options and not typed:
        choices.append({"label": OTHER, "picked": False})
    view["choices"] = choices
    return view


def question_blocks(items) -> list[dict]:
    """Questions as the form lays them out: section headings, single questions,
    and a grid's rows gathered into one table.

    Each item is a dict with a ``question``. A Google Form grid arrives as one
    question per row (the response sheet has a column per row), each carrying
    ``grid`` (`marketplace.form_structure`). Consecutive rows of one grid become
    one block, so "rank these 1st, 2nd, 3rd" reads as the table it was. Each
    question and grid block is numbered (``number``) as a reader counts them.
    """
    blocks: list[dict] = []
    number = 0
    for item in items:
        question = item["question"]
        if question.get("section"):
            blocks.append({"kind": "section", **question["section"]})
        grid = question.get("grid")
        if not grid:
            number += 1
            blocks.append({"kind": "question", "number": number, **item})
            continue
        last = blocks[-1] if blocks else None
        if not (last and last["kind"] == "grid" and last["id"] == grid.get("id")):
            number += 1
            last = {
                "kind": "grid",
                "number": number,
                "id": grid.get("id"),
                "title": grid.get("title", ""),
                "help": grid.get("help", ""),
                "columns": grid.get("columns", []),
                "multi": question.get("type") == "checkboxes",
                # A ranking: each column may be picked in one row only.
                "one_per_column": bool(grid.get("one_per_column")),
                # A required ranking: every column must go to some row.
                "required": bool(grid.get("required")),
                "rows": [],
            }
            blocks.append(last)
        last["rows"].append({"label": grid.get("row", ""), **item})
    return blocks


def answer_blocks(questions, stored: dict, answered_only: bool = False, extra=None) -> list[dict]:
    """A response's answers as `question_blocks`, each carrying `answer_view`.

    ``answered_only`` leaves out the questions nobody answered; a section
    heading on one of them moves to the next question kept. ``extra(question)``
    adds fields of the caller's own (the criteria a question is scored on).
    """
    items = []
    carried = None
    for q in questions or []:
        value = (stored or {}).get(q.get("id", ""), "")
        view = answer_view(q, value)
        if answered_only and not view["text"].strip():
            carried = carried or q.get("section")
            continue
        if carried and not q.get("section"):
            q = {**q, "section": carried}
        carried = None
        item = {"question": q, "id": q.get("id", ""), "answer": view}
        if extra is not None:
            item.update(extra(q))
        items.append(item)
    blocks = question_blocks(items)
    for block in blocks:
        if block["kind"] == "grid":
            for row in block["rows"]:
                # Which of the row's columns were ticked.
                row["cells"] = [str(c) in row["answer"]["picked"] for c in block["columns"]]
            # A grid is scored as one question: the criteria on any of its rows.
            criteria = {c.get("id"): c for row in block["rows"] for c in row.get("criteria") or []}
            block["criteria"] = list(criteria.values())
    return blocks


def answer_rows(blocks) -> list[dict]:
    """`answer_blocks` flattened to one row per question, in order.

    Each row carries the number its block shows (``n``), so a review's claims
    point at "Q4" the reader can find; every row of one grid shares its number.
    """
    rows = []
    for block in blocks:
        if block["kind"] == "question":
            members = [(block, block["question"].get("text", ""))]
        elif block["kind"] == "grid":
            members = [(row, f"{block['title']} [{row['label']}]") for row in block["rows"]]
        else:
            continue
        for item, text in members:
            rows.append({"id": item["id"], "question": text, "answer": item["answer"]["text"], "n": block["number"]})
    return rows


def typed_answers(questions, answers: dict) -> dict:
    """A submission's answers, each in its question's own terms (`typed_answer`)."""
    by_id = {q.get("id"): q for q in questions or []}
    return {qid: typed_answer(by_id.get(qid, {}), value) for qid, value in (answers or {}).items()}
