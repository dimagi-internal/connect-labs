"""One visit per form, following the app's case flow (``Manifest.form_flow``).

The default generator draws a stream of visits that each fill every question of
every form, all filed under the opportunity's first deliver unit, each with a new
entity_id. That cannot show a multi-form app: registrations carrying follow-up
answers, unpaid forms paid for, no beneficiary seen twice (connect-labs#2620).

Here each beneficiary is registered once and keeps one entity_id; every later form
it gets is its own visit, carrying that form's name, xmlns and questions only, and
on a paid form the deliver unit and the Connect <deliver> block it is paid under.
"""

from __future__ import annotations

import datetime as dt
import random
import re
import uuid
from dataclasses import dataclass
from typing import Any

from .copula import build_copula_sampler
from .engine import (
    _anomalies_at,
    _build_household_locations,
    _make_duplicate,
    _packed_location,
    _progression_sign,
    _sample_hour,
    _synthetic_traj_step,
)
from .fields import _get_nested, _set_nested, fill_form_json
from .manifest import BeneficiaryCohort, Condition, DeliverUnitRef, FlwPersona, FormSpec, Manifest
from .schema_loader import AppForm, FormSchema
from .status import decide_visit_status
from .timeline import expand_visit_schedule

# The namespace Connect finds a deliver form's <deliver> block by (commcare-connect
# form_receiver/const.py CCC_LEARN_XMLNS). A form without the block makes no visit.
CONNECT_DELIVER_XMLNS = "http://commcareconnect.com/data/v1/learn"


class FormFlowError(ValueError):
    """The manifest's form flow does not fit the app or the opportunity it is generated for."""


@dataclass
class _Form:
    spec: FormSpec
    xmlns: str
    schema: FormSchema  # the app's questions on this form, its <deliver> block left out
    paths: frozenset[str]  # every path this form records
    cohort: BeneficiaryCohort  # the cohort's distributions narrowed to ``paths``
    deliver: dict[str, Any] | None  # {"id", "slug", "name"}; None on an unpaid form
    deliver_path: str


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _match_app_form(spec: FormSpec, app_forms: list[AppForm]) -> AppForm | None:
    if not app_forms:
        return None
    if spec.xmlns:
        found = next((f for f in app_forms if f.xmlns == spec.xmlns), None)
    else:
        found = next((f for f in app_forms if f.name.strip().casefold() == spec.name.strip().casefold()), None)
    if found is None:
        # The app is known and has no such form: a typo here would otherwise generate a
        # form with no questions and nothing to say why.
        raise FormFlowError(
            f"form_flow form {spec.name!r} is not in the app; its forms are {sorted(f.name for f in app_forms)}"
        )
    return found


def _resolve_deliver_unit(spec: FormSpec, units: list[dict[str, Any]]) -> dict[str, Any] | None:
    ref = spec.deliver_unit
    if ref is None:
        return None
    if isinstance(ref, int):
        ref = DeliverUnitRef(id=ref)
    elif isinstance(ref, str):
        ref = DeliverUnitRef(slug=ref)
    keys = {k.casefold() for k in (ref.slug, ref.name) if k}
    match = next(
        (
            u
            for u in units
            if (ref.id is not None and u.get("id") == ref.id)
            or keys & {str(u.get(k) or "").casefold() for k in ("slug", "name")} - {""}
        ),
        {},
    )
    unit_id = ref.id if ref.id is not None else match.get("id")
    if unit_id is None:
        # Filing a paid form under no unit would quietly make it unpaid.
        raise FormFlowError(
            f"form {spec.name!r} is paid under deliver unit {spec.deliver_unit!r}, which the opportunity "
            f"does not list; give the unit's id (deliver_unit: {{id: ..., slug: ..., name: ...}})"
        )
    slug = match.get("slug") or ref.slug or _slug(ref.name or match.get("name") or str(unit_id))
    return {"id": unit_id, "slug": slug, "name": match.get("name") or ref.name or slug}


def _narrow(form_paths: frozenset[str], spec: FormSpec, cohort: BeneficiaryCohort) -> BeneficiaryCohort:
    """The cohort as one form sees it: distributions at that form's paths only, so a
    follow-up's answers are never drawn onto a registration."""
    dists = {p: d for p, d in cohort.field_distributions.items() if p in form_paths}
    dists.update(spec.field_distributions)
    repeats = {
        base: rg
        for base, rg in cohort.repeat_groups.items()
        if base in form_paths or any(p.startswith(base + ".") for p in form_paths)
    }
    return cohort.model_copy(update={"field_distributions": dists, "repeat_groups": repeats})


def _resolve_forms(
    manifest: Manifest, cohort: BeneficiaryCohort, app_forms: list[AppForm], detail: dict[str, Any]
) -> dict[str, _Form]:
    units = detail.get("deliver_units") or []
    forms: dict[str, _Form] = {}
    for spec in manifest.form_flow.forms:
        app_form = _match_app_form(spec, app_forms)
        deliver_path = (app_form.deliver_path if app_form else None) or "form.deliver"
        questions = [
            q
            for q in (app_form.schema.questions if app_form else [])
            if not (q.json_path == deliver_path or q.json_path.startswith(deliver_path + "."))
        ]
        paths = frozenset({q.json_path for q in questions} | set(spec.fields) | set(spec.field_distributions))
        forms[spec.name] = _Form(
            spec=spec,
            xmlns=spec.xmlns
            or (app_form.xmlns if app_form else "")
            or f"http://openrosa.org/formdesigner/synthetic/{_slug(spec.name)}",
            schema=FormSchema(questions=questions),
            paths=paths,
            cohort=_narrow(paths, spec, cohort),
            deliver=_resolve_deliver_unit(spec, units),
            deliver_path=deliver_path,
        )
    return forms


def _del_nested(obj: dict, dotted_path: str) -> None:
    *parents, leaf = dotted_path.split(".")
    for part in parents:
        obj = obj.get(part) if isinstance(obj, dict) else None
        if obj is None:
            return
    if isinstance(obj, dict):
        obj.pop(leaf, None)


def _holds(condition: Condition, answer) -> bool:
    return all(
        (value := answer(path)) is not None
        and str(value) in {str(e) for e in (want if isinstance(want, list) else [want])}
        for path, want in condition.items()
    )


class _Beneficiary:
    """One beneficiary's answers so far, and which conditional answers they have had."""

    def __init__(self) -> None:
        self.history: list[tuple[dt.date, dict]] = []
        self.taker: dict[tuple[str, int], bool] = {}
        self.fired: set[tuple[str, int]] = set()

    def answer_fn(self, as_of: dt.date, current: dict | None = None):
        def answer(path: str):
            if current is not None and (value := _get_nested(current, path)) is not None:
                return value
            for date, form_json in reversed(self.history):
                if date <= as_of and (value := _get_nested(form_json, path)) is not None:
                    return value
            return None

        return answer


def _apply_answers(form_json: dict, form: _Form, who: _Beneficiary, date: dt.date, rng: random.Random) -> None:
    answer = who.answer_fn(date, form_json)
    for i, ans in enumerate(form.spec.answers):
        key = (form.spec.name, i)
        fire = False
        if _holds(ans.when, answer) and not (ans.at_most_once and key in who.fired):
            if key not in who.taker:
                who.taker[key] = rng.random() < ans.share
            fire = who.taker[key]
        if fire:
            _set_nested(form_json, ans.path, ans.value)
            who.fired.add(key)
        elif ans.otherwise is not None:
            _set_nested(form_json, ans.path, ans.otherwise)
        elif str(_get_nested(form_json, ans.path)) == ans.value:
            _del_nested(form_json, ans.path)


def _working_days(manifest: Manifest) -> dict[str, list[dt.date]]:
    """Each worker's working days, from the same schedule the default generator draws
    its visit slots from (cadence, archetype, day-of-week)."""
    slots = expand_visit_schedule(
        manifest.timeline,
        manifest.flw_personas,
        random_seed=manifest.random_seed,
        day_of_week=(manifest.temporal.day_of_week if manifest.temporal else None),
    )
    days: dict[str, set[dt.date]] = {p.id: set() for p in manifest.flw_personas}
    for s in slots:
        days[s.flw_id].add(s.visit_date)
    return {flw: sorted(d) for flw, d in days.items()}


def build_form_flow_visits(
    *,
    manifest: Manifest,
    cohort: BeneficiaryCohort,
    app_forms: list[AppForm],
    opportunity_detail: dict[str, Any],
    rng: random.Random,
) -> list[dict[str, Any]]:
    flow = manifest.form_flow
    forms = _resolve_forms(manifest, cohort, app_forms, opportunity_detail)
    personas = {p.id: p for p in manifest.flw_personas}
    timeline = manifest.timeline
    start, end = timeline.start_date, timeline.end_date
    working_days = _working_days(manifest)
    copula = build_copula_sampler(cohort.correlation, cohort.field_distributions, seed=manifest.random_seed)
    traj = cohort.longitudinal if (cohort.longitudinal and cohort.longitudinal.mode == "synthetic") else None
    traj_rng = random.Random(manifest.random_seed ^ 0x7137A1) if traj else None
    traj_state: dict[int, dict[str, Any]] = {}
    narrowed_personas: dict[tuple[str, str], FlwPersona] = {}

    def persona_for(flw: str, form: _Form) -> FlwPersona:
        key = (flw, form.spec.name)
        if key not in narrowed_personas:
            p = personas[flw]
            overrides = {k: v for k, v in p.field_overrides.items() if k in form.paths}
            narrowed_personas[key] = p.model_copy(update={"field_overrides": overrides})
        return narrowed_personas[key]

    visits: list[dict[str, Any]] = []

    def emit(form: _Form, flw: str, date: dt.date, *, entity=None, who=None, location: str = "") -> dict | None:
        week = (date - start).days // 7 + 1
        anomalies = _anomalies_at(week, flw, manifest)
        if any(a.type == "missing_visits" for a in anomalies):
            return None
        forced = None
        if traj and entity is not None and any(p in form.paths for p in traj.fields):
            values, _ = _synthetic_traj_step(
                traj, _progression_sign(cohort.progression), traj_state, traj_rng, entity[0], date
            )
            forced = {p: v for p, v in values.items() if p in form.paths}
        form_json = fill_form_json(
            schema=form.schema,
            cohort=form.cohort,
            anomalies_for_visit=anomalies,
            rng=rng,
            persona=persona_for(flw, form),
            period=week,
            correlated_values=copula.draw() if copula else None,
            forced_values=forced,
        )
        _apply_answers(form_json, form, who or _Beneficiary(), date, rng)
        entity_id, entity_name = (entity[1], entity[2]) if entity else ("", "")
        form_json.setdefault("form", {}).update({"@name": form.spec.name, "@xmlns": form.xmlns})
        form_json["xmlns"] = form.xmlns
        if form.deliver:
            _set_nested(
                form_json,
                form.deliver_path,
                {
                    "@xmlns": CONNECT_DELIVER_XMLNS,
                    "@id": form.deliver["slug"],
                    "name": form.deliver["name"],
                    "entity_id": entity_id,
                    "entity_name": entity_name,
                },
            )
        if location:
            form_json.setdefault("metadata", {})["location"] = location
        status = decide_visit_status(
            persona=personas[flw],
            has_anomaly=any(a.type == "field_outlier" and a.field_path in form.paths for a in anomalies),
            rng=rng,
            flag_reason_distribution=manifest.flag_reason_distribution,
            over_limit_rate=manifest.over_limit_rate,
            status_by_flag=manifest.status_by_flag,
        )
        created = dt.datetime.combine(date, dt.time(_sample_hour(rng, manifest.temporal), 0))
        unit_id = form.deliver["id"] if form.deliver else None
        visit = {
            "id": rng.getrandbits(60),
            "xform_id": str(uuid.UUID(int=rng.getrandbits(128))),
            "opportunity_id": manifest.opportunity_id,
            "username": flw,
            "deliver_unit": str(unit_id) if unit_id is not None else "",
            "deliver_unit_id": unit_id,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "visit_date": date.isoformat(),
            "status": status.status,
            "reason": None,
            "location": location,
            "flagged": status.flagged,
            "flag_reason": status.flag_reason,
            "form_json": form_json,
            "completed_work": "",
            "status_modified_date": (created + dt.timedelta(hours=1)).isoformat(),
            "review_status": status.review_status,
            "review_created_on": (created + dt.timedelta(hours=1, minutes=30)).isoformat(),
            "justification": None,
            "date_created": created.isoformat(),
            "completed_work_id": None,
            "images": [],
        }
        visits.append(visit)
        if who is not None:
            who.history.append((date, form_json))
        if any(a.type == "duplicate_submission" for a in anomalies):
            visits.append(_make_duplicate(visit, created, rng))
        return visit

    beneficiary_forms = flow.ordered()
    if any(f.role == "registration" for f in beneficiary_forms):
        window_end = end
        if flow.registration_weeks:
            window_end = min(end, start + dt.timedelta(days=flow.registration_weeks * 7 - 1))
        owners = {idx: rng.choice(manifest.flw_personas).id for idx in range(1, cohort.size + 1)}
        households = (
            _build_household_locations(manifest.geography, cohort.size, rng, owners) if manifest.geography else None
        )
        for idx, flw in owners.items():
            days = [d for d in working_days[flw] if d <= window_end]
            registered = (
                rng.choice(days) if days else start + dt.timedelta(days=rng.randint(0, (window_end - start).days))
            )
            entity = (idx, str(uuid.UUID(int=rng.getrandbits(128))), cohort.entity_label(idx))
            location = _packed_location(households, idx, manifest.geography, rng)
            who = _Beneficiary()
            planned: dict[str, list[dt.date]] = {}
            for spec in beneficiary_forms:
                form = forms[spec.name]
                if spec.role == "registration":
                    dates = [registered]
                else:
                    priors = flow.prior_forms(spec)
                    if not all(planned.get(p) for p in priors):
                        continue
                    first = max(planned[p][-1] for p in priors) + dt.timedelta(
                        days=rng.randint(spec.delay_days.min, spec.delay_days.max)
                    )
                    if not _holds(spec.when, who.answer_fn(first)) or rng.random() >= spec.share:
                        continue
                    dates = [first]
                    for _ in range(spec.count - 1):
                        dates.append(
                            dates[-1] + dt.timedelta(days=rng.randint(spec.interval_days.min, spec.interval_days.max))
                        )
                # A visit the timeline ends before never happened.
                planned[spec.name] = [d for d in dates if d <= end]
                for date in planned[spec.name]:
                    emit(form, flw, date, entity=entity, who=who, location=location)

    for spec in flow.forms:
        if spec.role != "survey":
            continue
        whole, part = int(spec.per_week), spec.per_week - int(spec.per_week)
        for persona in manifest.flw_personas:
            for week in range(1, timeline.weeks + 1):
                week_start = start + dt.timedelta(days=(week - 1) * 7)
                days = [d for d in working_days[persona.id] if week_start <= d < week_start + dt.timedelta(days=7)]
                for _ in range(whole + (1 if rng.random() < part else 0)):
                    date = rng.choice(days) if days else week_start + dt.timedelta(days=rng.randint(0, 6))
                    if date <= end:
                        emit(forms[spec.name], persona.id, date)

    visits.sort(key=lambda v: v["visit_date"])
    return visits
