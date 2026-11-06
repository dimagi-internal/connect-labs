"""A round's questions as its Google Form asked them: choices, grids, sections.

The response sheet only carries a header row, so a round imported from it alone
shows every question as a text box, and a grid ("rank these 1st, 2nd, 3rd")
as one box per row. The form itself knows better, and the Google Forms API
(``forms.get``) says it in a documented shape: each item's choice type and
options, a grid's rows and columns, which questions are required, the help text,
and where each section starts.

The API answers only for an account that can edit the form, so -- as with the
response sheets -- a form is read only once someone shares it with the labs
service account. Nothing here needs the form's edit link: every form shared
with the account is listed, and each is matched to its round by the response
sheet it writes to (``linkedSheetId``) or by its public link (``responderUri``).
A form nobody shared leaves its round's questions as they were.
"""

from __future__ import annotations

import re

FORM_MIME = "application/vnd.google-apps.form"
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
FORMS_API = "https://forms.googleapis.com/v1/forms"

# What a question may carry once its form has been read. Cleared before each
# annotation so a re-import never keeps a stale choice list.
STRUCTURE_KEYS = ("type", "display", "options", "required", "help", "section", "grid")

# Google Forms names an "Other" choice nothing; the sheet holds what was typed.
OTHER = "Other"

_PUBLIC_ID = re.compile(r"/forms/d/e/([A-Za-z0-9_-]+)")


def public_id(url: str) -> str:
    """The id in a form's public link (``/forms/d/e/<id>/viewform``), or ""."""
    match = _PUBLIC_ID.search(url or "")
    return match.group(1) if match else ""


def shared_forms(get=None) -> list[dict]:
    """Every form shared with the labs service account, as ``forms.get`` returns it.

    ``get(url, params)`` returns parsed JSON; it defaults to an authenticated
    call as the service account. A form the account can list but not read is
    skipped rather than failing the import.
    """
    get = get or _service_account_get()
    forms, page = [], None
    while True:
        params = {
            "q": f"mimeType='{FORM_MIME}' and trashed=false",
            "fields": "nextPageToken,files(id)",
            "pageSize": "200",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page:
            params["pageToken"] = page
        listing = get(DRIVE_FILES, params)
        for f in listing.get("files", []):
            try:
                forms.append(get(f"{FORMS_API}/{f['id']}", {}))
            except Exception:  # noqa: BLE001 — one unreadable form must not stop the rest
                continue
        page = listing.get("nextPageToken")
        if not page:
            return forms


def _service_account_get():
    import httpx
    from google.auth.transport.requests import Request

    from connect_labs.labs.synthetic.gdrive import _load_credentials

    creds = _load_credentials()

    def get(url, params):
        if not creds.valid:
            creds.refresh(Request())
        got = httpx.get(url, params=params, headers={"Authorization": f"Bearer {creds.token}"}, timeout=60)
        got.raise_for_status()
        return got.json()

    return get


def find(forms: list[dict], form_entry: dict) -> dict | None:
    """The shared form behind one of a round's forms: by response sheet, else by public link."""
    sheet = (form_entry.get("response_spreadsheet_id") or "").strip()
    link = public_id(form_entry.get("form_url", ""))
    for form in forms:
        if sheet and form.get("linkedSheetId") == sheet:
            return form
    for form in forms:
        if link and public_id(form.get("responderUri", "")) == link:
            return form
    return None


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
