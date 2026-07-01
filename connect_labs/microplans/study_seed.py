"""Idempotent seeder for the Vitamin-A Kaura two-arm STUDY (microplans creation demo).

Single source of truth = ``scripts/walkthroughs/verified-monitoring/demo_config.json``
— the SAME file the monitoring narrative reads — plus ``geo/kaura_lga_wards.geojson``.
One **round** = ONE two-arm microplan on the labs-only program ``opportunity_id``
(e.g. ``10008``): both wards stored as arm-tagged ``input_areas`` — the treatment
ward in the ``intervention`` arm, the comparison ward in the ``comparison`` arm —
sampled together with the one shared ``study.sampling`` config so the arms are
comparable by construction. This mirrors the single-plan two-arm UI (one plan, the
arm on each area); the old per-ward-plans + study-group model is gone.

Two verbs, both safe to re-run:

* :func:`ensure_study` — make what's missing, reuse what's there; re-run is a no-op.
  The monitoring narrative calls this to guarantee all rounds exist before it
  visualises them; the creation narrative uses it for the R1–R5 backdrop.
* :func:`reset_round` — delete one round's group + its member plans, so the creation
  walkthrough can re-create that round (the ``live_demo_round``) live on camera.

Idempotency identity (so a re-run reuses, never duplicates):

* group ↔ ``data.name`` ("R6 — Attakar × Gura") within the program
* plan  ↔ the ward's stable admin-boundary id (``input_areas[0].boundary_id``)

The actual sampling reuses :func:`connect_labs.microplans.tasks.sample_group_plans`
— the exact code path the study-page "Generate" button runs — so seeded studies are
identical to hand-built ones.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Repo root: connect_labs/microplans/study_seed.py → parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_CONFIG_PATH = _REPO_ROOT / "scripts" / "walkthroughs" / "verified-monitoring" / "demo_config.json"

# Arm values the sampling engine + study UI expect (see frame.armPaint / generate
# _group_samples). Treatment ward → intervention arm; comparison ward → comparison arm.
ARM_INTERVENTION = "intervention"
ARM_COMPARISON = "comparison"


@dataclass(frozen=True)
class WardSpec:
    name: str
    arm: str  # ARM_INTERVENTION | ARM_COMPARISON
    boundary_id: str
    population: int | None
    geometry: dict  # GeoJSON geometry
    lga: str
    state: str


@dataclass(frozen=True)
class RoundSpec:
    key: str  # "r1".."r6"
    index: int  # 1-based
    label: str  # "R6 — Attakar × Gura" (also the group name)
    wards: tuple[WardSpec, WardSpec]  # (treatment/intervention, comparison)
    live_demo: bool

    @property
    def group_name(self) -> str:
        return self.label


@dataclass(frozen=True)
class StudyManifest:
    opportunity_id: int
    program_id: int  # = opportunity_id (labs-only program surface)
    program_name: str
    sampling: dict  # the shared FrameConfig payload for every arm
    rounds: list[RoundSpec]

    def round_by_key(self, key: str) -> RoundSpec:
        for r in self.rounds:
            if r.key == key:
                return r
        raise KeyError(f"no round {key!r} (have {[r.key for r in self.rounds]})")


# --------------------------------------------------------------------------- load


def _index_wards(geojson: dict) -> dict[str, dict]:
    """{ward name → feature}. Names are unique within the Kaura ward set."""
    out: dict[str, dict] = {}
    for f in geojson.get("features", []):
        name = (f.get("properties") or {}).get("name")
        if name:
            out[name] = f
    return out


def _ward_spec(wards_by_name: dict[str, dict], name: str, arm: str) -> WardSpec:
    feat = wards_by_name.get(name)
    if feat is None:
        raise KeyError(f"ward {name!r} not in wards geojson (have {sorted(wards_by_name)[:5]}…)")
    props = feat.get("properties") or {}
    pop = props.get("population")
    return WardSpec(
        name=name,
        arm=arm,
        boundary_id=str(props.get("boundary_id") or "").strip(),
        population=int(pop) if pop else None,
        geometry=feat["geometry"],
        lga=str(props.get("parent_name") or "").strip(),
        state="",  # labs-only demo; not pushed to Connect, so the importer label is cosmetic
    )


def load_manifest(config_path: Path | str = DEMO_CONFIG_PATH) -> StudyManifest:
    """Build the study manifest from the shared verified-monitoring demo config +
    its wards geojson. Raises if a referenced ward isn't in the geojson."""
    config_path = Path(config_path)
    cfg = json.loads(config_path.read_text())
    opp_id = int(cfg["opportunity_id"])
    geojson_path = config_path.parent / cfg["wards_geojson"]
    wards_by_name = _index_wards(json.loads(geojson_path.read_text()))

    study = cfg.get("study") or {}
    sampling = dict(study.get("sampling") or {})
    live_round = int(study.get("live_demo_round") or 0)

    rounds: list[RoundSpec] = []
    for i, pair in enumerate(cfg["rounds_wards"], start=1):
        treatment, comparison = pair["treatment"], pair["comparison"]
        rounds.append(
            RoundSpec(
                key=f"r{i}",
                index=i,
                label=f"R{i} — {treatment} × {comparison}",
                wards=(
                    _ward_spec(wards_by_name, treatment, ARM_INTERVENTION),
                    _ward_spec(wards_by_name, comparison, ARM_COMPARISON),
                ),
                live_demo=(i == live_round),
            )
        )
    return StudyManifest(
        opportunity_id=opp_id,
        program_id=opp_id,
        program_name=(cfg.get("program") or {}).get("name", ""),
        sampling=sampling,
        rounds=rounds,
    )


# ----------------------------------------------------------------- program backing


def ensure_synthetic_program(manifest: StudyManifest, *, user=None) -> int:
    """Make sure the backing labs-only ``SyntheticOpportunity`` exists + is enabled so
    ``ProgramPlanDataAccess(opp_id)`` routes to the local labs DB. Never clobbers an
    existing row (the monitoring narrative owns its gdrive fixtures). Returns opp id."""
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    row, created = SyntheticOpportunity.objects.get_or_create(
        opportunity_id=manifest.opportunity_id,
        defaults={
            "labs_only": True,
            "enabled": True,
            "label": manifest.program_name or "Vitamin-A Monitoring — Kaura",
            "program_name": manifest.program_name,
            "gdrive_folder_id": "",
            "created_by": user,
        },
    )
    if created:
        logger.info("study_seed: created labs-only synthetic opp %s", manifest.opportunity_id)
    elif not (row.labs_only and row.enabled):
        logger.warning(
            "study_seed: opp %s exists but labs_only=%s enabled=%s — leaving as-is",
            manifest.opportunity_id,
            row.labs_only,
            row.enabled,
        )
    return manifest.opportunity_id


def data_access_for(manifest: StudyManifest):
    """A ``ProgramPlanDataAccess`` bound to the manifest's labs-only program."""
    from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

    return ProgramPlanDataAccess(manifest.program_id, access_token="labs-local")


# ---------------------------------------------------------------------- ensure


def _boundary_id_of(plan) -> str | None:
    for a in plan.data.get("input_areas") or []:
        if a.get("boundary_id"):
            return a["boundary_id"]
    return None


def _plans_by_boundary(plans) -> dict[str, object]:
    out: dict[str, object] = {}
    for p in plans:
        bid = _boundary_id_of(p)
        if bid and bid not in out:
            out[bid] = p
    return out


_EMPTY_FC = {"type": "FeatureCollection", "features": []}


def _input_area_for(w: WardSpec) -> dict:
    """One arm-tagged ``input_area`` for a study ward — inline geometry (resilience +
    the footprints overlay), ``boundary_id`` (so a re-sample can re-resolve it), the
    study ``arm``, and population (plan KPIs). How the two-arm SINGLE plan stores each
    ward."""
    area = {"kind": "admin_boundary", "geometry": w.geometry, "arm": w.arm, "name": w.name}
    if w.boundary_id:
        area["boundary_id"] = w.boundary_id
    if w.population is not None:
        area["population"] = int(w.population)
    return area


def _round_plan(da, rnd: RoundSpec):
    """The round's single two-arm plan: a plan whose ``input_areas`` cover BOTH of the
    round's ward boundaries. ``None`` until it's created."""
    want = {w.boundary_id for w in rnd.wards if w.boundary_id}
    if not want:
        return None
    for p in da.list_plans():
        have = {a.get("boundary_id") for a in (p.data.get("input_areas") or []) if a.get("boundary_id")}
        if want.issubset(have):
            return p
    return None


def ensure_round(da, rnd: RoundSpec, manifest: StudyManifest, *, generate=True, progress=None) -> dict:
    """Idempotently ensure one round = ONE two-arm microplan: both wards stored as
    arm-tagged ``input_areas`` (treatment → intervention, comparison → comparison),
    and — when ``generate`` — the shared PSU sample across both arms. Re-run with the
    plan present (and sampled) is a no-op.

    Replaces the old per-ward-plans + study-group model: the single-plan two-arm UI is
    the one we keep, so the synthetic data mirrors it (one plan, arms on its areas)."""
    from connect_labs.microplans.tasks import sample_plans

    plan = _round_plan(da, rnd)
    created_plans: list[int] = []
    if plan is None:
        treatment = rnd.wards[0]
        plan = da.create_plan(
            region=treatment.name,
            name=rnd.label,
            mode="sampling",
            pins=dict(_EMPTY_FC),
            hulls=dict(_EMPTY_FC),
            input_areas=[_input_area_for(w) for w in rnd.wards],
            lga=treatment.lga,
            state=treatment.state,
        )
        created_plans = [plan.id]

    sampled = {"created": 0, "total": 0, "results": []}
    if generate and plan.phase != "sampled":
        from connect_labs.microplans.sampling.frame import FrameConfig

        fcfg = FrameConfig.from_payload(manifest.sampling)
        sampled = sample_plans(da, [plan], fcfg, progress=progress)

    return {
        "key": rnd.key,
        "label": rnd.label,
        "plan_id": plan.id,
        "created_plans": created_plans,
        "live_demo": rnd.live_demo,
        "sampled": sampled,
    }


def ensure_study(da, manifest: StudyManifest, *, generate=True, only_round=None, progress=None) -> dict:
    """Ensure every round of the study (or just ``only_round``). Idempotent."""
    rounds_out = []
    for rnd in manifest.rounds:
        if only_round and rnd.key != only_round:
            continue
        rounds_out.append(ensure_round(da, rnd, manifest, generate=generate, progress=progress))
    return {"program_id": manifest.program_id, "rounds": rounds_out}


# ----------------------------------------------------------------------- reset


def reset_round(da, manifest: StudyManifest, round_key: str) -> dict:
    """Delete the round's two-arm plan, so the creation walkthrough can re-create it
    live. Also cleans up any LEGACY artefacts from the old model (a study group + its
    per-ward member plans). Safe to run when nothing exists yet."""
    rnd = manifest.round_by_key(round_key)
    target_plan_ids: set[int] = set()

    # the current model: one two-arm plan covering both ward boundaries
    plan = _round_plan(da, rnd)
    if plan is not None:
        target_plan_ids.add(plan.id)

    # legacy model: per-ward plans (one boundary each) + a named study group
    plans_by_boundary = _plans_by_boundary(da.list_plans())
    for ward in rnd.wards:
        p = plans_by_boundary.get(ward.boundary_id)
        if p is not None:
            target_plan_ids.add(p.id)
    groups_by_name = {g.data.get("name"): g for g in da.list_groups()}
    group = groups_by_name.get(rnd.group_name)
    deleted_group = None
    if group is not None:
        target_plan_ids.update(group.plan_ids)
        da.delete_group(group.id)
        deleted_group = group.id

    deleted_plans = []
    for pid in sorted(target_plan_ids):
        try:
            da.delete_plan(pid)
            deleted_plans.append(pid)
        except Exception:  # noqa: BLE001
            pass

    return {
        "key": rnd.key,
        "label": rnd.label,
        "plan_id": (plan.id if plan else None),
        "group_id": deleted_group,
        "plan_ids": deleted_plans,
    }
