"""Generator for `tailwind/safelist-generated.txt`.

**Why this file exists.**

`tailwind/tailwind.css` uses Tailwind v4 automatic source detection, which
scans this repo's own tree. A workflow's `render_code` does NOT live in this
tree — it lives in the LabsRecord store (a `workflow_render_code` record, see
`connect_labs/workflow/data_access.py`) and is transpiled in the browser at
render time. So Tailwind never sees it, and any utility a dashboard author
reaches for that labs' own templates happen not to use is purged out of the
bundle.

It fails **silently**. A missing background-colour utility computes to
`rgba(0,0,0,0)` (an invisible chart bar); a missing text-colour utility
inherits near-black; a missing height utility collapses the element to zero
height while its inline percentage heights stay valid and the DOM still
reports the element as present. Text assertions and screenshot lenses all pass
while a viewer sees nothing.

And the purge is **per-utility, not per-family**, so there is no rule an author
can hold in their head. Measured on a bundle built from this repo (writing the
class names with the family and shade split apart, because Tailwind scans this
very file and a literal utility token in a comment would safelist itself --
which is worth knowing in its own right, and is exactly how fragile the
accidental-coverage status quo is):

    prefix   family    shade  ->  in bundle?
    text     slate       700      yes
    bg       slate       400      NO
    bg       emerald     500      yes
    bg       emerald     600      NO
    text     rose        600      yes
    text     rose        700      NO
    h        -            28      NO
    text     (arbitrary 11px)     yes
    min-w    (arbitrary 52px)     NO

Real cost: the rose-700 text utility styled "consent 89.7% - below the 90%
floor", the only pay-affecting figure on an LLO weekly-review dashboard, as
near-black for an unknown number of runs; and a missing height-28 utility
rendered a whole 12-week bar chart at zero height.
See dimagi-internal/connect-labs#1294.

**What this fixes.** Emitting the full colour palette and the standard spacing
scale as an explicit safelist gives DB-stored render code a guaranteed floor
that does not depend on what labs' own UI happens to use this week — which is
the property the accidental-coverage status quo lacks.

**Cost, measured** (`npx webpack --mode production --config webpack/prod.config.js`,
`connect_labs/static/bundles/css/tailwind.css`, this repo, Tailwind 4.1.11):

    tier                                              raw       gzip
    baseline (no generated safelist)               365,976     53,945
    bg/text/border colours only            (726)   404,561     58,708
    THIS FILE: + spacing/sizing scale     (1,776)  475,909     64,757
    + ring/fill/stroke colours            (2,502)  521,033     69,701
    + all 16 colour prefixes              (3,872)  908,460     86,965
    + all 16 colour prefixes and spacing  (4,922)  979,808     92,736

+10.8 KB gzip over baseline. The tiers above it were rejected on cost: the
thirteen extra colour prefixes (`from-`/`via-`/`to-`, `divide-`, `shadow-`,
etc.) cost a further 22 KB gzip for utilities no observed dashboard failure has
involved. They can be added here if one ever does.

**Arbitrary values are deliberately out of scope.** An arbitrary-value utility
(a bracketed literal such as a hard-coded pixel min-width) cannot be
enumerated, so no safelist can cover it. Note that arbitrary-vs-standard is NOT
the axis: some arbitrary values survive because labs' own templates happen to
use them, and plenty of ordinary utilities do not. The governing rule is just
"whatever string labs' own scanned sources contain". That residual is what the
write-time check in `connect_labs/workflow/render_code_lint.py` is for.

Regenerate with::

    python manage.py generate_tailwind_safelist

`connect_labs/labs/tests/test_tailwind_safelist.py` fails if the committed file
drifts from this generator.
"""

from pathlib import Path

# Tailwind v4's default colour palette.
PALETTE_FAMILIES = (
    "slate",
    "gray",
    "zinc",
    "neutral",
    "stone",
    "red",
    "orange",
    "amber",
    "yellow",
    "lime",
    "green",
    "emerald",
    "teal",
    "cyan",
    "sky",
    "blue",
    "indigo",
    "violet",
    "purple",
    "fuchsia",
    "pink",
    "rose",
)

SHADES = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950)

# Colour-taking prefixes. Kept deliberately narrow — see the cost table above.
COLOR_PREFIXES = ("bg", "text", "border")

# Tailwind v4's default spacing scale.
SPACING = (
    "0",
    "px",
    "0.5",
    "1",
    "1.5",
    "2",
    "2.5",
    "3",
    "3.5",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "11",
    "12",
    "14",
    "16",
    "20",
    "24",
    "28",
    "32",
    "36",
    "40",
    "44",
    "48",
    "52",
    "56",
    "60",
    "64",
    "72",
    "80",
    "96",
)

SIZE_PREFIXES = (
    "h",
    "w",
    "min-h",
    "min-w",
    "max-h",
    "max-w",
    "p",
    "px",
    "py",
    "pt",
    "pr",
    "pb",
    "pl",
    "m",
    "mx",
    "my",
    "mt",
    "mr",
    "mb",
    "ml",
    "gap",
    "gap-x",
    "gap-y",
    "space-x",
    "space-y",
    "top",
    "right",
    "bottom",
    "left",
    "inset",
)

HEADER = """\
# GENERATED FILE - DO NOT EDIT BY HAND.
#
# Regenerate with:  python manage.py generate_tailwind_safelist
# Generator + rationale + measured bundle cost:
#   connect_labs/labs/tailwind_safelist.py
#
# Tailwind scans this file (see `@source "./safelist-generated.txt"` in
# tailwind/tailwind.css) so that workflow render_code -- which lives in the
# database and is therefore never scanned -- has a guaranteed floor of colour
# and spacing utilities. Without it, whichever utilities labs' own templates
# happen not to use are purged, and a dashboard binding one renders invisible
# with no error. See dimagi-internal/connect-labs#1294.
#
# The hand-maintained companion list is tailwind/safelists.txt.
"""

SAFELIST_RELPATH = Path("tailwind") / "safelist-generated.txt"


def color_utilities() -> list[str]:
    return [
        f"{prefix}-{family}-{shade}" for prefix in COLOR_PREFIXES for family in PALETTE_FAMILIES for shade in SHADES
    ]


def size_utilities() -> list[str]:
    return [f"{prefix}-{value}" for prefix in SIZE_PREFIXES for value in SPACING]


def generate_safelist() -> str:
    """Return the full contents of `tailwind/safelist-generated.txt`."""
    utilities = color_utilities() + size_utilities()
    return HEADER + "\n" + "\n".join(utilities) + "\n"


def safelist_path(base_dir: Path | str) -> Path:
    return Path(base_dir) / SAFELIST_RELPATH
