"""On-box EMOD worker: run PMC schedule scenarios for one setting, from a cached burn-in.

    python run_scenarios.py --request s3://<bucket>/requests/<hash>.json --out s3://<bucket>/results/<hash>.json
    python run_scenarios.py --self-test

Request:  {"setting": {...}, "schedules": [{"code", "rounds": [[offset, interval, reps, age_min_y, age_max_y,
          coverage]], "drug": "SP" | "SPAQ" (optional, default "SP")}], "seeds": [0, 1, 2],
          "intervention_years": 2}
Result:   {"hash", "burnin": {"cached", "seconds"}, "runs": [{"code", "drug", "seed", "cases_3_24m", "kids_3_24m",
          "cases_u5", "kids_u5", "pfpr_2_5y", "doses"}], "seconds"}

Calibrate mode fits the setting's larval capacity to a measured PfPR 2-5y:
Request:  {"mode": "calibrate", "setting": {... without larval_capacity}, "target_pfpr": 0.27,
          "tolerance": 0.03 (optional)}
Result:   {"mode", "hash", "target_pfpr", "tolerance", "larval_capacity", "pfpr_2_5y", "fit_error" (pfpr - target),
          "fit": "ok" | "unreachable" | "loose", "iterations" (rounds run), "extended", "burnin_hash",
          "pfpr_basis": "Oct-Dec mean, 2-5y", "pfpr_2_5y_annual",
          "candidates": [{"round", "larval_capacity", "pfpr_2_5y", "pfpr_2_5y_annual"}],
          "rounds": [{"round", "n", "seconds"}], "seconds"}
          Round 1 burns in 8 log-spaced capacities (1e6-1e9) side by side; if none reaches the target, an extension
          round tries 4 from 1e9 up to 1e10; then 4 around the interpolated crossing, up to 3 times while
          unfit. pfpr_2_5y is the burn-in's last-year Oct-Dec mean (the DHS/MIS survey window,
          CALIBRATE_SURVEY_DOY); pfpr_2_5y_annual is that year's mean, for reference. The chosen capacity's
          burn-in is cached (and published) under its setting_hash, so a run request with that larval_capacity
          starts warm. Pass the returned larval_capacity on verbatim.

A setting is burned in once (2 years, no PMC) and its population serialized. Each (schedule, seed) run then
starts from that state and simulates only the intervention years. The burn-in is cached on local disk and,
for s3:// requests, at s3://<bucket>/burnin/<setting_hash>.dtk.

S3 is a thin layer (`main`) over `run_request`, which only knows local paths.

Environment:
  EMOD_TUTORIALS_DIR   directory holding the emodpy-malaria tutorials' manifest.py (default
                       /opt/emod/emodpy-malaria/tutorials)
  EMOD_CACHE_DIR       burn-in + job directory (default /var/cache/emod)
  EMOD_RUN_TIMEOUT_S   longest wait for one experiment, seconds (default 1800)
  EMOD_REQUEST_TIMEOUT_S  deadline for the whole request -- burn-in plus pick-ups -- in seconds (default
                       2100). Each experiment waits at most what is left of it, so a cold request cannot
                       take burn-in 1800 + pick-ups 1800; the caller's SSM limit (2400) sits above it.
  EMOD_ACTIVITY_FILE   liveness marker touched while working (default /var/run/emod/last-activity)
"""

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
from functools import partial

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_TUTORIALS_DIR = "/opt/emod/emodpy-malaria/tutorials"
DEFAULT_CACHE_DIR = "/var/cache/emod"
DEFAULT_ACTIVITY_FILE = "/var/run/emod/last-activity"
DEFAULT_RUN_TIMEOUT_S = 1800
DEFAULT_REQUEST_TIMEOUT_S = 2100
BURNIN_DAYS = 730
BURNIN_FILE = "state-00730.dtk"
# Bump when the model code changes in a way that invalidates stored burn-ins.
BURNIN_VERSION = "1"
MODES = ("run", "calibrate")
DRUGS = ("SP", "SPAQ")  # keep equal to pmc_sweep.DRUGS (a test checks); a pick-up choice, not in setting_hash
# Calibration: round 1 burns in 8 log-spaced larval capacities over 1e6-1e9; round 2 burns in 4 more around the
# interpolated crossing. The fit is "ok" within CALIBRATE_TOLERANCE of the target PfPR 2-5y (absolute).
CALIBRATE_LOG10_RANGE = (6.0, 9.0)
CALIBRATE_GRID_N = 8
CALIBRATE_REFINE_STEPS = (-0.45, -0.15, 0.15, 0.45)  # refine offsets, in units of the bracketing width (log10)
CALIBRATE_TOLERANCE = 0.03
# PfPR rises steeply with larval capacity (Delta: 0.10 -> 0.36 over one refine step), so one refine round can
# straddle the target. Refine again around the new, tighter crossing while nothing is within tolerance.
CALIBRATE_MAX_REFINE_ROUNDS = 3
# When round 1's highest PfPR is still below target - tolerance, one extension round of 4 log-spaced capacities
# above the grid (up to 1e10; a 1e11 burn-in takes hours) runs before the target is declared unreachable.
CALIBRATE_EXTENSION_LOG10 = (9.0, 10.0)
CALIBRATE_EXTENSION_N = 4
# The model PfPR 2-5y compared with the target: the mean over days of year 274-365 (1 Oct - 31 Dec, 1-based) of
# the burn-in's last year. DHS/MIS 2021 fieldwork ran Oct-Dec, near the end of the high season; in a seasonal
# state the annual mean sits well below what that survey measured.
CALIBRATE_SURVEY_DOY = (274, 365)
PFPR_BASIS = "Oct-Dec mean, 2-5y"


def activity_path():
    return os.environ.get("EMOD_ACTIVITY_FILE", DEFAULT_ACTIVITY_FILE)


def touch_activity():
    """Mark the box as busy; the idle watchdog reads this file's mtime."""
    path = activity_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pathlib.Path(path).touch()
    except OSError as exc:
        print(
            f"WARNING: cannot touch activity marker {path}: {exc}; the idle watchdog may stop this box",
            file=sys.stderr,
        )


class Heartbeat:
    """Calls touch_activity() now and then every `interval_s` until closed."""

    def __init__(self, interval_s):
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while True:
            touch_activity()  # looked up on the module each time, so it can be replaced
            if self._stop.wait(self._interval):
                return

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        touch_activity()


@contextlib.contextmanager
def workdir(cache_dir):
    """A fresh cwd under cache_dir for one request, removed afterwards.

    emodpy drops demographics_<timestamp>/ dirs in the cwd; this keeps them out of wherever we were started, and
    a directory of its own per request means two requests on one box never remove each other's cwd.
    """
    path = tempfile.mkdtemp(prefix="work-", dir=cache_dir)
    old = os.getcwd()
    os.chdir(path)
    try:
        yield pathlib.Path(path)
    finally:
        os.chdir(old)
        shutil.rmtree(path, ignore_errors=True)


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


# How setting_hash spells each numeric field, so equal values hash equal whatever JSON type they arrived as
# (6e7 vs 60000000, 30 vs 30.0). The spellings are the ones the deployed default setting already had, so its hash
# (and burn-in) is unchanged: larval/habitat values/coverages as floats, pop and integral day numbers as ints.
FLOAT_FIELDS = ("larval_capacity", "case_mgmt", "net_coverage")


def _canonical_number(v, kind):
    if not _num(v):
        return v
    if kind == "float":
        return float(v)
    return int(v) if float(v).is_integer() else float(v)


def canonical_setting(setting):
    out = {}
    for k, v in setting.items():
        if k in FLOAT_FIELDS:
            out[k] = _canonical_number(v, "float")
        elif k == "habitat_values" and isinstance(v, list):
            out[k] = [_canonical_number(x, "float") for x in v]
        elif k == "habitat_times" and isinstance(v, list):
            out[k] = [_canonical_number(x, "int") for x in v]
        elif k == "pop":
            out[k] = _canonical_number(v, "int")
        else:
            out[k] = v
    return out


def setting_hash(setting):
    """Identity of a burn-in: everything that shapes the serialized population (not the display name)."""
    keyed = {k: v for k, v in canonical_setting(setting).items() if k != "name"}
    return canonical_hash({"setting": keyed, "version": BURNIN_VERSION, "burnin_days": BURNIN_DAYS})


def request_hash(req):
    return canonical_hash(req)


def load_pmc_sweep():
    tutorials = os.environ.get("EMOD_TUTORIALS_DIR", DEFAULT_TUTORIALS_DIR)
    for p in (tutorials, str(HERE), str(HERE.parent)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import manifest
    import pmc_sweep

    return manifest, pmc_sweep


def ensure_binary(manifest):
    exe_dir = pathlib.Path(manifest.eradication_path).parent
    if not (exe_dir / "Eradication").exists() or not (exe_dir / "schema.json").exists():
        import emod_malaria.bootstrap as dtk

        dtk.setup(exe_dir)


# The EMOD runtime image the grid and the deployed worker ran on 2026-10-08, pinned by
# digest so a rebuilt box (or a re-pull of :latest) runs the same model. EMOD_IMAGE overrides.
EMOD_IMAGE_DIGEST = "sha256:91933ca254ac9c0dd49deb6ba9b48c59b312c2baae2970e547b6a6f5b896fbbd"
EMOD_IMAGE = f"ghcr.io/emod-hub/emod-ubuntu-runtime@{EMOD_IMAGE_DIGEST}"


def make_platform(manifest, job_dir):
    from idmtools.core.platform_factory import Platform

    return Platform(
        "Container",
        job_directory=str(job_dir),
        docker_image=os.environ.get("EMOD_IMAGE", EMOD_IMAGE),
        sym_link=False,
        max_job=os.cpu_count() or 1,
    )


def make_task(manifest, sweep, config_builder, campaign_builder, demographics_builder, report_builder):
    from emodpy.emod_task import EMODTask

    return EMODTask.from_defaults(
        eradication_path=manifest.eradication_path,
        schema_path=manifest.schema_path,
        config_builder=config_builder,
        campaign_builder=campaign_builder,
        demographics_builder=demographics_builder,
        report_builder=report_builder,
    )


def request_deadline(t0):
    """Monotonic time by which the whole request (burn-in plus pick-ups) must be done."""
    return t0 + float(os.environ.get("EMOD_REQUEST_TIMEOUT_S", DEFAULT_REQUEST_TIMEOUT_S))


def experiment_timeout(deadline=None):
    """Seconds one experiment may wait: EMOD_RUN_TIMEOUT_S, cut to what is left before `deadline`.

    Raises TimeoutError when the request deadline has already passed (nothing would be worth starting).
    """
    timeout = float(os.environ.get("EMOD_RUN_TIMEOUT_S", DEFAULT_RUN_TIMEOUT_S))
    if deadline is not None:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("request deadline (EMOD_REQUEST_TIMEOUT_S) passed before the next experiment")
        timeout = min(timeout, left)
    return int(max(1, timeout))


def run_experiment(exp, platform, deadline=None):
    """Run an idmtools experiment and wait at most EMOD_RUN_TIMEOUT_S (default 1800) seconds for it,
    and never past the request's `deadline` (monotonic).

    Raises TimeoutError past that; the SSM command timeout is a second, outer bound.
    """
    timeout = experiment_timeout(deadline)
    exp.run(wait_until_done=False, platform=platform)
    platform.wait_till_done_progress(exp, timeout=timeout, refresh_interval=5)


def build_burnin(manifest, sweep, setting, dest_dir, job_dir, deadline=None):
    """Run the 2-year burn-in once and copy its serialized population to dest_dir. Returns its PfPR (run_burnins)."""
    return run_burnins(manifest, sweep, [setting], [dest_dir], job_dir, deadline)[0]


def larval_setter(sweep, settings):
    """A sweep definition (simulation, index) that re-points the task's habitats at settings[index].

    Only the larval capacity may differ between `settings`; sweep.set_habitats replaces the habitats exactly as
    sweep.build_config builds them, so each simulation's config is the one a single burn-in of that setting gets.
    """

    def set_larval(simulation, index):
        sweep.set_habitats(simulation.task.config, settings[index])
        return {"candidate": index, "larval_capacity": settings[index]["larval_capacity"]}

    return set_larval


def run_burnins(manifest, sweep, settings, dest_dirs, job_dir, deadline=None):
    """Burn in each of `settings` (differing only in larval_capacity) as ONE experiment, so they run side by side
    (max_job = cpu count). Copies each serialized population to dest_dirs[i] and returns, per setting,
    {"pfpr_2_5y": the survey-window (CALIBRATE_SURVEY_DOY) mean of the last year, "pfpr_2_5y_annual": the last
    year's mean}, from summary reports of the burn-in itself. Reports are outputs: the population chunks of the
    .dtk are unchanged by them, and pick-ups from it give identical results.
    """
    from idmtools.builders import SimulationBuilder
    from idmtools.entities.experiment import Experiment

    base = settings[0]
    for s in settings:
        if {k: v for k, v in s.items() if k != "larval_capacity"} != {
            k: v for k, v in base.items() if k != "larval_capacity"
        }:
            raise ValueError("run_burnins: settings may differ only in larval_capacity")
    platform = make_platform(manifest, job_dir)
    task = make_task(
        manifest,
        sweep,
        partial(sweep.build_config, setting=base, duration_days=BURNIN_DAYS, serialization=("write", [BURNIN_DAYS])),
        partial(sweep.build_campaign, setting=base, rounds=()),
        partial(sweep.build_demographics, base),
        partial(sweep.build_burnin_reports, burnin_days=BURNIN_DAYS, survey_doy=CALIBRATE_SURVEY_DOY),
    )
    builder = SimulationBuilder()
    if len(settings) > 1:
        builder.add_sweep_definition(larval_setter(sweep, settings), list(range(len(settings))))
    builder.add_sweep_definition(sweep.set_seed, [0])
    exp = Experiment.from_builder(builder, task, name="pmc_burnin")
    run_experiment(exp, platform, deadline)
    if not exp.succeeded:
        raise RuntimeError(f"burn-in experiment {exp.id} failed")
    by_index = {int(sim.tags.get("candidate", 0)): sim for sim in exp.simulations}
    pfprs = []
    for i, dest_dir in enumerate(dest_dirs):
        sim_dir = pathlib.Path(by_index[i].get_directory())
        found = sorted(sim_dir.rglob(BURNIN_FILE))
        if not found:
            raise RuntimeError(f"burn-in produced no {BURNIN_FILE}")
        annual = read_summary(sim_dir, "annual", BURNIN_DAYS // 365)
        survey = read_summary(sim_dir, "survey", 1)
        pfprs.append(
            {"pfpr_2_5y": sweep.last_year_pfpr_2_5y(survey), "pfpr_2_5y_annual": sweep.last_year_pfpr_2_5y(annual)}
        )
        dest_dir = pathlib.Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest_dir / (BURNIN_FILE + ".part")
        shutil.copyfile(found[0], tmp)
        os.replace(tmp, dest_dir / BURNIN_FILE)
    shutil.rmtree(exp.get_directory(), ignore_errors=True)
    return pfprs


def read_summary(sim_dir, suffix, n_reports):
    """A simulation's MalariaSummaryReport_<suffix>.json, which must hold exactly n_reports reports."""
    name = f"MalariaSummaryReport_{suffix}.json"
    found = sorted(pathlib.Path(sim_dir).rglob(name))
    if not found:
        raise RuntimeError(f"burn-in produced no {name}")
    msr = json.loads(found[0].read_text())
    got = len(msr["DataByTimeAndAgeBins"]["PfPR by Age Bin"])
    if got != n_reports:
        raise RuntimeError(f"{name} has {got} reports, expected {n_reports}")
    return msr


def run_pickups(manifest, sweep, setting, schedules, seeds, years, burnin_dir, job_dir, deadline=None):
    from idmtools.builders import SimulationBuilder
    from idmtools.entities.experiment import Experiment
    from idmtools_platform_container.utils.general import map_container_path

    platform = make_platform(manifest, job_dir)
    container_dir = map_container_path(platform.job_directory, platform.data_mount, str(burnin_dir))
    duration = years * 365
    rounds_by_code = {s["code"]: s["rounds"] for s in schedules}
    drug_by_code = {s["code"]: s.get("drug", "SP") for s in schedules}
    task = make_task(
        manifest,
        sweep,
        partial(
            sweep.build_config,
            setting=setting,
            duration_days=duration,
            serialization=("read", container_dir, BURNIN_FILE),
        ),
        partial(sweep.build_campaign, setting=setting, rounds=(), start_shift=BURNIN_DAYS),
        partial(sweep.build_demographics, setting),
        partial(sweep.build_reports, report_start=0, report_end=duration, n_years=years),
    )
    builder = SimulationBuilder()
    builder.add_sweep_definition(
        sweep.scenario_setter(
            setting, rounds_by_code, drugs=drug_by_code, burn_in_days=BURNIN_DAYS, start_shift=BURNIN_DAYS
        ),
        list(rounds_by_code),
    )
    builder.add_sweep_definition(sweep.set_seed, list(seeds))
    exp = Experiment.from_builder(builder, task, name="pmc_pickup")
    run_experiment(exp, platform, deadline)
    if not exp.succeeded:
        raise RuntimeError(f"pickup experiment {exp.id} failed")
    out = tempfile.mkdtemp(prefix="results-", dir=job_dir)
    try:
        rows = sweep.summarise(exp, platform, out)
    finally:
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(exp.get_directory(), ignore_errors=True)
    return [
        {
            "code": r["scenario"],
            "drug": drug_by_code[r["scenario"]],
            "seed": r["seed"],
            "cases_3_24m": r["cases_3_24m"],
            "kids_3_24m": r["kids_3_24m"],
            "cases_u5": r["cases_u5"],
            "kids_u5": r["kids_u5"],
            "pfpr_2_5y": r["pfpr_2_5y"],
            "doses": r["doses"],
        }
        for r in rows
    ]


def nice_larval(x):
    """Round a larval capacity to 4 significant figures, kept a float (the cache key hashes its JSON text)."""
    return float(f"{x:.4g}")


def calibration_grid():
    lo, hi = CALIBRATE_LOG10_RANGE
    return [nice_larval(10 ** (lo + (hi - lo) * i / (CALIBRATE_GRID_N - 1))) for i in range(CALIBRATE_GRID_N)]


def interpolate_crossing(points, target):
    """Where PfPR 2-5y crosses `target`, interpolating log10(larval) linearly between the first bracketing pair.

    points: [(larval, pfpr)]. Returns (log10_estimate, (log10_lo, log10_hi)), or None when no adjacent pair
    (in larval order) brackets the target.
    """
    import math

    pts = sorted(points)
    for (la, pa), (lb, pb) in zip(pts, pts[1:]):
        if min(pa, pb) <= target <= max(pa, pb):
            xa, xb = math.log10(la), math.log10(lb)
            frac = 0.5 if pa == pb else (target - pa) / (pb - pa)
            return xa + frac * (xb - xa), (xa, xb)
    return None


def extension_grid():
    lo, hi = CALIBRATE_EXTENSION_LOG10
    return [
        nice_larval(10 ** (lo + (hi - lo) * k / CALIBRATE_EXTENSION_N)) for k in range(1, CALIBRATE_EXTENSION_N + 1)
    ]


def calibrate(evaluate, target, tolerance=CALIBRATE_TOLERANCE):
    """Fit larval capacity so the model's PfPR 2-5y matches `target`.

    evaluate(round_no, [larval, ...]) -> [pfpr or {"pfpr_2_5y": pfpr, ...extra}, ...] runs one round of burn-ins
    (in parallel); extra keys are kept on the candidate. Round 1 is the log-spaced grid (1e6-1e9). If its highest
    PfPR is below target - tolerance, an extension round tries 4 capacities from 1e9 up to 1e10. Then, when the
    target lies inside the PfPR range evaluated so far, a refine round runs CALIBRATE_REFINE_STEPS around the
    interpolated crossing, repeated (up to CALIBRATE_MAX_REFINE_ROUNDS) around the tighter crossing while no
    evaluated value is within `tolerance`. The answer is the evaluated value nearest the target -- never an
    interpolated one, so its burn-in exists.

    Returns {larval_capacity, pfpr_2_5y, fit_error (pfpr - target), fit, iterations (rounds run), extended,
    candidates}, plus the chosen candidate's extra keys. fit is "ok" within `tolerance`, "unreachable" when the
    target lies outside the evaluated PfPR range by more than `tolerance`, and "loose" when it is inside that
    range but no evaluated value came within `tolerance`.
    """
    candidates = []
    rounds_run = []

    def run_round(larvals):
        round_no = len(rounds_run) + 1
        rounds_run.append(round_no)
        for lv, res in zip(larvals, evaluate(round_no, larvals)):
            res = res if isinstance(res, dict) else {"pfpr_2_5y": res}
            candidates.append({"round": round_no, "larval_capacity": lv, **res})

    run_round(calibration_grid())
    lo10, hi10 = CALIBRATE_LOG10_RANGE
    extended = max(c["pfpr_2_5y"] for c in candidates) < target - tolerance
    if extended:
        run_round(extension_grid())
        hi10 = CALIBRATE_EXTENSION_LOG10[1]
    lo = min(c["pfpr_2_5y"] for c in candidates)
    hi = max(c["pfpr_2_5y"] for c in candidates)
    reachable = lo - tolerance <= target <= hi + tolerance
    seen = {c["larval_capacity"] for c in candidates}
    for _ in range(CALIBRATE_MAX_REFINE_ROUNDS):
        if min(abs(c["pfpr_2_5y"] - target) for c in candidates) <= tolerance and len(rounds_run) > 1 + extended:
            break
        crossing = interpolate_crossing([(c["larval_capacity"], c["pfpr_2_5y"]) for c in candidates], target)
        if crossing is None:
            break
        estimate, (xa, xb) = crossing
        width = xb - xa
        refine = []
        for step in CALIBRATE_REFINE_STEPS:
            lv = nice_larval(10 ** min(hi10, max(lo10, estimate + step * width)))
            if lv not in seen:
                seen.add(lv)
                refine.append(lv)
        if not refine:
            break
        run_round(refine)
    best = min(candidates, key=lambda c: (abs(c["pfpr_2_5y"] - target), c["round"]))
    err = best["pfpr_2_5y"] - target
    if abs(err) <= tolerance:
        fit = "ok"
    elif not reachable:
        fit = "unreachable"
    else:
        fit = "loose"
    extra = {k: v for k, v in best.items() if k not in ("round", "larval_capacity", "pfpr_2_5y")}
    return {
        "larval_capacity": best["larval_capacity"],
        "pfpr_2_5y": best["pfpr_2_5y"],
        **extra,
        "fit_error": round(err, 4),
        "fit": fit,
        "iterations": len(rounds_run),
        "extended": extended,
        "candidates": candidates,
    }


def run_calibration(req, cache_dir, heartbeat_s=60, fetch_burnin=None, publish_burnin=None):
    """Calibrate mode: fit larval_capacity to req["target_pfpr"] and cache the chosen value's burn-in under its
    setting_hash (and publish it), exactly where a run request with that larval_capacity looks for it.

    fetch_burnin is accepted for symmetry with run_request and not used: every candidate is burned in fresh.
    """
    t0 = time.time()
    deadline = request_deadline(time.monotonic())
    validate_request(req)
    cache_dir = pathlib.Path(cache_dir).resolve()
    base = dict(req["setting"])
    target = float(req["target_pfpr"])
    tolerance = float(req.get("tolerance", CALIBRATE_TOLERANCE))
    manifest, sweep = load_pmc_sweep()
    ensure_binary(manifest)
    cache_dir.mkdir(parents=True, exist_ok=True)
    rounds = []
    with Heartbeat(heartbeat_s), workdir(cache_dir):
        scratch = pathlib.Path(tempfile.mkdtemp(prefix="calibrate-", dir=cache_dir))
        try:
            dirs = {}

            def evaluate(round_no, larvals):
                r0 = time.time()
                settings = [dict(base, larval_capacity=lv) for lv in larvals]
                dests = [scratch / f"r{round_no}-{i}" for i in range(len(larvals))]
                pfprs = run_burnins(manifest, sweep, settings, dests, cache_dir, deadline)
                dirs.update(zip(larvals, dests))
                rounds.append({"round": round_no, "n": len(larvals), "seconds": round(time.time() - r0, 2)})
                return pfprs

            fit = calibrate(evaluate, target, tolerance)
            chosen = dict(base, larval_capacity=fit["larval_capacity"])
            key = setting_hash(chosen)
            burnin_dir = cache_dir / "burnin" / key
            burnin_dir.mkdir(parents=True, exist_ok=True)
            os.replace(dirs[fit["larval_capacity"]] / BURNIN_FILE, burnin_dir / BURNIN_FILE)
            if publish_burnin is not None:
                publish_burnin(key, burnin_dir / BURNIN_FILE)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    return {
        "mode": "calibrate",
        "hash": request_hash(req),
        "target_pfpr": target,
        "tolerance": tolerance,
        "pfpr_basis": PFPR_BASIS,
        **fit,
        "burnin_hash": key,
        "rounds": rounds,
        "seconds": round(time.time() - t0, 2),
    }


class RequestError(ValueError):
    pass


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_request(req):
    """Raise RequestError with a readable message unless `req` is well-formed. Runs before any burn-in."""

    def need(cond, msg):
        if not cond:
            raise RequestError(msg)

    need(isinstance(req, dict), "request must be a JSON object")
    mode = req.get("mode", "run")
    need(mode in MODES, f"mode must be one of {', '.join(MODES)}")
    calibrate = mode == "calibrate"
    setting = req.get("setting")
    need(isinstance(setting, dict), "request.setting must be an object")
    keys = ["name", "habitat_times", "habitat_values", "pop", "case_mgmt", "net_coverage"]
    if calibrate:
        need(
            "larval_capacity" not in setting,
            "setting.larval_capacity must be omitted in calibrate mode (it is fitted)",
        )
    else:
        keys.insert(1, "larval_capacity")
    for key in keys:
        need(key in setting, f"setting.{key} is missing")
    need(isinstance(setting["name"], str) and setting["name"], "setting.name must be a non-empty string")
    if not calibrate:
        need(
            _num(setting["larval_capacity"]) and setting["larval_capacity"] > 0, "setting.larval_capacity must be > 0"
        )
    need(isinstance(setting["pop"], int) and not isinstance(setting["pop"], bool), "setting.pop must be an integer")
    need(setting["pop"] > 0, "setting.pop must be > 0")
    for key in ("case_mgmt", "net_coverage"):
        need(_num(setting[key]) and 0 <= setting[key] <= 1, f"setting.{key} must be a number in [0, 1]")
    times, values = setting["habitat_times"], setting["habitat_values"]
    need(
        isinstance(times, list) and isinstance(values, list) and len(times) >= 2 and len(times) == len(values),
        "setting.habitat_times and habitat_values must be lists of the same length (>= 2)",
    )
    need(all(_num(x) for x in times + values), "setting.habitat_times/habitat_values must be numbers")
    need(all(a < b for a, b in zip(times, times[1:])), "setting.habitat_times must be strictly increasing")

    if calibrate:
        target = req.get("target_pfpr")
        need(_num(target) and 0 < target < 1, "target_pfpr must be a number in (0, 1)")
        tol = req.get("tolerance", CALIBRATE_TOLERANCE)
        need(_num(tol) and 0 < tol < 1, "tolerance must be a number in (0, 1)")
        return

    schedules = req.get("schedules")
    need(isinstance(schedules, list) and schedules, "schedules must be a non-empty list")
    codes = []
    for i, sched in enumerate(schedules):
        need(isinstance(sched, dict), f"schedules[{i}] must be an object")
        code = sched.get("code")
        need(isinstance(code, str) and code, f"schedules[{i}].code must be a non-empty string")
        codes.append(code)
        need(sched.get("drug", "SP") in DRUGS, f"schedule {code}: drug must be one of {', '.join(DRUGS)}")
        rounds = sched.get("rounds")
        need(isinstance(rounds, list), f"schedule {code}: rounds must be a list")
        for j, rnd in enumerate(rounds):
            where = f"schedule {code} round {j}"
            need(isinstance(rnd, (list, tuple)) and len(rnd) == 6, f"{where}: needs 6 numbers")
            need(all(_num(x) for x in rnd), f"{where}: all 6 values must be numbers")
            offset, interval, reps, age_min, age_max, coverage = rnd
            need(offset >= 0, f"{where}: offset must be >= 0")
            need(interval > 0, f"{where}: interval must be > 0")
            need(reps >= 1 and reps == int(reps), f"{where}: reps must be an integer >= 1")
            need(0 <= age_min < age_max, f"{where}: need 0 <= age_min < age_max")
            need(0 <= coverage <= 1, f"{where}: coverage must be in [0, 1]")
    need(len(set(codes)) == len(codes), "schedule codes must be unique")

    seeds = req.get("seeds")
    need(isinstance(seeds, list) and seeds, "seeds must be a non-empty list")
    need(all(isinstance(x, int) and not isinstance(x, bool) for x in seeds), "seeds must be integers")
    need(len(set(seeds)) == len(seeds), "seeds must be unique")
    years = req.get("intervention_years", 2)
    need(
        isinstance(years, int) and not isinstance(years, bool) and years > 0,
        "intervention_years must be an integer > 0",
    )


def run_request(req, cache_dir, heartbeat_s=60, fetch_burnin=None, publish_burnin=None):
    """Run a request against local paths. Returns the result dict.

    fetch_burnin(setting_hash, dest_file) -> bool and publish_burnin(setting_hash, src_file) are optional hooks
    for a remote burn-in store (S3); without them the burn-in cache is just `cache_dir`.
    """
    if isinstance(req, dict) and req.get("mode") == "calibrate":
        return run_calibration(req, cache_dir, heartbeat_s, fetch_burnin, publish_burnin)
    t0 = time.time()
    deadline = request_deadline(time.monotonic())
    validate_request(req)
    cache_dir = pathlib.Path(cache_dir).resolve()
    setting = req["setting"]
    years = int(req.get("intervention_years", 2))
    manifest, sweep = load_pmc_sweep()
    ensure_binary(manifest)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = setting_hash(setting)
    burnin_dir = cache_dir / "burnin" / key
    with Heartbeat(heartbeat_s), workdir(cache_dir):
        cached = (burnin_dir / BURNIN_FILE).exists()
        burnin_seconds = 0.0
        if not cached and fetch_burnin is not None:
            burnin_dir.mkdir(parents=True, exist_ok=True)
            cached = bool(fetch_burnin(key, burnin_dir / BURNIN_FILE))
        if not cached:
            b0 = time.time()
            build_burnin(manifest, sweep, setting, burnin_dir, cache_dir, deadline)
            burnin_seconds = time.time() - b0
            if publish_burnin is not None:
                publish_burnin(key, burnin_dir / BURNIN_FILE)
        runs = run_pickups(
            manifest, sweep, setting, req["schedules"], req["seeds"], years, burnin_dir, cache_dir, deadline
        )
    runs.sort(key=lambda r: (r["code"], r["seed"]))
    return {
        "hash": request_hash(req),
        "burnin": {"cached": cached, "seconds": round(burnin_seconds, 2)},
        "runs": runs,
        "seconds": round(time.time() - t0, 2),
    }


# ---- S3 layer (boto3 is imported lazily; not exercised by the local tests) ----------------------------------


def split_s3(uri):
    bucket, _, key = uri[len("s3://") :].partition("/")
    return bucket, key


def read_text(uri):
    if uri.startswith("s3://"):
        import boto3

        bucket, key = split_s3(uri)
        return boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read().decode()
    return pathlib.Path(uri).read_text()


def write_text(uri, text):
    if uri.startswith("s3://"):
        import boto3

        bucket, key = split_s3(uri)
        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=text.encode(), ContentType="application/json")
    else:
        path = pathlib.Path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def is_missing(exc):
    """True only for a 404 / NoSuchKey / NotFound; anything else (403, 5xx, throttling) is a real failure."""
    err = exc.response.get("Error", {})
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status == 404 or str(err.get("Code")) in ("404", "NoSuchKey", "NotFound")


def s3_burnin_hooks(request_uri):
    import boto3
    from botocore.exceptions import ClientError

    bucket, _ = split_s3(request_uri)
    client = boto3.client("s3")

    def fetch(key, dest):
        try:
            client.download_file(bucket, f"burnin/{key}.dtk", str(dest))
            return True
        except ClientError as exc:
            if is_missing(exc):
                return False
            raise

    def publish(key, src):
        client.upload_file(str(src), bucket, f"burnin/{key}.dtk")

    return fetch, publish


def self_test():
    """Import and binary check, then a tiny single run (pop 100, one intervention year)."""
    t0 = time.time()
    manifest, sweep = load_pmc_sweep()
    ensure_binary(manifest)
    if not pathlib.Path(manifest.eradication_path).exists():
        raise SystemExit("self-test failed: Eradication binary missing")
    setting = sweep.default_setting()
    setting.update(pop=100, name="self_test")
    req = {
        "setting": setting,
        "schedules": [{"code": "none", "rounds": []}],
        "seeds": [0],
        "intervention_years": 1,
    }
    base = os.environ.get("EMOD_CACHE_DIR")
    if base:
        os.makedirs(base, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="emod-selftest-", dir=base) as tmp:
        out = run_request(req, tmp, heartbeat_s=60)
    if len(out["runs"]) != 1:
        raise SystemExit("self-test failed: expected one run")
    print(f"OK ({time.time() - t0:.0f}s)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--request", help="s3://bucket/key or a local path")
    ap.add_argument("--out", help="s3://bucket/key or a local path")
    ap.add_argument("--cache-dir", default=os.environ.get("EMOD_CACHE_DIR", DEFAULT_CACHE_DIR))
    ap.add_argument("--heartbeat", type=float, default=60)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not (args.request and args.out):
        ap.error("--request and --out are required")
    req = json.loads(read_text(args.request))
    try:
        validate_request(req)
    except RequestError as exc:
        print(f"invalid request: {exc}", file=sys.stderr)
        return 2
    fetch = publish = None
    if args.request.startswith("s3://"):
        fetch, publish = s3_burnin_hooks(args.request)
    result = run_request(req, args.cache_dir, args.heartbeat, fetch, publish)
    write_text(args.out, json.dumps(result, indent=1))
    if "runs" in result:
        print(f"wrote {args.out}: {len(result['runs'])} runs in {result['seconds']}s")
    else:
        fitted = f"larval_capacity {result['larval_capacity']:.4g} ({result['fit']})"
        print(f"wrote {args.out}: {fitted} in {result['seconds']}s")


if __name__ == "__main__":
    sys.exit(main())
