"""Shared machinery for source loaders.

Loaders fetch, normalise, and hand back rows; ``upsert`` is the only writer.
Keeping the write in one place means every value acquires ``retrieved_at`` and a
licence without each loader having to remember.
"""

from __future__ import annotations

import difflib
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field

import requests
from django.utils import timezone

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import boundaries
from connect_labs.labs.indicators.models import IndicatorValue

logger = logging.getLogger(__name__)

USER_AGENT = "connect-labs-targeting/1.0 (+https://labs.connect.dimagi.com)"
TIMEOUT = 60


def http_json(url: str, params: dict | None = None, retries: int = 3, timeout: int | None = None) -> dict:
    """GET JSON with linear backoff. Raises on final failure."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout or TIMEOUT, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 — retried, then re-raised
            last = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last}")


def http_get_bytes(url: str, params, retries: int = 3, timeout: int | None = None) -> bytes:
    """GET raw bytes. Takes params as a sequence of pairs, not a dict.

    OGC services repeat a parameter to mean "and also" — a WCS subsets on
    ``subset=Lat(...)`` *and* ``subset=Long(...)`` — which a dict cannot express.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout or TIMEOUT, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            return resp.content
        except Exception as exc:  # noqa: BLE001 — retried, then re-raised
            last = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last}")


class RateLimited(RuntimeError):
    """The upstream refused us for quota reasons, not for this request's sake.

    Worth its own type because the right response is the opposite of a normal
    failure: stop the whole run at once. Retrying spends more of a quota that is
    already gone, and a three-retry loop across hundreds of pieces turns one
    refusal into a thousand.
    """


def http_json_post(url: str, data: dict, retries: int = 3, timeout: int | None = None) -> dict:
    """POST form-encoded, expect JSON back.

    Exists because a boundary polygon does not fit in a query string: WorldPop's
    stats endpoint accepts the geometry either way, but a GET of an ADM1 polygon
    returns 414 Request-URI Too Long. Sending it as a body removes the length
    limit entirely, so geometry never has to be degraded to fit a URL.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, data=data, timeout=timeout or TIMEOUT, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 429:
                raise RateLimited(f"POST {url} refused: quota exhausted ({resp.text[:160]})")
            resp.raise_for_status()
            return resp.json()
        except RateLimited:
            raise
        except Exception as exc:  # noqa: BLE001 — retried, then re-raised
            last = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"POST {url} failed after {retries} attempts: {last}")


@dataclass
class Row:
    """One value a loader wants written."""

    indicator: str
    boundary: AdminBoundary
    year: int
    value: float
    source: str
    source_ref: str = ""
    source_url: str = ""
    license_code: str = ""
    method: str = ""
    ci_low: float | None = None
    ci_high: float | None = None
    extra: dict = field(default_factory=dict)


def upsert(rows: list[Row]) -> int:
    """Write rows, replacing any existing value for the same natural key.

    The natural key is ``(indicator, boundary, year, source)`` — so re-running a
    loader refreshes its own numbers and leaves every other source's alone.
    """
    now = timezone.now()
    written = 0
    for r in rows:
        if r.value is None:
            continue
        IndicatorValue.objects.update_or_create(
            indicator=r.indicator,
            boundary=r.boundary,
            year=r.year,
            source=r.source,
            defaults={
                "iso_code": r.boundary.iso_code,
                "admin_level": r.boundary.admin_level,
                "value": float(r.value),
                "ci_low": r.ci_low,
                "ci_high": r.ci_high,
                "source_ref": r.source_ref,
                "source_url": r.source_url,
                "license_code": r.license_code,
                "method": r.method,
                "retrieved_at": now,
                "extra": r.extra,
            },
        )
        written += 1
    return written


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

#: Substrings that appear in survey labels but never in boundary names.
_NOISE = re.compile(
    r"\b(region|province|state|county|district|governorate|zone|department|prefecture)\b",
    re.I,
)
_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")

#: Survey-vocabulary → boundary-vocabulary, for the cases normalisation and
#: token-subset matching cannot reach. Keys AND values must already be in
#: normalised form (lowercase, unaccented, no punctuation) — an alias whose value
#: still contains a hyphen can never match, because boundary keys are normalised
#: too. Entries that normalisation alone already resolves do not belong here.
ALIASES: dict[str, str] = {
    "fct abuja": "abuja",
    "fct": "abuja",
    "nassarawa": "nasarawa",
    "murang a": "muranga",
    "addis ababa": "addis abeba",
    "benishangul gumuz": "benishangul gumz",
    "snnpr": "southern nations nationalities and peoples",
    "kinshasa city": "kinshasa",
    # Not a vocabulary difference — geoBoundaries spells Niger's Dosso region
    # "Dossa". Normalising both sides onto the correct spelling is the least-bad
    # fix; the alternative is loosening the fuzzy cutoff for everyone.
    "dossa": "dosso",
    # Ethiopian zone names reach us transliterated from Amharic while
    # geoBoundaries carries the English. These are the compass words, which are
    # what most of the mismatches turn on.
    "mirab": "west",
    "misraq": "east",
    "semien": "north",
    "debub": "south",
}

#: Applied token-by-token rather than to the whole string, since these appear as
#: one word inside a longer name ("Mirab Welega" -> "west welega").
TOKEN_ALIASES: dict[str, str] = {
    "mirab": "west",
    "misraq": "east",
    "semien": "north",
    "debub": "south",
    "mi irabaw": "west",
}


def normalize_name(raw: str) -> str:
    """Fold a survey's region label toward a boundary name.

    Handles DHS's ``..Benue`` nesting prefix, decorative punctuation, and the
    trailing unit words surveys add but boundary files don't.
    """
    s = (raw or "").strip()
    s = s.lstrip(".").strip()  # DHS marks nesting with leading dots
    s = s.lower()
    # Boundary files and survey exports disagree constantly about accents —
    # "Tillabéri" vs "Tillaberi", "Ségou" vs "Segou". Fold them away.
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.replace("&", " and ")
    s = _PUNCT.sub(" ", s)
    s = _NOISE.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    s = " ".join(TOKEN_ALIASES.get(t, t) for t in s.split())
    return ALIASES.get(s, s)


class BoundaryMatcher:
    """Match survey region labels to ADM1 boundaries for one country.

    Three passes, narrowing: exact on the normalised name, then a token-subset
    match, then a conservative fuzzy pass. The cutoff is deliberately high — a
    wrong region silently attaches a mortality rate to the wrong million people,
    which is far worse than reporting a miss.

    The token-subset pass exists because survey and boundary vocabularies
    disagree about qualifiers rather than about identity: "Tharaka-Nithi" against
    a boundary called "Tharaka", "FCT Abuja" against "Abuja Federal Capital
    Territory". It only fires when exactly one candidate matches and the shared
    tokens carry real information, so "North" cannot capture "North West".
    """

    FUZZY_CUTOFF = 0.85
    #: A token must be at least this long to anchor a subset match. Short tokens
    #: ("nord", "est", "sud") are qualifiers, not identities.
    MIN_ANCHOR = 4

    def __init__(self, iso_code: str, admin_level: int = 1):
        self.iso_code = iso_code
        self.admin_level = admin_level
        self._by_norm: dict[str, AdminBoundary] = {}
        self._ambiguous: set[str] = set()

        for b in boundaries.owned().filter(iso_code=iso_code, admin_level=admin_level):
            key = normalize_name(b.name)
            if key in self._by_norm and self._by_norm[key].pk != b.pk:
                self._ambiguous.add(key)
            self._by_norm.setdefault(key, b)

        self._tokens = {k: set(k.split()) for k in self._by_norm}
        self.misses: list[str] = []

    def __len__(self) -> int:
        return len(self._by_norm)

    def _subset_match(self, key: str) -> AdminBoundary | None:
        want = set(key.split())
        anchors = {t for t in want if len(t) >= self.MIN_ANCHOR}
        if not anchors:
            return None

        hits = [k for k, toks in self._tokens.items() if (want <= toks or toks <= want) and anchors & toks]
        if len(hits) == 1:
            return self._by_norm[hits[0]]
        return None

    def match(self, label: str) -> AdminBoundary | None:
        key = normalize_name(label)
        if not key or key in self._ambiguous:
            return None

        hit = self._by_norm.get(key)
        if hit is not None:
            return hit

        hit = self._subset_match(key)
        if hit is not None:
            return hit

        close = difflib.get_close_matches(key, list(self._by_norm), n=1, cutoff=self.FUZZY_CUTOFF)
        if close:
            return self._by_norm[close[0]]

        self.misses.append(label)
        return None
