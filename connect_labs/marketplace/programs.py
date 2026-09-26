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

# A round that is genuinely not a delivery program. A matching grant is a
# funding instrument and Learning Partners is a capability partnership; both
# cut across programs rather than being one. Recorded explicitly so that
# "nobody has tagged this yet" (blank) stays distinguishable from "somebody
# looked and it is not a program", which is the difference between work to do
# and work that is done.
# Stored data, not display text: people type this value into the directory
# sheet's program column and it is saved on rounds, so it keeps its original
# spelling even though every label now says "program".
PROGRAM_NONE = "not-a-programme"

PROGRAM_NONE_LABEL = "Cross-program"


def label(slug: str | None) -> str:
    """Display text for one program tag."""
    if slug == PROGRAM_NONE:
        return PROGRAM_NONE_LABEL
    return service_label(slug)


# Delivery types that are not field delivery. `other` is Connect's absence of a
# type; `ace` is Dimagi's own tooling running programs through Connect to test
# itself — real rows, real visits, but nothing a partner delivered and nothing a
# funder is buying. Its opportunities carry placeholder budgets (17 live ones
# held $68k against $143 ever paid), so leaving it in inflated every figure it
# touched.
NOT_DELIVERY = frozenset({PROGRAM_NONE, "other", "ace"})


def is_program(slug: str | None) -> bool:
    """Whether this tag names a delivery program rather than its absence."""
    return bool(slug) and slug not in NOT_DELIVERY


def known_slugs() -> set[str]:
    """Every delivery type this codebase has a confirmed name for."""
    return set(SERVICE_LABELS)


def chips(slugs) -> list[dict]:
    """Program tags ready to render, deduplicated and ordered by name.

    Drops `other`, Connect's unclassified bucket, which 264 opportunities sit
    in. Rendering "Unclassified" as a tag would put a label on the absence of
    one, which is the failure this whole vocabulary exists to avoid.
    """
    seen = {s for s in slugs if is_program(s)}
    return sorted(({"slug": s, "label": label(s)} for s in seen), key=lambda c: c["label"])


# One colour per program, so the same program reads as the same thing on
# every page that shows it — the program card, the chip on an organisation's
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


# The picture connect.dimagi.com/portfolio shows for the same program, copied
# into static/images/programs/ rather than hotlinked: the public site serves
# them under content-hashed names that change on every deploy there. Only the
# programs the portfolio actually pictures are listed — a borrowed photo of a
# different program would say something false about this one, so the rest
# show their colour instead.
#
# Each carries the CSS object-position that keeps its people in frame: the
# card crops every picture to one wide band, and a centred crop of a portrait
# photo takes the faces off the top.
_IMAGES = {
    "chc": ("chc.jpg", "50% 35%"),
    "kmc": ("kmc.jpg", "50% 25%"),
    "mbw": ("mbw.jpg", "50% 20%"),
    "readers": ("readers.jpg", "50% 35%"),
    "ecd": ("ecd.jpg", "70% 25%"),
    "nutrition": ("nutrition.svg", "50% 50%"),
    "water": ("water.svg", "50% 50%"),
    "interview": ("interview.svg", "50% 50%"),
}


def image(slug: str | None) -> dict | None:
    """The program's portfolio picture — static path and focal point — if it has one."""
    entry = _IMAGES.get(slug or "")
    return {"src": f"images/programs/{entry[0]}", "focus": entry[1]} if entry else None


# On the marketplace page a card is coloured by the section it sits in, so a
# section reads as one family: green for delivering, amber for in design, blue
# for proven-but-waiting-on-money. Every shade is dark enough to be legible as
# text on white, since the hue also colours the card's links and figures.
SECTION_HUES = {
    "delivering": ("#15803d", "#047857", "#4d7c0f", "#0f766e", "#166534", "#3f6212", "#059669", "#65a30d"),
    "design": ("#b45309", "#a16207", "#c2410c", "#92400e", "#ca8a04", "#9a3412", "#854d0e", "#d97706"),
    "funding": ("#1d4ed8", "#0369a1", "#4338ca", "#1e40af", "#0e7490", "#3730a3", "#2563eb", "#075985"),
}


def section_hue(state: str, index: int) -> str:
    """The index-th shade of a section's family, cycling if it runs out."""
    shades = SECTION_HUES[state]
    return shades[index % len(shades)]
