"""Resolve a Connect org slug to the delivery partner behind it.

Connect publishes partner **names** only for the orgs the polling account is a
member of — 10 of the 74 that actually deliver. The other 64 arrive as a slug
and carry 92% of all services, and no export endpoint will give up their names
(``OpportunitySerializer`` also emits ``organization`` as a slug).

So the names come from the team's master Organizations list, snapshotted into
``data/partner_master.json``, and are matched to slugs here.

**Several Connect orgs can be the same real partner.** Connect models this
properly — ``organization.LLOEntity`` with ``Organization.llo_entity`` pointing
at it — but ``OrganizationDataExportSerializer`` does not expose the FK, so the
grouping cannot be read from the API. Matching to the master list reconstructs
it: Solina Health runs both ``solina`` (382,942 services) and
``connect-nigeria`` (20,813, named "Solina ECD Nigeria"), which the display
otherwise showed as two unrelated partners.

Two rules keep this honest:

**Connect's own name wins.** It is a real name rather than an inference, and it
also matches the master list far better than a slug does — matching on it is
what links ``connect-nigeria`` to Solina at all.

**Only high-confidence tiers are applied.** A wrong parent name is worse than a
visible slug, so ``subset`` and ``fuzzy`` results are returned for a human to
confirm and never displayed as fact. Slugs are never de-slugified into a guess:
title-casing turns the real "C-WINS DGw" into "C Wins Dgw" and "EHA Clinics
REACH" into "Eha Clinics Reach".

Measured against labs prod on 2026-07-31: 97.0% of lifetime services resolve to
a parent at high confidence.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "partner_master.json"

# Tiers safe to display. Everything else is advisory only.
HIGH_CONFIDENCE = frozenset({"exact", "truncated", "suffixed", "same-tokens"})
REVIEW = frozenset({"subset", "fuzzy"})

# French/NGO prefixes Connect carries that the master list does not.
_LEAD = re.compile(r"^(ong|ongd|ngo|asbl)-")

# Applied ONLY to slugs that matched nothing. Never as a pre-filter: an earlier
# version keyed "looks like a person" off a loose pattern and filed `solina` —
# 382,942 services — as a personal workspace, and `connect-nigeria` reads
# internal but is really Solina ECD Nigeria.
_INTERNAL = re.compile(
    r"^(dimagi|ai-demo-space|auto-connect|march-demo|ccc-)|(^|-)(test|sandbox)(-|_|$)|_test|test_",
    re.I,
)


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _norm(value: str, drop_apostrophe: bool = False) -> str:
    text = _strip_accents(value).lower().replace("&", " and ").replace("’", "'")
    if drop_apostrophe:
        # Connect slugifies "D'Entraide" to "dentraide"; splitting on the
        # apostrophe instead gives "d-entraide" and the two never meet.
        text = text.replace("'", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _slugify(value: str, drop_apostrophe: bool = False) -> str:
    return _norm(value, drop_apostrophe).replace(" ", "-")


def _stems(value: str) -> set:
    """Plural-insensitive words, so "preterm-infants-…" meets "Preterm Infant …"."""
    return {t[:-1] if len(t) > 3 and t.endswith("s") else t for t in _norm(value).split()}


@lru_cache(maxsize=1)
def _candidates() -> list:
    raw = json.loads(DATA.read_text())
    out = []
    for index, org in enumerate(raw.get("organizations") or []):
        name = org.get("name") or ""
        short = org.get("short") or ""
        if not name:
            continue
        keys, shorts = set(), set()
        bare = re.sub(r"\([^)]*\)", " ", name)
        for drop in (False, True):
            keys.add(_slugify(name, drop))
            keys.add(_slugify(bare, drop))
            if short:
                shorts.add(_slugify(short, drop))
        # "Centre for … (C-WINS)" — the parenthetical is how people refer to it.
        for paren in re.findall(r"\(([^)]+)\)", name):
            shorts.add(_slugify(paren))
        out.append(
            {
                "index": index,
                "name": name,
                "short": short,
                "keys": {k for k in keys if k},
                "shorts": {s for s in shorts if s},
                "stems": _stems(name),
                "nameslug": _slugify(bare) or _slugify(name),
            }
        )
    return out


def _variants(slug: str) -> set:
    return {v for v in {slug, _LEAD.sub("", slug)} if v}


def _match_text(text: str):
    """Best candidate for one piece of text, with the tier that found it."""
    variants = _variants(_slugify(text))

    for value in variants:
        for cand in _candidates():
            if value in cand["keys"] or value in cand["shorts"]:
                return cand, "exact", "equals the master name, short name or acronym"

    # Connect truncates org slugs around 41 characters, so a long slug is often
    # a prefix of the real name: `zenith-of-the-girl-child-and-women-initiat`.
    for value in variants:
        for cand in _candidates():
            if len(value) >= 10 and cand["nameslug"].startswith(value) and len(_norm(value).split()) >= 2:
                return cand, "truncated", "master name begins with the slug"

    # Or it extends one with a workspace suffix: `c-wins-dgw`, `isodaf-kogi-1`.
    for value in variants:
        for cand in _candidates():
            for key in sorted(cand["keys"] | cand["shorts"], key=len, reverse=True):
                if len(key) >= 3 and value.startswith(key + "-"):
                    return cand, "suffixed", f"begins with '{key}-'"

    for value in variants:
        stems = _stems(value.replace("-", " "))
        for cand in _candidates():
            if stems and stems == cand["stems"]:
                return cand, "same-tokens", "identical words, ignoring plurals"

    # Below here is advisory only.
    best = None
    for value in variants:
        stems = _stems(value.replace("-", " "))
        for cand in _candidates():
            if len(stems) >= 3 and stems <= cand["stems"]:
                if best is None or len(cand["stems"]) < len(best[0]["stems"]):
                    best = (cand, "subset", "slug words are a subset of the master name")
    if best:
        return best

    pool = {k: c for c in _candidates() for k in (c["keys"] | c["shorts"])}
    for value in variants:
        near = difflib.get_close_matches(value, list(pool), n=1, cutoff=0.86)
        if near:
            return pool[near[0]], "fuzzy", f"close to '{near[0]}' — likely a spelling difference"

    return None, "none", ""


def resolve(slug: str, connect_name: str = "") -> dict:
    """The partner behind a Connect org slug.

    ``connect_name`` is Connect's own name for the org when it published one. It
    is tried first because it is a real name rather than an inference, and
    because it matches the master list far more reliably than a slug.

    Returns ``parent`` only for a high-confidence tier. A ``review`` result is
    the caller's cue to ask a human, not to display anything.
    """
    slug = (slug or "").strip()
    if not slug:
        return {"slug": slug, "parent": "", "short": "", "tier": "none", "why": "", "review": None}

    for source, text in (("connect name", connect_name), ("slug", slug)):
        if not text:
            continue
        cand, tier, why = _match_text(text)
        if cand is not None and tier in HIGH_CONFIDENCE:
            return {
                "slug": slug,
                "parent": cand["name"],
                "short": cand["short"],
                "tier": tier,
                "why": f"{why} (via {source})",
                "review": None,
            }

    cand, tier, why = _match_text(slug)
    if cand is not None and tier in REVIEW:
        # Surfaced so it can be confirmed, never shown as the partner's name.
        return {
            "slug": slug,
            "parent": "",
            "short": "",
            "tier": tier,
            "why": why,
            "review": {"candidate": cand["name"], "short": cand["short"], "why": why},
        }

    return {
        "slug": slug,
        "parent": "",
        "short": "",
        "tier": "not-an-llo" if _INTERNAL.search(slug) else "none",
        "why": "internal, test or demo workspace" if _INTERNAL.search(slug) else "",
        "review": None,
    }
