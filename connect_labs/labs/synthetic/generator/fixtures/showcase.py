"""Named demo cases: one corpus trajectory replayed as an openable case.

The probabilistic image knobs answer "does the audit find anything". They cannot
answer "show me the faltering-growth case", because a spread contains no case you
can name and mirror mode names its entities ``Beneficiary 47``.

A showcase case is built rather than sampled. It takes one series from the corpus
manifest — a real infant's real weight sequence — and emits one visit per point,
each carrying the photo that series recorded and, for a passing case, that photo's
gateway-confirmed reading as the entered weight. So the case is longitudinal by
construction, every visit has an image, and the entered value agrees with the
picture without any matching step: the trajectory IS the pairing.

That is also why a showcase case does not come out of the mirror transplant pool.
The pool has real weights but no photographs; the corpus has both. A demo case
needs both at every point, so it is layered onto the clone rather than drawn from
it — reproducible and named, at the cost of not descending from one specific real
infant in that opportunity.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

from . import corpus_manifest as cm
from .fields import _set_nested
from .images import failing_value
from .manifest import ImageConfig, ShowcaseCase


class ShowcaseError(Exception):
    """A showcase case names something the corpus does not contain."""


def _stable_entity_id(name: str, opportunity_id: int) -> str:
    """A case id derived from the name AND the opportunity.

    Stable across runs, so a link handed to someone still resolves next week —
    that is the whole point of a designated case, and a random uuid per run
    would defeat it.

    Salted by opportunity because the same showcase block is applied to every
    clone in a cohort. Keyed on the name alone, one demo case would carry ONE
    id across all eleven KMC opportunities, and anything grouping by entity_id
    across the programme — which is exactly what the cross-opp growth-curve
    work does — would see four infants with ~40 visits spread over ten sites in
    three countries. That reads as data corruption, not as a demo.
    """
    digest = hashlib.sha256(f"{opportunity_id}:{name}".encode()).digest()[:16]
    return str(uuid.UUID(bytes=digest))


def build_showcase_visits(
    config: ImageConfig,
    *,
    opportunity_id: int,
    start_date: dt.date,
    deliver_unit_id: Any = None,
    visit_gap_days: int = 7,
) -> list[dict[str, Any]]:
    """Emit the visits for every showcase case declared on ``config``.

    Returns visits in the engine's fixture shape, ready to extend the generated
    ``visits`` list before the shared tail assembles the endpoints.
    """
    if not config.showcase:
        return []

    series = cm.trajectories(config.corpus)
    readings = cm.readings_for(config.corpus)
    bands = cm.bands_for(config.corpus)
    _, bad_count = cm.pool_sizes(config.corpus)

    visits: list[dict[str, Any]] = []
    for case_index, case in enumerate(config.showcase):
        visits.extend(
            _build_case(
                case,
                case_index=case_index,
                config=config,
                series=series,
                readings=readings,
                bands=bands,
                bad_count=bad_count,
                opportunity_id=opportunity_id,
                start_date=start_date,
                deliver_unit_id=deliver_unit_id,
                visit_gap_days=visit_gap_days,
            )
        )
    return visits


def _entered_value(case: ShowcaseCase, true_reading: float, blob_id: str, bands: dict, factor: float) -> float:
    """The value the worker 'typed', given the outcome this case declares."""
    if case.outcome != "fail_number":
        return true_reading
    return failing_value(true_reading, blob_id, bands, factor)


def _build_case(
    case: ShowcaseCase,
    *,
    case_index: int,
    config: ImageConfig,
    series: dict,
    readings: dict,
    bands: dict,
    bad_count: int,
    opportunity_id: int,
    start_date: dt.date,
    deliver_unit_id: Any,
    visit_gap_days: int,
) -> list[dict[str, Any]]:
    points = series.get(case.trajectory)
    if not points:
        raise ShowcaseError(
            f"showcase case {case.name!r} names trajectory {case.trajectory!r}, "
            f"which corpus {config.corpus!r} does not carry. Available: {sorted(series)}"
        )

    entity_id = _stable_entity_id(case.name, opportunity_id)
    out: list[dict[str, Any]] = []

    for i, point in enumerate(points):
        blob_id = point["blob_id"]
        true_reading = float(point["reading_grams"])
        entered = _entered_value(case, true_reading, blob_id, bands, config.bad_reading_factor)

        if case.outcome == "fail_photo":
            # Nothing to read, so the weight stays the cohort's own and the visit
            # fails on the image. Round-robin the bad pool so a multi-visit case
            # does not repeat one frame.
            blob_id = f"synth-{config.corpus}-bad-{(i % max(bad_count, 1)) + 1:03d}"
            entered = true_reading

        visit_date = start_date + dt.timedelta(days=i * visit_gap_days)
        created = dt.datetime.combine(visit_date, dt.time(9, 0))
        filename = f"{config.corpus}_showcase_{case_index:02d}_{i:02d}.jpg"

        form_json: dict[str, Any] = {}
        _set_nested(form_json, config.question_path, filename)
        if config.reading_path:
            _set_nested(form_json, config.reading_path, entered)

        out.append(
            {
                "id": int.from_bytes(hashlib.sha256(f"{case.name}:{i}".encode()).digest()[:7], "big"),
                "xform_id": _stable_entity_id(f"{case.name}:xform:{i}", opportunity_id),
                "opportunity_id": opportunity_id,
                "username": case.flw,
                "deliver_unit": str(deliver_unit_id) if deliver_unit_id is not None else "",
                "deliver_unit_id": deliver_unit_id,
                "entity_id": entity_id,
                "entity_name": case.name,
                "visit_date": visit_date.isoformat(),
                # An over-limit or rejected visit never reaches review, so a demo
                # case has to be ordinary approved work — the whole point is that
                # the AI reviewer, not Connect's status, decides its fate.
                "status": "approved",
                "reason": None,
                "location": None,
                "flagged": False,
                "flag_reason": "",
                "form_json": form_json,
                "completed_work": "",
                "status_modified_date": (created + dt.timedelta(hours=1)).isoformat(),
                "review_status": "approved",
                "review_created_on": (created + dt.timedelta(hours=1, minutes=30)).isoformat(),
                "justification": None,
                "date_created": created.isoformat(),
                "completed_work_id": None,
                "images": [{"blob_id": blob_id, "name": filename}],
                # Provenance for whoever opens this case later.
                "showcase": {
                    "case": case.name,
                    "trajectory": case.trajectory,
                    "outcome": case.outcome,
                    "visit_seq": point["visit_seq"],
                    "photo_shows_grams": true_reading,
                },
            }
        )
    return out
