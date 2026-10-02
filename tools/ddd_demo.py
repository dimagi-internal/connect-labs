"""Serve a seeded local labs build for canopy's DDD inner loop.

    make serve-demo                          # port 8000, the default narrative
    make serve-demo DEMO_PORT=8010 NARRATIVE=supply-sophie-rutf
    make demo-seed  DEMO_PORT=8000 NARRATIVE=...   # reseed the running build only
    make stop-demo  DEMO_PORT=8000

Run through `make`, which puts the main checkout's venv on PATH, links `.env`
and sets the GDAL/GEOS paths. `serve` is idempotent and restarts cleanly:

1. stops the build already on this port (its pidfile, never a stranger's process);
2. makes sure postgres and redis answer, starting the main checkout's compose
   project if they do not;
3. uses a DEDICATED database per port, `labs_ddd_demo_<port>`, so it never
   touches the dev database and two builds can run side by side;
4. migrates it, rebuilds the JS bundles if any front-end source is newer than
   them, and seeds the narrative through its seeder's LOCAL mode;
5. starts `runserver` in the background with this worktree's code, stamps
   `/health/` with this checkout's HEAD as `git_sha`, and waits until it answers.

State (pidfile, server log) lives under ~/.cache/connect-labs-ddd-demo/<port>/,
keyed by port rather than worktree, so a re-run from another worktree replaces
the build on that port instead of colliding with it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, urlunparse

REPO = Path(__file__).resolve().parents[1]
STATE_ROOT = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "connect-labs-ddd-demo"
DEFAULT_NARRATIVE = "supply-sophie-unanswered-round"
COMPOSE_PROJECT = "connect-labs"
# Front-end inputs the bundles are built from. Tailwind v4 scans source files for
# class names, so templates and Python (workflow render strings) count too.
JS_INPUTS = ("connect_labs", "tailwind", "webpack", "package.json", "package-lock.json")
# Written only after a build SUCCEEDS: webpack writes webpack-stats.json even when it
# fails, so that file cannot say whether the bundles are good.
JS_STAMP = REPO / "connect_labs" / "static" / "bundles" / ".ddd-demo-built"


def log(msg: str) -> None:
    print(f"[serve-demo] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def _dotenv() -> dict:
    values = {}
    path = REPO / ".env"
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def database_name(port: int) -> str:
    return f"labs_ddd_demo_{port}"


def base_database_url() -> str:
    url = os.environ.get("DDD_DEMO_BASE_DATABASE_URL") or _dotenv().get("DATABASE_URL")
    if not url:
        sys.exit("no DATABASE_URL in .env; copy .env.tpl to .env in the main checkout")
    return url


def demo_database_url(port: int) -> str:
    parsed = urlparse(base_database_url())
    return urlunparse(parsed._replace(path="/" + database_name(port)))


def base_url(port: int) -> str:
    return f"http://localhost:{port}"


def demo_env(port: int) -> dict:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    env = dict(os.environ)
    env.update(
        DATABASE_URL=demo_database_url(port),
        DJANGO_SETTINGS_MODULE="config.settings.local",
        DJANGO_DEBUG="True",
        LABS_PUBLIC_URL=base_url(port),
        CELERY_TASK_ALWAYS_EAGER="True",
        GIT_SHA=sha or "unknown",
        DDD_DEMO_PORT=str(port),
        PYTHONUNBUFFERED="1",
    )
    return env


def state_dir(port: int) -> Path:
    path = STATE_ROOT / str(port)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def ensure_services() -> None:
    db = urlparse(base_database_url())
    db_host, db_port = db.hostname or "localhost", db.port or 5432
    if _open(db_host, db_port) and _open("localhost", 6379):
        return
    compose = REPO / "docker-compose.yml"
    log(f"postgres/redis not answering; starting compose project {COMPOSE_PROJECT!r}")
    subprocess.run(
        ["docker", "compose", "-p", COMPOSE_PROJECT, "-f", str(compose), "up", "-d", "--wait"],
        check=True,
    )
    for _ in range(60):
        if _open(db_host, db_port) and _open("localhost", 6379):
            return
        time.sleep(1)
    sys.exit("postgres/redis still not answering after docker compose up")


def ensure_database(port: int) -> None:
    import psycopg2

    base = urlparse(base_database_url())
    name = database_name(port)
    conn = psycopg2.connect(
        host=base.hostname,
        port=base.port or 5432,
        user=base.username,
        password=base.password,
        dbname="postgres",
    )
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            if not cur.fetchone():
                log(f"creating database {name}")
                cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        conn.close()


def manage(port: int, *args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "manage.py", *args], cwd=REPO, env=demo_env(port), **kwargs)


def migrate(port: int) -> None:
    started = time.monotonic()
    result = manage(port, "migrate", "--noinput", capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"migrate failed:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    log(f"migrated {database_name(port)} in {time.monotonic() - started:.0f}s")


def _newest_input() -> float:
    listed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "--", *JS_INPUTS],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    newest = 0.0
    for rel in listed:
        try:
            newest = max(newest, (REPO / rel).stat().st_mtime)
        except FileNotFoundError:
            continue
    return newest


def ensure_node_modules() -> None:
    """This checkout's own node_modules, matching ITS lockfile.

    Not a link to the main checkout's: that one is installed for whatever branch
    the main checkout has out, and a missing package there fails the build.
    """
    installed = REPO / "node_modules" / ".package-lock.json"
    lock = REPO / "package-lock.json"
    if installed.exists() and installed.stat().st_mtime >= lock.stat().st_mtime:
        return
    log("installing node_modules for this checkout's lockfile (npm ci)")
    started = time.monotonic()
    result = subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=REPO, capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"npm ci failed:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    log(f"npm ci in {time.monotonic() - started:.0f}s")


def build_js(force: bool = False) -> None:
    stale = force or not JS_STAMP.exists() or _newest_input() > JS_STAMP.stat().st_mtime
    if not stale:
        log("JS bundles are current")
        return
    ensure_node_modules()
    started = time.monotonic()
    result = subprocess.run(["npm", "run", "dev"], cwd=REPO, capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"npm run dev failed:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    JS_STAMP.touch()
    log(f"built JS bundles in {time.monotonic() - started:.0f}s")


def seed(port: int, narrative: str, outputs: str | None = None, call: str = "seed_local") -> None:
    """Run `<narrative>/seed.py:<call>` against this port's database, in a fresh process."""
    args = [sys.executable, str(Path(__file__).resolve()), "_seed_in_process", "--port", str(port)]
    args += ["--narrative", narrative, "--call", call]
    if outputs:
        args += ["--outputs", outputs]
    started = time.monotonic()
    result = subprocess.run(args, cwd=REPO, env=demo_env(port))
    if result.returncode:
        sys.exit(f"{narrative}:{call} failed (exit {result.returncode})")
    log(f"{narrative}:{call} done in {time.monotonic() - started:.0f}s")


def _seed_in_process(port: int, narrative: str, outputs: str | None, call: str) -> None:
    import importlib.util

    import django

    sys.path.insert(0, str(REPO))
    django.setup()
    seed_path = REPO / "scripts" / "walkthroughs" / narrative / "seed.py"
    if not seed_path.exists():
        sys.exit(f"no seeder at {seed_path.relative_to(REPO)}")
    spec = importlib.util.spec_from_file_location(f"walkthrough_seed_{narrative.replace('-', '_')}", seed_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not call.endswith("_local") or not hasattr(module, call):
        sys.exit(f"{seed_path.relative_to(REPO)} has no local-mode function {call!r}")
    getattr(module, call)(base_url=base_url(port), outputs=outputs)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def _pidfile(port: int) -> Path:
    return state_dir(port) / "server.json"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _ours(pid: int, port: int) -> bool:
    cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    return "manage.py runserver" in cmd and f":{port}" in cmd


def stop(port: int) -> None:
    pidfile = _pidfile(port)
    if pidfile.exists():
        info = json.loads(pidfile.read_text())
        pid = int(info["pid"])
        if _alive(pid) and _ours(pid, port):
            log(f"stopping the build on :{port} (pid {pid}, {info.get('worktree')} @ {info.get('git_sha', '')[:9]})")
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not _alive(pid):
                    break
                time.sleep(0.2)
            else:
                os.killpg(pid, signal.SIGKILL)
        pidfile.unlink(missing_ok=True)
    for _ in range(25):
        if not _open("127.0.0.1", port):
            return
        time.sleep(0.2)
    sys.exit(f"port {port} is held by a process this did not start; pick another with DEMO_PORT=")


def _health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base_url(port)}/health/", timeout=3) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def start(port: int, timeout: float = 90) -> dict:
    env = demo_env(port)
    logfile = state_dir(port) / "server.log"
    with open(logfile, "ab") as out:
        proc = subprocess.Popen(
            [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
            cwd=REPO,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    info = {
        "pid": proc.pid,
        "port": port,
        "base_url": base_url(port),
        "database": database_name(port),
        "worktree": str(REPO),
        "git_sha": env["GIT_SHA"],
        "log": str(logfile),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _pidfile(port).write_text(json.dumps(info, indent=1))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"runserver exited ({proc.returncode}); see {logfile}")
        body = _health(port)
        if body and body.get("git_sha") == env["GIT_SHA"]:
            return info
        time.sleep(0.5)
    sys.exit(f"{base_url(port)}/health/ did not answer within {timeout:.0f}s; see {logfile}")


def serve(port: int, narrative: str | None, *, force_js: bool = False) -> None:
    started = time.monotonic()
    stop(port)
    ensure_services()
    ensure_database(port)
    migrate(port)
    build_js(force=force_js)
    if narrative:
        seed(port, narrative)
    info = start(port)
    log(
        f"ready in {time.monotonic() - started:.0f}s: {info['base_url']} "
        f"({info['database']}, {Path(info['worktree']).name} @ {info['git_sha'][:9]}) — log {info['log']}"
    )
    print(json.dumps(info))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("serve", "seed", "stop", "status", "manage", "_seed_in_process"):
        p = sub.add_parser(name)
        p.add_argument("--port", type=int, default=int(os.environ.get("DEMO_PORT") or 8000))
        if name in ("serve", "seed", "_seed_in_process"):
            p.add_argument("--narrative", default=os.environ.get("NARRATIVE") or DEFAULT_NARRATIVE)
        if name in ("seed", "_seed_in_process"):
            p.add_argument("--outputs", default=None)
            p.add_argument("--call", default="seed_local", help="the seeder's local-mode function to run")
        if name == "serve":
            p.add_argument("--no-seed", action="store_true")
            p.add_argument("--force-js", action="store_true")
        if name == "manage":
            p.add_argument("command", nargs=argparse.REMAINDER, help="manage.py arguments")
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        serve(args.port, None if args.no_seed else args.narrative, force_js=args.force_js)
    elif args.cmd == "seed":
        seed(args.port, args.narrative, args.outputs, args.call)
    elif args.cmd == "_seed_in_process":
        _seed_in_process(args.port, args.narrative, args.outputs, args.call)
    elif args.cmd == "stop":
        stop(args.port)
    elif args.cmd == "manage":
        sys.exit(manage(args.port, *args.command).returncode)
    elif args.cmd == "status":
        pidfile = _pidfile(args.port)
        info = json.loads(pidfile.read_text()) if pidfile.exists() else {}
        print(json.dumps({**info, "health": _health(args.port)}, indent=1))


if __name__ == "__main__":
    if shutil.which("git") is None:
        sys.exit("git is required")
    main()
