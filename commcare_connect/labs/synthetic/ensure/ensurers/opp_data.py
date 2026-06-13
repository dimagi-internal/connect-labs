"""The ``opp_data`` ensurer: register the labs-only opp + load/stash its manifest.

This ensurer does exactly two things, idempotently:

1. **Register the labs-only synthetic opportunity** for ``resource.opportunity_id``
   so :func:`commcare_connect.labs.synthetic.registry.get_synthetic_opp` returns an
   enabled :class:`~commcare_connect.labs.synthetic.models.SyntheticOpportunity`. The
   bots API and the rest of the labs synthetic surface depend on this row existing.
2. **Load + validate the per-opp generator** ``Manifest`` (from
   ``commcare_connect.labs.synthetic.generator.fixtures.manifest``) from
   ``resource.manifest`` (resolved relative to ``ctx.env_dir``) and stash it on
   ``ctx.ids["manifest:<opp_id>"]`` for downstream ensurers to read.

**It does NOT persist visit fixtures.** The per-FLW signal (approval %, SAM/MAM,
flags) for PAR is carried by the weekly workflow runs' pipeline snapshots, which a
later ensurer (``weekly_runs``) materializes from this manifest. The fixture-generating
paths (``synthetic_generate_from_manifest``) push JSON to Google Drive (HTTP + creds)
or a per-user/24h-expiring table — neither fits an HTTP-free, durable, shared ensure
engine — so this ensurer deliberately leaves them out. Downstream ensurers materialize
the signal into pipeline snapshots / audits / tasks straight from the stashed manifest.

The registration reuses the same idempotent ``update_or_create`` on ``opportunity_id``
that the ``synthetic_create_labs_only`` MCP tool performs (same model, ``labs_only=True``,
``enabled=True``), minus the MCP request/user/id-allocation plumbing: the env manifest
already pins a concrete labs-only ``opportunity_id`` (>= 10_000) per opp, so we key the
upsert on it rather than allocating a fresh one.
"""

from __future__ import annotations

from pathlib import Path

from commcare_connect.labs.synthetic.generator.fixtures.manifest import Manifest
from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.labs.synthetic.registry import invalidate_cache


def ensure_opp_data(resource, ctx) -> dict:
    """Register the opp + load/stash its manifest; return a readiness marker.

    ``resource`` is an :class:`~..env_manifest.OppDataResource`; ``ctx`` is the
    run's :class:`~..engine.EnsureContext`. Returns
    ``{f"opp_{opportunity_id}_ready": True}`` for the realized map.
    """
    base = ctx.env_dir if ctx.env_dir is not None else Path.cwd()
    manifest_path = (Path(base) / resource.manifest).resolve()
    manifest = Manifest.from_yaml(manifest_path.read_text())

    SyntheticOpportunity.objects.update_or_create(
        opportunity_id=resource.opportunity_id,
        defaults={
            "labs_only": True,
            "enabled": True,
            "label": manifest.opportunity_name,
            # No GDrive fixtures back a PAR opp (manifest-only); the column is
            # NOT NULL, so default it empty rather than leaving it unset.
            "gdrive_folder_id": "",
        },
    )
    invalidate_cache()

    ctx.ids[f"manifest:{resource.opportunity_id}"] = manifest

    return {f"opp_{resource.opportunity_id}_ready": True}
