"""A round's questions as its Google Form asked them: choices, grids, sections.

The response sheet only carries a header row, so a round imported from it alone
shows every question as a text box, and a grid ("rank these 1st, 2nd, 3rd")
as one box per row. The form itself knows better.

It is read from the form's public page -- the page anyone filling it in loads --
so labs needs no access to the form at all. (The Google Forms API would say the
same thing in a documented shape, but it answers only an account that can EDIT
the form, and edit rights on a live form are too much to hand a service account
for a read.) The page carries the form as a JSON array, ``FB_PUBLIC_LOAD_DATA_``.
That array is not documented, so `parse` is strict about its shape and says why
when it cannot read one: the import reports it per form and the questions keep
their text boxes. A misread never becomes a wrong list of choices.

`parse` turns the page into the item shape the Forms API documents
(``questionItem``, ``questionGroupItem``, ``pageBreakItem``...), so the rest of
this module reads one documented structure whichever way a form arrives.
"""

from __future__ import annotations

import json
import re

# Google Forms names an "Other" choice nothing; the sheet holds what was typed.
from connect_labs.solicitations.answers import OTHER

# What a question may carry once its form has been read. Cleared before each
# annotation so a re-import never keeps a stale choice list.
STRUCTURE_KEYS = ("type", "display", "options", "required", "help", "section", "grid")

_DATA = re.compile(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(.*?);\s*</script>", re.S)

# The page's item type codes.
_SHORT, _PARAGRAPH, _RADIO, _DROPDOWN, _CHECKBOX, _SCALE, _TITLE, _GRID, _PAGE = range(9)
_CHOICE_TYPES = {_RADIO: "RADIO", _DROPDOWN: "DROP_DOWN", _CHECKBOX: "CHECKBOX"}


class FormUnreadable(ValueError):
    """The page was fetched but carries no form this module can read."""


def fetch(form_url: str, get=None) -> dict:
    """The form behind a public link, as `parse` returns it.

    ``get(url)`` returns the page's HTML; it defaults to a plain anonymous GET
    that follows redirects (a ``forms.gle`` short link). Raises on a network
    error, and `FormUnreadable` on a page that is not an open form -- one
    restricted to signed-in users, say.
    """
    if not (form_url or "").startswith("https://"):
        raise FormUnreadable("no public form link")
    get = get or _anonymous_get
    return parse(get(form_url))


def _anonymous_get(url: str) -> str:
    import httpx

    got = httpx.get(url, follow_redirects=True, timeout=30, headers={"Accept-Language": "en"})
    got.raise_for_status()
    return got.text


def _page_options(raw) -> list[dict]:
    opts = []
    for o in raw or []:
        if len(o) > 4 and o[4] == 1:
            opts.append({"isOther": True})
        elif o and o[0]:
            opts.append({"value": str(o[0])})
    return opts


def _item(raw: list) -> dict:
    """One page item as a Forms API item."""
    kind = raw[3]
    item = {"itemId": str(raw[0]), "title": raw[1] or "", "description": raw[2] or ""}
    specs = raw[4] if len(raw) > 4 and raw[4] else []
    first = specs[0] if specs else None
    required = bool(first[2]) if first and len(first) > 2 else False
    if kind in (_SHORT, _PARAGRAPH):
        item["questionItem"] = {"question": {"required": required, "textQuestion": {"paragraph": kind == _PARAGRAPH}}}
    elif kind in _CHOICE_TYPES:
        choice = {"type": _CHOICE_TYPES[kind], "options": _page_options(first[1])}
        item["questionItem"] = {"question": {"required": required, "choiceQuestion": choice}}
    elif kind == _SCALE:
        values = [int(o[0]) for o in first[1] if str(o[0]).isdigit()]
        scale = {"low": min(values), "high": max(values)}
        item["questionItem"] = {"question": {"required": required, "scaleQuestion": scale}}
    elif kind == _GRID:
        multi = bool(first[11][0]) if len(first) > 11 and first[11] else False
        columns = {"type": "CHECKBOX" if multi else "RADIO", "options": _page_options(first[1])}
        rows = [{"required": bool(r[2]), "rowQuestion": {"title": r[3][0]}} for r in specs]
        item["questionGroupItem"] = {"grid": {"columns": columns}, "questions": rows}
    elif kind == _TITLE:
        item["textItem"] = {}
    elif kind == _PAGE:
        item["pageBreakItem"] = {}
    else:
        item["questionItem"] = {"question": {"required": required}}  # date, time, upload: text
    return item


def parse(html: str) -> dict:
    """A form's public page as ``{"title", "items"}`` in the Forms API's item shape."""
    match = _DATA.search(html or "")
    if not match:
        raise FormUnreadable("the page carries no form (closed, or restricted to signed-in users?)")
    try:
        data = json.loads(match.group(1))
        raw_items = data[1][1] or []
        return {"title": data[1][8] or "", "items": [_item(raw) for raw in raw_items]}
    except (ValueError, IndexError, TypeError, KeyError) as exc:
        raise FormUnreadable(f"the form's layout has changed ({exc.__class__.__name__}: {exc})") from exc


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _options(spec: dict) -> list[str]:
    opts = []
    for o in spec.get("options", []):
        if o.get("isOther"):
            opts.append(OTHER)
        elif o.get("value"):
            opts.append(o["value"])
    return opts


def _choice(spec: dict) -> dict:
    """A choice question's type and options, in the solicitation question vocabulary."""
    kind = spec.get("type", "RADIO")
    if kind == "CHECKBOX":
        return {"type": "checkboxes", "options": _options(spec)}
    return {
        "type": "multiple_choice",
        "display": "dropdown" if kind == "DROP_DOWN" else "radio",
        "options": _options(spec),
    }


def _single(question: dict) -> dict:
    """One question's structure, or {} for a kind shown as text (date, time, file)."""
    if "choiceQuestion" in question:
        return _choice(question["choiceQuestion"])
    if "textQuestion" in question:
        return {"type": "textarea" if question["textQuestion"].get("paragraph") else "text"}
    if "scaleQuestion" in question:
        scale = question["scaleQuestion"]
        low, high = int(scale.get("low", 1)), int(scale.get("high", 5))
        return {"type": "multiple_choice", "display": "radio", "options": [str(n) for n in range(low, high + 1)]}
    if "ratingQuestion" in question:
        top = int(question["ratingQuestion"].get("ratingScaleLevel", 5))
        return {"type": "multiple_choice", "display": "radio", "options": [str(n) for n in range(1, top + 1)]}
    return {}


def by_header(form: dict) -> dict[str, dict]:
    """Normalised sheet header -> what that column's question is.

    The sheet names a question by its title, and a grid row as
    ``"<grid title> [<row>]"``. A section (a page break, or a title-and-
    description item) is attached to the first question after it.
    """
    found: dict[str, dict] = {}
    section = None
    for item in form.get("items", []):
        title = (item.get("title") or "").strip()
        help_text = (item.get("description") or "").strip()
        if "pageBreakItem" in item or "textItem" in item:
            section = {"title": title, "help": help_text} if title or help_text else None
            continue
        entries: list[tuple[str, dict]] = []
        if "questionItem" in item:
            question = item["questionItem"].get("question", {})
            shape = _single(question)
            shape["required"] = bool(question.get("required"))
            if help_text:
                shape["help"] = help_text
            entries.append((title, shape))
        elif "questionGroupItem" in item:
            group = item["questionGroupItem"]
            columns = group.get("grid", {}).get("columns", {})
            cell = _choice(columns)
            grid = {"id": item.get("itemId", title), "title": title, "columns": cell["options"]}
            if help_text:
                grid["help"] = help_text
            for row in group.get("questions", []):
                label = (row.get("rowQuestion", {}).get("title") or "").strip()
                shape = dict(cell, required=bool(row.get("required")), grid=dict(grid, row=label))
                entries.append((f"{title} [{label}]", shape))
        else:
            continue  # an image or a video: nothing was answered
        for i, (header, shape) in enumerate(entries):
            if section and i == 0:
                shape["section"] = section
                section = None
            found.setdefault(_key(header), shape)
    return found


def annotate(questions: list[dict], form: dict, form_key: str | None = None) -> int:
    """Give each of a round's questions its form's structure, in place.

    Only the questions ``form_key``'s form asked are touched (all of them when
    it is None), so an English and a French form each annotate their own.
    Returns how many questions matched; one the form does not name -- a
    Timestamp, the collected email -- keeps its plain text box.
    """
    shapes = by_header(form)
    matched = 0
    for q in questions:
        if form_key and form_key not in q.get("forms", [form_key]):
            continue
        shape = shapes.get(_key(q.get("text", "")))
        if shape is None:
            continue
        for k in STRUCTURE_KEYS:
            q.pop(k, None)
        q.update(shape)
        matched += 1
    return matched
