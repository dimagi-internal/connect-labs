"""Guards a specific, previously-shipped-broken class of mistake in
bulk_assessment.html: a literal `"` inside a `//` JS comment that lives
inside a double-quoted `x-data="{...}"` Alpine attribute.

That character terminates the HTML attribute value right there -- everything
after it (the rest of the x-data object, every getter, every method) is
parsed as garbage HTML attributes instead of JS, and the ENTIRE per-image
tile's Alpine component goes dead (buttons, badges, keyboard shortcuts, all
of it). This happened THREE separate times while building the duplicate-
detection review UI in this file, caught only by manual/reviewer inspection
each time -- cheap enough to guard permanently instead.

Single quotes inside these comments are always safe; double quotes are not.
"""
from pathlib import Path
from html.parser import HTMLParser

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3] / "connect_labs" / "templates" / "audit" / "bulk_assessment.html"
)


class _XDataCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.x_data_values = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "x-data":
                self.x_data_values.append(value)


def _parse_x_data_attributes(html: str) -> list[str]:
    parser = _XDataCollector()
    parser.feed(html)
    return parser.x_data_values


def test_every_x_data_attribute_parses_as_one_intact_value():
    """If a stray `"` terminates an x-data attribute early, Python's own HTML
    parser -- the same tokenizer a browser uses -- either raises while
    parsing the now-malformed markup that follows, or silently returns a
    truncated attribute value. Either way this test catches it; a browser
    would not."""
    html = TEMPLATE_PATH.read_text()
    x_data_values = _parse_x_data_attributes(html)
    assert len(x_data_values) == html.count('x-data="')

    for value in x_data_values:
        if len(value) > 200:
            # A real object-literal x-data (not a trivial `{ foo: false }`)
            # must end at its own closing brace, not mid-comment.
            assert value.rstrip().endswith("}"), (
                "An x-data attribute looks truncated -- likely a literal '\"' inside a "
                "// comment terminated the attribute early. Ends with: " + repr(value[-120:])
            )


def test_no_double_quote_inside_a_js_comment_within_the_main_tile_x_data():
    """Belt-and-suspenders on top of the parser check above: scan the raw
    source between the tile's x-data="{ ... }" delimiters for any // comment
    line containing a literal double quote. This is the exact mistake --
    catching it by direct inspection means a future contributor gets a clear
    error message instead of a cryptic parser assertion."""
    html = TEMPLATE_PATH.read_text()
    lines = html.splitlines()

    start_idx = next(i for i, line in enumerate(lines) if 'x-data="{' in line)
    # The attribute closes on the first subsequent line that is just the
    # closing brace + quote (with only leading whitespace).
    end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i].strip() == '}"')

    offending = [
        (i + 1, line)
        for i, line in enumerate(lines[start_idx + 1 : end_idx], start=start_idx + 1)
        if "//" in line and '"' in line.split("//", 1)[1]
    ]
    assert not offending, (
        "Found a literal double-quote inside a // comment within the tile's x-data "
        "attribute -- this terminates the HTML attribute early and breaks the whole "
        "component. Use single quotes instead. Offending line(s): " + repr(offending)
    )
