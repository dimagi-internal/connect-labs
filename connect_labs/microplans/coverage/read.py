"""Read a campaign's household rows through the Data query path, for the MCP tool and the page alike.

One reader, so the tool and the page see the same rows under the same rules: as the caller, over a data
space, with the Data query's own limits and its sensitive-data rule. The households are read in shards that
stay under the query row cap, so a large campaign is several reads, never a silently cut one.
"""

from __future__ import annotations

from connect_labs.labsdata import service
from connect_labs.microplans.coverage import check


class RowCapExceeded(Exception):
    """A read hit the Data query's row cap: the answer would have been a smaller campaign."""


def _rows(caller, space, sql, agent):
    out = service.query(caller, space, sql, max_rows=5000, for_agent=agent, shaped=False)
    if out.get("truncated"):
        raise RowCapExceeded("A read hit the row cap; narrow the opportunities and run again.")
    return out["rows"]


def read_households(caller, space, spec: check.CheckSpec, agent: bool = False) -> list:
    """Every household row of the space, shard by shard."""
    count = _rows(caller, space, check.count_sql(spec), agent)
    households = int(count[0][0]) if count and count[0] and count[0][0] is not None else 0
    rows: list = []
    for shard in check.shard_bounds(households):
        rows.extend(_rows(caller, space, check.points_sql(spec, shard), agent))
    return rows
