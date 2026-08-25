"""No multi-line ``{# ... #}`` comments in any template.

Django's brace-hash comment is **single-line only**. A multi-line one is not lexed as a
comment at all — the whole thing renders verbatim onto the page:

    >>> Template('A{# one\\nline #}B').render(Context({}))
    'A{# one\\nline #}B'

This shipped. A five-line explanatory comment in the admin schedules table rendered its own
text into the Status cell of EVERY schedule row, on a page shared by the whole team, and two
older ones were doing the same on the Pulse report pages. Nothing caught it because a
template that renders wrong still renders — there is no error, just prose on the page.

``{% comment %} ... {% endcomment %}`` is the multi-line form and is what to use.
"""

import re
from pathlib import Path

import connect_labs

TEMPLATE_ROOT = Path(connect_labs.__file__).resolve().parent

# A `{#` with no `#}` after it on the same line.
_UNCLOSED = re.compile(r"\{#(?![^\n]*#\})")


def _offenders():
    found = []
    for path in sorted(TEMPLATE_ROOT.rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _UNCLOSED.search(line):
                found.append((path.relative_to(TEMPLATE_ROOT), lineno, line.strip()[:80]))
    return found


def test_the_scan_actually_sees_templates():
    """A guard that silently finds nothing to check is worse than no guard."""
    assert len(list(TEMPLATE_ROOT.rglob("*.html"))) > 50


def test_no_multiline_brace_hash_comments():
    offenders = _offenders()
    assert (
        not offenders
    ), "multi-line {# #} renders verbatim to the page - use {% comment %}/{% endcomment %}:\n" + "\n".join(
        f"  {path}:{lineno}  {text}" for path, lineno, text in offenders
    )


def test_the_pattern_recognises_both_forms():
    """Pins the detector itself, so a broken regex cannot make the suite look clean."""
    assert _UNCLOSED.search("{# opens and never closes")
    assert not _UNCLOSED.search("{# closed on one line #}")
    assert not _UNCLOSED.search("<div>no comment here</div>")
    # Two comments on one line, both closed, is fine.
    assert not _UNCLOSED.search("{# a #} text {# b #}")
