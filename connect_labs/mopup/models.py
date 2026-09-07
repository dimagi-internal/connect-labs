"""Models for the CHC mop-up feature.

This app is a sibling of `connect_labs.microplans`, not a plan-type bolted onto
it — Phase 1/2 (scope selection, ward-scoped visit pulling, cluster detection,
thresholds) are genuinely new and touch none of microplans' own files; at the
Phase 2->3 handoff this app builds the same `areas`/`area_targets` inputs
microplans' `core.data_access.ProgramPlanDataAccess.create_plan()` already
expects and calls it directly, then redirects into the existing, unmodified
microplans review page.

No Django models here — run/session state is a `LocalLabsRecord` proxy
(`connect_labs.mopup.core.models.MopupRunRecord`), matching the labs
convention (see `microplans.core.models` for the identical pattern). This
file stays empty unless the app ever needs a genuine local Postgres table
(e.g. a cache, the way `microplans.models.FootprintArea` is one).
"""

from __future__ import annotations
