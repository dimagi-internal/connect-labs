"""Turning a Google Form response sheet into questions and submissions.

Across the rounds in this directory no two forms share a question set. One sheet
has two blank column headers, another asks for "Email Address" twice, timestamps
arrive in both US and European order, and the same concept is worded three
different ways ("Organization name" / "Please provide the name of your
organization" / "What is the full, registered name of your organization?").

So nothing here assumes a schema. The header row *is* the question list, kept
verbatim; the whole submitted row is stored against it; and the handful of
fields matching needs are auto-detected by pattern, with the sheet's Column Map
as an override for when detection is wrong.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

# Patterns that identify the few fields matching and display need. Ordered:
# the first header that matches wins, so put the most specific first.
# Rounds are not all in English: the 2026 Readers round ran an English and a
# French form over the same window, and the French one matched nothing until its
# wording was added here. A round whose headers no pattern reaches matches zero
# submissions silently, which is why the import reports per-round match counts.
DETECTORS = {
    "org_name": [
        r"full,? registered name of your organi[sz]ation",
        r"name of your organi[sz]ation",
        r"^organi[sz]ation'?s? name",
        r"^organi[sz]ation name",
        r"^name of organi[sz]ation",
        r"^nom de l'?organisation",
        r"^nom de votre organisation",
    ],
    "email": [
        r"valid email",
        r"^email address",
        r"^e-?mail",
        r"^courriel du contact",
        r"^courriel",
        r"adresse (e-?mail|électronique)",
    ],
    "contact_name": [
        r"contact person",
        r"primary contact",
        r"contact full name",
        r"contact name",
        r"^nom du contact",
    ],
    "country": [
        r"^country",
        r"countr(y|ies) (of|do you)",
        r"which country",
        r"^pays",
    ],
    "website": [r"^website", r"website (link|details)", r"^site web"],
}

_EMAIL = re.compile(r"[^\s,;]+@[^\s,;]+\.[^\s,;]+")


@dataclass
class Question:
    id: str
    text: str
    column: int


@dataclass
class Submission:
    source_row: int
    answers: dict
    submitted_at: dt.datetime | None = None
    org_name: str = ""
    emails: list[str] = field(default_factory=list)
    contact_name: str = ""
    country: str = ""
    website: str = ""


def build_questions(header: list[str]) -> list[Question]:
    """The header row as a question list, with stable ids.

    Blank and duplicate headers get positional ids so they stay addressable
    rather than colliding or silently vanishing — the 2024 CHC sheet has two
    blank headers, and one round asks for "Email Address" twice.
    """
    questions: list[Question] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(header):
        text = (raw or "").strip()
        base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or f"column_{index + 1}"
        seen[base] = seen.get(base, 0) + 1
        qid = base if seen[base] == 1 else f"{base}__{seen[base]}"
        questions.append(Question(id=qid, text=text or f"(unlabelled column {index + 1})", column=index))
    return questions


def detect_columns(questions: list[Question], overrides: dict | None = None) -> dict:
    """field -> question id, by header pattern, with the sheet's map winning.

    Auto-detection rather than per-round configuration because there are
    eighteen rounds and more coming; the override exists for where it is wrong.
    """
    found: dict = {}
    for field_name, patterns in DETECTORS.items():
        for pattern in patterns:
            match = next((q for q in questions if re.search(pattern, q.text.strip().lower())), None)
            if match is not None:
                found[field_name] = match.id
                break
    for field_name, header_text in (overrides or {}).items():
        match = next((q for q in questions if q.text.strip().lower() == str(header_text).strip().lower()), None)
        if match is not None:
            found[field_name] = match.id
    return found


def parse_timestamp(raw: str) -> dt.datetime | None:
    """A Google Forms timestamp, in whichever order the sheet's locale used.

    Both 3/4/2026 and 4/3/2026 appear across these sheets. Where a value is
    ambiguous, day-first is preferred only when month-first is impossible, so a
    real date is never invented — an unparseable stamp returns None and the
    submission keeps its verbatim answer.
    """
    value = (raw or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            pass
    match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:[ ,]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", value)
    if not match:
        return None
    a, b, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    hour, minute, second = (int(match.group(i)) if match.group(i) else 0 for i in (4, 5, 6))
    month, day = (a, b) if a <= 12 else (b, a)
    try:
        return dt.datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def parse_submissions(rows: list[list[str]], overrides: dict | None = None) -> tuple[list[Question], list[Submission]]:
    """A response sheet as its question list and its submissions."""
    if not rows:
        return [], []
    questions = build_questions(rows[0])
    mapping = detect_columns(questions, overrides)

    submissions: list[Submission] = []
    for index, row in enumerate(rows[1:], start=2):
        if not any((c or "").strip() for c in row):
            continue
        answers = {q.id: (row[q.column].strip() if len(row) > q.column and row[q.column] else "") for q in questions}

        def pick(name: str) -> str:
            qid = mapping.get(name)
            return answers.get(qid, "") if qid else ""

        raw_emails = pick("email")
        # One cell can hold two addresses, separated by a comma or a newline.
        emails = [m.group(0).lower() for m in _EMAIL.finditer(raw_emails)]

        submissions.append(
            Submission(
                source_row=index,
                answers=answers,
                submitted_at=parse_timestamp(answers.get(questions[0].id, "")),
                org_name=pick("org_name"),
                emails=emails,
                contact_name=pick("contact_name"),
                country=pick("country"),
                website=pick("website"),
            )
        )
    return questions, submissions
