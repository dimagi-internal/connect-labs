"""The Postgres the execution tests run against, and the rule for when it is absent.

The parity and execution tests EXECUTE compiled SQL -- compiling is not the bar --
so they need a real Postgres. Locally they skip when none is reachable. In CI they
must not: a parity suite that silently skips is worse than none, because it reads
as a pass. CI sets SEMANTIC_TEST_REQUIRE_DB, which turns an unreachable database
into a failure.
"""

from __future__ import annotations

import os

import pytest

DSN = os.environ.get(
    "SEMANTIC_TEST_DSN",
    "host=127.0.0.1 port=5432 user=postgres password=postgres dbname=postgres",
)


def connect_or_skip(purpose: str):
    import psycopg2

    try:
        return psycopg2.connect(DSN, connect_timeout=4)
    except Exception as exc:  # pragma: no cover - environment-dependent
        if os.environ.get("SEMANTIC_TEST_REQUIRE_DB"):
            pytest.fail(f"SEMANTIC_TEST_REQUIRE_DB is set but Postgres is unreachable for {purpose}: {exc}")
        pytest.skip(f"no Postgres for {purpose}: {exc}")
