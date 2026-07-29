"""Safe JSON serialization for embedding inside inline ``<script>`` blocks.

``json.dumps`` does NOT escape ``<``, ``>`` or ``&``, so a value containing
``</script>`` breaks out of the surrounding ``<script>`` element and executes as
markup — a classic XSS when the value is emitted via ``{{ x|safe }}``. It also
leaves U+2028 / U+2029 raw, which are line terminators in JavaScript strings and
break the script parse.

``safe_json_for_script`` escapes those characters to their ``\\uXXXX`` forms.
The result is still valid JSON and, embedded in a JavaScript object/string
literal, parses to exactly the same value — so callers that emit it inline (an
object literal or ``{{ ... |safe }}``) need no other change. Prefer Django's
``json_script`` template tag for new code; this helper exists for the inline
``|safe`` sinks that already ship.
"""
from __future__ import annotations

import json
from typing import Any

# Map each dangerous character to its JSON/JS unicode escape. The two JS line
# terminators are written via chr() rather than literal glyphs so an editor or
# source-encoding step can't mangle them or collapse them into one dict entry.
_ESCAPES = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    chr(0x2028): "\\u2028",  # JS line separator
    chr(0x2029): "\\u2029",  # JS paragraph separator
}


def safe_json_for_script(value: Any) -> str:
    """``json.dumps(value)`` hardened for inline ``<script>`` embedding."""
    dumped = json.dumps(value)
    for char, replacement in _ESCAPES.items():
        dumped = dumped.replace(char, replacement)
    return dumped
