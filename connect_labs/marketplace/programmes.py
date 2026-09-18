"""What an organisation actually works on, in Connect's own words.

The directory used to carry a "Primary Sector(s)" column, which organisations
filled in themselves. It could not survive contact with use: 191 of 240 orgs
said "Health", `WASH (Water, Sanitation, Hygiene)` comma-split into three
separate sectors, and the long tail held single-org values like "We are a
multidisciplinary consulting firm." A filter where four fifths of the registry
sits under one value and the rest is spelling variants is not a filter.

So the vocabulary here is **Connect's `delivery_type`** — the tag prod already
puts on every opportunity, which pulse mirrors onto `PulseOpportunity`,
`PulseEvent` and `PulseWork`. Using it means the two questions worth asking of
this registry are asked in the same word:

    what has this organisation DELIVERED   (from the pulse spine)
    what has it APPLIED to                 (from its EOI/RFP submissions)

Nothing here invents a label. `service_label()` is pulse's, and it deliberately
falls through to the slug in capitals rather than guessing — three labels were
confidently wrong for months when that table was first written from slugs
alone, and a wrong label does not look uncertain.
"""

from __future__ import annotations

from connect_labs.pulse.normalize import SERVICE_LABELS, service_label

# A round that is genuinely not a delivery programme. A matching grant is a
# funding instrument and Learning Partners is a capability partnership; both
# cut across programmes rather than being one. Recorded explicitly so that
# "nobody has tagged this yet" (blank) stays distinguishable from "somebody
# looked and it is not a programme", which is the difference between work to do
# and work that is done.
PROGRAMME_NONE = "not-a-programme"

PROGRAMME_NONE_LABEL = "Cross-programme"


def label(slug: str | None) -> str:
    """Display text for one programme tag."""
    if slug == PROGRAMME_NONE:
        return PROGRAMME_NONE_LABEL
    return service_label(slug)


# Delivery types that are not field delivery. `other` is Connect's absence of a
# type; `ace` is Dimagi's own tooling running programmes through Connect to test
# itself — real rows, real visits, but nothing a partner delivered and nothing a
# funder is buying. Its opportunities carry placeholder budgets (17 live ones
# held $68k against $143 ever paid), so leaving it in inflated every figure it
# touched.
NOT_DELIVERY = frozenset({PROGRAMME_NONE, "other", "ace"})


def is_programme(slug: str | None) -> bool:
    """Whether this tag names a delivery programme rather than its absence."""
    return bool(slug) and slug not in NOT_DELIVERY


def known_slugs() -> set[str]:
    """Every delivery type this codebase has a confirmed name for."""
    return set(SERVICE_LABELS)


def chips(slugs) -> list[dict]:
    """Programme tags ready to render, deduplicated and ordered by name.

    Drops `other`, Connect's unclassified bucket, which 264 opportunities sit
    in. Rendering "Unclassified" as a tag would put a label on the absence of
    one, which is the failure this whole vocabulary exists to avoid.
    """
    seen = {s for s in slugs if is_programme(s)}
    return sorted(({"slug": s, "label": label(s)} for s in seen), key=lambda c: c["label"])


# One colour per programme, so the same programme reads as the same thing on
# every page that shows it — the programme card, the chip on an organisation's
# row, the round's tag. Picked to stay distinguishable from its neighbours and
# legible as text on white; anything unlisted falls back to the brand indigo.
_HUES = {
    "chc": "#3843d0",
    "kmc": "#cf4270",
    "readers": "#c07a0a",
    "malaria": "#0e8585",
    "nutrition": "#3d8a52",
    "water": "#1b8fc4",
    "ecd": "#7b46c9",
    "mbw": "#d0573f",
    "cholera": "#a8412e",
    "ivp": "#4a7fd6",
    "wellme": "#8a4a9e",
    "hhs": "#6f7f26",
    "tms": "#a17a1c",
    "conversation": "#b04f8a",
    "interview": "#5d6678",
}


def hue(slug: str | None) -> str:
    return _HUES.get(slug or "", "#3843d0")
