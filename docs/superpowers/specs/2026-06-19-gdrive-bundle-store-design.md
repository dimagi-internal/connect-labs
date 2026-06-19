# Durable GDrive-backed profile bundles (follow-up to #655)

**Date:** 2026-06-19
**Status:** Design — pending review
**Author:** Jonathan Jackson (with Claude)

## 1. Goal

Make the Phase-1 **profile bundle** durable by storing it in **Google Drive** (where the
generated synthetic fixtures already live), instead of only on the ECS container's local
disk. This:

1. **Survives container recycles** and works across labs' multiple web containers — the
   profile→generate handoff no longer depends on both phases hitting the same box.
2. **Enables error recovery** — Phase 2 is already idempotent on `cloned_from_opportunity_id`;
   with bundles on GDrive, a failure partway is just "re-run generate" (skips done opps), no
   prod re-touch.
3. **Enables recreate-without-prod** — the bundle is self-contained aggregate stats; re-run
   generate (optionally `fresh=True`) any time to rebuild the cohort with zero prod access.

What's already durable (unchanged): the generated fixtures (per-opp GDrive folder) + the
`SyntheticOpportunity` rows. This work only persists the **Phase-1 bundle**.

## 2. Constraint vs. local FS

GDrive addresses by **folder id**, not path. So there is no `root/<id>/manifest.yaml` path;
instead a **run folder** contains one **subfolder per source opp** (named `str(source_opp_id)`),
each holding the 3 bundle files. Read discovers them by `list_folder`.

Privacy unchanged: a bundle is histograms + a correlation matrix + the app schema + scrubbed
opp metadata — strictly less sensitive than the per-visit fixtures already in GDrive.

## 3. Design

### 3.1 `BundleStore` abstraction (`bundle.py`)

A small interface with two implementations; the clone functions depend on the interface.

```python
class BundleStore(Protocol):
    def write(self, source_opp_id: int, *, manifest_yaml: str, app_structure: dict, opportunity: dict) -> str
        # persist the 3 files for one opp; return a handle (local: dir path; gdrive: subfolder id)
    def read(self, handle: str) -> ProfileBundle
    def list_handles(self) -> list[str]
        # all per-opp bundle handles under this store's root (for bulk generate)
```

- **`LocalBundleStore(root: Path)`** — write → `root/<id>/` (the existing 3-file layout); `list_handles` → subdir paths; `read` → existing loader.
- **`GDriveBundleStore(drive: DriveClient, root_folder_id: str)`** — write → `create_folder(str(id), root_folder_id)` then `upload_file` ×3; handle = subfolder id. `list_handles` → `list_folder(root_folder_id).values()`. `read(folder_id)` → `list_folder(folder_id)` → `download_file` each → `ProfileBundle`.

**Robustness change:** `ProfileBundle.source_opp_id` is derived from the loaded
**manifest's `opportunity_id`** (which equals the source opp id by construction —
`profile(opportunity_id=source_opp_id, …)`), not from the directory name. This makes `read`
identical for both backends and removes the dir-name dependency. (Behavior-preserving: the
two were equal already.)

### 3.2 Location scheme — one `bundle_root` string, prefix selects the backend

- Plain path → `LocalBundleStore` (e.g. `/tmp/kmc-bundles`). **Default; backward compatible.**
- `gdrive:<folder_id>` → `GDriveBundleStore` rooted at that folder id.

Factory `make_bundle_store(bundle_root: str, *, drive=None) -> BundleStore` parses the prefix.
Phase 1 with GDrive: if given `gdrive:` with no id (or a parent id), create a timestamped run
folder under `LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID` and return `gdrive:<run_folder_id>` as the
handle to pass to Phase 2.

### 3.3 Threading through `clone_from_prod.py`

- `profile_opp_to_bundle` / `profile_opps_bulk` accept `bundle_root` (string) instead of a
  raw `out_dir`; build the store via the factory; `store.write(...)`. Bulk returns the
  resolved `bundle_root` (esp. the created `gdrive:<run_folder_id>`) so the caller knows where
  Phase 2 should read.
- `generate_opp_from_bundle` accepts a single bundle `handle` + store (or `bundle_root` +
  `handle`); `generate_opps_bulk(bundle_root, …)` builds the store and loops `store.list_handles()`.
  Phase 2 still makes **zero prod calls** (GDrive is not prod).

### 3.4 MCP tools + management commands

- Tools/commands gain GDrive support purely via the `bundle_root` / `out_dir` string accepting
  the `gdrive:` prefix (no new flags). `synthetic_profile_opps_bulk(..., out_dir="gdrive:")`
  returns the created `gdrive:<run_folder_id>`; `synthetic_generate_opps_bulk(bundle_root="gdrive:<id>")`.
- Server runs use `gdrive:` (durable, container-independent); local testing uses a path.

## 4. Idempotency / resume / recreate

- **Resume after partial failure:** re-run `generate_opps_bulk(<same gdrive root>)` — bundles
  persist; `cloned_from_opportunity_id` skip handles already-done opps.
- **Recreate from scratch:** `generate_opps_bulk(<gdrive root>, fresh=True)` regenerates all
  fixtures from the persisted bundles; no prod, no re-profile.
- **Re-profile (only if prod changed):** re-run Phase 1 into a new `gdrive:` run folder.

## 5. Testing

- `GDriveBundleStore` round-trip with a **fake DriveClient** (in-memory folders/files):
  write→read recovers manifest_yaml + app_structure + opportunity + source_opp_id (from manifest).
- `make_bundle_store` selects Local vs GDrive by prefix; plain path still works.
- `list_handles` returns one handle per written opp; bulk generate over a GDrive store registers
  all opps under one program (reuse the Phase-2 fake-drive test pattern).
- `read` derives `source_opp_id` from the manifest for both backends.
- Local-path path unchanged (existing bundle/clone tests still green).
- macOS pytest needs `GDAL_LIBRARY_PATH`/`GEOS_LIBRARY_PATH`.

## 6. Files

| File | Change |
|---|---|
| `synthetic/bundle.py` | add `BundleStore` protocol, `LocalBundleStore`, `GDriveBundleStore`, `make_bundle_store`; `read` derives source_opp_id from manifest |
| `synthetic/clone_from_prod.py` | profile/generate accept `bundle_root` string + use the store factory |
| `mcp/tools/synthetic.py` | profile/generate tools accept `gdrive:` in their dir param; build a `DriveClient` for gdrive |
| `labs/management/commands/synthetic_profile_opps.py` / `synthetic_generate_opps.py` | same `gdrive:` support |
| tests | as in §5 |

## 7. Out of scope / decisions

- Keep the **local-FS path** (handy for local testing); GDrive is opt-in via the `gdrive:` prefix.
- No EFS/persistent-volume mount — GDrive is already wired and is the synthetic store.
- No change to the generated-fixture storage (already GDrive) or the labs DB registration.
