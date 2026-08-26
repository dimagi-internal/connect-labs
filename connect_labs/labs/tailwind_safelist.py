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

    tier                                                raw       gzip
    baseline (no generated safelist)                 365,976     53,945
    bg/text/border colours only              (726)   404,561     58,708
    + spacing/sizing scale                 (1,776)   475,909     64,757
    THIS FILE: + negatives + structural    (2,377)   502,252     67,239
    (rejected) + ring/fill/stroke colours  (2,502)   521,033     69,701
    (rejected) + all 16 colour prefixes    (3,872)   908,460     86,965
    (rejected) + all 16 prefixes, spacing  (4,922)   979,808     92,736

+13.3 KB gzip (+24.6%) over baseline. The rejected tiers cost a further 2.5 to
25 KB gzip for utilities no observed dashboard failure has involved; the
thirteen extra colour prefixes (`from-`/`via-`/`to-`, `divide-`, `shadow-`,
etc.) are the expensive ones because each emits several custom properties. Add
them here if a failure ever does involve one.

**Arbitrary values are deliberately out of scope.** An arbitrary-value utility
(a bracketed literal such as a hard-coded pixel min-width) cannot be
enumerated, so no safelist can cover it. **Variants are the same class of
residual**, and are easy to miss: Tailwind generates per candidate *string*, so
safelisting a colour utility does NOT safelist its `hover:` / `focus:` /
`md:` form, and does not cover a `/opacity` suffix either. Enumerating
variant x palette would multiply the matrix by the number of variants; the
write-time check covers them instead.

An arbitrary value Note that arbitrary-vs-standard is NOT
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

# Negative spacing. The repo's own render code uses these (`-mb-px` in
# flw_audit_trend_dashboard, `-mx-4 sm:-mx-6 lg:-mx-8` in kmc_flw_flags), and
# `SIZE_PREFIXES x SPACING` alone emits only the positive forms.
NEGATABLE_PREFIXES = ("m", "mx", "my", "mt", "mr", "mb", "ml", "top", "right", "bottom", "left", "inset")

# The structural core: layout, typography and border utilities that carry no
# colour or spacing value, so they fall outside both matrices above but make up
# the head of what real render code actually uses. Cross-referencing the
# extractor's output over this repo's 34 render templates against the colour +
# spacing tiers alone covered 201/427 distinct tokens; the uncovered head was
# almost entirely this list.
STRUCTURAL_UTILITIES = (
    # display / layout
    "block",
    "inline",
    "inline-block",
    "inline-flex",
    "flex",
    "inline-grid",
    "grid",
    "contents",
    "hidden",
    "table",
    "table-cell",
    "table-row",
    "flow-root",
    "isolate",
    "flex-row",
    "flex-col",
    "flex-wrap",
    "flex-nowrap",
    "flex-1",
    "flex-auto",
    "flex-none",
    "grow",
    "grow-0",
    "shrink",
    "shrink-0",
    "grid-cols-1",
    "grid-cols-2",
    "grid-cols-3",
    "grid-cols-4",
    "grid-cols-5",
    "grid-cols-6",
    "grid-cols-12",
    "col-span-1",
    "col-span-2",
    "col-span-3",
    "col-span-full",
    "items-start",
    "items-center",
    "items-end",
    "items-baseline",
    "items-stretch",
    "justify-start",
    "justify-center",
    "justify-end",
    "justify-between",
    "justify-around",
    "self-start",
    "self-center",
    "self-end",
    "self-stretch",
    "static",
    "relative",
    "absolute",
    "fixed",
    "sticky",
    "overflow-auto",
    "overflow-hidden",
    "overflow-x-auto",
    "overflow-y-auto",
    "overflow-visible",
    "w-full",
    "w-auto",
    "w-screen",
    "h-full",
    "h-auto",
    "h-screen",
    "min-w-full",
    "min-h-full",
    "max-w-full",
    "max-w-none",
    "max-w-xs",
    "max-w-sm",
    "max-w-md",
    "max-w-lg",
    "max-w-xl",
    "max-w-2xl",
    "max-w-3xl",
    "max-w-4xl",
    "max-w-5xl",
    "max-w-6xl",
    "max-w-7xl",
    "mx-auto",
    "my-auto",
    "ml-auto",
    "mr-auto",
    # typography
    "text-xs",
    "text-sm",
    "text-base",
    "text-lg",
    "text-xl",
    "text-2xl",
    "text-3xl",
    "text-4xl",
    "text-left",
    "text-center",
    "text-right",
    "text-justify",
    "font-thin",
    "font-light",
    "font-normal",
    "font-medium",
    "font-semibold",
    "font-bold",
    "font-extrabold",
    "font-mono",
    "font-sans",
    "font-serif",
    "italic",
    "not-italic",
    "underline",
    "line-through",
    "no-underline",
    "uppercase",
    "lowercase",
    "capitalize",
    "normal-case",
    "truncate",
    "whitespace-nowrap",
    "whitespace-normal",
    "whitespace-pre-wrap",
    "break-words",
    "break-all",
    "tabular-nums",
    "tracking-tight",
    "tracking-wide",
    "leading-none",
    "leading-tight",
    "leading-snug",
    "leading-normal",
    "leading-relaxed",
    "leading-loose",
    "list-disc",
    "list-decimal",
    "list-none",
    "list-inside",
    # borders / effects
    "border",
    "border-0",
    "border-2",
    "border-4",
    "border-8",
    "border-t",
    "border-r",
    "border-b",
    "border-l",
    "border-none",
    "rounded",
    "rounded-none",
    "rounded-sm",
    "rounded-md",
    "rounded-lg",
    "rounded-xl",
    "rounded-2xl",
    "rounded-3xl",
    "rounded-full",
    "shadow",
    "shadow-none",
    "shadow-sm",
    "shadow-md",
    "shadow-lg",
    "shadow-xl",
    "shadow-2xl",
    "opacity-0",
    "opacity-25",
    "opacity-50",
    "opacity-75",
    "opacity-100",
    "cursor-pointer",
    "cursor-default",
    "cursor-not-allowed",
    "select-none",
    "pointer-events-none",
    "transition",
    "transition-all",
    "transition-colors",
    "duration-150",
    "duration-200",
    "duration-300",
    "visible",
    "invisible",
    "sr-only",
    "antialiased",
    # colour keywords outside the family x shade matrix
    "bg-white",
    "bg-black",
    "bg-transparent",
    "bg-current",
    "text-white",
    "text-black",
    "text-transparent",
    "text-current",
    "border-white",
    "border-black",
    "border-transparent",
    "border-current",
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
    positive = [f"{prefix}-{value}" for prefix in SIZE_PREFIXES for value in SPACING]
    negative = [f"-{prefix}-{value}" for prefix in NEGATABLE_PREFIXES for value in SPACING if value != "0"]
    return positive + negative


def generate_safelist() -> str:
    """Return the full contents of `tailwind/safelist-generated.txt`."""
    utilities = color_utilities() + size_utilities() + list(STRUCTURAL_UTILITIES)
    return HEADER + "\n" + "\n".join(utilities) + "\n"


def safelist_path(base_dir: Path | str) -> Path:
    return Path(base_dir) / SAFELIST_RELPATH
