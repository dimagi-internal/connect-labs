"""Read a handout message (a pasted WhatsApp text, an email body) and match it to a planned run.

Deterministic on purpose. A partner writes "Wuse PHC — 12 cartons" or "12 cartons to Wuse PHC",
one place per line, often copied out of WhatsApp with its timestamps. This reads those shapes and
nothing cleverer: a line it cannot read is handed back as unread, never guessed at, and a name
that is not clearly one planned place is left for a person to match. (No OCR or language model
sits behind this: a photo of a paper list is kept as evidence and matched by hand on the same
screen.)

Pure functions, no database: `read_message` -> entries, `match_to_plan` -> a proposal.
"""

import re
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

# "[12/10/2026, 09:41:02] Name: " and "12/10/26, 9:41 am - Name: " -- the prefix WhatsApp puts on each message.
_STAMP = re.compile(
    r"^\s*\[?\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4},?\s+\d{1,2}[:.]\d{2}(?:[:.]\d{2})?\s*(?:[ap]\.?m\.?)?\]?\s*[-–—]?\s*"
    r"(?:[^:]{1,40}:\s)?",
    re.I,
)
_BULLET = re.compile(r"^\s*(?:[-•*·▪●✅✔☑>]+|\(?\d{1,3}[.)])\s+")
_QTY = r"(?P<qty>\d{1,3}(?:[ ,]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
_UNIT = r"(?P<unit>[A-Za-z]{2,12})"
# "Wuse PHC - 12 cartons", "Wuse PHC: 12", "Wuse PHC = 12 ctn", "Wuse PHC 12 cartons"
_NAME_FIRST = re.compile(rf"^(?P<name>.*?[A-Za-z].*?)\s*(?:[-–—:=]|\s)\s*{_QTY}\s*{_UNIT}?\s*\.?$")
# "12 cartons to Wuse PHC", "12 cartons for Wuse PHC"
_QTY_FIRST = re.compile(rf"^{_QTY}\s*{_UNIT}?\s+(?:to|for|->|→)\s+(?P<name>.+?)\s*\.?$", re.I)

UNIT_WORDS = {
    "carton": "carton",
    "cartons": "carton",
    "ctn": "carton",
    "ctns": "carton",
    "box": "carton",
    "boxes": "carton",
    "sachet": "sachet",
    "sachets": "sachet",
    "sach": "sachet",
}
# Words that say nothing about WHICH place: "Wuse PHC" and "Wuse Primary Health Centre" are the same one.
_GENERIC = {
    "phc",
    "primary",
    "health",
    "centre",
    "center",
    "clinic",
    "hc",
    "chc",
    "post",
    "the",
    "of",
    "facility",
    "ward",
    "to",
    "for",
}


def _quantity(text) -> Decimal | None:
    try:
        return Decimal(str(text).replace(" ", "").replace(",", ""))
    except InvalidOperation:
        return None


def read_message(text: str) -> dict:
    """{entries: [{raw, name, quantity, unit}], unread: [raw lines that held no place and number]}."""
    entries, unread = [], []
    for raw in (text or "").splitlines():
        line = _STAMP.sub("", raw.strip())
        line = _BULLET.sub("", line).strip(" \t*_~")
        if not line:
            continue
        found = _QTY_FIRST.match(line)
        if found is None:
            found = _NAME_FIRST.match(line)
        if found is None:
            unread.append(raw.strip())
            continue
        quantity = _quantity(found.group("qty"))
        unit_word = (found.group("unit") or "").lower()
        name = found.group("name").strip(" \t-–—:=*_~.,")
        # "Wuse PHC 12" where the "unit" group swallowed nothing; or a word after the number that is not a unit.
        unit = UNIT_WORDS.get(unit_word, "")
        if unit_word and not unit:
            unread.append(raw.strip())
            continue
        if quantity is None or not name:
            unread.append(raw.strip())
            continue
        entries.append({"raw": raw.strip(), "name": name, "quantity": quantity, "unit": unit})
    return {"entries": entries, "unread": unread}


def _tokens(name: str) -> frozenset:
    words = re.findall(r"[a-z0-9]+", name.lower())
    kept = [w for w in words if w not in _GENERIC]
    return frozenset(kept or words)


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if ta == tb:
        return 1.0
    if ta and tb and (ta <= tb or tb <= ta):
        return 0.9
    return SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()


ACCEPT = 0.8


def match_to_plan(entries: list[dict], planned: list[dict]) -> dict:
    """Match each message entry to at most one planned line, and compare the quantities.

    `planned` is [{line_id, name, quantity, unit}]. Returns {lines, extras}: `lines` has one row per
    planned line (status matches / differs / unit_differs / not_mentioned, with `given` and
    `difference`); `extras` is every entry that was not clearly one planned place (a handout nobody
    planned, or a name that needs matching by hand). Two entries for one place are both left to a
    person rather than added up.
    """
    taken: dict[int, dict] = {}
    extras = []
    for entry in entries:
        scored = sorted(((similarity(entry["name"], p["name"]), p) for p in planned), key=lambda s: -s[0])
        best = scored[0] if scored else None
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        clear = best is not None and best[0] >= ACCEPT and best[0] - runner_up >= 0.1
        if not clear:
            extras.append({**entry, "reason": "not clearly one of the planned places"})
            continue
        line_id = best[1]["line_id"]
        if line_id in taken:
            extras.append({**entry, "reason": f"{best[1]['name']} appears twice in the message"})
            continue
        taken[line_id] = entry
    rows = []
    for p in planned:
        entry = taken.get(p["line_id"])
        row = {
            "line_id": p["line_id"],
            "name": p["name"],
            "planned": p["quantity"],
            "unit": p["unit"],
            "given": None,
            "difference": None,
            "status": "not_mentioned",
            "heard_as": None,
        }
        if entry is not None:
            row["heard_as"] = entry["raw"]
            if entry["unit"] and entry["unit"] != _canonical(p["unit"]):
                row["status"] = "unit_differs"
                row["message_unit"] = entry["unit"]
                row["given"] = entry["quantity"]
            else:
                row["given"] = entry["quantity"]
                row["difference"] = entry["quantity"] - Decimal(str(p["quantity"]))
                row["status"] = "matches" if row["difference"] == 0 else "differs"
        rows.append(row)
    return {"lines": rows, "extras": extras}


def _canonical(unit: str) -> str:
    return UNIT_WORDS.get((unit or "").lower(), (unit or "").lower())
