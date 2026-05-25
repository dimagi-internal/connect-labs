"""Serve stock images for synthetic opportunities.

Maps synthetic blob_ids (e.g. synth-muac-003) to stock image files in a
GDrive folder. Images are cached in-process since the stock set is immutable.
"""

from __future__ import annotations

import logging
import re

from django.conf import settings

from commcare_connect.labs.synthetic.gdrive import DriveClient

logger = logging.getLogger(__name__)

# Legacy form: ``synth-muac-NNN`` → ``muac_NNN.jpg`` (uncategorized pool).
# Pooled form: ``synth-muac-good-NNN`` / ``synth-muac-bad-NNN`` → the
# corresponding ``muac_good_NNN.jpg`` / ``muac_bad_NNN.jpg`` in the same
# folder. Both share the cache; the pool prefix is part of the cache key
# via the blob_id itself.
_SYNTH_PATTERN = re.compile(r"^synth-muac-(?:(good|bad)-)?(\d+)$")

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

    @staticmethod
    def _stock_filename(blob_id: str) -> str | None:
        m = _SYNTH_PATTERN.match(blob_id)
        if not m:
            return None
        pool = m.group(1)  # 'good', 'bad', or None
        n = int(m.group(2))
        if pool is None:
            return f"muac_{n:03d}.jpg"
        return f"muac_{pool}_{n:03d}.jpg"

    @staticmethod
    def is_synthetic_blob(blob_id: str) -> bool:
        return bool(_SYNTH_PATTERN.match(blob_id))

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

    def get_image(self, blob_id: str) -> bytes | None:
        if blob_id in self._cache:
            return self._cache[blob_id]

        filename = self._stock_filename(blob_id)
        if not filename:
            return None

        if not self._stock_folder_id:
            logger.warning("LABS_SYNTHETIC_STOCK_IMAGES_FOLDER_ID not set")
            return None

        listing = self.list_stock_folder()
        file_id = listing.get(filename)
        if not file_id:
            logger.warning("Stock image %s not found in folder %s", filename, self._stock_folder_id)
            return None

        data = self._drive.download_file(file_id)
        self._cache[blob_id] = data
        return data
