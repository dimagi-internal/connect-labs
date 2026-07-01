"""Template coaching conversations for synthetic OCS transcripts."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

TEMPLATES: dict[str, list[dict]] = json.loads((Path(__file__).parent / "ocs_templates.json").read_text())

_DEFAULT_KEY = "positive_reinforcement"


def resolve_template_key(base_key: str | None, reason_key: str | None = None) -> str | None:
    """Resolve the effective template key for a (task archetype, reason) pair.

    Prefers the reason-specific variant (``"<base>__<reason>"``) when one
    exists in TEMPLATES; falls back to the base key otherwise. This is how
    a seeded task's coaching transcript stays coherent with the flag that
    spawned it — a ``gender_skew`` task gets the gender-balance
    conversation, never a photo-framing one.
    """
    if not base_key:
        return None
    if reason_key:
        variant = f"{base_key}__{reason_key}"
        if variant in TEMPLATES:
            return variant
    return base_key


def render_transcript(
    *,
    template_key: str,
    flw_name: str,
    base_timestamp: dt.datetime,
    close_timestamp: dt.datetime | None = None,
) -> list[dict[str, str]]:
    """Fill a template with the FLW name and realistic absolute timestamps.

    Turns are spaced with varied, plausible reply gaps (the coach replies
    within a couple of minutes; the worker takes longer) instead of a
    metronomic fixed interval — a transcript whose every message lands in
    the same few minutes reads as fake.

    ``close_timestamp`` (set for closed tasks) stamps the FINAL coach
    message — the one announcing the closure — just before the task's
    History close event, so the transcript and the History agree on when
    the case was closed instead of the coach "closing" days earlier.

    Deterministic per (template_key, flw_name) so regenerations are stable.
    """
    import random

    template = TEMPLATES.get(template_key, TEMPLATES[_DEFAULT_KEY])
    rng = random.Random(f"{template_key}:{flw_name}")

    stamps: list[dt.datetime] = []
    ts = base_timestamp
    for i, msg in enumerate(template):
        if i:
            gap_minutes = rng.randint(1, 3) if msg["role"] == "bot" else rng.randint(2, 9)
            ts = ts + dt.timedelta(minutes=gap_minutes, seconds=rng.randint(0, 59))
        stamps.append(ts)
    if close_timestamp is not None and stamps:
        closing_ts = close_timestamp - dt.timedelta(minutes=rng.randint(3, 9))
        if closing_ts > stamps[-1]:
            stamps[-1] = closing_ts

    result = []
    for msg, ts in zip(template, stamps):
        result.append(
            {
                "role": msg["role"],
                "text": msg["text"].format(flw_name=flw_name),
                "ts": ts.isoformat(),
            }
        )
    return result
