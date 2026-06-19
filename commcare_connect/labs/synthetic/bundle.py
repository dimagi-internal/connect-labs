"""Per-opp profile bundle: the self-contained, prod-free handoff between
Phase 1 (profile, prod-touching) and Phase 2 (generate, offline).

A bundle is three files — ``manifest.yaml`` + ``app_structure.json`` +
``opportunity.json`` — persisted per source opportunity. They can live on the
local filesystem (``LocalBundleStore``, handy for local testing) or in Google
Drive (``GDriveBundleStore``, durable + container-independent for the labs
server). ``make_bundle_store`` picks the backend from a ``bundle_root`` string:
a plain path is local; a ``gdrive:<folder_id>`` (or bare ``gdrive:``) is Drive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Opportunity-detail keys that are program config / metadata (no beneficiary PII).
_OPP_KEEP_KEYS = {
    "id",
    "name",
    "description",
    "currency",
    "start_date",
    "end_date",
    "max_visits_per_user",
    "daily_max_visits_per_user",
    "budget_per_visit",
    "total_budget",
    "payment_units",
    "deliver_units",
    "organization",
    "program",
}

_BUNDLE_MANIFEST = "manifest.yaml"
_BUNDLE_APP = "app_structure.json"
_BUNDLE_OPP = "opportunity.json"


def scrub_opportunity(detail: dict) -> dict:
    return {k: v for k, v in (detail or {}).items() if k in _OPP_KEEP_KEYS}


@dataclass
class ProfileBundle:
    source_opp_id: int
    manifest_yaml: str
    app_structure: dict
    opportunity: dict


def _bundle_from_parts(*, manifest_yaml: str, app_structure: dict, opportunity: dict) -> ProfileBundle:
    # source_opp_id is recovered from the scrubbed opportunity detail's id, which
    # equals the real source opp id by construction (profile() is keyed on it).
    # This is identical for the local and GDrive backends — no reliance on the
    # container (dir / folder) name.
    return ProfileBundle(
        source_opp_id=int(opportunity["id"]),
        manifest_yaml=manifest_yaml,
        app_structure=app_structure,
        opportunity=opportunity,
    )


def write_bundle(out_dir, source_opp_id: int, *, manifest_yaml: str, app_structure: dict, opportunity: dict) -> Path:
    d = Path(out_dir) / str(source_opp_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / _BUNDLE_MANIFEST).write_text(manifest_yaml)
    (d / _BUNDLE_APP).write_text(json.dumps(app_structure or {}, indent=2))
    (d / _BUNDLE_OPP).write_text(json.dumps(scrub_opportunity(opportunity), indent=2))
    return d


def read_bundle(bundle_dir) -> ProfileBundle:
    d = Path(bundle_dir)
    return _bundle_from_parts(
        manifest_yaml=(d / _BUNDLE_MANIFEST).read_text(),
        app_structure=json.loads((d / _BUNDLE_APP).read_text()),
        opportunity=json.loads((d / _BUNDLE_OPP).read_text()),
    )


# ---------------------------------------------------------------------------
# Bundle stores: a uniform write/read/list surface over local FS or GDrive.
# ---------------------------------------------------------------------------


class BundleStore(Protocol):
    def write(self, source_opp_id: int, *, manifest_yaml: str, app_structure: dict, opportunity: dict) -> str:
        ...

    def read(self, handle: str) -> ProfileBundle:
        ...

    def list_handles(self) -> list[str]:
        ...


class LocalBundleStore:
    """Bundles as ``<root>/<source_opp_id>/`` directories on the local filesystem."""

    def __init__(self, root):
        self.root = Path(root)

    def write(self, source_opp_id, *, manifest_yaml, app_structure, opportunity) -> str:
        return str(
            write_bundle(
                self.root,
                source_opp_id,
                manifest_yaml=manifest_yaml,
                app_structure=app_structure,
                opportunity=opportunity,
            )
        )

    def read(self, handle: str) -> ProfileBundle:
        return read_bundle(handle)

    def list_handles(self) -> list[str]:
        return [str(p) for p in sorted(self.root.iterdir()) if p.is_dir()]


class GDriveBundleStore:
    """Bundles as one Drive subfolder per source opp (named ``str(source_opp_id)``)
    under ``root_folder_id``, each holding the three bundle files. Durable and
    independent of which web container runs each phase."""

    def __init__(self, drive, root_folder_id: str):
        self.drive = drive
        self.root_folder_id = root_folder_id

    def write(self, source_opp_id, *, manifest_yaml, app_structure, opportunity) -> str:
        sub = self.drive.create_folder(str(source_opp_id), self.root_folder_id)
        self.drive.upload_file(sub, _BUNDLE_MANIFEST, manifest_yaml.encode())
        self.drive.upload_file(sub, _BUNDLE_APP, json.dumps(app_structure or {}, indent=2).encode())
        self.drive.upload_file(sub, _BUNDLE_OPP, json.dumps(scrub_opportunity(opportunity), indent=2).encode())
        return sub

    def read(self, handle: str) -> ProfileBundle:
        names = self.drive.list_folder(handle)  # {filename: file_id}
        return _bundle_from_parts(
            manifest_yaml=self.drive.download_file(names[_BUNDLE_MANIFEST]).decode(),
            app_structure=json.loads(self.drive.download_file(names[_BUNDLE_APP]).decode()),
            opportunity=json.loads(self.drive.download_file(names[_BUNDLE_OPP]).decode()),
        )

    def list_handles(self) -> list[str]:
        return list(self.drive.list_folder(self.root_folder_id).values())


def make_bundle_store(bundle_root: str, *, drive=None) -> BundleStore:
    """Resolve a ``bundle_root`` string to a store.

    - plain path  -> ``LocalBundleStore`` (default; backward compatible)
    - ``gdrive:<folder_id>`` -> ``GDriveBundleStore`` rooted at that folder
    - ``gdrive:`` (no id) -> create a timestamped run folder under
      ``LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID`` and root there
    """
    s = str(bundle_root)
    if s.startswith("gdrive:"):
        if drive is None:
            raise ValueError("gdrive bundle store requires a drive client")
        folder_id = s[len("gdrive:") :]
        if not folder_id:
            from django.conf import settings
            from django.utils import timezone

            parent = getattr(settings, "LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID", "")
            if not parent:
                raise RuntimeError("LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID is not set.")
            folder_id = drive.create_folder(f"kmc-bundles-{timezone.now():%Y%m%d-%H%M%S}", parent)
        return GDriveBundleStore(drive, folder_id)
    return LocalBundleStore(bundle_root)
