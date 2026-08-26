"""Write-time check that a workflow's render_code only uses CSS classes that
actually exist in the deployed stylesheet bundles.

**Why.** A workflow's `render_code` lives in the LabsRecord store, not on disk,
so Tailwind's source detection never scans it (see
`connect_labs/labs/tailwind_safelist.py` for the full mechanism). Anything an
author writes that labs' own sources happen not to contain is purged out of the
bundle, and the failure is silent: an invisible chart bar, a zero-height
wrapper, text that inherits near-black. dimagi-internal/connect-labs#1294.

`tailwind/safelist-generated.txt` gives the colour palette and spacing scale a
guaranteed floor. This module covers what a safelist structurally cannot: an
arbitrary-value utility (a bracketed literal), a variant nobody enumerated, a
typo. It answers the question by **reading the built stylesheets**, so it is
not a prediction of what Tailwind will do and it cannot go stale as labs'
own UI changes — the authority is the artifact that is actually served.

**Why this warns rather than rejects.** The extractor is a regex over JSX, so
it cannot be certain every token it pulls out is a CSS class the browser will
apply. `connect_labs/mcp/tools/workflows.py::_validate_render_code` already
carries the repo's lesson here: its policy checks were removed because they
blocked valid modern JS. A false *reject* stops all authoring on that
workflow; a false *warning* costs one line of output. So the finding is
returned alongside a successful write, where the author (human or agent) sees
it at exactly the moment they would otherwise ship an invisible panel.

**Fail-open by design.** The stylesheet bundles are a build artifact
(`npm run build` -> `connect_labs/static/bundles/css/`). They are present in
the deployed image (`Dockerfile`) but NOT in the test/CI environment, which
runs no npm step. When they are missing this returns "no opinion" rather than
warning about everything.
"""

import logging
import re
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

BUNDLE_CSS_RELDIR = Path("connect_labs") / "static" / "bundles" / "css"

# Cap what we hand back: a render file that fails wholesale (e.g. bundles built
# from a different branch) should not return a thousand-item list.
MAX_REPORTED = 25

# Class attributes in JSX/HTML. Handles class="..." / className="..." /
# className={"..."} / className={`...`} / clsx("...", cond && "...").
_CLASS_ATTR_RE = re.compile(r"""class(?:Name)?\s*=\s*(?P<body>"[^"]*"|'[^']*'|\{(?:[^{}]|\{[^{}]*\})*\})""")

# String literals inside a className={...} expression.
_STRING_LITERAL_RE = re.compile(r"""(?:"([^"]*)"|'([^']*)'|`([^`]*)`)""")

# A `${...}` interpolation inside a template literal: its contents are code,
# not classes. It is replaced with a sentinel rather than split on, because the
# fragments on either side are partial tokens (`text-${c}-500` must not yield
# a candidate named `text-`) and the sentinel makes any token containing it
# fail _UTILITY_TOKEN_RE.
_INTERPOLATION_RE = re.compile(r"\$\{[^}]*\}")
_INTERPOLATION_SENTINEL = "\x00"

# A token we are willing to have an opinion about. Deliberately conservative:
# must start with a lowercase letter, may carry `variant:` prefixes, may carry
# an arbitrary `[...]` value or a `/opacity` suffix. Anything with an unexpected
# character (a leftover interpolation fragment, a URL, a template placeholder)
# is skipped rather than guessed at.
_UTILITY_TOKEN_RE = re.compile(r"^(?:[a-z][a-z0-9._-]*:)*-?[a-z][a-zA-Z0-9._-]*(?:\[[^\]\s]+\])?(?:/[0-9.]+)?$")

# Class selectors in a compiled stylesheet, including CSS-escaped characters
# (`.bg-emerald-500\/20`, `.min-w-\[52px\]`, `.hover\:bg-blue-500`).
_CSS_CLASS_RE = re.compile(r"\.((?:\\.|[A-Za-z0-9_-])+)")

_UNESCAPE_RE = re.compile(r"\\(.)")

_cache: dict[str, object] = {}


def _bundle_dir() -> Path:
    return Path(settings.BASE_DIR) / BUNDLE_CSS_RELDIR


def _parse_stylesheet_classes(css: str) -> set[str]:
    return {_UNESCAPE_RE.sub(r"\1", match) for match in _CSS_CLASS_RE.findall(css)}


def available_classes(bundle_dir: Path | None = None) -> frozenset[str] | None:
    """Every class selector defined across the built stylesheet bundles.

    Returns None when the bundles are not on disk (nothing has been built),
    which callers must treat as "no opinion", never as "everything is missing".
    """
    directory = Path(bundle_dir) if bundle_dir is not None else _bundle_dir()
    try:
        sheets = sorted(directory.glob("*.css"))
    except OSError:
        return None
    if not sheets:
        return None

    # Cheap staleness key so a rebuild is picked up without a restart.
    try:
        key = (str(directory), tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sheets))
    except OSError:
        return None
    if _cache.get("key") == key:
        return _cache["classes"]  # type: ignore[return-value]

    classes: set[str] = set()
    for sheet in sheets:
        try:
            classes |= _parse_stylesheet_classes(sheet.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            logger.warning("Could not read stylesheet bundle %s", sheet)
    result = frozenset(classes)
    _cache["key"] = key
    _cache["classes"] = result
    return result


def extract_class_candidates(source: str) -> set[str]:
    """Pull the CSS class tokens out of a JSX/HTML source string."""
    tokens: set[str] = set()
    for match in _CLASS_ATTR_RE.finditer(source or ""):
        body = match.group("body")
        if body[0] in "\"'":
            literals = [body[1:-1]]
        else:
            literals = [g for groups in _STRING_LITERAL_RE.findall(body) for g in groups if g]
        for literal in literals:
            # A token touching an interpolation is not a class we can judge.
            masked = _INTERPOLATION_RE.sub(_INTERPOLATION_SENTINEL, literal)
            for token in masked.split():
                if _UTILITY_TOKEN_RE.match(token):
                    tokens.add(token)
    return tokens


def find_unresolved_classes(source: str, bundle_dir: Path | None = None) -> list[str]:
    """Classes used by `source` that no built stylesheet defines.

    Empty when everything resolves, when nothing recognisable was found, or
    when the stylesheet bundles are unavailable.
    """
    known = available_classes(bundle_dir)
    if known is None:
        return []
    return sorted(token for token in extract_class_candidates(source) if token not in known)


def render_code_warning(source: str, bundle_dir: Path | None = None) -> dict | None:
    """A write-response warning payload, or None when there is nothing to say."""
    unresolved = find_unresolved_classes(source, bundle_dir=bundle_dir)
    if not unresolved:
        return None
    shown = unresolved[:MAX_REPORTED]
    return {
        "unresolved_classes": shown,
        "unresolved_class_count": len(unresolved),
        "message": (
            f"{len(unresolved)} CSS class(es) used by this render_code are not defined in any "
            "built stylesheet, so the browser will silently ignore them — a missing colour "
            "renders transparent, a missing size collapses the element to nothing, and nothing "
            "errors. render_code is stored in the database, so Tailwind never scans it and only "
            "ships utilities it saw elsewhere. Fix: use a utility from the generated safelist "
            "(the full colour palette and spacing scale are guaranteed), or set the value with an "
            "inline style={{...}}, which cannot be purged. Unresolved: " + ", ".join(shown)
        ),
    }
