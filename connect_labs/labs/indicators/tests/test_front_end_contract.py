"""Contracts between the targeting registry and the page that renders it.

Seven of this surface's defects were one mistake: a string constant sitting
beside a variable number, written when the page had eleven mortality indicators
and never revisited as it reached fifty-two. A table column headed `U5MR` over
ORS percentages; a heading reading "Areas above threshold" over a coverage
measure that selects below it; a map tooltip saying "per 1,000" over a percent.

Each was found by looking. These tests are what stop the next one needing to be.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from connect_labs.labs.indicators import measures

JS_DIR = Path(settings.APPS_DIR if hasattr(settings, "APPS_DIR") else ".") / "static/indicators/targeting"
if not JS_DIR.exists():  # running from the repo root rather than the app dir
    JS_DIR = Path("connect_labs/static/indicators/targeting")
TEMPLATE = Path("connect_labs/templates/indicators/targeting.html")


def _js_source() -> str:
    return "\n".join(p.read_text() for p in sorted(JS_DIR.glob("*.js")) if not p.name.endswith(".test.js"))


#: Elements whose text is a property of the SELECTED MEASURE, not of the page.
#: Every one of these must be written from JS. Two of them were not — they were
#: literals typed into the template and never touched again, so they described
#: under-5 mortality no matter what the reader had chosen.
MEASURE_DERIVED_ELEMENTS = {
    "th-value": "the column head over the value (was the literal 'U5MR')",
    "th-burden": "the burden column head (Deaths/yr vs Untreated/now vs Unreached)",
    "tg-table-title": "'Areas above/below threshold' — direction is per measure",
    "tg-legend-title": "the map legend's measure name and unit",
    "tg-indicator-label": "the chosen indicator's name in the trigger",
    "tg-threshold-label": "'Show me where <measure> is above/below'",
    "tg-threshold-unit": "the unit beside the threshold number",
    "tg-burden-label": "the burden tile's subject",
}


class TestEveryMeasureDerivedElementIsActuallyWritten:
    def test_the_template_declares_each_one(self):
        html = TEMPLATE.read_text()
        missing = [el for el in MEASURE_DERIVED_ELEMENTS if f'id="{el}"' not in html]
        assert not missing, f"these ids no longer exist in the template: {missing}"

    def test_javascript_assigns_each_one(self):
        """An id with no writer is a literal pretending to be a value.

        `#th-value` carried an id and the text 'U5MR' for every one of the 52
        indicators because nothing ever assigned it. The id looked like
        intent; only the absence of a writer gave it away.
        """
        js = _js_source()
        unwritten = []
        for element, why in MEASURE_DERIVED_ELEMENTS.items():
            # getElementById('x') anywhere, and an assignment to its text.
            referenced = f"'{element}'" in js or f'"{element}"' in js
            if not referenced:
                unwritten.append(f"{element} ({why})")
        assert not unwritten, "no JS writes these, so they render a constant: " + "; ".join(unwritten)


class TestTheClientDoesNotKeepItsOwnCopyOfTheRegistry:
    def test_the_mortality_list_matches_the_registry(self):
        """table.js hardcodes which measures have expected-deaths as their subject.

        A new mortality rate added to the registry would not appear in that
        array, and its burden tile and column would silently go missing. The
        registry's own signal for the same set is the unit: a mortality rate is
        the thing quoted per 1,000 live births.
        """
        js = (JS_DIR / "table.js").read_text()
        match = re.search(r"var MORTALITY = \[([^\]]*)\]", js)
        assert match, "table.js no longer declares MORTALITY — update or delete this test"
        in_js = {c.strip().strip("'\"") for c in match.group(1).split(",") if c.strip()}

        from_registry = {code for code, m in measures.MEASURES.items() if m.unit == "per 1,000 live births"}

        assert in_js == from_registry, (
            f"table.js has {sorted(in_js)}, the registry has {sorted(from_registry)}. "
            "A measure in one and not the other renders the wrong burden."
        )
