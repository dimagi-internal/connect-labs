"""Guards the x-data integrity of ``workflow/list.html``.

Same failure class as ``audit/tests/test_bulk_assessment_template_html_integrity.py``: a
literal ``"`` in static text inside a double-quoted ``x-data="{...}"`` Alpine attribute
ends the attribute right there. Everything after it is parsed as stray HTML attributes
instead of JS, so the component goes dead — and on THIS page that means every workflow
card at once: schedule dialog, copy, share, delete.

It has already happened here once, in a comment explaining how not to do it.

One subtlety that makes the obvious test wrong: parsing the raw Django template reports a
false break on ``default:"{}"``, because a filter argument's quotes never reach a browser —
Django replaces the whole ``{{ ... }}`` first. Only STATIC text survives rendering
unchanged, so the expressions are blanked out before parsing. That is also why the check
lives here rather than being folded into a test that renders one hand-copied fragment: the
fragment is not what ships.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import connect_labs

TEMPLATE_PATH = Path(connect_labs.__file__).resolve().parent / "templates" / "workflow" / "list.html"


class _XDataCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.x_data_values = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "x-data":
                self.x_data_values.append(value)


def _static_x_data_values():
    """x-data attribute values with Django expressions blanked out.

    A quote-free placeholder stands in for each ``{{ ... }}`` so the surrounding static
    text is parsed exactly as a browser would receive it.
    """
    raw = TEMPLATE_PATH.read_text(encoding="utf-8")
    stripped = re.sub(r"\{\{.*?\}\}", "TPLVALUE", raw, flags=re.S)
    stripped = re.sub(r"\{%.*?%\}", "", stripped, flags=re.S)
    parser = _XDataCollector()
    parser.feed(stripped)
    return parser.x_data_values


def test_the_template_still_has_its_x_data_components():
    """A guard that silently finds nothing to check is worse than no guard."""
    assert len(_static_x_data_values()) >= 4


def test_every_x_data_attribute_parses_as_one_intact_value():
    """An attribute cut short does not end in the closing brace of its object literal."""
    broken = []
    for index, value in enumerate(_static_x_data_values()):
        if not (value or "").rstrip().endswith("}"):
            broken.append((index, (value or "")[-160:]))

    assert not broken, "x-data attribute(s) terminated early — Alpine is dead on these cards:\n" + "\n".join(
        f"  [{i}] ...{tail!r}" for i, tail in broken
    )


def test_no_double_quote_inside_a_js_comment_within_x_data():
    """The specific shape that broke it: a `//` comment mentioning an HTML attribute.

    Single quotes in these comments are always safe; double quotes never are. Checked
    separately from the parse test because a quote can sit in a comment on the LAST line
    of an attribute and still leave it looking intact.
    """
    offenders = []
    for index, value in enumerate(_static_x_data_values()):
        for line in (value or "").split("\n"):
            stripped = line.strip()
            if stripped.startswith("//") and '"' in stripped:
                offenders.append((index, stripped[:100]))

    assert not offenders, "double quote inside a JS comment within x-data:\n" + "\n".join(
        f"  [{i}] {text!r}" for i, text in offenders
    )
