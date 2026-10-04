"""Google Drive caches stay valid until the FILES change, not until a clock runs out.

Every other source is re-read on the cache TTL (``PIPELINE_CACHE_TTL_HOURS``, an hour
in production) because nothing cheap says whether it moved. A Drive source can say:
one ``files.list`` call returns every matching file's id, size, checksum and
modification time. So for a gdrive pipeline:

- Its raw, computed-visit, FLW and entity rows are written with a long expiry
  (``PIPELINE_GDRIVE_CACHE_MAX_AGE_HOURS``, a week by default) -- the upper bound on
  how long anything is trusted without a re-read.
- Alongside them we record the FINGERPRINT of the files they were built from
  (``gdrive_fetcher.fingerprint_metas``): one record for the shared raw slot, and one
  per computed result (keyed by the result's config hash).
- On read, one listing gives the live fingerprint. A result whose recorded
  fingerprint matches is served however old it is (within the max age); one that
  does not -- or that has no record, e.g. it was written before this existed -- is
  rebuilt, and the raw slot is re-read from Drive only if ITS fingerprint is stale.

The live fingerprint is memoised for ``LIVE_FINGERPRINT_REUSE_SECONDS`` across
processes, so a page streaming N pipelines over one folder lists it once.

None of this is a permission. The records say which files a cache came from, never
who may read it: every read still passes ``check_gdrive_access`` with the pipeline's
own stamp and the caller's own membership, cached or not (``AnalysisPipeline.
_check_drive_access``). A listing that fails (Drive down, credentials missing) does
not block a cached read: the cache is served -- bounded by the max age -- and the
failure is logged; a rebuild would have failed on the same Drive anyway.

Records live in Django's cache (Redis in production). Losing one is safe: the
result it described is treated as stale and rebuilt once.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

DEFAULT_GDRIVE_CACHE_MAX_AGE_HOURS = 24 * 7
# How long one listing answers "what are the files now" for every reader. Long
# enough that one page load over many pipelines lists once; short enough that an
# upload is picked up on the next page load.
LIVE_FINGERPRINT_REUSE_SECONDS = 60


def max_age() -> timedelta:
    """How long a gdrive cache row lives without a re-read, whatever the files do."""
    hours = getattr(settings, "PIPELINE_GDRIVE_CACHE_MAX_AGE_HOURS", DEFAULT_GDRIVE_CACHE_MAX_AGE_HOURS)
    return timedelta(hours=hours)


def _record_timeout() -> int:
    # Outlive the rows it describes by a little, never by much: a record without
    # rows is harmless (a miss is a miss), rows without a record just rebuild.
    return int(max_age().total_seconds()) + 3600


def _target_key(data_source) -> str:
    target = "|".join(str(getattr(data_source, k, "") or "") for k in ("file_id", "folder_id", "file_pattern"))
    return hashlib.sha256(target.encode()).hexdigest()[:32]


def _live_key(data_source) -> str:
    return f"gdrive_fp:live:{_target_key(data_source)}"


def _raw_key(opportunity_id, raw_slot_id) -> str:
    return f"gdrive_fp:raw:{opportunity_id}:{raw_slot_id}"


def _computed_key(opportunity_id, config) -> str:
    from connect_labs.labs.analysis.utils import get_config_hash

    return f"gdrive_fp:computed:{opportunity_id}:{config.pipeline_id}:{get_config_hash(config)}"


# --------------------------------------------------------------------------- live


def live_fingerprint(data_source) -> str | None:
    """The fingerprint of the source's files now, or None when Drive cannot say."""
    from connect_labs.labs.analysis.backends.sql.gdrive_fetcher import GDriveSourceError, source_fingerprint

    key = _live_key(data_source)
    fingerprint = cache.get(key)
    if fingerprint:
        return fingerprint
    try:
        fingerprint = source_fingerprint(data_source)
    except GDriveSourceError as e:
        logger.warning("[GDriveFreshness] cannot list %s, serving what is cached: %s", _target_key(data_source), e)
        return None
    cache.set(key, fingerprint, timeout=LIVE_FINGERPRINT_REUSE_SECONDS)
    return fingerprint


def remember_live_fingerprint(data_source, fingerprint: str) -> None:
    """A fetch just read the files: its fingerprint is the freshest answer there is."""
    if fingerprint:
        cache.set(_live_key(data_source), fingerprint, timeout=LIVE_FINGERPRINT_REUSE_SECONDS)


# --------------------------------------------------------------------------- records


def recorded_raw_fingerprint(opportunity_id, raw_slot_id) -> str | None:
    return cache.get(_raw_key(opportunity_id, raw_slot_id))


def record_raw_fingerprint(opportunity_id, raw_slot_id, fingerprint: str | None) -> None:
    if fingerprint:
        cache.set(_raw_key(opportunity_id, raw_slot_id), fingerprint, timeout=_record_timeout())
    else:
        cache.delete(_raw_key(opportunity_id, raw_slot_id))


def recorded_computed_fingerprint(opportunity_id, config) -> str | None:
    return cache.get(_computed_key(opportunity_id, config))


def record_computed_fingerprint(opportunity_id, config, fingerprint: str | None) -> None:
    if fingerprint:
        cache.set(_computed_key(opportunity_id, config), fingerprint, timeout=_record_timeout())
    else:
        cache.delete(_computed_key(opportunity_id, config))


def computed_cache_is_current(opportunity_id, config) -> bool:
    """Whether a gdrive pipeline's cached result was built from the files as they are now.

    True for every non-gdrive config (their validity is the TTL's business), and
    when Drive cannot be listed (see the module docstring). Says nothing about
    whether the cache EXISTS -- callers check that as before.
    """
    if getattr(getattr(config, "data_source", None), "type", None) != "gdrive":
        return True
    live = live_fingerprint(config.data_source)
    if live is None:
        return True
    return recorded_computed_fingerprint(opportunity_id, config) == live
