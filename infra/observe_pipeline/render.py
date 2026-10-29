"""Copy each scrubber's source into its ZipFile block in labs-observe-pipeline.yml.

    python infra/observe_pipeline/render.py           # rewrite the template
    python infra/observe_pipeline/render.py --check   # exit 1 if it is stale

CloudFormation inlines Lambda code only as `ZipFile` (4096 bytes at most), and
the scrubbers are what keeps PHI out of /labs-jj/observe/*, so their source has
to be tested where it lives. connect_labs/utils/tests/test_observe_scrubbers.py
fails when the template and these files differ.
"""

from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "labs-observe-pipeline.yml"
SOURCES = ("events_projector.py", "request_telemetry_scrubber.py", "alb_scrubber.py")
INDENT = " " * 10
MAX_BYTES = 4096


def rendered(text: str) -> str:
    for name in SOURCES:
        code = (HERE / name).read_text()
        if len(code.encode()) > MAX_BYTES:
            raise SystemExit(f"{name} is {len(code.encode())} bytes; ZipFile allows {MAX_BYTES}")
        body = "".join(f"{INDENT}{line}\n" if line else "\n" for line in code.splitlines())
        pat = re.compile(rf"({INDENT}# BEGIN {re.escape(name)}\n).*?({INDENT}# END {re.escape(name)}\n)", re.S)
        if not pat.search(text):
            raise SystemExit(f"no BEGIN/END markers for {name} in {TEMPLATE.name}")
        text = pat.sub(lambda m: m.group(1) + body + m.group(2), text)
    return text


def embedded(text: str, name: str) -> str:
    m = re.search(rf"{INDENT}# BEGIN {re.escape(name)}\n(.*?){INDENT}# END {re.escape(name)}\n", text, re.S)
    return "".join(line[len(INDENT) :] + "\n" if line else "\n" for line in m.group(1).splitlines())


if __name__ == "__main__":
    current = TEMPLATE.read_text()
    new = rendered(current)
    if "--check" in sys.argv:
        sys.exit(0 if new == current else 1)
    TEMPLATE.write_text(new)
