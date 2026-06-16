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

Plan lifecycle (how a plan gets its work areas)
-----------------------------------------------
A plan's ``phase`` is derived from whether it has work areas yet (see
``PlanRecord.phase``): ``"boundary"`` (area defined in ``input_areas``, no work
areas) → ``"sampled"`` (work areas exist). The two modes reach ``sampled``
differently:

* **Coverage** is sampled *at creation* — gridding a ward into cells is cheap and
  deterministic, so ``create_plan`` materialises the grid in one step.
* **Sampling** is *two-step* — a plan is created **boundary-only**, then the PSU
  sample (PPS → primary/alternate) is drawn as a separate, config-driven pass. This
  split exists because sampling is tunable (PSU count, sources, confidence) and,
  for a two-arm study, every arm must be sampled with one shared config for
  comparability. The sampling pass runs per-plan in the editor ("Generate sample")
  or for a whole study at once via ``generate_group_samples_task`` ("Generate" on
  the study page) — both call ``generate_frame`` then ``regenerate_plan``.
"""

import json
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


def _rank_ward_matches(results: list[dict]) -> list[dict]:
    """Best-match-first: highest distribution overlap leads; rows with no overlap
    (insufficient data / errored) sink to the bottom."""

    def key(r):
        ov = r.get("overlap")
        return (1 if ov is not None else 0, ov if ov is not None else -1.0)

    return sorted(results, key=key, reverse=True)


# Distinct temporary fills for the surrounding-ward map overlay — chosen to avoid
# the intervention green / control blue / boundary purple already on the map, so
# each candidate's boundary reads as its own colour and matches its panel row.
_COMPARE_PALETTE = [
    "#f97316",  # orange
    "#14b8a6",  # teal
    "#ec4899",  # pink
    "#eab308",  # amber
    "#06b6d4",  # cyan
    "#f43f5e",  # rose
    "#92400e",  # brown
    "#c026d3",  # fuchsia
    "#0ea5e9",  # sky
    "#a16207",  # bronze
]


def _simplify_geojson(geom, tol: float = 0.0008):
    """Lighten a full-resolution boundary polygon for the map overlay (~80 m tol).
    The analysis still uses the full-res geometry; this only shrinks the payload the
    panel draws. Best-effort — returns the original on any failure."""
    try:
        from shapely.geometry import mapping, shape

        return mapping(shape(geom).simplify(tol, preserve_topology=True))
    except Exception:  # noqa: BLE001
        return geom


@celery_app.task(bind=True)
def compare_surrounding_wards_task(self, selected, config_payload):
    """Rank the wards adjacent to the selected (intervention) ward by how well their
    settlement-density DISTRIBUTION matches it — the macro control-finder.

    For the reference ward and each same-level neighbour, build the candidate-PSU
    density distribution (``ward_density_distribution`` — the cheap fetch+cluster
    path, no PPS draw) and score the overlap. Reports per-ward progress as a growing
    ranked ``results`` list so the panel fills in live (``CompareSurroundingStatusView``).
    Each ward is its own cold Overture fetch, which is why this runs on the worker."""
    from commcare_connect.microplans.core.admin_boundaries import adjacent_boundaries
    from commcare_connect.microplans.core.comparability import density_distribution_match
    from commcare_connect.microplans.sampling.frame import FrameConfig, ward_density_distribution

    config = FrameConfig.from_payload(config_payload or {})
    selected = selected or {}
    ref_id = selected.get("boundary_id") or (selected.get("ref") or {}).get("boundary_id")

    set_task_progress(self, "Finding neighbouring wards…")
    adj = adjacent_boundaries(ref_id) if ref_id else {"supported": False}
    if not adj.get("supported"):
        return {
            "status": "error",
            "detail": "Surrounding-ward comparison is available for Enriched Boundaries only.",
        }

    ref, candidates = adj["reference"], adj["candidates"]
    reference = {"boundary_id": ref["boundary_id"], "name": selected.get("name") or ref["name"]}
    total = len(candidates)
    if total == 0:
        return {
            "status": "ok",
            "reference": reference,
            "results": [],
            "total": 0,
            "detail": "No neighbouring wards at the same level were found.",
        }

    # Seed every candidate up front with a stable colour + simplified geometry, so the
    # map fills each neighbour's boundary immediately (outline while pending → solid
    # once scored) and the panel rows colour-match the map.
    results: list[dict] = []
    rows_by_id: dict[str, dict] = {}
    for index, cand in enumerate(candidates):
        row = {
            "boundary_id": cand["boundary_id"],
            "name": cand["name"],
            "population": cand.get("population"),
            "color": _COMPARE_PALETTE[index % len(_COMPARE_PALETTE)],
            "geometry": _simplify_geojson(cand["geometry"]),
            "status": "pending",
        }
        results.append(row)
        rows_by_id[cand["boundary_id"]] = row

    def emit(message):
        set_task_progress(self, message, results=_rank_ward_matches(results), total=total, reference=reference)

    # Reference distribution first — its own (possibly cold) fetch.
    emit("Analysing the selected ward…")
    try:
        ref_dist = ward_density_distribution(ref["geometry"], config)
    except Exception:  # noqa: BLE001
        logger.exception("compare_surrounding: reference ward failed (%s)", ref_id)
        return {"status": "error", "detail": "Could not analyse the selected ward."}
    # Self-match yields the reference ward's own quartiles/median on the same scale
    # the per-row numbers use, so the panel header and rows agree.
    ref_self = density_distribution_match(ref_dist["densities"], ref_dist["densities"])
    reference["median_density"] = ref_self.get("median_ref")
    reference["q"] = ref_self.get("q_ref")
    reference["n_clusters"] = ref_dist["n_clusters"]

    for index, cand in enumerate(candidates):
        emit(f"Analysing {cand['name']}… ({index + 1}/{total})")
        row = rows_by_id[cand["boundary_id"]]
        try:
            cand_dist = ward_density_distribution(cand["geometry"], config)
            row.pop("detail", None)
            row.update({"status": "ok", **density_distribution_match(ref_dist["densities"], cand_dist["densities"])})
        except ValueError as e:
            row.update({"status": "error", "detail": str(e)})
        except Exception:  # noqa: BLE001
            logger.exception("compare_surrounding: candidate failed (%s)", cand["boundary_id"])
            row.update({"status": "error", "detail": "analysis failed"})

    return {
        "status": "ok",
        "reference": reference,
        "results": _rank_ward_matches(results),
        "total": total,
        "truncated": adj.get("truncated", False),
    }


@celery_app.task(bind=True)
def fetch_footprints_task(self, areas):
    """Building footprints (polygons, centroid-Point fallback) inside the drawn area(s)."""
    from shapely.ops import unary_union

    from commcare_connect.microplans.core.area_input import resolve_area
    from commcare_connect.microplans.core.footprints import fetch_buildings

    set_task_progress(self, _FETCHING)
    try:
        geom = unary_union([resolve_area(a) for a in areas])
        # with_geom=True surfaces the real building polygon (`geom_json`); the
        # overlay prefers it and only falls back to a centroid Point when a row
        # has no stored geometry (matches the saved-plan footprints path).
        df = fetch_buildings(geom, min_confidence=None, with_geom=True)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    has_geom = "geom_json" in df.columns
    features = []
    for _, row in df.iterrows():
        poly = row["geom_json"] if has_geom else None
        geometry = poly if poly else {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]}
        features.append({"type": "Feature", "geometry": geometry, "properties": {}})
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
                stats=params.get("stats"),
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
    """Create one draft plan per confirmed admin boundary (one ward each), on the worker.

    Plans get their work areas one of two ways (see ``PlanRecord.phase`` and the
    module docstring's "Plan lifecycle"):

    * **Coverage** is sampled at creation — each ward is gridded into work areas via
      ``generate_coverage_frame`` (the Overture fetch + clustering, which is why this
      runs on the worker, not the web tier).
    * **Sampling** is two-step — the plan is created *boundary-only*
      (``phase="boundary"``: the ward lives in ``input_areas``, no work areas yet);
      the PSU sample is drawn later as its own config-driven pass (study "Generate" →
      ``generate_group_samples_task``, or the single-plan editor).

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
            plan = create_boundary_plan(
                da,
                mode=mode,
                name=display_name,
                geometry=json.loads(boundary.geometry.geojson),
                boundary_id=boundary_id,
                population=int(boundary.population) if boundary.population is not None else None,
                cell_size_m=cell_size_m,
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


def _initial_plan_hulls(geometry, mode, cell_size_m):
    """The ``hulls`` FeatureCollection ``create_plan`` materialises into work areas
    at creation, by mode. ``geometry`` is the ward's GeoJSON geometry.

    * **Coverage** is sampled at creation: tile the ward into a grid via
      ``generate_coverage_frame`` (one work area per cell).
    * **Sampling** is two-step: the plan is created *boundary-only* (no work areas;
      ``phase="boundary"`` — the ward lives in ``input_areas``), and the PSU sample
      is drawn later by ``generate_group_samples_task`` / the single-plan editor.
      So there are no hulls yet — and ``materialize_work_areas`` reads ``pins``, not
      ``hulls``, for sampling anyway — hence an empty collection."""
    if mode == "coverage":
        from commcare_connect.microplans.coverage.frame import CoverageConfig, generate_coverage_frame

        cfg = CoverageConfig.from_payload({"cell_size_m": cell_size_m})
        return generate_coverage_frame([{"geometry": geometry}], cfg).areas_geojson
    return {"type": "FeatureCollection", "features": []}


def create_boundary_plan(
    da,
    *,
    mode,
    name,
    geometry,
    region=None,
    boundary_id="",
    population=None,
    cell_size_m=100.0,
    lga="",
    state="",
    grouping=None,
):
    """Create one boundary plan from an admin boundary — the shared core of BOTH
    bulk-create-from-boundaries paths (the sync study "add wards from map" view and
    the async bulk-create-page task), so they build plans identically.

    Coverage is gridded into work areas at creation; sampling starts boundary-only
    and is sampled later (see the module "Plan lifecycle"). Stores ONE consistent
    ``input_areas`` entry: inline ``geometry`` (resilience + the footprints overlay),
    the ``boundary_id`` (so the study "Generate" pass can re-resolve the ward), and
    the boundary ``population`` (for plan KPIs — looked up from ``boundary_id`` when
    not supplied)."""
    if population is None and boundary_id:
        from commcare_connect.labs.admin_boundaries.models import AdminBoundary

        b = AdminBoundary.objects.filter(boundary_id=boundary_id).first()
        population = int(b.population) if (b is not None and b.population is not None) else None
    input_area = {"kind": "admin_boundary", "geometry": geometry}
    if boundary_id:
        input_area["boundary_id"] = boundary_id
    if name:
        input_area["name"] = name
    if population is not None:
        input_area["population"] = int(population)
    return da.create_plan(
        region=region if region is not None else name,
        name=name,
        mode=mode,
        pins={"type": "FeatureCollection", "features": []},
        hulls=_initial_plan_hulls(geometry, mode, cell_size_m),
        input_areas=[input_area],
        lga=lga,
        state=state,
        grouping=grouping,
    )


def sample_group_plans(da, group, fcfg, *, progress=None):
    """Sample every member plan of a group with ONE shared ``FrameConfig``, in-process.

    The shared core of the study "Generate" pass: resolve each plan's stored area
    (``input_areas``, tagged with the plan's study arm), draw the PSU sample, and
    regenerate the plan to ``phase:sampled``. The only intended difference between
    arms is the area — arm stays labs-side (colour/comparability, never the work
    areas). Returns ``{"results": [...], "created": <n_ok>, "total": <n_members>}``.

    ``progress(done, total, results, ok)`` is called after each plan when supplied.
    Shared by the Celery ``generate_group_samples_task`` and the study seeder
    (``microplans.study_seed``), so both sample studies identically."""
    import json as _json

    from commcare_connect.labs.admin_boundaries.models import AdminBoundary
    from commcare_connect.microplans.core.plan import plan_sample_areas
    from commcare_connect.microplans.sampling.frame import generate_frame

    plans_by_id = {p.id: p for p in da.list_plans()}
    members = [plans_by_id[pid] for pid in group.plan_ids if pid in plans_by_id]
    total = len(members)
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
                    p.id,
                    mode="sampling",
                    pins=res.pins_geojson,
                    hulls=res.hulls_geojson,
                    input_areas=input_areas,
                    stats=res.stats,
                )
                ok += 1
                results.append({"plan_id": p.id, "name": p.name, "status": "ok"})
            except ValueError as e:
                results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": str(e)})
            except Exception:  # noqa: BLE001
                logger.exception("sample_group_plans: plan failed (group=%s plan=%s)", group.id, p.id)
                results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": "generate failed"})
        if progress is not None:
            progress(index + 1, total, results, ok)

    return {"results": results, "created": ok, "total": total}


def sample_plans(da, plans, fcfg, *, progress=None):
    """Sample a list of plans in-process with ONE shared ``FrameConfig``, reading each
    plan's study arm from its ``input_areas`` — a two-arm SINGLE plan tags each area
    with its own arm and ``plan_sample_areas`` honours that. The single-plan analogue
    of :func:`sample_group_plans` (a study round is now ONE two-arm plan, not a group
    of per-ward plans). Same ``{"results", "created", "total"}`` return shape."""
    import json as _json

    from commcare_connect.labs.admin_boundaries.models import AdminBoundary
    from commcare_connect.microplans.core.plan import plan_sample_areas
    from commcare_connect.microplans.sampling.frame import generate_frame

    total = len(plans)
    results: list[dict] = []
    ok = 0
    _geom_cache: dict[str, dict | None] = {}

    def resolve_boundary(bid):
        if bid not in _geom_cache:
            b = AdminBoundary.objects.filter(boundary_id=bid).first()
            _geom_cache[bid] = _json.loads(b.geometry.geojson) if (b and b.geometry) else None
        return _geom_cache[bid]

    for index, p in enumerate(plans):
        input_areas = p.data.get("input_areas") or []
        # per-area arm wins; "intervention" is only the fallback for an untagged area.
        areas = plan_sample_areas(input_areas, "intervention", resolve_boundary)
        if not areas:
            results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": "no area to sample"})
        else:
            try:
                res = generate_frame(areas, fcfg)
                da.regenerate_plan(
                    p.id,
                    mode="sampling",
                    pins=res.pins_geojson,
                    hulls=res.hulls_geojson,
                    input_areas=input_areas,
                    stats=res.stats,
                )
                ok += 1
                results.append({"plan_id": p.id, "name": p.name, "status": "ok"})
            except ValueError as e:
                results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": str(e)})
            except Exception:  # noqa: BLE001
                logger.exception("sample_plans: plan failed (plan=%s)", p.id)
                results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": "generate failed"})
        if progress is not None:
            progress(index + 1, total, results, ok)

    return {"results": results, "created": ok, "total": total}


@celery_app.task(bind=True)
def generate_group_samples_task(self, program_id, group_id, config, access_token):
    """Celery wrapper around :func:`sample_group_plans`: sample a study group's member
    plans with one shared config, reporting per-plan progress and flipping the group
    to status ``sampled`` at the end. Returns the same ``{"status": "ok", ...}``
    envelope the preview status view expects."""
    from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess
    from commcare_connect.microplans.sampling.frame import FrameConfig

    da = ProgramPlanDataAccess(int(program_id), access_token=access_token)
    group = da.get_group(int(group_id))
    fcfg = FrameConfig.from_payload(config or {})

    def progress(done, total, results, ok):
        set_task_progress(self, f"{done}/{total}", results=list(results), created=ok, total=total)

    out = sample_group_plans(da, group, fcfg, progress=progress)
    if out["created"]:
        try:
            da.update_group(int(group_id), status="sampled")
        except Exception:  # noqa: BLE001
            logger.exception("generate_group_samples: status flip failed (group=%s)", group_id)
    return {"status": "ok", **out}
