"""Laptop-side batch driver: run IDM's EMOD per Nigerian state on the on-demand box, write the per-state grid.

For every state in ``inputs.json`` it

1. builds the state's setting: ``DEFAULT_SETTING`` overlaid with ONLY the state's habitat curve
   (``emod/states.habitat_curve`` of its monthly rain) and the fitted ``larval_capacity``;
2. CALIBRATES ``larval_capacity`` on the box so the modelled PfPR 2-5 matches the state's DHS prevalence
   (worker ``mode: calibrate``); a ``loose`` or ``unreachable`` fit is recorded and the state's designs skipped;
3. runs ONE request: the no-PMC baseline plus every design from ``designs.designs_for`` x seeds 0..2;
4. reduces it to per-design effects and writes ``connect_labs/labs/indicators/data/pmc_state_grid.json``
   (atomically, after EACH state), which ``emod/rank.py`` reads.

Resumable (a state already in the output is skipped), ``--states A,B`` restricts the run.

No Django import. ``boto3`` and ``canopy_sdk`` are imported only by ``main`` so the logic is testable with a
fake instance and a fake S3. Run it (the box id and bucket are never in code)::

    LABS_EMOD_INSTANCE_ID=i-... LABS_EMOD_BUCKET=... AWS_PROFILE=... \\
        python tools/pmc_emod/states/run_grid.py --image <digest> --emodpy-commit <sha>

Effect maths mirrors ``emod/live.py`` (``_under5_effect`` / ``summarise``): per seed ``(1 - design/none) x 100``
paired by seed, mean across seeds, CI = two-sided 95% Student-t half-width ``t(n-1) x stdev / sqrt(n)``.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import importlib.util
import json
import logging
import math
import os
import re
import shlex
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
GRID_PATH = REPO / "connect_labs/labs/indicators/data/pmc_state_grid.json"
INPUTS_PATH = HERE / "inputs.json"
SWEEP_JSON = REPO / "connect_labs/labs/indicators/data/pmc_emod_sweep.json"

logger = logging.getLogger("pmc_run_grid")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


designs = _load("pmc_run_grid_designs", HERE / "designs.py")
states_mod = _load("pmc_run_grid_states", REPO / "connect_labs/labs/indicators/emod/states.py")

#: The setting every live run starts from. Read from the grid's own record (``setting.model_inputs`` in
#: pmc_emod_sweep.json) because ``emod/runner.py`` is Django-dependent; test_run_grid.py asserts this is
#: identical to ``runner.DEFAULT_SETTING`` (and test_emod_runner.py pins the runner to the same JSON).
DEFAULT_SETTING = json.loads(SWEEP_JSON.read_text())["setting"]["model_inputs"]

BASELINE_SCHEDULE = {"code": "none", "rounds": []}  # same as runner.BASELINE_SCHEDULE
SEEDS = [0, 1, 2]
INTERVENTION_YEARS = 2
SETTING_KEYS = ("larval_capacity", "habitat_times", "habitat_values")

WORKER_PYTHON = "/opt/emod/.venv/bin/python"
WORKER_SCRIPT = "/opt/emod/run_scenarios.py"
BOOT_TIMEOUT_S = 600
READY_TIMEOUT_S = 900
#: Grid requests (baseline + designs x 3 seeds) are longer than a live demo run: the worker's own deadline
#: (EMOD_REQUEST_TIMEOUT_S), and the SSM limit above it so the worker reports its own timeout first.
GRID_REQUEST_TIMEOUT_S = 2800
GRID_SSM_TIMEOUT_S = 3000
#: Calibrate requests (two burn-in rounds) get their own pair of constants; the same values today.
CALIBRATE_REQUEST_TIMEOUT_S = 2800
CALIBRATE_SSM_TIMEOUT_S = 3000
#: Abort the batch after this many states fail in a row (a dead box would otherwise fail all of them).
MAX_CONSECUTIVE_FAILURES = 3

WORKER_DIR = REPO / "tools/pmc_emod/worker"

#: Two-sided 95% t-values by degrees of freedom (same table as emod/live.py).
_T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26}


# --- request building ---------------------------------------------------------------------------------


def state_setting(rain_monthly, larval_capacity: float | None = None) -> dict:
    """DEFAULT_SETTING overlaid with ONLY the habitat curve and (when fitted) larval_capacity.

    The same overlay ``runner.fitted_setting`` builds from the stored grid entry, so a live run of this
    state hits the burn-ins this batch made. Without ``larval_capacity`` it is the calibrate request's
    setting (the worker rejects one that carries it).
    """
    times, values = states_mod.habitat_curve(rain_monthly)
    out = copy.deepcopy(DEFAULT_SETTING)
    out["habitat_times"], out["habitat_values"] = list(times), list(values)
    if larval_capacity is None:
        del out["larval_capacity"]
    else:
        out["larval_capacity"] = float(larval_capacity)
    return out


# Fit tolerance for the batch (absolute PfPR). The model side is a single-seed Oct-Dec mean (about +/-0.03 at
# pop 5000) and the DHS target carries a similar sampling error, so the worker's 0.03 default would mark many
# states "loose" and drop them from the ranking.
CALIBRATE_TOLERANCE = 0.05


def calibrate_request(state: dict) -> dict:
    return {
        "mode": "calibrate",
        "setting": state_setting(state["rain_monthly"]),
        "target_pfpr": state["pfpr_target"],
        "tolerance": CALIBRATE_TOLERANCE,
    }


def grid_request(setting: dict, state_designs: list[dict]) -> dict:
    schedules = [copy.deepcopy(BASELINE_SCHEDULE)] + [
        {"code": d["code"], "rounds": copy.deepcopy(d["rounds"]), "drug": d["drug"]} for d in state_designs
    ]
    return {
        "setting": setting,
        "schedules": schedules,
        "seeds": list(SEEDS),
        "intervention_years": INTERVENTION_YEARS,
    }


def worker_runtime() -> dict:
    """The runtime the worker is built from, read out of the repo's own deploy files (flags override)."""
    digest = re.search(r'^EMOD_IMAGE_DIGEST\s*=\s*"([^"]+)"', (WORKER_DIR / "run_scenarios.py").read_text(), re.M)
    commit = re.search(r"^EMODPY_COMMIT=([0-9a-f]{7,40})\s*$", (WORKER_DIR / "bootstrap.sh").read_text(), re.M)
    if not digest or not commit:
        raise RuntimeError(
            "cannot read EMOD_IMAGE_DIGEST / EMODPY_COMMIT from the worker files; pass --image and --emodpy-commit"
        )
    return {"image": digest.group(1), "emodpy_commit": commit.group(1)}


def request_hash(req: dict) -> str:
    """sha256 of the canonical JSON, the S3 key the live runner uses (emod/runner.py request_hash)."""
    return hashlib.sha256(json.dumps(req, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --- effects ------------------------------------------------------------------------------------------


def _mean_ci(pcts: list[float]) -> tuple[float, float]:
    half = 0.0
    if len(pcts) > 1:
        half = _T95.get(len(pcts) - 1, 1.96) * statistics.stdev(pcts) / math.sqrt(len(pcts))
    return statistics.fmean(pcts), half


def design_effect(result: dict, design: dict) -> dict | None:
    """One design's grid row from a worker result, or None when no seed has a usable baseline.

    averted_u5_pct / averted_3_24m_pct: mean over seeds of (1 - cases_design / cases_none) x 100, paired by
    seed (seeds whose baseline recorded no cases are dropped). averted_u5_ci: 95% t half-width across seeds.
    doses_per_child_per_year = mean doses / mean TARGETED children / intervention years, where the doses
    are the whole simulated population's PMC_Dose events over the intervention years and the targeted
    children are: PMC (3-24 m) the worker's measured ``kids_3_24m``; SMC (3-59 m) ``kids_u5`` x the
    design's ``target_pop_fraction`` (56/60), since the worker reports no 3-59-month count.
    """
    runs = result.get("runs") or []
    base = {r["seed"]: r for r in runs if r["code"] == "none"}
    mine = {r["seed"]: r for r in runs if r["code"] == design["code"]}
    seeds = sorted(set(base) & set(mine))
    u5 = [(1 - mine[s]["cases_u5"] / base[s]["cases_u5"]) * 100 for s in seeds if base[s]["cases_u5"]]
    m324 = [(1 - mine[s]["cases_3_24m"] / base[s]["cases_3_24m"]) * 100 for s in seeds if base[s]["cases_3_24m"]]
    if not u5 or not m324:
        return None
    if min(len(u5), len(m324)) < len(SEEDS):
        logger.warning(
            "%s: only %d of %d seeds have a usable baseline/design pair; the CI is wider",
            design["code"],
            min(len(u5), len(m324)),
            len(SEEDS),
        )
    mean_u5, half_u5 = _mean_ci(u5)
    if design["kind"] == "smc":
        kids = statistics.fmean(mine[s]["kids_u5"] for s in seeds) * design["target_pop_fraction"]
    else:
        kids = statistics.fmean(mine[s]["kids_3_24m"] for s in seeds)
    doses = statistics.fmean(mine[s]["doses"] for s in seeds)
    return {
        "label": design["label"],
        "kind": design["kind"],
        "drug": design["drug"],
        "target_pop_fraction": design["target_pop_fraction"],
        "averted_u5_pct": round(mean_u5, 1),
        "averted_u5_ci": round(half_u5, 1),
        "doses_per_child_per_year": round(doses / kids / INTERVENTION_YEARS, 2),
        "averted_3_24m_pct": round(statistics.fmean(m324), 1),
    }


# --- the box ------------------------------------------------------------------------------------------


class Box:
    """The on-demand instance + artifacts bucket: put a request, run the worker over SSM, read the result."""

    abort_after: int | None = MAX_CONSECUTIVE_FAILURES

    def __init__(self, instance, s3, bucket: str):
        self.instance, self.s3, self.bucket = instance, s3, bucket

    def start(self) -> None:
        up = self.instance.ensure_running(boot_timeout_s=BOOT_TIMEOUT_S, ready_timeout_s=READY_TIMEOUT_S)
        logger.info("box up (cold=%s) %s", up.cold, up.timings)

    def call(self, req: dict) -> dict:
        calibrating = req.get("mode") == "calibrate"
        deadline = CALIBRATE_REQUEST_TIMEOUT_S if calibrating else GRID_REQUEST_TIMEOUT_S
        ssm_timeout = CALIBRATE_SSM_TIMEOUT_S if calibrating else GRID_SSM_TIMEOUT_S
        key = request_hash(req)
        request_key, result_key = f"requests/{key}.json", f"results/{key}.json"
        self.s3.put_object(
            Bucket=self.bucket,
            Key=request_key,
            Body=json.dumps(req, sort_keys=True).encode(),
            ContentType="application/json",
        )
        command = (
            f"EMOD_REQUEST_TIMEOUT_S={deadline} {WORKER_PYTHON} {WORKER_SCRIPT} "
            f"--request {shlex.quote(f's3://{self.bucket}/{request_key}')} "
            f"--out {shlex.quote(f's3://{self.bucket}/{result_key}')}"
        )
        res = self.instance.run([command], timeout_s=ssm_timeout)
        if not res.ok:
            tail = (res.stderr or res.stdout or "").strip()[-1500:]
            raise RuntimeError(f"worker exited {res.exit_code} ({res.status}): {tail}")
        return json.loads(self.s3.get_object(Bucket=self.bucket, Key=result_key)["Body"].read())


class CachedBox(Box):
    """Rebuild a grid from results a previous batch already left in S3: no box, no compute.

    Requests are content-hashed, so the same inputs + driver code name the same ``results/<hash>.json``. A
    missing result raises, which ``run_grid`` records as a failed state (left out of the file).
    """

    #: A missing result costs nothing, so a run of them is not a sick box: keep reading the rest.
    abort_after = None

    def __init__(self, s3, bucket: str):
        super().__init__(None, s3, bucket)

    def start(self) -> None:
        pass

    def call(self, req: dict) -> dict:
        result_key = f"results/{request_hash(req)}.json"
        try:
            return json.loads(self.s3.get_object(Bucket=self.bucket, Key=result_key)["Body"].read())
        except Exception as exc:  # noqa: BLE001 - botocore NoSuchKey and friends
            raise RuntimeError(f"no cached result at s3://{self.bucket}/{result_key}: {exc}") from exc


# --- per state ----------------------------------------------------------------------------------------


def run_state(box: Box, state: dict) -> dict:
    """The grid entry for one state (see module docstring). Raises on a box/worker failure."""
    name = state["name"]
    t0 = time.monotonic()
    cal = box.call(calibrate_request(state))
    t_cal = time.monotonic() - t0
    fit = {"fit": cal["fit"], "pfpr_2_5y": cal["pfpr_2_5y"], "fit_error": cal["fit_error"]}
    larval = cal["larval_capacity"]
    if fit["fit"] == "loose":
        # The worker has no narrower-bracket mode and calibration is deterministic, so a second call
        # returns the same point: accept it and record "loose" (its designs are skipped like unreachable).
        logger.warning("%s: loose fit (error %+.3f); recorded, no refinement available", name, fit["fit_error"])
    setting = state_setting(state["rain_monthly"], larval)
    entry = {
        "inputs": {
            k: state[k] for k in ("pfpr_target", "incidence_per_1000", "pop_u5", "rain_wettest_quarter", "onset_month")
        },
        "setting": {k: setting[k] for k in SETTING_KEYS},
        "fit": fit,
        "designs": {},
    }
    if fit["fit"] != "ok":
        logger.warning("%s: fit %s; designs skipped (calibrate %.0fs)", name, fit["fit"], t_cal)
        return entry
    state_designs = designs.designs_for(state)
    result = box.call(grid_request(setting, state_designs))
    for d in state_designs:
        row = design_effect(result, d)
        if row is None:
            logger.warning("%s: %s has no seed with a baseline case count; omitted", name, d["code"])
            continue
        entry["designs"][d["code"]] = row
    logger.info(
        "%s: fit ok (%+.3f), %d designs; calibrate %.0fs, grid %.0fs",
        name,
        fit["fit_error"],
        len(entry["designs"]),
        t_cal,
        time.monotonic() - t0 - t_cal,
    )
    return entry


def add_designs(box: Box, state: dict, entry: dict) -> dict:
    """``entry`` with the designs ``designs.designs_for`` now lists but the entry lacks, run in the state's
    STORED setting (no re-calibration). Raises if the rebuilt setting differs from the stored one: a
    different setting would put two models in one state's row."""
    name = state["name"]
    missing = [d for d in designs.designs_for(state) if d["code"] not in entry["designs"]]
    if not missing or (entry.get("fit") or {}).get("fit") != "ok":
        return entry
    setting = state_setting(state["rain_monthly"], entry["setting"]["larval_capacity"])
    if {k: setting[k] for k in SETTING_KEYS} != entry["setting"]:
        raise RuntimeError(f"{name}: the rebuilt setting differs from the stored one; re-run the state instead")
    t0 = time.monotonic()
    result = box.call(grid_request(setting, missing))
    out = {**entry, "designs": dict(entry["designs"])}
    for d in missing:
        row = design_effect(result, d)
        if row is None:
            logger.warning("%s: %s has no seed with a baseline case count; omitted", name, d["code"])
            continue
        out["designs"][d["code"]] = row
    logger.info(
        "%s: added %d designs in %.0fs", name, len(out["designs"]) - len(entry["designs"]), time.monotonic() - t0
    )
    return out


# --- the whole grid -----------------------------------------------------------------------------------


def write_atomic(path: Path, grid: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    grid["generated"] = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        os.chmod(tmp, 0o644)  # mkstemp makes it 0600; the grid is a committed data file other accounts read
        with os.fdopen(fd, "w") as f:
            json.dump(grid, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_existing(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def run_grid(
    box: Box,
    states: list[dict],
    out_path: Path,
    runtime: dict,
    *,
    only: list[str] | None = None,
    concurrency: int = 1,
    force_runtime: bool = False,
    add: bool = False,
) -> tuple[dict, list[str]]:
    """Run every (selected) state not already in ``out_path``; write the file after each.

    Returns ``(grid, failed)``. A state whose run raises is logged, named in ``failed`` and left out of the
    file, so the next run retries it. ``only`` matches names case-insensitively.
    """
    grid = load_existing(out_path) or {"version": 1, "runtime": runtime, "states": {}}
    if grid["states"] and grid.get("runtime") != runtime:
        if not force_runtime:
            raise RuntimeError(
                f"{out_path} was built on runtime {grid.get('runtime')} but this run is {runtime}; mixing them "
                "would put two models in one grid. Use a new --out, or --force-runtime to continue anyway."
            )
        logger.warning("runtime differs from the existing file's (%s vs %s); forced", grid.get("runtime"), runtime)
    grid["runtime"] = runtime if not grid["states"] else grid["runtime"]
    if only:
        wanted = {n.strip().lower() for n in only}
        unknown = wanted - {s["name"].lower() for s in states}
        if unknown:
            raise ValueError(f"unknown state(s): {', '.join(sorted(unknown))}")
        states = [s for s in states if s["name"].lower() in wanted]
    if add:
        # States already in the file that lack a design designs_for now lists (fitted states only).
        todo = [
            s
            for s in states
            if s["name"] in grid["states"]
            and grid["states"][s["name"]]["fit"]["fit"] == "ok"
            and {d["code"] for d in designs.designs_for(s)} - set(grid["states"][s["name"]]["designs"])
        ]
    else:
        todo = [s for s in states if s["name"] not in grid["states"]]
    logger.info("%d states selected, %d already done, %d to run", len(states), len(states) - len(todo), len(todo))
    lock = threading.Lock()
    failed: list[str] = []
    streak = [0]
    aborted = threading.Event()

    def one(state):
        if aborted.is_set():
            return
        t = time.monotonic()
        try:
            box.start()  # the box idles down between states; a no-op when it is up
            entry = add_designs(box, state, grid["states"][state["name"]]) if add else run_state(box, state)
        except Exception as exc:  # noqa: BLE001 - one bad state must not lose the others
            logger.error("%s FAILED after %.0fs: %s", state["name"], time.monotonic() - t, exc)
            with lock:
                failed.append(state["name"])
                streak[0] += 1
                if box.abort_after is not None and streak[0] >= box.abort_after:
                    aborted.set()
                    logger.error("%d states failed in a row; aborting the batch", streak[0])
            return
        with lock:
            streak[0] = 0
            grid["states"][state["name"]] = entry
            write_atomic(out_path, grid)
        logger.info("%s done in %.0fs (%d/%d)", state["name"], time.monotonic() - t, len(grid["states"]), len(states))

    if concurrency <= 1:
        for s in todo:
            one(s)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for fut in as_completed([pool.submit(one, s) for s in todo]):
                fut.result()
    if failed:
        logger.error("failed (rerun to retry): %s", ", ".join(failed))
    return grid, failed


# --- CLI ----------------------------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--states", help="comma-separated state names to run (default: all in inputs.json)")
    ap.add_argument("--concurrency", type=int, default=1, help="states at once (default 1; see the task report)")
    ap.add_argument(
        "--out", type=Path, default=None, help=f"output file (default {GRID_PATH.name}; required with --states)"
    )
    ap.add_argument("--inputs", type=Path, default=INPUTS_PATH)
    ap.add_argument("--instance-id", default=os.environ.get("LABS_EMOD_INSTANCE_ID"))
    ap.add_argument("--bucket", default=os.environ.get("LABS_EMOD_BUCKET"))
    ap.add_argument("--region", default=os.environ.get("LABS_EMOD_REGION", "us-east-1"))
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    ap.add_argument("--image", default=os.environ.get("LABS_EMOD_IMAGE"), help="override the worker image digest")
    ap.add_argument("--emodpy-commit", default=os.environ.get("LABS_EMOD_EMODPY_COMMIT"), help="override the commit")
    ap.add_argument("--force-runtime", action="store_true", help="continue a file built on a different runtime")
    ap.add_argument(
        "--from-s3", action="store_true", help="rebuild from results already in the bucket; never touches the box"
    )
    ap.add_argument(
        "--add-designs",
        action="store_true",
        help="run only the designs states in --out lack, in their stored fitted setting (no re-calibration)",
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.bucket or not (args.instance_id or args.from_s3):
        ap.error("set LABS_EMOD_INSTANCE_ID and LABS_EMOD_BUCKET (or --instance-id / --bucket)")
    if args.out is None:
        if args.states:
            ap.error("--out is required with --states (a subset must not land in the committed grid by accident)")
        args.out = GRID_PATH
    runtime = worker_runtime()
    runtime = {
        "image": args.image or runtime["image"],
        "emodpy_commit": args.emodpy_commit or runtime["emodpy_commit"],
    }
    if args.profile:
        os.environ["AWS_PROFILE"] = args.profile
    import boto3

    if not args.from_s3:
        from canopy_sdk.ondemand import OnDemandInstance

    s3 = boto3.client("s3", region_name=args.region)
    box = (
        CachedBox(s3, args.bucket)
        if args.from_s3
        else Box(OnDemandInstance(args.instance_id, args.region, "emod"), s3, args.bucket)
    )
    states = json.loads(args.inputs.read_text())["states"]
    _, failed = run_grid(
        box,
        states,
        args.out,
        runtime,
        only=args.states.split(",") if args.states else None,
        concurrency=args.concurrency,
        force_runtime=args.force_runtime,
        add=args.add_designs,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
