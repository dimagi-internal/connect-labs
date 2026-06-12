"""Idempotent seeder for the Vitamin-A Kaura two-arm STUDY (microplans creation demo).

Single source of truth = ``scripts/walkthroughs/verified-monitoring/demo_config.json``
— the SAME file the monitoring narrative reads — plus ``geo/kaura_lga_wards.geojson``.
One **round** = one two-arm study group of per-ward microplans on the labs-only
program ``opportunity_id`` (e.g. ``10008``): the treatment ward in the
``intervention`` arm, the comparison ward in the ``comparison`` arm, both sampled
with the one shared ``study.sampling`` config so the arms are comparable by
construction. Arm assignment is labs-side only (never written onto the plans).

Two verbs, both safe to re-run:

* :func:`ensure_study` — make what's missing, reuse what's there; re-run is a no-op.
  The monitoring narrative calls this to guarantee all rounds exist before it
  visualises them; the creation narrative uses it for the R1–R5 backdrop.
* :func:`reset_round` — delete one round's group + its member plans, so the creation
  walkthrough can re-create that round (the ``live_demo_round``) live on camera.

Idempotency identity (so a re-run reuses, never duplicates):

* group ↔ ``data.name`` ("R6 — Attakar × Gura") within the program
* plan  ↔ the ward's stable admin-boundary id (``input_areas[0].boundary_id``)

The actual sampling reuses :func:`commcare_connect.microplans.tasks.sample_group_plans`
— the exact code path the study-page "Generate" button runs — so seeded studies are
identical to hand-built ones.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Repo root: commcare_connect/microplans/study_seed.py → parents[2].
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
    from commcare_connect.labs.synthetic.models import SyntheticOpportunity

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
    from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

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


def ensure_round(da, rnd: RoundSpec, manifest: StudyManifest, *, generate=True, progress=None) -> dict:
    """Idempotently ensure one round: its two ward plans (by boundary_id), the study
    group (by name) with arms + shared config, and — when ``generate`` — the PSU
    sample for any not-yet-sampled member. Re-run with everything present is a no-op.
    """
    from commcare_connect.microplans.tasks import create_boundary_plan, sample_group_plans

    plans_by_boundary = _plans_by_boundary(da.list_plans())
    plan_ids: list[int] = []
    arms: dict[str, str] = {}
    created_plans: list[int] = []

    for ward in rnd.wards:
        plan = plans_by_boundary.get(ward.boundary_id)
        if plan is None:
            plan = create_boundary_plan(
                da,
                mode="sampling",
                name=ward.name,
                region=ward.name,
                geometry=ward.geometry,
                boundary_id=ward.boundary_id,
                population=ward.population,
                lga=ward.lga,
                state=ward.state,
            )
            plans_by_boundary[ward.boundary_id] = plan
            created_plans.append(plan.id)
        plan_ids.append(plan.id)
        arms[str(plan.id)] = ward.arm

    groups_by_name = {g.data.get("name"): g for g in da.list_groups()}
    group = groups_by_name.get(rnd.group_name)
    if group is None:
        group = da.create_group(
            name=rnd.group_name,
            plan_ids=plan_ids,
            kind="study",
            arms=arms,
            sampling_config=manifest.sampling,
        )
    else:
        # Reconcile drift (membership / arms / config / kind) without recreating.
        fields = {}
        if set(group.plan_ids) != set(plan_ids):
            fields["plan_ids"] = plan_ids
        if dict(group.arms) != arms:
            fields["arms"] = arms
        if group.data.get("sampling_config") != manifest.sampling:
            fields["sampling_config"] = manifest.sampling
        if group.data.get("kind") != "study":
            fields["kind"] = "study"
        if fields:
            group = da.update_group(group.id, **fields)

    sampled = {"created": 0, "total": 0, "results": []}
    if generate:
        from commcare_connect.microplans.sampling.frame import FrameConfig

        members = [da.get_plan(pid) for pid in plan_ids]
        if any(p.phase != "sampled" for p in members):
            fcfg = FrameConfig.from_payload(manifest.sampling)
            sampled = sample_group_plans(da, group, fcfg, progress=progress)
            da.update_group(group.id, status="sampled")

    return {
        "key": rnd.key,
        "label": rnd.label,
        "group_id": group.id,
        "plan_ids": plan_ids,
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
    """Delete one round's study group + its member plans (and any plan still matching
    the round's ward boundaries), so the creation walkthrough can re-create it live.
    Safe to run when nothing exists yet."""
    rnd = manifest.round_by_key(round_key)
    groups_by_name = {g.data.get("name"): g for g in da.list_groups()}
    group = groups_by_name.get(rnd.group_name)

    target_plan_ids: set[int] = set(group.plan_ids) if group else set()
    plans_by_boundary = _plans_by_boundary(da.list_plans())
    for ward in rnd.wards:
        p = plans_by_boundary.get(ward.boundary_id)
        if p is not None:
            target_plan_ids.add(p.id)

    deleted_group = None
    if group is not None:
        da.delete_group(group.id)
        deleted_group = group.id
    deleted_plans = []
    for pid in sorted(target_plan_ids):
        da.delete_plan(pid)
        deleted_plans.append(pid)

    return {"key": rnd.key, "label": rnd.label, "group_id": deleted_group, "plan_ids": deleted_plans}
