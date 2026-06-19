"""Two-phase clone: Phase 1 profiles a real opp into a self-contained bundle
(prod-touching); Phase 2 generates fixtures from the bundle (offline, no prod)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .bundle import make_bundle_store, read_bundle
from .dump import _fetch_endpoint
from .generator.fixtures.engine import generate as _generate
from .generator.fixtures.manifest import Manifest
from .generator.fixtures.profiler import profile as _profile
from .generator.fixtures.schema_loader import parse_form_schema_from_app_json
from .generator.io.uploader import upload_fixtures
from .models import SyntheticOpportunity
from .provisioning import allocate_shared_program_id, register_labs_only_opp

logger = logging.getLogger(__name__)


def profile_opp_to_bundle(source_opp_id: int, *, base_url: str, oauth_token: str, store) -> str:
    """Fetch real prod exports for *source_opp_id* and write a self-contained profile bundle.

    All prod network calls go through the module-level ``_fetch_endpoint`` name so that
    Phase-2 tests can patch it to assert zero prod calls::

        with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=...):
            ...

    Args:
        store: a :class:`~commcare_connect.labs.synthetic.bundle.BundleStore`
            (local FS or GDrive) the bundle is written to.

    Returns:
        The bundle handle (local dir path, or GDrive subfolder id).

    Raises:
        ValueError: if the opportunity has no user_visits (cannot profile).
    """
    detail = _fetch_endpoint(base_url, source_opp_id, "", oauth_token)
    user_visits = _fetch_endpoint(base_url, source_opp_id, "user_visits", oauth_token)
    user_data = _fetch_endpoint(base_url, source_opp_id, "user_data", oauth_token)
    app_structure = _fetch_endpoint(base_url, source_opp_id, "app_structure", oauth_token) or {}

    if not isinstance(user_visits, list) or not user_visits:
        raise ValueError(f"No user_visits for opportunity_id={source_opp_id}")

    manifest_yaml = _profile(
        opportunity_id=source_opp_id,
        user_visits=user_visits,
        user_data=user_data if isinstance(user_data, list) else [],
        opportunity_detail=detail if isinstance(detail, dict) else {},
        app_structure=app_structure if isinstance(app_structure, dict) else {},
    )
    return store.write(
        source_opp_id,
        manifest_yaml=manifest_yaml,
        app_structure=app_structure if isinstance(app_structure, dict) else {},
        opportunity=detail if isinstance(detail, dict) else {},
    )


def profile_opps_bulk(
    source_ids, *, base_url: str, oauth_token: str, bundle_root, drive=None
) -> tuple[str, list[str]]:
    """Profile multiple opportunities into one bundle store, isolating per-opp failures.

    The store is built ONCE (so a ``gdrive:`` run folder is shared across all opps).
    A single bad opp (network error, empty visits, etc.) is logged and skipped; the
    rest are still processed.

    Args:
        bundle_root: a local path, or ``gdrive:`` / ``gdrive:<folder_id>``. When
            ``gdrive:``, a run folder is created and its id is returned (below).
        drive: a Drive client, required when ``bundle_root`` is a ``gdrive:`` uri.

    Returns:
        ``(resolved_bundle_root, handles)`` — ``resolved_bundle_root`` is the
        location Phase 2 should read from (``gdrive:<run_folder_id>`` for GDrive,
        else the path), and ``handles`` are the per-opp bundle handles written.
    """
    store = make_bundle_store(bundle_root, drive=drive)
    handles: list[str] = []
    for sid in source_ids:
        try:
            handles.append(profile_opp_to_bundle(sid, base_url=base_url, oauth_token=oauth_token, store=store))
        except Exception:  # noqa: BLE001
            logger.exception("profile_opps_bulk: failed for opp %s", sid)
    resolved = f"gdrive:{store.root_folder_id}" if hasattr(store, "root_folder_id") else str(bundle_root)
    return resolved, handles


# ---------------------------------------------------------------------------
# Phase 2: offline generation from a bundle (no prod calls)
# ---------------------------------------------------------------------------


@dataclass
class CloneResult:
    """Result of generating a synthetic opportunity from a profile bundle."""

    source_opportunity_id: int
    opportunity_id: int
    gdrive_folder_id: str | None
    folder_url: str | None
    record_counts: dict
    app_structure_present: bool
    skipped: bool


def generate_opp_from_bundle(
    bundle_dir,
    *,
    drive,
    program_id: int,
    program_name: str,
    org_name: str,
    label: str | None = None,
    allowed_domains=None,
    fresh: bool = False,
) -> CloneResult:
    """Generate fixtures and register a labs-only opp from a profile bundle.

    This function makes **no prod calls** — it reads the self-contained bundle
    written by :func:`profile_opp_to_bundle` and generates all fixtures locally.
    The module-level ``_fetch_endpoint`` name is never called; Phase-2 tests
    monkeypatch it to raise so this guarantee is machine-verified.

    Idempotency: if a ``SyntheticOpportunity`` with
    ``cloned_from_opportunity_id == source`` already exists and ``fresh=False``,
    the existing row is returned immediately with ``skipped=True``.

    Args:
        bundle_dir: Path to the bundle directory written by :func:`profile_opp_to_bundle`.
        drive: Drive client (``create_folder`` + ``upload_file``).
        program_id: Labs-only program id to file this opp under.
        program_name: Display program name.
        org_name: Display org name.
        label: Override the opportunity label. Defaults to ``[Synthetic] <opp_name>``.
        allowed_domains: Email-domain allowlist. Defaults to ``["@dimagi.com", "@dimagi-ai.com"]``.
        fresh: If ``True``, regenerate even if a row already exists.

    Returns:
        :class:`CloneResult` describing the created (or skipped) opportunity.
    """
    return _generate_one(
        read_bundle(bundle_dir),
        drive=drive,
        program_id=program_id,
        program_name=program_name,
        org_name=org_name,
        label=label,
        allowed_domains=allowed_domains,
        fresh=fresh,
    )


def _generate_one(
    bundle,
    *,
    drive,
    program_id: int,
    program_name: str,
    org_name: str,
    label: str | None = None,
    allowed_domains=None,
    fresh: bool = False,
) -> CloneResult:
    """Generate fixtures + register a labs-only opp from an already-read bundle.

    Backend-agnostic core shared by the single-opp and bulk entry points; makes
    no prod calls. Idempotent on ``cloned_from_opportunity_id``.
    """
    source = bundle.source_opp_id

    existing = SyntheticOpportunity.objects.filter(cloned_from_opportunity_id=source).first()
    if existing and not fresh:
        return CloneResult(
            source_opportunity_id=source,
            opportunity_id=existing.opportunity_id,
            gdrive_folder_id=existing.gdrive_folder_id,
            folder_url=None,
            record_counts={},
            app_structure_present=bool(existing.gdrive_folder_id),
            skipped=True,
        )

    manifest = Manifest.from_yaml(bundle.manifest_yaml)
    form_schema = parse_form_schema_from_app_json(bundle.app_structure, app_type="deliver")
    fixtures = _generate(
        manifest=manifest,
        opportunity_detail=bundle.opportunity,
        form_schema=form_schema,
        app_structure=bundle.app_structure,
    )

    opp_id = existing.opportunity_id if existing else max(SyntheticOpportunity.next_labs_only_opp_id(), program_id + 1)
    upload = upload_fixtures(drive=drive, opportunity_id=opp_id, fixtures=fixtures)

    row = register_labs_only_opp(
        opportunity_id=opp_id,
        label=label or f"[Synthetic] {manifest.opportunity_name}",
        gdrive_folder_id=upload.folder_id,
        org_name=org_name,
        program_name=program_name,
        program_id=program_id,
        allowed_domains=allowed_domains if allowed_domains is not None else ["@dimagi.com", "@dimagi-ai.com"],
        cloned_from=source,
    )
    SyntheticOpportunity.objects.filter(opportunity_id=row.opportunity_id).update(
        visit_count=len(fixtures.get("user_visits") or [])
    )
    return CloneResult(
        source_opportunity_id=source,
        opportunity_id=row.opportunity_id,
        gdrive_folder_id=upload.folder_id,
        folder_url=upload.folder_url,
        record_counts=upload.record_counts,
        app_structure_present=bool(fixtures.get("app_structure")),
        skipped=False,
    )


def generate_opps_bulk(
    bundle_root,
    *,
    drive,
    program_name: str = "KMC (Synthetic)",
    org_name: str = "Dimagi-KMC (Synthetic)",
    fresh: bool = False,
) -> list[CloneResult]:
    """Generate fixtures for every bundle subdirectory under *bundle_root*.

    Allocates one shared program_id for the whole cohort, then loops over
    bundle subdirectories sorted by name, isolating per-opp failures so a
    single bad bundle doesn't abort the rest.

    Args:
        bundle_root: Directory whose immediate subdirectories are bundle dirs
            (each named after the source opportunity_id).
        drive: Drive client (``create_folder`` + ``upload_file``).
        program_name: Display program name shared by all generated opps.
        org_name: Display org name shared by all generated opps.
        fresh: Passed through to :func:`generate_opp_from_bundle`.

    Returns:
        List of :class:`CloneResult` for every bundle that succeeded.
    """
    store = make_bundle_store(bundle_root, drive=drive)
    program_id = allocate_shared_program_id()
    results: list[CloneResult] = []
    for handle in store.list_handles():
        try:
            results.append(
                _generate_one(
                    store.read(handle),
                    drive=drive,
                    program_id=program_id,
                    program_name=program_name,
                    org_name=org_name,
                    fresh=fresh,
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("generate_opps_bulk: failed for bundle %s", handle)
    return results
