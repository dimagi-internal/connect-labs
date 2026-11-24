"""Who holds the connections on the shared labs Postgres — one read, no literals.

`labs-jj-rds-connections-high` reads `DatabaseConnections` for the whole instance, and
the instance is SHARED: connect-labs (`commcare_connect`), canopy-web (`canopy_web`)
and ace-web each hold their own connections on it. The metric cannot say which, so
"connections are high" says nothing about labs until this has been run. On
2026-11-24 the page read 104; labs itself held ~10 and canopy-web's single task held
57-63, one idle connection per live WebSocket (canopy-web#2889).

Run it on prod without AWS credentials (Hal: `bin/hal-labs-prod-read`; otherwise the
`run-labs-command` workflow with `db_connection_holders`). Read-only: it SELECTs from
`pg_stat_activity` and nothing else.

Output, per database:
  * connections by state, how many distinct client addresses hold them (one address
    holding dozens is one task, i.e. a per-task pool or a leak), and the oldest
    backend and longest time in that state;
  * the most common LAST statement on that database's idle connections, with every
    literal stripped by the same `normalize_sql` the request telemetry uses. A pile of
    idle connections that all ended on one statement points at the code path that
    opened them and never closed them.
"""

from collections import Counter

from django.core.management.base import BaseCommand
from django.db import connection

from connect_labs.utils.request_telemetry import normalize_sql

SHAPE_CHARS = 160

ACTIVITY_SQL = """
    SELECT coalesce(datname, '-'), coalesce(state, '-'), client_addr::text,
           extract(epoch FROM now() - backend_start)::int,
           extract(epoch FROM now() - state_change)::int,
           query
    FROM pg_stat_activity
    WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()
"""


def summarise(rows, top=5):
    """`rows` as (datname, state, client, backend_age_s, in_state_s, query) tuples.

    Returns {datname: {"total", "states": {state: {n, clients, oldest_s, longest_in_state_s}},
    "idle_last_statements": [(n, shape), ...]}}, largest database first."""
    out = {}
    for datname, state, client, age, in_state, query in rows:
        db = out.setdefault(datname, {"total": 0, "states": {}, "_clients": {}, "_idle": Counter()})
        db["total"] += 1
        st = db["states"].setdefault(state, {"n": 0, "clients": 0, "oldest_s": 0, "longest_in_state_s": 0})
        st["n"] += 1
        st["oldest_s"] = max(st["oldest_s"], age or 0)
        st["longest_in_state_s"] = max(st["longest_in_state_s"], in_state or 0)
        db["_clients"].setdefault(state, set()).add(client)
        if state.startswith("idle") and query:
            db["_idle"][normalize_sql(query)[:SHAPE_CHARS]] += 1
    for db in out.values():
        for state, clients in db.pop("_clients").items():
            db["states"][state]["clients"] = len(clients)
        db["idle_last_statements"] = [(n, shape) for shape, n in db.pop("_idle").most_common(top)]
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["total"]))


class Command(BaseCommand):
    help = "Read-only: which database, client and last statement hold the shared RDS's connections."

    def add_arguments(self, parser):
        parser.add_argument("--top", type=int, default=5, help="idle last-statement shapes per database")

    def handle(self, *args, **options):
        with connection.cursor() as cur:
            cur.execute(ACTIVITY_SQL)
            rows = cur.fetchall()
            cur.execute("SHOW max_connections")
            ceiling = cur.fetchone()[0]
        summary = summarise(rows, top=options["top"])
        total = sum(db["total"] for db in summary.values())
        self.stdout.write(f"client connections: {total} of max_connections {ceiling}")
        for datname, db in summary.items():
            self.stdout.write(f"\n{datname}: {db['total']}")
            for state, st in sorted(db["states"].items(), key=lambda kv: -kv[1]["n"]):
                self.stdout.write(
                    f"  {state:<22} n={st['n']:<4} clients={st['clients']:<3} "
                    f"oldest={st['oldest_s']}s longest_in_state={st['longest_in_state_s']}s"
                )
            for n, shape in db["idle_last_statements"]:
                self.stdout.write(f"  idle after ({n}): {shape}")
