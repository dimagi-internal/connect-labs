"""The shipped registry YAML parses into the shape its authors meant.

YAML flow mappings (`meta: { ... }`) split on bare commas. C14's `scope_note`
was written as unquoted prose containing commas, so it parsed as
`scope_note: 'All LLOs pooled'` plus two meta KEYS made of the rest of the
sentence, each with a null value. Nothing failed: validation ignores unknown
meta keys, and the report just showed a truncated caveat. These checks make that
class of mistake loud in the file where it happens.

Deliberately a test on the shipped files, not a write-path rule: records
already seeded from the broken file carry the junk keys, and refusing every
edit to them until someone cleans up would be worse than the bug.
"""

import re
from pathlib import Path

import pytest
import yaml

REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "kmc"
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@pytest.fixture(scope="module")
def measures():
    return yaml.safe_load((REGISTRY / "indicators.yml").read_text())["measures"]


def test_every_meta_key_is_an_identifier(measures):
    bad = [(m["name"], k) for m in measures for k in (m.get("meta") or {}) if not IDENTIFIER.match(k)]
    assert not bad, f"meta keys that look like split prose (quote the value): {bad}"


def test_c14_scope_note_is_the_whole_caveat(measures):
    c14 = next(m for m in measures if m["name"] == "c14")
    assert "non-recorders add denominator without deaths" in c14["meta"]["scope_note"]
