"""Models for the CHC mop-up feature.

This app is a sibling of `connect_labs.microplans`, not a plan-type bolted onto
it — Phase 1/2 (scope selection, ward-scoped visit pulling, cluster detection,
thresholds) are genuinely new and touch none of microplans' own files; at the
Phase 2->3 handoff this app builds the same `areas`/`area_targets` inputs
microplans' `core.data_access.ProgramPlanDataAccess.create_plan()` already
expects and calls it directly, then redirects into the existing, unmodified
microplans review page.

No models yet — Phase 1 (run/session state for a mop-up analysis in progress)
adds them.
"""

from __future__ import annotations
