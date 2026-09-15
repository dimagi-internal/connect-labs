"""Import site for the benchmarks MCP tools.

The tools are plain local ORM writes/reads against ``BenchmarkCohort`` /
``BenchmarkCohortMember`` in connect_labs/benchmarks/mcp_tools.py; this module
exists so the labs MCP registry keeps one uniform import location.
"""

from connect_labs.benchmarks import mcp_tools  # noqa: F401
