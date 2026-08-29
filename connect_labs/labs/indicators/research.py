"""Research notes that argue with themselves.

A note records what we worked out about an indicator so the next session does
not repeat the work. The danger in that is obvious: a conclusion written in
March reads exactly like a conclusion written this morning, and the data
underneath it has moved. Prose cannot tell you which one you are holding.

So every note carries **checks** — claims narrow enough to re-run against the
live database in milliseconds. Reading a note re-runs them and returns the
verdict beside the text. A note whose checks all hold is evidence. A note with
a drifted check is still useful, but the reader is told exactly which sentence
to stop believing.

There are four kinds of check, and the vocabulary is deliberately small. A
check has to be cheap enough to run on every read and specific enough that
"still true" means something; anything richer than this belongs in the body as
prose, where it is honestly labelled as an argument rather than a fact.

    coverage   how many units carry this indicator at this level
    value      what this indicator reads for one place
    source     whether a named source still supplies this indicator
    measure    whether a measure still has the shape the note assumed

Separately, ``scanned_at`` records when we last went looking for sources we did
*not* already know about. No check can answer that: checks confirm what was
found, and only a fresh scan can tell you whether something better has appeared.
The two staleness questions are different and the tools report them separately.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from django.utils import timezone

from connect_labs.labs.indicators import boundaries, measures
from connect_labs.labs.indicators.models import IndicatorValue, ResearchNote

logger = logging.getLogger(__name__)

#: Default tolerance for a ``value`` check, as a fraction. Loose enough that a
#: source's routine re-release does not cry wolf, tight enough that a changed
#: method or a broken aggregation does.
DEFAULT_TOLERANCE = 0.05

CHECK_KINDS = ("coverage", "value", "source", "measure")


@dataclass(frozen=True)
class CheckResult:
    kind: str
    describes: str
    holds: bool
    expected: object
    actual: object
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _owned(indicator: str, level: int | None = None, iso: str | None = None):
    qs = IndicatorValue.objects.filter(indicator=indicator, boundary__in=boundaries.owned())
    if level is not None:
        qs = qs.filter(admin_level=level)
    if iso:
        qs = qs.filter(iso_code=iso.upper())
    return qs


def _check_coverage(spec: dict) -> CheckResult:
    """How many distinct units carry this indicator at this level."""
    indicator = spec["indicator"]
    level = int(spec.get("level", 1))
    expected = int(spec["expected"])
    actual = _owned(indicator, level=level).values("boundary").distinct().count()
    # Coverage that has grown is not drift — it is the backfill working. Only a
    # figure that has *fallen* means the note's premise no longer holds.
    holds = actual >= expected
    return CheckResult(
        kind="coverage",
        describes=f"{indicator} at ADM{level}",
        holds=holds,
        expected=expected,
        actual=actual,
        detail="" if holds else f"coverage fell by {expected - actual} units since the note was written",
    )


def _check_value(spec: dict) -> CheckResult:
    """What this indicator reads for one place, within a tolerance."""
    indicator = spec["indicator"]
    iso = spec["iso"]
    level = int(spec.get("level", 0))
    expected = float(spec["expected"])
    tolerance = float(spec.get("tolerance", DEFAULT_TOLERANCE))
    qs = _owned(indicator, level=level, iso=iso)
    if source := spec.get("source"):
        qs = qs.filter(source=source)
    row = qs.order_by("-year").first()
    if row is None:
        return CheckResult(
            kind="value",
            describes=f"{indicator} for {iso} at ADM{level}",
            holds=False,
            expected=expected,
            actual=None,
            detail="the value the note was built on is gone",
        )
    drift = abs(row.value - expected) / expected if expected else 0.0
    return CheckResult(
        kind="value",
        describes=f"{indicator} for {iso} at ADM{level}",
        holds=drift <= tolerance,
        expected=expected,
        actual=row.value,
        detail="" if drift <= tolerance else f"moved {drift:.1%}, past the {tolerance:.0%} the note allowed",
    )


def _check_source(spec: dict) -> CheckResult:
    """Whether a named source still supplies this indicator at all."""
    indicator = spec["indicator"]
    source = spec["source"]
    expected = bool(spec.get("expected", True))
    actual = _owned(indicator).filter(source=source).exists()
    return CheckResult(
        kind="source",
        describes=f"{source} supplies {indicator}",
        holds=actual == expected,
        expected=expected,
        actual=actual,
        detail="" if actual == expected else ("source has gone" if expected else "source has appeared since"),
    )


def _check_measure(spec: dict) -> CheckResult:
    """Whether a measure still has the shape the note assumed.

    Cheap, and it catches the nastiest kind of drift: a note reasoning about an
    indicator as a rate after somebody redefined it as a count, or a burden
    measure that has since acquired a denominator and become coverage.
    """
    code = spec["code"]
    try:
        m = measures.get(code)
    except KeyError:
        return CheckResult("measure", code, False, spec.get("expected"), None, "the measure no longer exists")
    actual = {"kind": m.kind.value, "unit": m.unit, "family": "coverage" if m.coverage_of else "burden"}
    expected = {k: v for k, v in spec.get("expected", {}).items()}
    mismatched = {k: (v, actual.get(k)) for k, v in expected.items() if actual.get(k) != v}
    return CheckResult(
        kind="measure",
        describes=f"{code} is still {expected}",
        holds=not mismatched,
        expected=expected,
        actual=actual,
        detail=""
        if not mismatched
        else "; ".join(f"{k}: note said {v[0]!r}, now {v[1]!r}" for k, v in mismatched.items()),
    )


_RUNNERS = {
    "coverage": _check_coverage,
    "value": _check_value,
    "source": _check_source,
    "measure": _check_measure,
}


def run_check(spec: dict) -> CheckResult:
    kind = spec.get("kind")
    runner = _RUNNERS.get(kind)
    if runner is None:
        return CheckResult(
            kind=str(kind),
            describes=str(spec),
            holds=False,
            expected=None,
            actual=None,
            detail=f"unknown check kind; expected one of {', '.join(CHECK_KINDS)}",
        )
    try:
        return runner(spec)
    except KeyError as exc:
        return CheckResult(str(kind), str(spec), False, None, None, f"malformed check: missing {exc}")
    except Exception as exc:  # noqa: BLE001 — a broken check must not break the read
        logger.warning("research check failed: %s (%s)", spec, exc)
        return CheckResult(str(kind), str(spec), False, None, None, f"check could not run: {exc}")


def revalidate(note: ResearchNote) -> list[CheckResult]:
    return [run_check(spec) for spec in (note.checks or [])]


def scan_age_days(note: ResearchNote) -> int | None:
    if not note.scanned_at:
        return None
    return (timezone.now() - note.scanned_at).days


def rescan_due(note: ResearchNote) -> bool:
    """True when nobody has looked for *new* sources recently enough.

    A note that has never been scanned is due by definition: it records what
    somebody happened to know, not the result of a sweep.
    """
    age = scan_age_days(note)
    return age is None or age > ResearchNote.SCAN_INTERVAL_DAYS


def describe(note: ResearchNote, *, include_body: bool = True) -> dict:
    """A note plus a verdict on whether it still describes reality."""
    results = revalidate(note)
    failed = [r for r in results if not r.holds]
    if not results:
        trust = "unverified"
        advice = (
            "This note carries no checks, so nothing about it can be confirmed. Treat it as a lead, "
            "not a finding, and add checks when you next verify it."
        )
    elif not failed:
        trust = "holds"
        advice = "Every check still passes. The note describes the data as it is now."
    else:
        trust = "drifted"
        advice = (
            f"{len(failed)} of {len(results)} checks no longer hold. The reasoning may still be sound, "
            "but do not quote any figure the failed checks cover without re-deriving it."
        )

    out = {
        "indicator": note.indicator or None,
        "topic": note.topic,
        "summary": note.summary,
        "author": note.author,
        "updated_at": note.updated_at.date().isoformat(),
        "age_days": (timezone.now() - note.updated_at).days,
        "trust": trust,
        "advice": advice,
        "checks": [r.as_dict() for r in results],
        "alternatives": note.alternatives or [],
        "last_full_source_scan": note.scanned_at.date().isoformat() if note.scanned_at else None,
        "scan_age_days": scan_age_days(note),
        "rescan_due": rescan_due(note),
    }
    if rescan_due(note):
        out["rescan_advice"] = (
            "No full alternative-source scan in "
            + (f"{scan_age_days(note)} days" if note.scanned_at else "the life of this note")
            + ". The checks above confirm what we found; they cannot tell you whether something better has since "
            "been published. Ask the user whether to run a fresh source scan before relying on this for a "
            "deliverable."
        )
    if include_body:
        out["body"] = note.body
    return out


def for_indicator(indicator: str | None = None, *, topic: str | None = None) -> list[ResearchNote]:
    """Notes about one indicator, plus the cross-cutting ones that always apply."""
    qs = ResearchNote.objects.all()
    if topic:
        qs = qs.filter(topic=topic)
    if indicator:
        qs = qs.filter(indicator__in=[indicator, ""])
    return list(qs)
