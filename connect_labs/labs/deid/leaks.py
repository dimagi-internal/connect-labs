"""The words that would identify someone, and a search for them in what is leaving.

Two users: a coach's note must not name the worker's peers (``workflow/actions.py``),
and a de-identified export must not carry a name, username, phone, email, case id or
GPS point from its source (``labsdata.exports``). Both build terms from what they know
about the real people and search the outgoing text for them as whole words.
"""

from __future__ import annotations

import re
from typing import Any

from .classify import UUID_RE

#: Words too common to count as a name on their own, however a source spells a person.
STOPWORDS = frozenset(
    {
        "yes",
        "no",
        "none",
        "nil",
        "null",
        "the",
        "and",
        "baby",
        "babe",
        "mother",
        "child",
        "twin",
        "boy",
        "girl",
        "male",
        "female",
        "unknown",
        "test",
        "other",
        "mama",
        "mrs",
        "miss",
    }
)


def identity_terms(people: list[tuple[str, str]], worker_name: str = "") -> list[str]:
    """What would identify a peer, from ``(name, username)`` pairs of everyone the
    worker must not see named: each full name, each username, and each word of a
    person-like name (two or more capitalised words) that is not also in the worker's
    own name."""
    own = {w.lower() for w in (worker_name or "").split()}
    terms: set[str] = set()
    for name, username in people:
        name, username = (name or "").strip(), (username or "").strip()
        if len(name) >= 3:
            terms.add(name.lower())
        if len(username) >= 3:
            terms.add(username.lower())
        words = name.split()
        if len(words) >= 2 and all(w[:1].isupper() for w in words):
            terms.update(w.lower() for w in words if len(w) >= 3 and w.lower() not in own)
    return sorted(terms)


def identity_leaks(text: str, terms: list[str]) -> list[str]:
    """The ``terms`` that appear in ``text`` as whole words (case-insensitive)."""
    low = (text or "").lower()
    return [t for t in terms if re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", low)]


def strings(node: Any):
    """Every string inside a nested dict/list."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)


# ---------------------------------------------------------------------------
# Scanning an export
# ---------------------------------------------------------------------------

_PHONE = re.compile(r"(?<!\d)\+?\d{7,}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_GEOPOINT = re.compile(r"-?\d{1,2}\.\d{3,}[ ,]+-?\d{1,3}\.\d{3,}")
_UUID_IN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
#: A word is letters only: "Amina's" and "Amina-Wanjiru" are read as their parts, so a
#: possessive or a double-barrelled name never hides a learned name.
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

#: What the scan reports a hit as. Never the matching text: the report travels with
#: the export and is shown to people signing it off.
PATTERNS = ("phone", "email", "gps", "case_id", "known_name", "known_value")


class LeakScanner:
    """Collects what identifies the people in a source, then scans output for it.

    ``learn`` is fed the source's identifying values (usernames, entity names, the
    values of identifier fields, case ids); ``scan`` checks one output cell and returns
    the kinds of hit it found. Holds the terms in memory for one export, never stores
    them."""

    def __init__(self):
        self.words: set[str] = set()
        self.values: set[str] = set()

    def learn(self, value) -> None:
        text = str(value or "").strip().lower()
        if len(text) < 3:
            return
        if UUID_RE.match(text):
            self.values.add(text)
            return
        self.values.add(text)
        for word in _WORD.findall(text):
            if len(word) >= 3 and not word.isdigit() and word not in STOPWORDS:
                self.words.add(word)

    def learn_value(self, value) -> None:
        """Learn a whole value (an id, a username) without its words."""
        text = str(value or "").strip().lower()
        if len(text) >= 3:
            self.values.add(text)

    def scan(self, cell) -> set[str]:
        text = str(cell or "")
        if not text:
            return set()
        hits = set()
        if _PHONE.search(text):
            hits.add("phone")
        if _EMAIL.search(text):
            hits.add("email")
        if _GEOPOINT.search(text):
            hits.add("gps")
        if _UUID_IN.search(text):
            hits.add("case_id")
        low = text.strip().lower()
        if low in self.values:
            hits.add("known_value")
        if self.words and any(w.lower() in self.words for w in _WORD.findall(text)):
            hits.add("known_name")
        return hits
