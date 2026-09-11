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

The trajectory supplies the weights and the photographs; everything else about
the case is CLONED from a standard case in the same cohort. A mirrored case opens
with the Child Registration Form and continues with Record Visit Details forms,
each carrying the full field set the real app writes (hundreds of fields). The
showcase builder deep-copies one such case -- registration plus the first N
weighed follow-ups -- and then applies only the showcase specifics: identity,
dates (shifted so the internal offsets between birth, discharge, registration
and visits are the template's own), the weights, the photos, and a birth /
enrolment weight and gestational age consistent with the trajectory. Built from
nothing, a showcase case was a weight and a photo per visit; the pipeline saw no
registration, the record rail read as dashes and the growth chart had no age
axis (run 5620, 2026-09-10). Jon: "follow the clone path as much as possible
(duplicate a standard case) and then apply the showcase specifics".

When the cohort offers no template (no case alive throughout with a registration
and enough weighed follow-ups), the builder falls back to synthesising the forms
at the pipeline's extraction paths, which is complete for everything the pages
read but not field-for-field.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import re
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
    template_visits: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Emit the visits for every showcase case declared on ``config``.

    ``template_visits`` is the cohort's own visit list (the mirror clone, before
    the showcase cases are appended). Each showcase case is a deep copy of one
    standard case drawn from it, with the showcase specifics applied on top; see
    the module docstring. Without it the forms are synthesised.

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
                template_visits=template_visits,
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


# ── Clone path ────────────────────────────────────────────────────────────────

_WEIGHT_KEYS = {"child_weight_visit", "child_weight", "child_weight_last_visit"}
_BIRTH_WEIGHT_KEYS = {"child_weight_birth", "birth_weight"}
_ENROL_WEIGHT_KEYS = {"child_weight_reg"}
_GA_KEY = re.compile(r"^gestational_age")
_ISO_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(.*)$")
_ALIVE_KEYS = {"child_alive"}


def _form_name(visit: dict[str, Any]) -> str:
    form = (visit.get("form_json") or {}).get("form") or {}
    return str(form.get("@name") or "")


def _walk(node: Any):
    """Yield (parent, key) for every leaf in a nested dict/list."""
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if isinstance(v, (dict, list)):
                yield from _walk(v)
            else:
                yield node, k
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, (dict, list)):
                yield from _walk(v)
            else:
                yield node, i


def _get_path(form_json: dict[str, Any], dotted: str) -> Any:
    node: Any = form_json
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _has_weight(visit: dict[str, Any], write_path: str | None) -> bool:
    fj = visit.get("form_json") or {}
    if write_path and _get_path(fj, write_path) not in (None, ""):
        return True
    return any(
        k in _WEIGHT_KEYS and v not in (None, "")
        for parent, k in _walk(fj)
        if isinstance(parent, dict)
        for v in [parent[k]]
    )


_EVENT_KEY = re.compile(r"referr|danger|death|died", re.I)
# Procedural leaves that live inside an event group without being an event:
# consent, an equipment check, the image-capture confirmation, labels. On the
# KMC cohort three such fields read "yes" on nearly every case, which made
# every case eventful and left nothing to clone.
_PROCEDURAL_LEAF = re.compile(r"consent|equipment|capture|check(ed|list)?$|_lbl$|^space", re.I)


def _walk_paths(node: Any, prefix: str = ""):
    """Yield (dotted path, value) for every leaf, so a group name such as
    ``Danger_Signs_Checklist`` counts even when the leaf is just ``jaundice``."""
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                yield from _walk_paths(v, path)
            else:
                yield path, v
    elif isinstance(node, list):
        for i, v in enumerate(node):
            path = f"{prefix}[{i}]"
            if isinstance(v, (dict, list)):
                yield from _walk_paths(v, path)
            else:
                yield path, v


def _is_uneventful(visit: dict[str, Any]) -> bool:
    """No referral, danger sign or death answered on this form.

    A showcase case tells one story -- the growth and the photos -- and the
    template supplies everything else. The first template the BERI cohort
    offered for "Steady Gain" had a positive danger sign and a hospital referral
    on its second visit, so the clean-growth demo read "Referrals 5" (run 5620,
    2026-09-10). Keyed on field NAMES so it holds for any form vocabulary: a
    key naming a referral, danger sign or death whose value reads yes / true
    or names a referral state disqualifies the case; the match is on the full
    dotted path, so a group named for danger signs covers its leaves.
    """
    fj = visit.get("form_json") or {}
    for path, v in _walk_paths(fj):
        if not _EVENT_KEY.search(path):
            continue
        if _PROCEDURAL_LEAF.search(path.rsplit(".", 1)[-1]):
            continue
        if isinstance(v, bool):
            if v:
                return False
            continue
        sv = str(v).strip().lower()
        if sv in ("yes", "true", "1") or "referr" in sv:
            return False
    return True


def _is_alive(visit: dict[str, Any]) -> bool:
    fj = visit.get("form_json") or {}
    vals = [parent[k] for parent, k in _walk(fj) if isinstance(parent, dict) and k in _ALIVE_KEYS]
    return all(str(v).lower() != "no" for v in vals)


def _pick_template(
    template_visits: list[dict[str, Any]] | None,
    case: ShowcaseCase,
    *,
    n_followups: int,
    write_path: str | None,
) -> dict[str, Any] | None:
    """One standard case to duplicate: a registration form plus weighed
    follow-ups (ideally ``n_followups`` of them; the last is reused when the
    cohort is too young to offer that many), the child alive throughout and nothing
    eventful (no referral, danger sign or death) on any of its forms. The same
    worker's cases are preferred so the clone reads as that worker's ordinary
    work; any worker's will do, because the username is overridden anyway.

    Deterministic: candidates are ordered by entity id and the pick is keyed on
    the case name, so a showcase case clones the same template every run and two
    showcase cases in one cohort do not clone the same one unless they must.
    """
    if not template_visits:
        return None
    by_case: dict[str, list[dict[str, Any]]] = {}
    for v in template_visits:
        if v.get("showcase") or not v.get("entity_id"):
            continue
        by_case.setdefault(str(v["entity_id"]), []).append(v)

    def qualifies(vs: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        vs = sorted(vs, key=lambda v: str(v.get("visit_date") or ""))
        regs = [v for v in vs if "regist" in _form_name(v).lower()]
        if not regs:
            return None
        reg = regs[0]
        followups = [
            v
            for v in vs
            if v is not reg
            and "regist" not in _form_name(v).lower()
            and str(v.get("visit_date") or "") >= str(reg.get("visit_date") or "")
            and _has_weight(v, write_path)
        ]
        # A young cohort may hold no case with as many weighed follow-ups as
        # the trajectory has points (opp 2166 was a month old when cloned: three
        # of four demo cases found no template and fell back to synthesised
        # forms). One weighed follow-up is enough to clone from; the last one is
        # reused for the extra weighings.
        if not followups:
            return None
        if not all(_is_alive(v) and _is_uneventful(v) for v in vs):
            return None
        return reg, followups[:n_followups]

    same_flw = []
    others = []
    for eid in sorted(by_case):
        picked = qualifies(by_case[eid])
        if picked is None:
            continue
        (same_flw if by_case[eid][0].get("username") == case.flw else others).append((eid, picked))
    pool = same_flw or others
    if not pool:
        return None
    full = [c for c in pool if len(c[1][1]) >= n_followups]
    pool = full or pool
    idx = int.from_bytes(hashlib.sha256(case.name.encode()).digest()[:4], "big") % len(pool)
    eid, (reg, followups) = pool[idx]
    return {"entity_id": eid, "registration": reg, "followups": followups}


def _shift_dates(node: Any, delta: dt.timedelta) -> None:
    """Move every ISO date string in a form by ``delta``, in place, keeping any
    time suffix. The template's own offsets (birth to discharge to registration
    to each visit) are what make the record internally consistent."""
    for parent, key in _walk(node):
        v = parent[key]
        if not isinstance(v, str):
            continue
        m = _ISO_DATE.match(v)
        if not m:
            continue
        try:
            d = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        parent[key] = (d + delta).isoformat() + m.group(2)


def _replace_values(node: Any, old: str, new: str) -> None:
    for parent, key in _walk(node):
        if parent[key] == old:
            parent[key] = new


def _set_keys(node: Any, keys: set[str], value: Any) -> int:
    n = 0
    for parent, key in _walk(node):
        if isinstance(parent, dict) and key in keys:
            parent[key] = value
            n += 1
    return n


def _set_key_re(node: Any, pattern: re.Pattern, value: Any) -> int:
    n = 0
    for parent, key in _walk(node):
        if isinstance(parent, dict) and isinstance(key, str) and pattern.match(key):
            parent[key] = value
            n += 1
    return n


def _stamp(
    visit: dict[str, Any],
    *,
    case: ShowcaseCase,
    tag: str,
    entity_id: str,
    template_entity_id: str,
    opportunity_id: int,
    deliver_unit_id: Any,
    new_date: dt.date,
) -> None:
    """The identity and timing every cloned visit gets, whatever else it carries."""
    old_date_s = str(visit.get("visit_date") or "")[:10]
    try:
        old_date = dt.date.fromisoformat(old_date_s)
    except ValueError:
        old_date = new_date
    delta = new_date - old_date
    created = dt.datetime.combine(new_date, dt.time(9, 0))
    visit["id"] = int.from_bytes(hashlib.sha256(f"{case.name}:{tag}".encode()).digest()[:7], "big")
    visit["xform_id"] = _stable_entity_id(f"{case.name}:xform:{tag}", opportunity_id)
    visit["opportunity_id"] = opportunity_id
    visit["username"] = case.flw
    visit["deliver_unit"] = str(deliver_unit_id) if deliver_unit_id is not None else ""
    visit["deliver_unit_id"] = deliver_unit_id
    visit["entity_id"] = entity_id
    visit["entity_name"] = case.name
    visit["visit_date"] = new_date.isoformat()
    # An over-limit or rejected visit never reaches review, so a demo case has
    # to be ordinary approved work -- the AI reviewer decides its fate.
    visit["status"] = "approved"
    visit["reason"] = None
    visit["flagged"] = False
    visit["flag_reason"] = ""
    visit["review_status"] = "approved"
    visit["status_modified_date"] = (created + dt.timedelta(hours=1)).isoformat()
    visit["review_created_on"] = (created + dt.timedelta(hours=1, minutes=30)).isoformat()
    visit["date_created"] = created.isoformat()
    visit["completed_work_id"] = None
    visit.pop("location", None)
    visit["location"] = None
    fj = visit.setdefault("form_json", {})
    _shift_dates(fj, delta)
    _replace_values(fj, template_entity_id, entity_id)
    _set_keys(fj, _ALIVE_KEYS, "yes")


def _clone_case(
    case: ShowcaseCase,
    *,
    template: dict[str, Any],
    case_index: int,
    config: ImageConfig,
    points: list[dict[str, Any]],
    bands: dict,
    bad_count: int,
    entity_id: str,
    opportunity_id: int,
    start_date: dt.date,
    deliver_unit_id: Any,
    visit_gap_days: int,
    write_path: str | None,
) -> list[dict[str, Any]]:
    """Duplicate the template case, then apply the showcase specifics."""
    first_reading = float(points[0]["reading_grams"])
    birth_weight = max(500.0, round((first_reading - 100.0) / 5.0) * 5.0)
    ga = _gestational_age_wks(birth_weight)
    tid = str(template["entity_id"])

    reg = copy.deepcopy(template["registration"])
    reg.pop("images", None)
    reg["images"] = []
    _stamp(
        reg,
        case=case,
        tag="registration",
        entity_id=entity_id,
        template_entity_id=tid,
        opportunity_id=opportunity_id,
        deliver_unit_id=deliver_unit_id,
        new_date=start_date - dt.timedelta(days=1),
    )
    fj = reg["form_json"]
    _set_keys(fj, _BIRTH_WEIGHT_KEYS, birth_weight)
    _set_keys(fj, _ENROL_WEIGHT_KEYS, first_reading)
    _set_key_re(fj, _GA_KEY, ga)
    reg["showcase"] = {
        "case": case.name,
        "trajectory": case.trajectory,
        "outcome": case.outcome,
        "visit_seq": 0,
        "form": "registration",
        "cloned_from": tid,
        "birth_weight_g": birth_weight,
        "gestational_age_wks": ga,
    }
    out = [reg]

    followups = template["followups"]
    for i, point in enumerate(points):
        tmpl = followups[min(i, len(followups) - 1)]
        blob_id = point["blob_id"]
        true_reading = float(point["reading_grams"])
        entered = _entered_value(case, true_reading, blob_id, bands, config.bad_reading_factor)
        if case.outcome == "fail_photo":
            blob_id = f"synth-{config.corpus}-bad-{(i % max(bad_count, 1)) + 1:03d}"
            entered = true_reading
        filename = f"{config.corpus}_showcase_{case_index:02d}_{i:02d}.jpg"

        v = copy.deepcopy(tmpl)
        _stamp(
            v,
            case=case,
            tag=str(i),
            entity_id=entity_id,
            template_entity_id=tid,
            opportunity_id=opportunity_id,
            deliver_unit_id=deliver_unit_id,
            new_date=start_date + dt.timedelta(days=i * visit_gap_days),
        )
        fj = v["form_json"]
        # The weight the worker "typed", at every place the template records a
        # visit weight, and at the cohort's resolved path in case the template
        # kept it somewhere else.
        _set_keys(fj, _WEIGHT_KEYS, entered)
        if write_path:
            _set_nested(fj, write_path, entered)
        _set_nested(fj, config.question_path, filename)
        v["images"] = [{"blob_id": blob_id, "name": filename}]
        v["showcase"] = {
            "case": case.name,
            "trajectory": case.trajectory,
            "outcome": case.outcome,
            "visit_seq": point["visit_seq"],
            "cloned_from": tid,
            "photo_shows_grams": true_reading,
        }
        out.append(v)
    return out


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
    template_visits: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    points = series.get(case.trajectory)
    if not points:
        raise ShowcaseError(
            f"showcase case {case.name!r} names trajectory {case.trajectory!r}, "
            f"which corpus {config.corpus!r} does not carry. Available: {sorted(series)}"
        )

    entity_id = _stable_entity_id(case.name, opportunity_id)
    first_reading = float(points[0]["reading_grams"])
    write_path = reading_path or (config.reading_paths[0] if config.reading_paths else None)

    template = _pick_template(template_visits, case, n_followups=len(points), write_path=write_path)
    if template is not None:
        return _clone_case(
            case,
            template=template,
            case_index=case_index,
            config=config,
            points=points,
            bands=bands,
            bad_count=bad_count,
            entity_id=entity_id,
            opportunity_id=opportunity_id,
            start_date=start_date,
            deliver_unit_id=deliver_unit_id,
            visit_gap_days=visit_gap_days,
            write_path=write_path,
        )

    out: list[dict[str, Any]] = [
        _registration_visit(
            case,
            entity_id=entity_id,
            first_reading=first_reading,
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
        # A synthesised visit has no existing value to resolve the path against.
        # Take the path the cohort was observed to use when the caller supplies
        # it, so the demo cases land in the SAME field an audit reads for
        # everyone else.
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
