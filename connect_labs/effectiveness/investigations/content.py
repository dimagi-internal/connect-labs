"""The tracker's hand-edited content: its shape, the starting copy, and the one check every save goes through.

The page is public, so ``clean`` is strict: it keeps only the fields named here, refuses anything it does not
understand, and accepts a link only if it is ``https``. The page itself builds every node with ``textContent``, so
no field is ever read as HTML; the check on links is what stops a ``javascript:`` address.

Pure: no Django, no database.
"""

from __future__ import annotations

import copy
import datetime
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from connect_labs.effectiveness import engine

DATA = Path(__file__).resolve().parents[1] / "data" / "investigations.json"

GROUP_KEYS = ("active", "invest", "idle")
STAGES = ("At scale", "Piloting", "Designing", "Idea", "Paused", "Completed")
#: Where a program's cost-effectiveness model stands. Only ``public`` shows a number of ours.
MODEL_STATES = ("public", "draft", "planned", "reference", "none")
#: Where a public model's link goes: its Google Sheet, or the model explorer (which needs a sign-in today).
MODEL_LINKS = ("sheet", "explorer")

MAX_PROGRAMS = 60
#: More visits than Connect could plausibly hold; also keeps a typed number a sane size.
MAX_COUNT = 10**10
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SERVICE = re.compile(r"^[a-z0-9-]{1,48}$")


def _is_day(value: str) -> bool:
    if not _DAY.match(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


class ContentError(ValueError):
    """What is wrong with a piece of content, as sentences an editor can act on."""

    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


@lru_cache(maxsize=1)
def _default() -> dict:
    with DATA.open(encoding="utf-8") as f:
        return clean(json.load(f))


def default() -> dict:
    """The copy the page starts from, before anyone has saved a version."""
    return copy.deepcopy(_default())


class _Checker:
    def __init__(self):
        self.errors: list[str] = []

    def text(self, where: str, value, *, limit: int, required: bool = False) -> str:
        if value is None:
            value = ""
        if not isinstance(value, str):
            self.errors.append(f"{where} must be text.")
            return ""
        value = value.strip()
        if required and not value:
            self.errors.append(f"{where} is empty.")
        if len(value) > limit:
            self.errors.append(f"{where} is longer than {limit} characters.")
        return value

    def choice(self, where: str, value, options) -> str:
        if value not in options:
            self.errors.append(f"{where} must be one of: {', '.join(options)}.")
            return options[0]
        return value

    def link(self, where: str, value) -> str:
        value = self.text(where, value, limit=500)
        if value and not value.startswith("https://"):
            self.errors.append(f"{where} must start with https://.")
        return value

    def month(self, where: str, value) -> str:
        value = self.text(where, value, limit=7)
        if value and not (_MONTH.match(value) and 2000 <= int(value[:4]) <= 2100):
            self.errors.append(f"{where} must look like 2026-09 (year and month).")
        return value

    def count(self, where: str, value) -> int:
        if value in (None, ""):
            return 0
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if not ok or value < 0 or value > MAX_COUNT or value != int(value):
            self.errors.append(f"{where} must be a whole number, zero or more.")
            return 0
        return int(value)

    def names(self, where: str, value, *, limit: int = 20) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list) or len(value) > limit:
            self.errors.append(f"{where} must be a list of at most {limit}.")
            return []
        return [v for v in (self.text(where, item, limit=80) for item in value) if v]


def _reference(c: _Checker, where: str, raw) -> dict | None:
    if not raw:
        return None
    if not isinstance(raw, dict):
        c.errors.append(f"{where} is not in the expected shape.")
        return None
    links = []
    raw_links = raw.get("links") or []
    if not isinstance(raw_links, list) or len(raw_links) > 6:
        c.errors.append(f"{where} takes at most 6 links.")
        raw_links = []
    for i, pair in enumerate(raw_links, 1):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            c.errors.append(f"{where}, link {i}: give a label and an address.")
            continue
        label = c.text(f"{where}, link {i} label", pair[0], limit=120, required=True)
        url = c.link(f"{where}, link {i} address", pair[1])
        if not url:
            c.errors.append(f"{where}, link {i} has no address.")
        links.append([label, url])
    ref = {
        "short": c.text(f"{where} short figure", raw.get("short"), limit=60),
        "text": c.text(f"{where} text", raw.get("text"), limit=1200),
        "links": links,
    }
    return ref if (ref["short"] or ref["text"] or links) else None


def _program(c: _Checker, n: int, raw) -> dict:
    if not isinstance(raw, dict):
        c.errors.append(f"Program {n} is not in the expected shape.")
        return {}
    name = c.text(f"Program {n} name", raw.get("name"), limit=120, required=True)
    where = f"{name or 'Program ' + str(n)}:"
    p = {
        "name": name,
        "group": c.choice(f"{where} group", raw.get("group"), GROUP_KEYS),
        "stage": c.choice(f"{where} stage", raw.get("stage"), STAGES),
        "now": c.names(f"{where} countries now", raw.get("now")),
        "earlier": c.text(f"{where} earlier countries", raw.get("earlier"), limit=300),
        "planned": c.text(f"{where} planned countries", raw.get("planned"), limit=300),
        "place": c.text(f"{where} place", raw.get("place"), limit=120),
        "visits": c.count(f"{where} verified visits", raw.get("visits")),
        "since": c.month(f"{where} start month", raw.get("since")),
        "ended": c.month(f"{where} end month", raw.get("ended")),
        "model": c.choice(f"{where} model status", raw.get("model"), MODEL_STATES),
        "model_text": c.text(f"{where} model label", raw.get("model_text"), limit=60, required=True),
        "model_key": c.text(f"{where} Connect model", raw.get("model_key"), limit=12),
        "sheet": c.link(f"{where} model sheet", raw.get("sheet")),
        "what": c.text(f"{where} what it is", raw.get("what"), limit=600, required=True),
        "done": c.text(f"{where} delivery", raw.get("done"), limit=300),
        "note": c.text(f"{where} cost-effectiveness note", raw.get("note"), limit=600),
        "next": c.text(f"{where} next step", raw.get("next"), limit=400),
    }
    if p["model_key"] and p["model_key"] not in engine.MODELS:
        c.errors.append(f"{where} Connect model must be one of: {', '.join(engine.MODELS)} (or left empty).")
        p["model_key"] = ""
    if p["model_key"] and p["model"] != "public":
        # A number of ours is shown only for a model we have published: a draft's result must not reach this page.
        c.errors.append(f"{where} a Connect model can be named only when the model status is public.")
    if p["model"] == "public" and not p["model_key"]:
        c.errors.append(f"{where} a public model must name its Connect model.")
    ref = _reference(c, f"{where} reference", raw.get("ref"))
    if ref:
        p["ref"] = ref
    connect = raw.get("connect") or {}
    services = c.names(
        f"{where} Connect delivery types", connect.get("services") if isinstance(connect, dict) else connect, limit=8
    )
    for s in services:
        if not _SERVICE.match(s):
            c.errors.append(f"{where} delivery type {s!r} is not a Connect delivery type code.")
    if services:
        p["connect"] = {"services": services}
    return p


def clean(raw) -> dict:
    """``raw`` as content the page can show, or ``ContentError`` naming everything wrong with it."""
    c = _Checker()
    if not isinstance(raw, dict):
        raise ContentError(["The content is not in the expected shape."])
    stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
    snapshot_date = c.text("Snapshot date", raw.get("snapshot_date"), limit=10, required=True)
    if snapshot_date and not _is_day(snapshot_date):
        c.errors.append("Snapshot date must look like 2026-11-18.")

    groups = []
    raw_groups = raw.get("groups") if isinstance(raw.get("groups"), list) else []
    by_key = {g["key"]: g for g in raw_groups if isinstance(g, dict) and isinstance(g.get("key"), str)}
    for key in GROUP_KEYS:
        g = by_key.get(key) or {}
        groups.append(
            {
                "key": key,
                "title": c.text(f"Group {key} title", g.get("title"), limit=60, required=True),
                "def": c.text(f"Group {key} description", g.get("def"), limit=200),
            }
        )

    footer = []
    raw_footer = raw.get("footer") if isinstance(raw.get("footer"), list) else []
    if len(raw_footer) > 6:
        c.errors.append("The footer takes at most 6 paragraphs.")
    for i, para in enumerate(raw_footer[:6], 1):
        para = para if isinstance(para, dict) else {}
        text = c.text(f"Footer paragraph {i}", para.get("text"), limit=1200)
        if text:
            footer.append({"heading": c.text(f"Footer heading {i}", para.get("heading"), limit=120), "text": text})

    raw_programs = raw.get("programs")
    if not isinstance(raw_programs, list) or not raw_programs:
        c.errors.append("There are no programs.")
        raw_programs = []
    if len(raw_programs) > MAX_PROGRAMS:
        c.errors.append(f"There are more than {MAX_PROGRAMS} programs.")
        raw_programs = raw_programs[:MAX_PROGRAMS]
    programs = [_program(c, n, p) for n, p in enumerate(raw_programs, 1)]
    names = [p.get("name", "").lower() for p in programs if p.get("name")]
    for name in sorted({n for n in names if names.count(n) > 1}):
        c.errors.append(f"Two programs are both called {name!r}.")

    out = {
        "title": c.text("Title", raw.get("title"), limit=120, required=True),
        "lede": c.text("Introduction", raw.get("lede"), limit=800),
        "models_note": c.text("Models note", raw.get("models_note"), limit=200),
        "bar_note": c.text("Funding bar date", raw.get("bar_note"), limit=40),
        "snapshot_date": snapshot_date,
        "model_link": c.choice("Model link", raw.get("model_link") or "sheet", MODEL_LINKS),
        "stats": {
            "visits": c.count("Verified visits to date", stats.get("visits")),
            "countries": c.text("Countries", stats.get("countries"), limit=12),
            "organizations": c.text("Local organizations", stats.get("organizations"), limit=12),
        },
        "groups": groups,
        "footer": footer,
        "programs": programs,
    }
    if c.errors:
        raise ContentError(c.errors)
    return out
