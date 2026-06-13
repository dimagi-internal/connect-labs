"""The ``ensure`` layer: realize a demo's declared synthetic environment.

A composite env manifest (:class:`~.env_manifest.EnvManifest`) is the single
source of truth for *what environment must exist*. Later tasks add the engine
and the per-kind ensurers that idempotently reuse/create/reset records.
"""
