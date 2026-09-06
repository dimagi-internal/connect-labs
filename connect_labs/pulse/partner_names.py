"""Resolve a Connect org slug to the delivery partner behind it.

Connect publishes partner **names** only for the orgs the polling account is a
member of — a small minority of those that actually deliver. The rest arrive as
a slug and carry most of the delivery, and no export endpoint will give up their
names (``OpportunitySerializer`` also emits ``organization`` as a slug).

So the names come from the team's LLO Directory, loaded into ``PulsePartner`` by
``pulse_partner_import`` and matched to slugs here. The sheet is the source of
truth; this is a cache of it. Nothing about partner identity is written down in
this repository, because the people who own that identity do not review pull
requests — and a name that lives in code drifts from the directory with nothing
to detect the drift.

**Several Connect orgs can be the same real partner.** Connect models this
properly — ``organization.LLOEntity`` with ``Organization.llo_entity`` pointing
at it — but ``OrganizationDataExportSerializer`` does not expose the FK, so the
grouping cannot be read from the API. Matching to the directory reconstructs it:
a partner running two workspaces otherwise shows up as two unrelated partners.

Three rules keep this honest:

**Connect's own name wins.** It is a real name rather than an inference, and it
also matches the directory far better than a slug does.

**Only high-confidence tiers are applied.** A wrong parent name is worse than a
visible slug, so ``subset`` and ``fuzzy`` results are returned for a human to
confirm and never displayed as fact. Slugs are never de-slugified into a guess:
mechanical title-casing reads plausibly and is wrong exactly where a partner's
real name is stylised, hyphenated or capitalised unusually.

**What no rule can reach becomes an alias, not a looser rule.** A handful of
real partners are unreachable by any string comparison — a second workspace
sharing no stem with the first, an abbreviation the slug never spells out, a
typo in the directory itself. Loosening the matcher would buy those few at the
cost of guessing everywhere else, so they are confirmed by a human on the
directory's mapping tab and carried in ``PulsePartnerAlias``.

An empty table is safe rather than wrong: every partner renders as its slug,
exactly as an unmatched slug always has, and no name is ever guessed.
"""

from __future__ import annotations

import difflib
import re
import time
import unicodedata

# Tiers safe to display. Everything else is advisory only.

HIGH_CONFIDENCE = frozenset({"exact", "truncated", "suffixed", "same-tokens", "alias"})
REVIEW = frozenset({"subset", "fuzzy"})

# French/NGO prefixes Connect carries that the master list does not.
_LEAD = re.compile(r"^(ong|ongd|ngo|asbl)-")

# Applied ONLY to slugs that matched nothing. Never as a pre-filter: an earlier
# version keyed "looks like a person" off a loose pattern and filed the single
# largest delivery partner as a personal workspace. Real partners also run
# workspaces whose slugs read internal, so this cannot run before matching.
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


# Connectives carry no identity: "Peace Restoration And Integral Global
# Development Initiative" and its slug (which drops the "and") are the same
# organisation, and keeping such words demoted real partners like PRIDE to a
# review-only subset match -- shown as a raw slug, unfindable by their name.
_STOPWORDS = frozenset({"and", "of", "the", "for", "a", "an", "de", "du", "des", "la", "le", "les", "et", "da", "di"})


def _stems(value: str) -> set:
    """Plural- and connective-insensitive words, so "preterm-infants-…" meets
    "Preterm Infant …" and a slug that drops an "and" still meets its name."""
    return {t[:-1] if len(t) > 3 and t.endswith("s") else t for t in _norm(value).split() if t not in _STOPWORDS}


# The directory changes when someone edits it, not when we deploy, so a process
# must pick up an import without being restarted — but resolve() is called once
# per partner per request and cannot afford a query each time. A short TTL is
# the whole of the compromise: at most this many seconds of staleness, and one
# query per process per window.
_CACHE_TTL_SECONDS = 60
_cache: dict = {"loaded_at": 0.0, "candidates": [], "aliases": ()}


def invalidate() -> None:
    """Drop the cache. Called after an import, and by tests that seed partners."""
    _cache["loaded_at"] = 0.0


def _build(name: str, short: str, index: int) -> dict:
    keys, shorts = set(), set()
    bare = re.sub(r"\([^)]*\)", " ", name)
    for drop in (False, True):
        keys.add(_slugify(name, drop))
        keys.add(_slugify(bare, drop))
        if short:
            shorts.add(_slugify(short, drop))
    # "Centre for … (ACRONYM)" — the parenthetical is how people refer to it.
    for paren in re.findall(r"\(([^)]+)\)", name):
        shorts.add(_slugify(paren))
    return {
        "index": index,
        "name": name,
        "short": short,
        "keys": {k for k in keys if k},
        "shorts": {s for s in shorts if s},
        "stems": _stems(name),
        "nameslug": _slugify(bare) or _slugify(name),
    }


def _load() -> None:
    if _cache["loaded_at"] and (time.monotonic() - _cache["loaded_at"]) < _CACHE_TTL_SECONDS:
        return
    from connect_labs.pulse.models import PulsePartner, PulsePartnerAlias

    rows = list(PulsePartner.objects.values_list("name", "short"))
    _cache["candidates"] = [_build(name, short or "", i) for i, (name, short) in enumerate(rows) if name]
    _cache["aliases"] = tuple(PulsePartnerAlias.objects.select_related("partner").values_list("slug", "partner__name"))
    _cache["loaded_at"] = time.monotonic()


def _candidates() -> list:
    _load()
    return _cache["candidates"]


def _aliases() -> tuple:
    _load()
    return _cache["aliases"]


def _alias(slug: str) -> str:
    """The curated parent for a slug, or "" — exact key or ``key-`` prefix."""
    for key, name in _aliases():
        if slug == key or slug.startswith(key + "-"):
            return name
    return ""


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
                return cand, "same-tokens", "identical words, ignoring plurals and connectives"

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

    # A human decision outranks any inference, including Connect's own name.
    alias = _alias(slug)
    if alias:
        cand = next((c for c in _candidates() if c["name"] == alias), None)
        return {
            "slug": slug,
            "parent": alias,
            "short": cand["short"] if cand else "",
            "tier": "alias",
            "why": "confirmed by hand on the directory's Connect Org Mapping tab",
            "review": None,
        }

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
