"""Tolerance-aware deep-diff for dashboard payloads.

Walks a `DASHBOARD_CONTRACT` of `Leaf` entries; for each leaf, fetches the
value from both payloads at that path and compares with the leaf's
declared tolerance. Returns a structured report so test failures are
self-explanatory ("flw_summaries[3].avg_case_distance_km: v1=3.42, v3=3.41,
delta=0.01, tolerance=epsilon(0.005) → FAIL").

Path syntax:
    "gps_data.total_visits"            — direct dict key
    "gps_data.flw_summaries[]"         — every element of the list
    "gps_data.flw_summaries[].name"    — name of every element
    "overview_data.mother_counts{}"    — every value of dict-keyed-by-FLW
"""

from dataclasses import dataclass, field
from typing import Any

from commcare_connect.workflow.tests.mbw_parity.payload_contract import Leaf, Tolerance


@dataclass
class LeafDiff:
    """One disagreement between v1 and v3 at a specific path."""

    path: str  # concrete path including resolved indexes/keys
    v1_value: Any
    v3_value: Any
    delta: Any
    tolerance: Tolerance
    reason: str  # human-readable why-it-failed


@dataclass
class ParityReport:
    leaves_checked: int = 0
    diffs: list[LeafDiff] = field(default_factory=list)
    missing_in_v1: list[str] = field(default_factory=list)
    missing_in_v3: list[str] = field(default_factory=list)

    @property
    def is_match(self) -> bool:
        return not self.diffs and not self.missing_in_v1 and not self.missing_in_v3

    def format(self) -> str:
        lines = [f"Parity report — {self.leaves_checked} leaves checked"]
        if self.is_match:
            lines.append("  ✓ all leaves match within tolerance")
            return "\n".join(lines)
        for d in self.diffs:
            lines.append(
                f"  ✗ {d.path}: v1={d.v1_value!r} v3={d.v3_value!r} "
                f"(delta={d.delta}, tol={d.tolerance.kind} eps={d.tolerance.epsilon}) — {d.reason}"
            )
        for m in self.missing_in_v1:
            lines.append(f"  ✗ missing in v1 payload: {m}")
        for m in self.missing_in_v3:
            lines.append(f"  ✗ missing in v3 payload: {m}")
        return "\n".join(lines)


_MISSING = object()


def _walk(payload: Any, parts: list[str]) -> list[tuple[str, Any]]:
    """Walk a payload along a path, expanding `[]` and `{}` wildcards.

    Returns a list of (concrete_path, value) — one tuple per concrete leaf.
    Missing keys / out-of-range indexes yield (concrete_path, _MISSING).
    """
    if not parts:
        return [("", payload)]

    head, *rest = parts

    # List wildcard: head is "<key>[]" or just "[]"
    if head.endswith("[]"):
        key = head[:-2]
        container = payload.get(key, _MISSING) if isinstance(payload, dict) and key else payload
        if container is _MISSING or not isinstance(container, list):
            return [(f"{key}[]", _MISSING)]
        out = []
        for i, item in enumerate(container):
            for sub_path, sub_val in _walk(item, rest):
                out.append((f"{key}[{i}]" + (f".{sub_path}" if sub_path else ""), sub_val))
        return out

    # Dict-of-FLWs wildcard: head is "<key>{}"
    if head.endswith("{}"):
        key = head[:-2]
        container = payload.get(key, _MISSING) if isinstance(payload, dict) and key else payload
        if container is _MISSING or not isinstance(container, dict):
            return [(f"{key}{{}}", _MISSING)]
        out = []
        for k, v in container.items():
            for sub_path, sub_val in _walk(v, rest):
                out.append((f"{key}[{k!r}]" + (f".{sub_path}" if sub_path else ""), sub_val))
        return out

    # Plain key
    if not isinstance(payload, dict):
        rest_path = ("." + ".".join(rest)) if rest else ""
        return [(head + rest_path, _MISSING)]
    if head not in payload:
        rest_path = ("." + ".".join(rest)) if rest else ""
        return [(head + rest_path, _MISSING)]
    sub = _walk(payload[head], rest)
    return [(head + (f".{p}" if p else ""), v) for p, v in sub]


def _compare(v1_value: Any, v3_value: Any, tol: Tolerance) -> tuple[bool, Any, str]:
    """Compare two values under the given tolerance. Returns (ok, delta, reason)."""
    if tol.kind == "exact":
        if v1_value == v3_value:
            return True, 0, ""
        return False, None, "exact mismatch"

    # Numeric tolerances
    if v1_value is None and v3_value is None:
        return True, 0, ""
    if v1_value is None or v3_value is None:
        return False, None, "one side is null"
    try:
        a = float(v1_value)
        b = float(v3_value)
    except (TypeError, ValueError):
        return False, None, f"non-numeric values for {tol.kind} tolerance"

    if tol.kind == "epsilon":
        delta = abs(a - b)
        return (delta < tol.epsilon, delta, "" if delta < tol.epsilon else "outside epsilon")
    if tol.kind == "relative":
        denom = max(abs(a), abs(b), 1.0)
        rel = abs(a - b) / denom
        return (rel < tol.epsilon, rel, "" if rel < tol.epsilon else "outside relative epsilon")
    return False, None, f"unknown tolerance kind {tol.kind!r}"


def diff_payloads(v1: dict, v3: dict, contract: list[Leaf]) -> ParityReport:
    """Run a leaf-by-leaf parity check across the contract."""
    report = ParityReport()
    for leaf in contract:
        parts = leaf.path.split(".")
        v1_pairs = dict(_walk(v1, parts))
        v3_pairs = dict(_walk(v3, parts))
        all_paths = sorted(set(v1_pairs) | set(v3_pairs))
        for path in all_paths:
            report.leaves_checked += 1
            v1_val = v1_pairs.get(path, _MISSING)
            v3_val = v3_pairs.get(path, _MISSING)
            if v1_val is _MISSING and v3_val is _MISSING:
                continue
            if v1_val is _MISSING:
                report.missing_in_v1.append(path)
                continue
            if v3_val is _MISSING:
                report.missing_in_v3.append(path)
                continue
            ok, delta, reason = _compare(v1_val, v3_val, leaf.tolerance)
            if not ok:
                report.diffs.append(
                    LeafDiff(
                        path=path,
                        v1_value=v1_val,
                        v3_value=v3_val,
                        delta=delta,
                        tolerance=leaf.tolerance,
                        reason=reason,
                    )
                )
    return report
