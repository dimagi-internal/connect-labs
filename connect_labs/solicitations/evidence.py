"""What a review rests on: claims, and the response text each one cites.

A review is read beside the response it judges, and a reader should be able to
click any statement in it and see the words it came from. So a review's
`claims` are its statements, each with the passages behind it:

    {"id": "c1", "about": "criterion:reach" | "strength" | "concern" | "flag" | "outcome",
     "text": "...", "basis": "quoted" | "synthesized",
     "cites": [
        {"question": "<question id>", "quote": "exact words", "start": 12, "end": 40},
        {"question": "<question id>", "absent": true},     # about what the answer leaves out
        {"question": "<question id>", "whole": true},      # drawn from the whole answer
        {"source": "labs" | "web", "detail": "...", "url": "..."},
     ]}

A synthesized claim ("generic text throughout") may cite whole answers or
nothing at all: it is a judgement drawn from across the response, which is a
source, just not a passage.

Every quote is checked against the answer it names when the review is saved,
and its position stored. An AI review cannot cite words the applicant never
wrote, and a highlight cannot drift onto the wrong text later. Matching ignores
case, runs of whitespace, curly quotes and dash variants -- the differences a
model introduces when it copies text -- and nothing else.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

ABOUT_FIXED = ("strength", "concern", "flag", "outcome")
BASES = ("quoted", "synthesized")
SOURCES = ("labs", "web")

_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def _normalised(text: str) -> tuple[str, list[int]]:
    """The text folded for matching, and for each folded character its index in the original."""
    out, where = [], []
    previous_space = False
    for index, char in enumerate(text or ""):
        if char.isspace():
            if previous_space:
                continue
            out.append(" ")
            previous_space = True
        else:
            out.append(char.translate(_FOLD).lower())
            previous_space = False
        where.append(index)
    return "".join(out), where


def locate(quote: str, answer: str) -> tuple[int, int] | None:
    """Where `quote` sits in `answer`, as [start, end) in the original text, or None."""
    needle, _ = _normalised(quote.strip())
    if not needle:
        return None
    hay, where = _normalised(answer)
    found = hay.find(needle)
    if found < 0:
        return None
    return where[found], where[found + len(needle) - 1] + 1


def resolve(claims, answers: dict, criterion_ids) -> list[dict]:
    """The claims, checked, with every quote's position filled in.

    `answers` maps question id -> the submitted answer. Raises ValidationError
    listing every problem at once, so a model can fix them all in one retry.
    """
    if claims in (None, ""):
        return []
    if not isinstance(claims, list):
        raise ValidationError({"claims": "claims must be a list."})
    criterion_ids = set(criterion_ids or [])
    problems, out, seen = [], [], set()
    for n, raw in enumerate(claims, 1):
        if not isinstance(raw, dict):
            problems.append(f"claim {n} is not an object")
            continue
        cid = str(raw.get("id") or f"c{n}").strip()[:40]
        if cid in seen:
            cid = f"{cid}-{n}"
        seen.add(cid)
        about = str(raw.get("about") or "").strip()
        if about.startswith("criterion:"):
            if criterion_ids and about.split(":", 1)[1] not in criterion_ids:
                problems.append(f"claim {cid}: {about!r} is not one of this round's criteria")
        elif about not in ABOUT_FIXED:
            problems.append(f"claim {cid}: about must be criterion:<id> or one of {list(ABOUT_FIXED)}")
        text = str(raw.get("text") or "").strip()
        if not text:
            problems.append(f"claim {cid} has no text")
        basis = raw.get("basis") or (
            "quoted" if any(c.get("quote") for c in raw.get("cites") or []) else "synthesized"
        )
        if basis not in BASES:
            problems.append(f"claim {cid}: basis must be one of {list(BASES)}")
        cites = []
        for cite in raw.get("cites") or []:
            if not isinstance(cite, dict):
                problems.append(f"claim {cid}: a cite is not an object")
                continue
            if cite.get("source"):
                if cite["source"] not in SOURCES:
                    problems.append(f"claim {cid}: source must be one of {list(SOURCES)}")
                    continue
                if cite["source"] == "web" and not cite.get("url"):
                    problems.append(f"claim {cid}: a web source needs its url")
                    continue
                cites.append(
                    {
                        "source": cite["source"],
                        "detail": str(cite.get("detail") or ""),
                        "url": str(cite.get("url") or ""),
                    }
                )
                continue
            question = str(cite.get("question") or "")
            if question not in answers:
                problems.append(f"claim {cid}: no question {question!r} in this response")
                continue
            if cite.get("absent") or cite.get("whole"):
                cites.append({"question": question, "absent" if cite.get("absent") else "whole": True})
                continue
            quote = str(cite.get("quote") or "")
            span = locate(quote, answers[question] or "")
            if span is None:
                problems.append(f"claim {cid}: {quote[:80]!r} is not in the answer to {question!r}")
                continue
            start, end = span
            cites.append({"question": question, "quote": answers[question][start:end], "start": start, "end": end})
        out.append({"id": cid, "about": about, "text": text, "basis": basis, "cites": cites})
    if problems:
        raise ValidationError({"claims": problems})
    return out


def flatten(claims: list[dict]) -> dict:
    """The flat fields a review has always had, written from its claims.

    Pages, exports and tools that read `strengths`, `concerns` and
    `criteria_notes` keep working, and say the same as the claims.
    """
    strengths = [c["text"] for c in claims if c["about"] == "strength"]
    concerns = [c["text"] for c in claims if c["about"] == "concern"]
    notes: dict[str, list[str]] = {}
    for c in claims:
        if c["about"].startswith("criterion:"):
            notes.setdefault(c["about"].split(":", 1)[1], []).append(c["text"])
    return {
        "strengths": "\n".join(f"- {s}" for s in strengths),
        "concerns": "\n".join(f"- {s}" for s in concerns),
        "criteria_notes": {k: " ".join(v) for k, v in notes.items()},
    }


def answers_of(response, questions) -> dict:
    """Question id -> answer text, for the questions a review can cite."""
    stored = response.responses or {}
    return {q["id"]: str(stored.get(q["id"]) or "") for q in questions if q.get("id")}


# One colour per criterion, in the order the round lists them; strengths and
# concerns keep their own so a page reads the same whichever review is open.
PALETTE = ["#4f46e5", "#0891b2", "#c2410c", "#7c3aed", "#0d9488", "#b45309", "#be185d", "#4d7c0f"]
FIXED_COLOURS = {"strength": "#15803d", "concern": "#b91c1c", "flag": "#a16207", "outcome": "#475569"}


def legend(criteria) -> list[dict]:
    """Each kind of claim with its label and colour, for the page and its script."""
    out = [
        {"about": f"criterion:{c['id']}", "label": c.get("name", c["id"]), "colour": PALETTE[i % len(PALETTE)]}
        for i, c in enumerate(criteria or [])
        if c.get("id")
    ]
    out += [
        {"about": "strength", "label": "Strength", "colour": FIXED_COLOURS["strength"]},
        {"about": "concern", "label": "Concern", "colour": FIXED_COLOURS["concern"]},
        {"about": "flag", "label": "Flag", "colour": FIXED_COLOURS["flag"]},
        {"about": "outcome", "label": "Outcome", "colour": FIXED_COLOURS["outcome"]},
    ]
    return out


def claims_of(review) -> list[dict]:
    """A review's claims; for one written before claims existed, its flat fields as claims with no cites."""
    if review.claims:
        return list(review.claims)
    out = []
    for about, text in (("strength", review.strengths), ("concern", review.concerns)):
        for line in (text or "").splitlines():
            line = line.strip().lstrip("-•* ").strip()
            if line:
                out.append({"about": about, "text": line})
    for cid, note in (review.criteria_notes or {}).items():
        if note:
            out.append({"about": f"criterion:{cid}", "text": note})
    return [{"id": f"c{n}", "basis": "synthesized", "cites": [], **c} for n, c in enumerate(out, 1)]


def panel(review, criteria) -> list[dict]:
    """A review's claims grouped the way the side panel lists them: each
    criterion with its score, then strengths, concerns and flags."""
    claims = claims_of(review)
    colours = {row["about"]: row["colour"] for row in legend(criteria)}
    groups = []
    for c in criteria or []:
        about = f"criterion:{c['id']}"
        groups.append(
            {
                "label": c.get("name", c["id"]),
                "weight": c.get("weight"),
                "score": (review.criteria_scores or {}).get(c["id"]),
                "colour": colours.get(about),
                "claims": [x for x in claims if x["about"] == about],
            }
        )
    for about, label in (
        ("strength", "Strengths"),
        ("concern", "Concerns"),
        ("flag", "Flags"),
        ("outcome", "Outcome"),
    ):
        found = [x for x in claims if x["about"] == about]
        if found:
            groups.append({"label": label, "colour": colours.get(about), "claims": found})
    return groups
