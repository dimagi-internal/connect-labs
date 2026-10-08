"""On-box EMOD worker: run PMC schedule scenarios for one setting, from a cached burn-in.

    python run_scenarios.py --request s3://<bucket>/requests/<hash>.json --out s3://<bucket>/results/<hash>.json
    python run_scenarios.py --self-test

Request:  {"setting": {...}, "schedules": [{"code", "rounds": [[offset, interval, reps, age_min_y, age_max_y,
          coverage]]}], "seeds": [0, 1, 2], "intervention_years": 2}
Result:   {"hash", "burnin": {"cached", "seconds"}, "runs": [{"code", "seed", "cases_3_24m", "kids_3_24m",
          "pfpr_2_5y", "doses"}], "seconds"}

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
def workdir(path):
    """emodpy drops demographics_<timestamp>/ dirs in the cwd; keep them out of wherever we were started."""
    path.mkdir(parents=True, exist_ok=True)
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)
        shutil.rmtree(path, ignore_errors=True)


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def setting_hash(setting):
    """Identity of a burn-in: everything that shapes the serialized population (not the display name)."""
    keyed = {k: v for k, v in setting.items() if k != "name"}
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


def make_platform(manifest, job_dir):
    from idmtools.core.platform_factory import Platform

    return Platform(
        "Container",
        job_directory=str(job_dir),
        docker_image=manifest.plat_image,
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
    """Run the 2-year burn-in once and copy its serialized population to dest_dir."""
    from idmtools.builders import SimulationBuilder
    from idmtools.entities.experiment import Experiment

    platform = make_platform(manifest, job_dir)
    task = make_task(
        manifest,
        sweep,
        partial(
            sweep.build_config, setting=setting, duration_days=BURNIN_DAYS, serialization=("write", [BURNIN_DAYS])
        ),
        partial(sweep.build_campaign, setting=setting, rounds=()),
        partial(sweep.build_demographics, setting),
        None,
    )
    builder = SimulationBuilder()
    builder.add_sweep_definition(sweep.set_seed, [0])
    exp = Experiment.from_builder(builder, task, name="pmc_burnin")
    run_experiment(exp, platform, deadline)
    if not exp.succeeded:
        raise RuntimeError(f"burn-in experiment {exp.id} failed")
    found = sorted(pathlib.Path(exp.simulations[0].get_directory()).rglob(BURNIN_FILE))
    if not found:
        raise RuntimeError(f"burn-in produced no {BURNIN_FILE}")
    dest_dir = pathlib.Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / (BURNIN_FILE + ".part")
    shutil.copyfile(found[0], tmp)
    os.replace(tmp, dest_dir / BURNIN_FILE)
    shutil.rmtree(exp.get_directory(), ignore_errors=True)


def run_pickups(manifest, sweep, setting, schedules, seeds, years, burnin_dir, job_dir, deadline=None):
    from idmtools.builders import SimulationBuilder
    from idmtools.entities.experiment import Experiment
    from idmtools_platform_container.utils.general import map_container_path

    platform = make_platform(manifest, job_dir)
    container_dir = map_container_path(platform.job_directory, platform.data_mount, str(burnin_dir))
    duration = years * 365
    rounds_by_code = {s["code"]: s["rounds"] for s in schedules}
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
        sweep.scenario_setter(setting, rounds_by_code, burn_in_days=BURNIN_DAYS, start_shift=BURNIN_DAYS),
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
            "seed": r["seed"],
            "cases_3_24m": r["cases_3_24m"],
            "kids_3_24m": r["kids_3_24m"],
            "pfpr_2_5y": r["pfpr_2_5y"],
            "doses": r["doses"],
        }
        for r in rows
    ]


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
    setting = req.get("setting")
    need(isinstance(setting, dict), "request.setting must be an object")
    for key in ("name", "larval_capacity", "habitat_times", "habitat_values", "pop", "case_mgmt", "net_coverage"):
        need(key in setting, f"setting.{key} is missing")
    need(isinstance(setting["name"], str) and setting["name"], "setting.name must be a non-empty string")
    need(_num(setting["larval_capacity"]) and setting["larval_capacity"] > 0, "setting.larval_capacity must be > 0")
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

    schedules = req.get("schedules")
    need(isinstance(schedules, list) and schedules, "schedules must be a non-empty list")
    codes = []
    for i, sched in enumerate(schedules):
        need(isinstance(sched, dict), f"schedules[{i}] must be an object")
        code = sched.get("code")
        need(isinstance(code, str) and code, f"schedules[{i}].code must be a non-empty string")
        codes.append(code)
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
    with Heartbeat(heartbeat_s), workdir(cache_dir / "work"):
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
    print(f"wrote {args.out}: {len(result['runs'])} runs in {result['seconds']}s")


if __name__ == "__main__":
    sys.exit(main())
