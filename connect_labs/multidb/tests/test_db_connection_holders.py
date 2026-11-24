"""db_connection_holders: the shared-RDS "who holds them" read behind labs-jj-rds-connections-high.

What these pin: one client holding many idle connections is reported as ONE client (the
2026-11-24 shape: canopy-web's single task held 57-63), and the idle last-statement
shapes carry no literals, so the output is safe to paste into an issue.
"""

from io import StringIO

import pytest
from django.core.management import call_command

from connect_labs.multidb.management.commands.db_connection_holders import summarise

PAT_STAMP = (
    "UPDATE personal_tokens SET last_used_at = '2026-11-24T13:15:00Z', "
    "last_used_client = 'canopy-runner/1.2' WHERE id = 4711"
)


def test_one_task_holding_many_idle_connections_reads_as_one_client():
    rows = [("canopy_web", "idle", "10.0.2.69/32", 600, 600, PAT_STAMP)] * 36
    rows += [("commcare_connect", "idle", f"10.0.2.{i}/32", 50, 5, "SELECT 1") for i in range(4)]
    rows += [("commcare_connect", "active", "10.0.2.9/32", 3, 0, "SELECT 2")]

    summary = summarise(rows)

    assert list(summary) == ["canopy_web", "commcare_connect"]  # largest first
    idle = summary["canopy_web"]["states"]["idle"]
    assert (idle["n"], idle["clients"], idle["oldest_s"]) == (36, 1, 600)
    assert summary["commcare_connect"]["states"]["idle"]["clients"] == 4
    assert summary["commcare_connect"]["total"] == 5


def test_idle_last_statements_are_counted_without_literals():
    rows = [("canopy_web", "idle", "10.0.2.69/32", 600, 600, PAT_STAMP)] * 3
    rows += [("canopy_web", "active", "10.0.2.69/32", 1, 0, "SELECT secret FROM t WHERE k = 'x'")]

    [(n, shape)] = summarise(rows)["canopy_web"]["idle_last_statements"]

    assert n == 3
    assert shape.startswith("UPDATE personal_tokens SET last_used_at = ?")
    assert "4711" not in shape and "canopy-runner" not in shape and "2026" not in shape


@pytest.mark.django_db
def test_command_runs_against_a_real_database():
    out = StringIO()
    call_command("db_connection_holders", stdout=out)
    assert "client connections:" in out.getvalue()
    assert "max_connections" in out.getvalue()
