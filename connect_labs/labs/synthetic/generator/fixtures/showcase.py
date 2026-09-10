"""Named demo cases: one corpus trajectory replayed as an openable case.

The probabilistic image knobs answer "does the audit find anything". They cannot
answer "show me the faltering-growth case", because a spread contains no case you
can name and mirror mode names its entities ``Beneficiary 47``.

A showcase case is built rather than sampled. It takes one series from the corpus
manifest — a real infant's real weight sequence — and emits a registration form
followed by one visit per point,
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
    reading_path: str | None = None,
) -> list[dict[str, Any]]:
    """Emit the visits for every showcase case declared on ``config``.

    ``reading_path`` overrides which of ``config.reading_paths`` the entered
    value is written to. The engine passes the path the COHORT was observed to
    use, so the demo cases land in the same field as everyone else even when the
    spec lists several candidates (#1602).

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
                reading_path=reading_path,
            )
        )
    return visits


REGISTRATION_FORM_NAME = "Child Registration Form"
FOLLOWUP_FORM_NAME = "Record Visit Details"

# Gestational age at birth, by birthweight band -- the ordinary preterm picture,
# so "weight for postmenstrual age" has an axis to draw on.
_GA_BY_BIRTHWEIGHT = [(1000, 29.0), (1500, 31.0), (2000, 33.0), (2500, 35.0)]


def _gestational_age_wks(birth_weight_g: float) -> float:
    for ceiling, weeks in _GA_BY_BIRTHWEIGHT:
        if birth_weight_g < ceiling:
            return weeks
    return 37.0


def _registration_visit(
    case: ShowcaseCase,
    *,
    entity_id: str,
    first_reading: float,
    female: bool,
    opportunity_id: int,
    start_date: dt.date,
    deliver_unit_id: Any,
) -> dict[str, Any]:
    """The registration form every mirrored case opens with, and a showcase case
    used to lack.

    A cohort case is a mirror of a real infant: its first visit is the Child
    Registration Form (DOB, sex, gestational age, birth and enrolment weight,
    discharge date, KMC status), and the follow-ups carry a form name. A showcase
    case was built from nothing -- a weight and a photo per visit -- so the KMC
    case-properties pipeline saw no registration at all: the record rail rendered
    dashes, the growth chart fell back to "days since first weighing" (no DOB, no
    gestational age), the registration weight had nothing to plot, and the growth
    class stayed ungraded for want of a birthweight band. Seen on run 5620,
    2026-09-10.

    Values are derived from the trajectory so the record fits the story: born six
    days before the first weighing, discharged two days before it, registered the
    day before it (so it counts as enrolled within three days), birth weight a
    round 100 g below the first reading, enrolment weight equal to the first
    reading (a seed reading distinct from the birth weight, so it is not a
    birth-copy), gestational age by birthweight band. Written at the pipeline's
    extraction paths (form.subcase_0.case.update.* and the child_details /
    hosp_lbl / mothers_details mirrors), which is what the cohort's own
    registration forms carry.
    """
    dob = start_date - dt.timedelta(days=6)
    discharged = start_date - dt.timedelta(days=2)
    registered = start_date - dt.timedelta(days=1)
    birth_weight = max(500.0, round((first_reading - 100.0) / 5.0) * 5.0)
    ga = _gestational_age_wks(birth_weight)
    sex = "Female" if female else "Male"
    created = dt.datetime.combine(registered, dt.time(9, 0))

    form_json: dict[str, Any] = {}
    for path, value in (
        ("form.@name", REGISTRATION_FORM_NAME),
        ("form.case.@case_id", entity_id),
        ("form.case.update.child_alive", "yes"),
        ("form.subcase_0.case.update.reg_date", registered.isoformat()),
        ("form.subcase_0.case.update.child_DOB", dob.isoformat()),
        ("form.subcase_0.case.update.child_gender", sex),
        ("form.subcase_0.case.update.child_weight_birth", birth_weight),
        ("form.subcase_0.case.update.child_weight_reg", first_reading),
        ("form.subcase_0.case.update.gestational_age_at_birth_lmp", ga),
        ("form.subcase_0.case.update.gestational_age_at_birth_preemie", ga),
        ("form.subcase_0.case.update.date_hospital_discharge", discharged.isoformat()),
        ("form.subcase_0.case.update.kmc_status", "enrolled"),
        ("form.subcase_0.case.update.child_alive", "yes"),
        ("form.child_details.birth_weight_group.child_weight_birth", birth_weight),
        ("form.child_details.birth_weight_reg.child_weight_reg", first_reading),
        ("form.child_details.child_gender", sex),
        ("form.child_details.child_age_at_reg_discharge_date", float((registered - discharged).days)),
        ("form.hosp_lbl.date_hospital_discharge", discharged.isoformat()),
        ("form.mothers_details.gestational_age_at_birth_lmp", ga),
    ):
        _set_nested(form_json, path, value)

    return {
        "id": int.from_bytes(hashlib.sha256(f"{case.name}:registration".encode()).digest()[:7], "big"),
        "xform_id": _stable_entity_id(f"{case.name}:xform:registration", opportunity_id),
        "opportunity_id": opportunity_id,
        "username": case.flw,
        "deliver_unit": str(deliver_unit_id) if deliver_unit_id is not None else "",
        "deliver_unit_id": deliver_unit_id,
        "entity_id": entity_id,
        "entity_name": case.name,
        "visit_date": registered.isoformat(),
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
        # A registration carries no weighing, so no photo: the audit's
        # "n of n weighings photographed" stays exact.
        "images": [],
        "showcase": {
            "case": case.name,
            "trajectory": case.trajectory,
            "outcome": case.outcome,
            "visit_seq": 0,
            "form": "registration",
            "birth_weight_g": birth_weight,
            "gestational_age_wks": ga,
        },
    }


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
    reading_path: str | None = None,
) -> list[dict[str, Any]]:
    points = series.get(case.trajectory)
    if not points:
        raise ShowcaseError(
            f"showcase case {case.name!r} names trajectory {case.trajectory!r}, "
            f"which corpus {config.corpus!r} does not carry. Available: {sorted(series)}"
        )

    entity_id = _stable_entity_id(case.name, opportunity_id)
    out: list[dict[str, Any]] = [
        _registration_visit(
            case,
            entity_id=entity_id,
            first_reading=float(points[0]["reading_grams"]),
            female=(case_index % 2 == 0),
            opportunity_id=opportunity_id,
            start_date=start_date,
            deliver_unit_id=deliver_unit_id,
        )
    ]

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
        # The follow-up form's identity and case-update fields, at the paths
        # the KMC case-properties pipeline reads. Without them a showcase visit
        # was a bare weight and a photo: the semantic layer could only count
        # it (n_form_names = 0 falls back to "any visit"), and the worker
        # review's record rail read as dashes.
        _set_nested(form_json, "form.@name", FOLLOWUP_FORM_NAME)
        _set_nested(form_json, "form.case.@case_id", entity_id)
        _set_nested(form_json, "form.case.update.child_alive", "yes")
        _set_nested(form_json, "form.case.update.kmc_status", "KMC visits in progress")
        _set_nested(form_json, "form.child_alive", "yes")
        _set_nested(form_json, "form.kmc_status_entered", "continue")
        _set_nested(form_json, config.question_path, filename)
        # A showcase visit is built from nothing, so unlike a cohort visit there
        # is no existing value to resolve the path against. Take the path the
        # cohort was observed to use when the caller supplies it, so the demo
        # cases land in the SAME field an audit reads for everyone else.
        write_path = reading_path or (config.reading_paths[0] if config.reading_paths else None)
        if write_path:
            _set_nested(form_json, write_path, entered)

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
