"""Serve stock images for synthetic opportunities.

Maps synthetic blob_ids (e.g. synth-muac-003, synth-scale-good-007) to stock image
files in a GDrive folder. Images are cached in-process since the stock set is immutable.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from connect_labs.labs.synthetic.gdrive import DriveClient

logger = logging.getLogger(__name__)

# Legacy form: ``synth-<corpus>-NNN`` → ``<corpus>_NNN.jpg`` (uncategorized pool).
# Pooled form: ``synth-<corpus>-good-NNN`` / ``synth-<corpus>-bad-NNN`` → the
# corresponding ``<corpus>_good_NNN.jpg`` / ``<corpus>_bad_NNN.jpg`` in the same
# folder. Both share the cache; the pool prefix is part of the cache key
# via the blob_id itself.
#
# The corpus segment was hardcoded to ``muac`` until 2026-09-05. Nothing else could
# reach a synthetic visit — so KMC's scale-photo reviewers (scale_validation and
# scale_dial_read, both shipped and both written FOR KMC) had no synthetic data to
# run against, and get_image simply returned None with no error anywhere.
# The corpus segment may contain hyphens ("kmc-scale"), so it is matched LAZILY:
# a greedy class that includes "-" would swallow the pool segment, turning
# synth-kmc-scale-good-001 into corpus="kmc-scale-good" with no pool.
#
# Allowing hyphens makes the grammar ambiguous on its own -- "synth-muac-other-001"
# parses just as happily as corpus "muac-other" as it does as corpus "muac" with a
# bogus pool "other". Before hyphens it was unambiguous and such a blob id resolved
# to None. That strictness is worth keeping (a typo must not resolve a filename that
# does not exist), so the corpus is validated against the corpora that actually
# DECLARE themselves in corpora/*.json rather than against a character class.
_SYNTH_PATTERN = re.compile(r"^synth-([a-z0-9][a-z0-9-]*?)-(?:(good|bad)-)?(\d+)$")

_CORPORA_DIR = Path(__file__).parent / "generator" / "fixtures" / "corpora"


@lru_cache(maxsize=1)
def _known_corpora() -> frozenset[str]:
    """Corpus ids that ship a manifest. Empty set means "do not enforce".

    Degrading open on a missing directory is deliberate: an install without the
    fixtures package should still serve images by name rather than fail closed on
    every blob id.
    """
    try:
        return frozenset(p.stem for p in _CORPORA_DIR.glob("*.json"))
    except OSError:  # pragma: no cover - unreadable fixtures dir
        return frozenset()


_instance: SyntheticImageServer | None = None


def get_image_server() -> SyntheticImageServer:
    global _instance
    if _instance is None:
        _instance = SyntheticImageServer()
    return _instance


class SyntheticImageServer:
    def __init__(self):
        self._drive = DriveClient()
        self._stock_folder_id = getattr(settings, "LABS_SYNTHETIC_STOCK_IMAGES_FOLDER_ID", "")
        self._cache: dict[str, bytes] = {}
        self._folder_listing: dict[str, str] | None = None
        self._corpus_folders: dict[str, str | None] = {}
        self._corpus_listings: dict[str, dict[str, str]] = {}

    @staticmethod
    def _stock_filename(blob_id: str) -> str | None:
        m = _SYNTH_PATTERN.match(blob_id)
        if not m:
            return None
        corpus = m.group(1)
        known = _known_corpora()
        if known and corpus not in known:
            return None
        pool = m.group(2)  # 'good', 'bad', or None
        n = int(m.group(3))
        if pool is None:
            return f"{corpus}_{n:03d}.jpg"
        return f"{corpus}_{pool}_{n:03d}.jpg"

    @staticmethod
    def is_synthetic_blob(blob_id: str) -> bool:
        return SyntheticImageServer._stock_filename(blob_id) is not None

    @property
    def stock_folder_id(self) -> str:
        """Public accessor for the configured stock-images folder id."""
        return self._stock_folder_id

    def list_stock_folder(self) -> dict[str, str]:
        """Public listing of {filename: drive_file_id} for the stock folder.

        Cached on the instance after first call. Returns {} if no folder is
        configured; raises ``DriveAPIError`` on access failure (caller's
        responsibility to handle).
        """
        if not self._stock_folder_id:
            return {}
        if self._folder_listing is None:
            self._folder_listing = self._drive.list_folder(self._stock_folder_id)
        return self._folder_listing

    def _corpus_folder_id(self, corpus: str) -> str | None:
        """Drive id of the ``<corpus>/`` subfolder, or None if there isn't one.

        The stock area was always shaped one-folder-per-corpus -- the parent is
        literally named ``stock-images`` and held a single ``muac`` child. This
        server flattened that by resolving every corpus out of ONE configured
        folder by filename prefix, so a second corpus had to be poured into the
        folder named after the first. Two corpora now exist, so the shape is
        restored: ``stock-images/muac/`` and ``stock-images/scale/``.

        Both configurations work, deliberately. Point the setting at
        ``stock-images`` and each corpus resolves from its own subfolder; leave
        it pointed at a corpus folder (the historical value) and the flat lookup
        below still finds that corpus's files. That is what lets the folder move
        and the settings change land independently instead of as one flag day.
        """
        if corpus in self._corpus_folders:
            return self._corpus_folders[corpus]
        # list_folder returns every immediate child, folders included. A corpus
        # name has no extension and stock filenames always do, so an exact-name
        # hit here is the subfolder and never one of the images.
        found = self.list_stock_folder().get(corpus)
        self._corpus_folders[corpus] = found
        return found

    def _listing_for(self, corpus: str) -> dict[str, str]:
        """{filename: drive_file_id} for one corpus, preferring its own subfolder."""
        fid = self._corpus_folder_id(corpus)
        if fid is None:
            return self.list_stock_folder()
        if corpus not in self._corpus_listings:
            self._corpus_listings[corpus] = self._drive.list_folder(fid)
        return self._corpus_listings[corpus]

    def get_image(self, blob_id: str) -> bytes | None:
        if blob_id in self._cache:
            return self._cache[blob_id]

        filename = self._stock_filename(blob_id)
        if not filename:
            return None

        if not self._stock_folder_id:
            logger.warning("LABS_SYNTHETIC_STOCK_IMAGES_FOLDER_ID not set")
            return None

        m = _SYNTH_PATTERN.match(blob_id)
        corpus = m.group(1)
        listing = self._listing_for(corpus)
        file_id = listing.get(filename)
        if not file_id:
            logger.warning(
                "Stock image %s not found for corpus '%s' (searched %s)",
                filename,
                corpus,
                self._corpus_folder_id(corpus) or self._stock_folder_id,
            )
            return None

        data = self._drive.download_file(file_id)
        self._cache[blob_id] = data
        return data
