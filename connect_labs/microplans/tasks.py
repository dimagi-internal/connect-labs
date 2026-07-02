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

from config import celery_app
from connect_labs.utils.celery import set_task_progress

logger = logging.getLogger(__name__)

_FETCHING = "Fetching building footprints…"


@celery_app.task(bind=True)
def generate_frame_task(self, areas, config_payload):
    """Sampling-mode preview: footprints → PPS-sampled pins + cluster hulls."""
    from connect_labs.microplans.sampling.frame import FrameConfig, generate_frame

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
    from connect_labs.microplans.coverage.frame import CoverageConfig, generate_coverage_frame

    set_task_progress(self, _FETCHING)
    config = CoverageConfig.from_payload(config_payload or {})
    try:
        result = generate_coverage_frame(areas, config)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "ok", "areas": result.areas_geojson, "stats": result.stats}


def _rank_ward_matches(results: list[dict]) -> list[dict]:
    """Best-match-first ranking for the control finder.

    Headline key is the **best-achievable matched balance** — the cross-arm density
    SMD the matched selector would realise after restricting to common support
    (``matched_smd``, lower is better) — so candidates rank by the balance you'd
    actually get, not raw distribution overlap. Distribution ``overlap`` is the
    secondary tiebreaker (kept as context). Wards that share no density support
    (``incomparable``) or have no score (insufficient data / errored) sink to the
    bottom. Legacy rows with no ``matched_smd`` fall back to overlap-only ordering."""

    def key(r):
        incomparable = bool(r.get("incomparable"))
        msmd = r.get("matched_smd")
        ov = r.get("overlap")
        has_match = msmd is not None and not incomparable
        # Tier 1: scorable + comparable. Tier 0: everything else (sinks).
        tier = 1 if (has_match or (ov is not None and not incomparable)) else 0
        # Within the scorable tier, smaller matched SMD ranks first; -msmd so that the
        # reverse=True sort puts the smallest SMD on top. Overlap breaks ties.
        neg_smd = -float(msmd) if msmd is not None else -999.0
        ov_key = ov if ov is not None else -1.0
        return (tier, neg_smd, ov_key)

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
    from connect_labs.microplans.core.admin_boundaries import adjacent_boundaries
    from connect_labs.microplans.core.comparability import density_bin_edges, density_distribution_match
    from connect_labs.microplans.sampling.frame import FrameConfig, ward_density_distribution

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

    # Announce the scope up front — the seeded pending rows + this message let the UI
    # show "Processing N surrounding areas" (and draw all N outlines) before the work.
    emit(f"Processing {total} surrounding area{'' if total == 1 else 's'}…")

    # Reference distribution first — its own (possibly cold) fetch.
    emit("Analysing the selected ward…")
    try:
        ref_dist = ward_density_distribution(ref["geometry"], config)
    except Exception:  # noqa: BLE001
        logger.exception("compare_surrounding: reference ward failed (%s)", ref_id)
        return {"status": "error", "detail": "Could not analyse the selected ward."}
    # Fixed bins anchored on the intervention ward, so every candidate's histogram
    # shares one axis (identical grey bars) and the overlaps are comparable.
    edges = density_bin_edges(ref_dist["densities"])
    # Self-match yields the reference ward's own quartiles/median on the same scale
    # the per-row numbers use, so the panel header and rows agree.
    ref_self = density_distribution_match(ref_dist["densities"], ref_dist["densities"], edges=edges)
    reference["median_density"] = ref_self.get("median_ref")
    reference["q"] = ref_self.get("q_ref")
    reference["spark"] = ref_self.get("spark")
    reference["n_clusters"] = ref_dist["n_clusters"]
    reference["buildings"] = ref_dist.get("n_buildings")
    reference["population"] = ref.get("population")

    for index, cand in enumerate(candidates):
        emit(f"Analysing {cand['name']}… ({index + 1}/{total})")
        row = rows_by_id[cand["boundary_id"]]
        try:
            cand_dist = ward_density_distribution(cand["geometry"], config)
            row.pop("detail", None)
            match = density_distribution_match(ref_dist["densities"], cand_dist["densities"], edges=edges)
            row.update(
                {
                    "status": "ok",
                    "buildings": cand_dist.get("n_buildings"),
                    "n_clusters": cand_dist.get("n_clusters"),
                    **match,
                }
            )
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
    import pandas as pd

    from connect_labs.microplans.core.area_input import resolve_area
    from connect_labs.microplans.core.footprints import fetch_buildings

    set_task_progress(self, _FETCHING)
    try:
        # Fetch each area on its OWN bounding box and concat. Unioning scattered
        # wards (e.g. GRID3 wards in different LGAs) yields one giant bbox that
        # trips the area-size guard — mirror the per-area coverage generator.
        # with_geom=True surfaces the real building polygon (`geom_json`); the
        # overlay prefers it and only falls back to a centroid Point when a row
        # has no stored geometry (matches the saved-plan footprints path).
        frames = [fetch_buildings(resolve_area(a), min_confidence=None, with_geom=True) for a in areas]
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["lon", "lat"])
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
def preview_area_stats_task(self, areas, min_confidence=None):
    """Per-area building counts by provider, for the setup planning table.

    For each area (in the same order it was posted) fetch its footprints on its
    OWN bbox (cached after the first pull) and return the per-provider counts.
    The client sums the providers it currently has ticked, so toggling a
    building provider on/off updates the table with no re-fetch. Confidence is
    applied here (it's a Google-only threshold), so changing it re-fetches."""
    from connect_labs.microplans.core.area_input import resolve_area
    from connect_labs.microplans.core.footprints import fetch_buildings, source_counts

    set_task_progress(self, _FETCHING)
    stats = []
    try:
        for i, a in enumerate(areas):
            # sources=None → every provider; the client filters by its checkboxes.
            df = fetch_buildings(resolve_area(a), min_confidence=min_confidence, with_geom=False)
            counts = source_counts(df)
            stats.append({"index": i, "source_counts": counts, "total": int(sum(counts.values()))})
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "ok", "stats": stats}


@celery_app.task(bind=True)
def apply_plan_mutation_task(self, op, program_id, plan_id, params, actor, access_token):
    """Run a heavy plan mutation (regroup / reassign / regenerate) off the web tier.

    These re-run BFS grouping / minimax assignment / re-materialization over up to
    MAX_WORK_AREAS cells — synchronously in a request they pin a gthread (same
    starvation class as the previews #352 offloaded). Returns ``plan_to_json`` on
    success, ``{status: conflict}`` on a stale-revision clash (#355) so the client
    can warn+reload, or ``{status: error}`` for an actionable failure."""
    from connect_labs.microplans import serialization
    from connect_labs.microplans.core.data_access import ProgramPlanDataAccess, StalePlanError

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
                area_targets=params.get("area_targets"),
            )
        else:
            return {"status": "error", "detail": f"unknown op {op!r}"}
    except StalePlanError as e:
        return {"status": "conflict", "detail": str(e)}
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return serialization.plan_to_json(plan)


@celery_app.task(bind=True)
def bulk_create_plans_task(
    self,
    program_id,
    plans_input,
    mode,
    grouping,
    cell_size_m,
    access_token,
    group_id=None,
    coverage_config=None,
    run_id=None,
    actor=None,
):
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
    from connect_labs.labs.admin_boundaries.models import AdminBoundary
    from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

    da = ProgramPlanDataAccess(int(program_id), access_token=access_token)
    total = len(plans_input)
    results: list[dict] = []
    ok = 0

    # Batch provenance: one run_id ties every plan in this bulk create together, so a
    # 40-ward batch is groupable + a parameter-tuning run is reproducible. Caller may
    # pass one in (to echo it back before the task finishes); else mint it here.
    import uuid
    from datetime import datetime, timezone

    run_id = run_id or f"bulk-{uuid.uuid4().hex[:12]}"
    run_meta = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor or "",
        "mode": mode,
        "cell_size_m": cell_size_m,
        "coverage_config": dict(coverage_config or {}),
        "grouping": dict(grouping or {}),
        "n_wards": total,
    }

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
                coverage_config=coverage_config,
                run_meta={**run_meta, "index": index},
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

    return {"status": "ok", "run_id": run_id, "results": results, "created": ok, "total": total}


def _coverage_config_payload(cell_size_m, coverage_config, population):
    """Merge the caller's coverage-parameter dict with the per-plan ``cell_size_m``
    and boundary ``population`` into ONE payload for ``CoverageConfig.from_payload``.

    The explicit ``cell_size_m``/``population`` args win over any same-named keys in
    ``coverage_config`` (they're the per-plan values the bulk-create path threads),
    but every *other* coverage knob (min_confidence, sources, area/cell exclusion
    filters, …) flows straight through — so adding a field to ``CoverageConfig`` needs
    no change here. ``coverage_config`` is the single source of truth for the surface."""
    payload = dict(coverage_config or {})
    payload["cell_size_m"] = cell_size_m
    if population is not None:
        payload.setdefault("population", population)
    return payload


def _coverage_frame(geometry, cell_size_m, coverage_config=None, population=None):
    """Grid one ward's geometry into coverage work-area cells, returning the FULL
    ``CoverageFrameResult`` (``areas_geojson`` + ``stats``). The shared coverage-
    generation core so both the ``hulls`` (work areas) and the frame ``stats`` the
    review UI shows come from ONE fetch/grid pass with the same config."""
    from connect_labs.microplans.coverage.frame import CoverageConfig, generate_coverage_frame

    cfg = CoverageConfig.from_payload(_coverage_config_payload(cell_size_m, coverage_config, population))
    return generate_coverage_frame([{"geometry": geometry}], cfg)


def _initial_plan_hulls(geometry, mode, cell_size_m, coverage_config=None):
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
        return _coverage_frame(geometry, cell_size_m, coverage_config).areas_geojson
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
    coverage_config=None,
    run_meta=None,
):
    """Create one boundary plan from an admin boundary — the shared core of BOTH
    bulk-create-from-boundaries paths (the sync study "add wards from map" view and
    the async bulk-create-page task), so they build plans identically.

    Coverage is gridded into work areas at creation; sampling starts boundary-only
    and is sampled later (see the module "Plan lifecycle"). Stores ONE consistent
    ``input_areas`` entry: inline ``geometry`` (resilience + the footprints overlay),
    the ``boundary_id`` (so the study "Generate" pass can re-resolve the ward), and
    the boundary ``population`` (for plan KPIs — looked up from ``boundary_id`` when
    not supplied).

    ``coverage_config`` is the full coverage parameter surface (everything on
    ``CoverageConfig`` beyond ``cell_size_m``: confidence gate, source filter, area +
    cell exclusion filters, population weighting). For coverage plans it's gridded
    into work areas AND persisted alongside the resulting frame stats, so the plan
    records exactly what params produced it. ``run_meta`` is stamped through for
    batch provenance (run_id/actor/…)."""
    if population is None and boundary_id:
        from connect_labs.labs.admin_boundaries.models import AdminBoundary

        b = AdminBoundary.objects.filter(boundary_id=boundary_id).first()
        population = int(b.population) if (b is not None and b.population is not None) else None
    input_area = {"kind": "admin_boundary", "geometry": geometry}
    if boundary_id:
        input_area["boundary_id"] = boundary_id
    if name:
        input_area["name"] = name
    if population is not None:
        input_area["population"] = int(population)

    # Coverage grids at creation — generate ONCE so the work-area hulls and the frame
    # stats (the numbers the coverage preview shows) come from the same fetch/config,
    # and persist both the stats and the config used. Sampling starts boundary-only.
    if mode == "coverage":
        result = _coverage_frame(geometry, cell_size_m, coverage_config, population)
        hulls = result.areas_geojson
        cov_stats = result.stats
        cov_config = _coverage_config_payload(cell_size_m, coverage_config, population)
    else:
        hulls = {"type": "FeatureCollection", "features": []}
        cov_stats = None
        cov_config = None

    return da.create_plan(
        region=region if region is not None else name,
        name=name,
        mode=mode,
        pins={"type": "FeatureCollection", "features": []},
        hulls=hulls,
        input_areas=[input_area],
        lga=lga,
        state=state,
        grouping=grouping,
        coverage_config=cov_config,
        coverage_stats=cov_stats,
        run_meta=run_meta,
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

    from connect_labs.labs.admin_boundaries.models import AdminBoundary
    from connect_labs.microplans.core.plan import plan_sample_areas
    from connect_labs.microplans.sampling.frame import generate_frame

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

    # Resolve every member plan's areas, tagged with its study arm. When the group is
    # a clean two-arm split (each plan is its own arm, exactly two distinct arms), the
    # arms are sampled JOINTLY in ONE generate_frame call so the matched density-
    # stratified selector balances settlement density across arms by construction —
    # the cross-arm coordination point. Otherwise (single arm, or >1 plan per arm) we
    # fall back to per-plan independent draws.
    plan_arm = {p.id: (group.arm_for(p.id) or "intervention") for p in members}
    plan_input_areas = {p.id: (p.data.get("input_areas") or []) for p in members}
    plan_areas = {p.id: plan_sample_areas(plan_input_areas[p.id], plan_arm[p.id], resolve_boundary) for p in members}
    distinct_arms = {plan_arm[p.id] for p in members if plan_areas[p.id]}
    one_plan_per_arm = len({plan_arm[p.id] for p in members if plan_areas[p.id]}) == len(
        [p for p in members if plan_areas[p.id]]
    )
    coordinated = len(distinct_arms) >= 2 and one_plan_per_arm

    def _write_plan(p, res, status_extra=None):
        nonlocal ok
        da.regenerate_plan(
            p.id,
            mode="sampling",
            pins=res.pins_geojson,
            hulls=res.hulls_geojson,
            input_areas=plan_input_areas[p.id],
            stats=res.stats,
        )
        ok += 1
        results.append({"plan_id": p.id, "name": p.name, "status": "ok", **(status_extra or {})})

    if coordinated:
        joint_areas: list[dict] = []
        for p in members:
            joint_areas.extend(plan_areas[p.id])
        try:
            res = generate_frame(joint_areas, fcfg)
            arm_to_res = _split_frame_result_by_arm(res)
            for index, p in enumerate(members):
                if not plan_areas[p.id]:
                    results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": "no area to sample"})
                else:
                    _write_plan(p, arm_to_res[plan_arm[p.id]])
                if progress is not None:
                    progress(index + 1, total, results, ok)
            return {"results": results, "created": ok, "total": total}
        except Exception:  # noqa: BLE001
            logger.exception("sample_group_plans: joint sample failed (group=%s); falling back per-plan", group.id)
            results.clear()
            ok = 0

    for index, p in enumerate(members):
        areas = plan_areas[p.id]
        if not areas:
            results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": "no area to sample"})
        else:
            try:
                res = generate_frame(areas, fcfg)
                _write_plan(p, res)
            except ValueError as e:
                results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": str(e)})
            except Exception:  # noqa: BLE001
                logger.exception("sample_group_plans: plan failed (group=%s plan=%s)", group.id, p.id)
                results.append({"plan_id": p.id, "name": p.name, "status": "error", "detail": "generate failed"})
        if progress is not None:
            progress(index + 1, total, results, ok)

    return {"results": results, "created": ok, "total": total}


def _split_frame_result_by_arm(res):
    """Partition a multi-arm ``FrameResult`` back into one single-arm ``FrameResult``
    per arm, so each member plan of a jointly-sampled study group is written with only
    its own arm's pins/hulls/stats. Used by ``sample_group_plans``' coordinated path."""
    from connect_labs.microplans.sampling.frame import FrameResult

    out: dict[str, object] = {}
    arms = {f["properties"]["arm"] for f in res.pins_geojson.get("features", [])}
    arms |= {f["properties"]["arm"] for f in res.hulls_geojson.get("features", [])}
    arms |= {s.get("arm", "intervention") for s in res.stats}
    for arm in arms:
        pins = [f for f in res.pins_geojson.get("features", []) if f["properties"].get("arm") == arm]
        hulls = [f for f in res.hulls_geojson.get("features", []) if f["properties"].get("arm") == arm]
        stats = [s for s in res.stats if s.get("arm", "intervention") == arm]
        out[arm] = FrameResult(
            pins_geojson={"type": "FeatureCollection", "features": pins},
            hulls_geojson={"type": "FeatureCollection", "features": hulls},
            stats=stats,
        )
    return out


def sample_plans(da, plans, fcfg, *, progress=None):
    """Sample a list of plans in-process with ONE shared ``FrameConfig``, reading each
    plan's study arm from its ``input_areas`` — a two-arm SINGLE plan tags each area
    with its own arm and ``plan_sample_areas`` honours that. The single-plan analogue
    of :func:`sample_group_plans` (a study round is now ONE two-arm plan, not a group
    of per-ward plans). Same ``{"results", "created", "total"}`` return shape."""
    import json as _json

    from connect_labs.labs.admin_boundaries.models import AdminBoundary
    from connect_labs.microplans.core.plan import plan_sample_areas
    from connect_labs.microplans.sampling.frame import generate_frame

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
    from connect_labs.microplans.core.data_access import ProgramPlanDataAccess
    from connect_labs.microplans.sampling.frame import FrameConfig

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
