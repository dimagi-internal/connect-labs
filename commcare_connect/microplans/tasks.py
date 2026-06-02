"""Celery tasks for microplans map generation.

`generate_frame` / `generate_coverage_frame` / `fetch_buildings` first-fetch an
area from Overture S3 (tens of seconds; subsequent runs hit the PG footprint
cache). Run synchronously inside a request they block a gunicorn gthread worker
for the whole fetch — with ``WEB_CONCURRENCY=3`` (see ``docker/start``) a few
concurrent cold previews exhaust the pool and stall every request, including
auth. These tasks move that work onto the Celery worker (``labs-jj-worker``):
the preview views enqueue and return ``202 {task_id, poll_url}``,
``PreviewStatusView`` reports progress/result, and the front-end polls.

Each task returns the same response envelope the old synchronous view returned —
``{"status": "ok", ...data}`` on success, or ``{"status": "error", "detail": …}``
for an *expected*, user-actionable ``ValueError`` (e.g. "area too large"). An
unexpected exception is allowed to propagate so Celery marks the task FAILURE
and the status view surfaces a generic message without leaking internals.
"""

import logging

from commcare_connect.utils.celery import set_task_progress
from config import celery_app

logger = logging.getLogger(__name__)

_FETCHING = "Fetching building footprints…"


@celery_app.task(bind=True)
def generate_frame_task(self, areas, config_payload):
    """Sampling-mode preview: footprints → PPS-sampled pins + cluster hulls."""
    from commcare_connect.microplans.sampling.frame import FrameConfig, generate_frame

    set_task_progress(self, _FETCHING)
    config = FrameConfig.from_payload(config_payload or {})
    try:
        result = generate_frame(areas, config)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return {
        "status": "ok",
        "pins": result.pins_geojson,
        "hulls": result.hulls_geojson,
        "stats": result.stats,
    }


@celery_app.task(bind=True)
def generate_coverage_task(self, areas, config_payload):
    """Coverage-mode preview: footprints → balanced/grid cluster polygons."""
    from commcare_connect.microplans.coverage.frame import CoverageConfig, generate_coverage_frame

    set_task_progress(self, _FETCHING)
    config = CoverageConfig.from_payload(config_payload or {})
    try:
        result = generate_coverage_frame(areas, config)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "ok", "areas": result.areas_geojson, "stats": result.stats}


@celery_app.task(bind=True)
def fetch_footprints_task(self, areas):
    """Building footprints (as point features) inside the drawn area(s)."""
    from shapely.ops import unary_union

    from commcare_connect.microplans.core.area_input import resolve_area
    from commcare_connect.microplans.core.footprints import fetch_buildings

    set_task_progress(self, _FETCHING)
    try:
        geom = unary_union([resolve_area(a) for a in areas])
        df = fetch_buildings(geom, min_confidence=None)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
            "properties": {},
        }
        for _, row in df.iterrows()
    ]
    return {
        "status": "ok",
        "footprints": {"type": "FeatureCollection", "features": features},
        "count": len(features),
    }


@celery_app.task(bind=True)
def apply_plan_mutation_task(self, op, program_id, plan_id, params, actor, access_token):
    """Run a heavy plan mutation (regroup / reassign / regenerate) off the web tier.

    These re-run BFS grouping / minimax assignment / re-materialization over up to
    MAX_WORK_AREAS cells — synchronously in a request they pin a gthread (same
    starvation class as the previews #352 offloaded). Returns ``plan_to_json`` on
    success, ``{status: conflict}`` on a stale-revision clash (#355) so the client
    can warn+reload, or ``{status: error}`` for an actionable failure."""
    from commcare_connect.microplans import serialization
    from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess, StalePlanError

    da = ProgramPlanDataAccess(int(program_id), access_token=access_token)
    base_revision = params.get("revision")
    set_task_progress(self, "Applying…")
    try:
        if op == "regroup":
            plan = da.regroup_plan(int(plan_id), params.get("grouping") or {}, actor, base_revision=base_revision)
        elif op == "reassign":
            plan = da.reassign_plan(int(plan_id), params.get("assignment") or {}, actor, base_revision=base_revision)
        elif op == "regenerate":
            plan = da.regenerate_plan(
                int(plan_id),
                mode=params.get("mode"),
                pins=params.get("pins"),
                hulls=params.get("hulls"),
                input_areas=params.get("input_areas") or [],
                grouping=params.get("grouping") or {},
                base_revision=base_revision,
            )
        else:
            return {"status": "error", "detail": f"unknown op {op!r}"}
    except StalePlanError as e:
        return {"status": "conflict", "detail": str(e)}
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return serialization.plan_to_json(plan)


@celery_app.task(bind=True)
def bulk_create_plans_task(self, program_id, plans_input, mode, grouping, cell_size_m, access_token, group_id=None):
    """Create N draft plans from confirmed admin boundaries — one per ward.

    Unlike the old inline streaming view, each ward is GRIDDED here: coverage plans
    run ``generate_coverage_frame`` over the ward boundary (the Overture fetch +
    clustering, which is why this is on the worker, not the web tier) so the plan is
    a real grid of work areas rather than one cell covering the whole ward.

    Reports incremental per-ward progress via ``set_task_progress`` (a growing
    ``results`` list in the task meta) so the front-end can flip each row's pill as
    its ward finishes. Returns the final ``{results, created, total}`` summary."""
    from commcare_connect.labs.admin_boundaries.models import AdminBoundary
    from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

    da = ProgramPlanDataAccess(int(program_id), access_token=access_token)
    total = len(plans_input)
    results: list[dict] = []
    ok = 0

    # One DB round-trip for all wards' geometries.
    wanted = [str((p or {}).get("boundary_id") or "").strip() for p in plans_input]
    boundary_by_id = {b.boundary_id: b for b in AdminBoundary.objects.filter(boundary_id__in=[w for w in wanted if w])}

    def _emit(progress_msg):
        set_task_progress(self, progress_msg, results=list(results), created=ok, total=total)

    for index, spec in enumerate(plans_input):
        spec = spec if isinstance(spec, dict) else {}
        name = (spec.get("name") or "").strip()[:255]
        boundary_id = (spec.get("boundary_id") or "").strip()
        row = {"index": index, "name": name, "boundary_id": boundary_id}

        boundary = boundary_by_id.get(boundary_id) if boundary_id else None
        if not boundary:
            results.append(
                {**row, "status": "error", "detail": "boundary not found" if boundary_id else "missing boundary_id"}
            )
            _emit(f"{index + 1}/{total}")
            continue

        display_name = name or boundary.name
        row["name"] = display_name
        try:
            ward_geojson = _ward_grid_hulls(boundary, mode, cell_size_m)
            plan = da.create_plan(
                region=display_name,
                name=display_name,
                mode=mode,
                pins={"type": "FeatureCollection", "features": []},
                hulls=ward_geojson,
                input_areas=[
                    {
                        "kind": "admin_boundary",
                        "boundary_id": boundary_id,
                        "name": boundary.name,
                        # Carry the boundary's population estimate onto the plan so
                        # plan_kpis().plan.total_population reports the boundary's
                        # known population, not a bottom-up sum of work-area
                        # apportionments. None when the boundary has no population
                        # estimate loaded; plan_kpis falls back gracefully.
                        "population": int(boundary.population) if boundary.population is not None else None,
                    }
                ],
                grouping=grouping,
            )
            ok += 1
            if group_id is not None:
                # File the new plan into the group. Don't fail the ward on a
                # group hiccup — the plan exists; surface a soft warning instead.
                try:
                    da.add_plan_to_group(int(group_id), plan.id)
                except Exception:  # noqa: BLE001
                    logger.exception("bulk_create: add to group failed (group=%s plan=%s)", group_id, plan.id)
            results.append({**row, "status": "ok", "plan_id": plan.id, "work_areas": len(plan.work_areas)})
        except ValueError as e:
            # Actionable (e.g. area too large / too many cells) — surface to the row.
            results.append({**row, "status": "error", "detail": str(e)})
        except Exception:  # noqa: BLE001
            logger.exception("bulk_create: ward failed (program=%s, ward=%s)", program_id, boundary_id)
            results.append({**row, "status": "error", "detail": "create failed"})
        _emit(f"{index + 1}/{total}")

    return {"status": "ok", "results": results, "created": ok, "total": total}


def _ward_grid_hulls(boundary, mode, cell_size_m):
    """Grid a ward boundary into the ``hulls`` FeatureCollection create_plan expects.

    Coverage: tile the ward via ``generate_coverage_frame`` (one cell per work area).
    Sampling/other: fall back to the single-feature boundary (one work area)."""
    import json

    geom_geojson = json.loads(boundary.geometry.geojson)
    if mode == "coverage":
        from commcare_connect.microplans.coverage.frame import CoverageConfig, generate_coverage_frame

        cfg = CoverageConfig.from_payload({"cell_size_m": cell_size_m})
        result = generate_coverage_frame([{"geometry": geom_geojson}], cfg)
        return result.areas_geojson
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"boundary_id": boundary.boundary_id, "name": boundary.name},
                "geometry": geom_geojson,
            }
        ],
    }


@celery_app.task(bind=True)
def generate_group_samples_task(self, program_id, group_id, config, access_token):
    """Run the rooftop sampling engine across every member plan of a group, using
    one shared config so the only intended difference between arms is the area.

    Each member plan's stored area (``input_areas``) is resolved to geometry,
    tagged with the plan's study arm, sampled, and the plan regenerated to
    ``phase:sampled``. Reports per-plan progress; flips the group to status
    ``sampled`` at the end. Arm stays labs-side (it shapes colour/comparability,
    never the plan's work areas)."""
    import json as _json

    from commcare_connect.labs.admin_boundaries.models import AdminBoundary
    from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess
    from commcare_connect.microplans.core.plan import plan_sample_areas
    from commcare_connect.microplans.sampling.frame import FrameConfig, generate_frame

    da = ProgramPlanDataAccess(int(program_id), access_token=access_token)
    group = da.get_group(int(group_id))
    plans_by_id = {p.id: p for p in da.list_plans()}
    members = [plans_by_id[pid] for pid in group.plan_ids if pid in plans_by_id]
    total = len(members)
    fcfg = FrameConfig.from_payload(config or {})
    results: list[dict] = []
    ok = 0

    _geom_cache: dict[str, dict | None] = {}

    def resolve_boundary(bid):
        if bid not in _geom_cache:
            b = AdminBoundary.objects.filter(boundary_id=bid).first()
            _geom_cache[bid] = _json.loads(b.geometry.geojson) if (b and b.geometry) else None
        return _geom_cache[bid]

    for index, p in enumerate(members):
        arm = group.arm_for(p.id) or "intervention"
        input_areas = p.data.get("input_areas") or []
        areas = plan_sample_areas(input_areas, arm, resolve_boundary)
        if not areas:
            results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": "no area to sample"})
        else:
            try:
                res = generate_frame(areas, fcfg)
                da.regenerate_plan(
                    p.id, mode="sampling", pins=res.pins_geojson, hulls=res.hulls_geojson, input_areas=input_areas
                )
                ok += 1
                results.append({"plan_id": p.id, "name": p.name, "status": "ok"})
            except ValueError as e:
                results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": str(e)})
            except Exception:  # noqa: BLE001
                logger.exception("generate_group_samples: plan failed (group=%s plan=%s)", group_id, p.id)
                results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": "generate failed"})
        set_task_progress(self, f"{index + 1}/{total}", results=list(results), created=ok, total=total)

    if ok:
        try:
            da.update_group(int(group_id), status="sampled")
        except Exception:  # noqa: BLE001
            logger.exception("generate_group_samples: status flip failed (group=%s)", group_id)
    return {"status": "ok", "results": results, "created": ok, "total": total}
