"""
Parity check: compare v1 CSV vs v2 JSON export outputs for migrated endpoints.

Fetches the same opportunity's data via both the v1 streaming CSV path AND
the v2 paginated JSON path, normalizes both to a canonical dict shape, and
diffs them field-by-field. Prints a summary and exits non-zero on any mismatch.

This exists to validate the v2 migration against real production data without
requiring manual smoke testing. Run it against opportunities of varying size
before merging / deploying.

Usage:
    python manage.py compare_v1_v2_exports --opportunity-id 765
    python manage.py compare_v1_v2_exports --opportunity-id 765 --endpoint user_visits
    python manage.py compare_v1_v2_exports --opportunity-id 765 --endpoint user_data
    python manage.py compare_v1_v2_exports --opportunity-id 765 --verbose
    python manage.py compare_v1_v2_exports --opportunity-id 765 --access-token $TOKEN

Auth (first match wins):
  1. --access-token arg
  2. CONNECT_ACCESS_TOKEN env var
  3. ~/.commcare-connect/token.json via TokenManager (same file used by
     `python manage.py get_cli_token`)

Exit codes:
  0 — parity OK across every requested endpoint
  1 — at least one mismatch found
  2 — fetch error (bad token, network failure, etc.)
"""
import ast
import csv
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

from commcare_connect.labs.integrations.connect.export_client import ExportAPIClient, ExportAPIError

# --------------------------------------------------------------------------
# Per-endpoint configuration
# --------------------------------------------------------------------------


@dataclass
class EndpointConfig:
    """Describes how to fetch, parse, and normalize a single migrated endpoint."""

    name: str
    path_template: str  # with {opp_id} placeholder
    id_field: str  # field used to match rows across v1/v2
    # Extra query params (e.g., ?images=true for user_visits)
    extra_params: dict = field(default_factory=dict)
    # Fields that v1 returns as Python repr strings and v2 returns as parsed
    # dicts/lists. The command will ast.literal_eval v1 and deep-compare.
    json_fields: tuple = ()
    # Fields that represent timestamps. v1 and v2 formats may differ; the
    # command normalizes via dateutil or falls back to lexical comparison.
    timestamp_fields: tuple = ()
    # Fields where v1 returns "True"/"False" strings and v2 returns booleans.
    bool_fields: tuple = ()
    # Fields where v1 returns stringified numbers and v2 returns ints.
    int_fields: tuple = ()
    # Fields where labs coerces both sides to string (typically FK PKs that
    # are stored in CharField-backed caches). v1 CSV returns strings natively,
    # v2 JSON returns raw ints, record_to_visit_dict coerces to string.
    str_fields: tuple = ()


ENDPOINTS = {
    "user_visits": EndpointConfig(
        name="user_visits",
        path_template="/export/opportunity/{opp_id}/user_visits/",
        id_field="id",
        extra_params={"images": "true"},
        json_fields=("form_json", "images", "flag_reason", "completed_work"),
        timestamp_fields=(
            "visit_date",
            "status_modified_date",
            "review_created_on",
            "date_created",
        ),
        bool_fields=("flagged",),
        int_fields=("id", "opportunity_id", "deliver_unit_id", "completed_work_id"),
        # deliver_unit is a FK PK; v1 CSV stringifies it, v2 JSON emits the int.
        # labs.record_to_visit_dict coerces to string to match the CharField cache.
        str_fields=("deliver_unit",),
    ),
    "user_data": EndpointConfig(
        name="user_data",
        path_template="/export/opportunity/{opp_id}/user_data/",
        id_field="username",
        # claim_limits is a nested list of dicts — v1 emits Python repr,
        # v2 emits parsed JSON
        json_fields=("claim_limits",),
        timestamp_fields=(
            "date_learn_started",
            "suspension_date",
            "invited_date",
            "completed_learn_date",
            "last_active",
            "date_claimed",
        ),
        bool_fields=("suspended",),
        int_fields=("payment_accrued",),
    ),
    "completed_works": EndpointConfig(
        name="completed_works",
        path_template="/export/opportunity/{opp_id}/completed_works/",
        # No integer id on this endpoint; composite key via username+entity_id+payment_unit_id
        id_field="__composite__",
        timestamp_fields=("last_modified", "status_modified_date", "payment_date", "date_created"),
        int_fields=(
            "opportunity_id",
            "payment_unit_id",
            "saved_completed_count",
            "saved_approved_count",
            # v2 returns these as native ints; v1 CSV stringified them
            "saved_payment_accrued",
            "saved_org_payment_accrued",
        ),
    ),
    "completed_module": EndpointConfig(
        name="completed_module",
        path_template="/export/opportunity/{opp_id}/completed_module/",
        id_field="__composite__",  # username + module + date
        timestamp_fields=("date",),
        int_fields=("module", "opportunity_id"),
    ),
}


# --------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------


_MISSING = object()


def _parse_maybe_repr(raw: Any) -> Any:
    """Parse a value that might be JSON, a Python repr string, or already native."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        return raw  # already parsed (v2 path)
    # Try JSON first (handles null/true/false)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    # Fall back to Python repr (single quotes, True/False/None)
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw  # give up, compare as string


def _normalize_timestamp(raw: Any) -> Any:
    """Coerce a timestamp to a canonical comparable form.

    v1 CSV might return "2026-04-01 12:34:56+00:00" while v2 JSON might return
    "2026-04-01T12:34:56Z". Both parse to the same datetime. We normalize by
    parsing and re-emitting as ISO 8601 with 'Z' suffix for UTC.
    """
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        return raw
    # dateutil is already a transitive dep via Django
    try:
        from dateutil import parser as dateutil_parser

        dt = dateutil_parser.parse(raw)
        # Canonicalize: ISO 8601 with Z suffix for UTC, microsecond-precision
        if dt.tzinfo is None:
            return dt.isoformat(sep="T", timespec="microseconds")
        return dt.astimezone(dt.tzinfo).isoformat(sep="T", timespec="microseconds")
    except (ValueError, TypeError, ImportError):
        return raw


def _normalize_bool(raw: Any) -> Any:
    """Coerce 'True'/'False'/'true'/'false'/1/0/True/False → Python bool."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no", ""):
            return False
    return raw


def _normalize_int(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    try:
        return int(str(raw))
    except (ValueError, TypeError):
        return raw


def _normalize_str(raw: Any) -> Any:
    """Coerce to string. Used for FK PK fields that labs stores in CharField."""
    if raw is None or raw == "":
        return None
    return str(raw)


def _normalize_scalar(raw: Any) -> Any:
    """Generic field normalization for anything not covered by a specific rule."""
    if raw is None:
        return None
    if isinstance(raw, str):
        # Treat empty string as None (v1 CSV often has "", v2 JSON often has null)
        s = raw.strip()
        return None if s == "" else s
    return raw


def _normalize_row(row: dict, cfg: EndpointConfig) -> dict:
    """Apply per-field normalization rules so v1 and v2 become directly comparable."""
    normalized = {}
    for key, value in row.items():
        if key in cfg.json_fields:
            normalized[key] = _parse_maybe_repr(value)
        elif key in cfg.timestamp_fields:
            normalized[key] = _normalize_timestamp(value)
        elif key in cfg.bool_fields:
            normalized[key] = _normalize_bool(value)
        elif key in cfg.int_fields:
            normalized[key] = _normalize_int(value)
        elif key in cfg.str_fields:
            normalized[key] = _normalize_str(value)
        else:
            normalized[key] = _normalize_scalar(value)
    return normalized


def _row_key(row: dict, cfg: EndpointConfig) -> str:
    """Compute a stable identity key for a row (for cross-v1/v2 matching)."""
    if cfg.id_field == "__composite__":
        if cfg.name == "completed_works":
            parts = [
                str(row.get("username") or ""),
                str(row.get("entity_id") or ""),
                str(row.get("payment_unit_id") or ""),
                str(row.get("date_created") or ""),
            ]
        elif cfg.name == "completed_module":
            parts = [
                str(row.get("username") or ""),
                str(row.get("module") or ""),
                str(row.get("date") or ""),
            ]
        else:
            parts = [str(v) for v in row.values()]
        return "|".join(parts)
    return str(row.get(cfg.id_field) or "")


# --------------------------------------------------------------------------
# Fetch implementations
# --------------------------------------------------------------------------


def _fetch_v1(base_url: str, access_token: str, cfg: EndpointConfig, opp_id: int) -> tuple[list[dict], float]:
    """Fetch via the v1 streaming CSV path — explicitly omit the version header."""
    url = f"{base_url.rstrip('/')}{cfg.path_template.format(opp_id=opp_id)}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept-Encoding": "gzip, deflate",
        # No Accept header → prod defaults to version 1.0 → streaming CSV
    }

    started = time.monotonic()
    with httpx.Client(timeout=600.0) as client:
        response = client.get(url, params=cfg.extra_params or None, headers=headers)
        response.raise_for_status()
    elapsed = time.monotonic() - started

    content_type = response.headers.get("content-type", "")
    if "text/csv" not in content_type and "application/csv" not in content_type:
        raise RuntimeError(
            f"v1 fetch returned non-CSV content-type {content_type!r} — did prod already remove v1 support?"
        )

    reader = csv.DictReader(io.StringIO(response.text))
    rows = [dict(r) for r in reader]
    return rows, elapsed


def _fetch_v2(base_url: str, access_token: str, cfg: EndpointConfig, opp_id: int) -> tuple[list[dict], float, int]:
    """Fetch via the v2 paginated JSON path through ExportAPIClient."""
    endpoint = cfg.path_template.format(opp_id=opp_id)
    started = time.monotonic()
    pages = 0
    rows: list[dict] = []
    with ExportAPIClient(base_url=base_url, access_token=access_token, timeout=180.0) as client:
        for page in client.paginate(endpoint, params=cfg.extra_params or None):
            pages += 1
            rows.extend(page)
    elapsed = time.monotonic() - started
    return rows, elapsed, pages


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


@dataclass
class FieldReport:
    field_name: str
    matches: int = 0
    mismatches: list = field(default_factory=list)  # list of (row_key, v1_value, v2_value)


@dataclass
class EndpointReport:
    endpoint: str
    v1_count: int
    v1_elapsed: float
    v2_count: int
    v2_elapsed: float
    v2_pages: int
    common_keys: int
    only_in_v1: list = field(default_factory=list)
    only_in_v2: list = field(default_factory=list)
    field_reports: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        if self.only_in_v1 or self.only_in_v2:
            return False
        return all(not fr.mismatches for fr in self.field_reports.values())


def _compare(v1_rows: list[dict], v2_rows: list[dict], cfg: EndpointConfig, sample: int | None) -> EndpointReport:
    # Apply sample limit if requested (matched by index, after ordering)
    v1_rows_n = _normalize_rows(v1_rows, cfg)
    v2_rows_n = _normalize_rows(v2_rows, cfg)

    if sample is not None:
        v1_rows_n = v1_rows_n[:sample]
        v2_rows_n = v2_rows_n[:sample]

    v1_map = {_row_key(r, cfg): r for r in v1_rows_n}
    v2_map = {_row_key(r, cfg): r for r in v2_rows_n}

    v1_keys = set(v1_map.keys())
    v2_keys = set(v2_map.keys())
    common = v1_keys & v2_keys

    report = EndpointReport(
        endpoint=cfg.name,
        v1_count=len(v1_rows),
        v1_elapsed=0.0,
        v2_count=len(v2_rows),
        v2_elapsed=0.0,
        v2_pages=0,
        common_keys=len(common),
        only_in_v1=sorted(v1_keys - v2_keys)[:10],
        only_in_v2=sorted(v2_keys - v1_keys)[:10],
    )

    # Determine the set of fields to compare — union of all keys present in
    # either side's rows (individual rows may omit optional fields).
    all_fields: set = set()
    for r in v1_rows_n:
        all_fields.update(r.keys())
    for r in v2_rows_n:
        all_fields.update(r.keys())

    for field_name in sorted(all_fields):
        fr = FieldReport(field_name=field_name)
        for key in common:
            v1_val = v1_map[key].get(field_name, _MISSING)
            v2_val = v2_map[key].get(field_name, _MISSING)
            if v1_val == v2_val:
                fr.matches += 1
            else:
                # Treat _MISSING as None on either side — the other serializer
                # may simply not emit the key when null
                if (v1_val is _MISSING and v2_val is None) or (v2_val is _MISSING and v1_val is None):
                    fr.matches += 1
                else:
                    fr.mismatches.append((key, v1_val, v2_val))
        report.field_reports[field_name] = fr

    return report


def _normalize_rows(rows: list[dict], cfg: EndpointConfig) -> list[dict]:
    return [_normalize_row(r, cfg) for r in rows]


# --------------------------------------------------------------------------
# Output formatting
# --------------------------------------------------------------------------


def _format_value(value: Any, max_len: int = 60) -> str:
    if value is _MISSING:
        return "<missing>"
    s = repr(value)
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def _print_report(stdout, report: EndpointReport, cfg: EndpointConfig, verbose: bool):
    stdout.write(f"\n{'=' * 78}\n")
    stdout.write(f"Endpoint: {cfg.path_template.format(opp_id='<id>')}\n")
    stdout.write(f"  v1 fetch: {report.v1_count:,} rows in {report.v1_elapsed:.1f}s\n")
    stdout.write(
        f"  v2 fetch: {report.v2_count:,} rows in {report.v2_elapsed:.1f}s across {report.v2_pages} page(s)\n"
    )
    stdout.write(f"  matched:  {report.common_keys:,} rows (by {cfg.id_field})\n")

    if report.only_in_v1:
        stdout.write(f"  ⚠ only in v1 ({len(report.only_in_v1)} shown): {report.only_in_v1}\n")
    if report.only_in_v2:
        stdout.write(f"  ⚠ only in v2 ({len(report.only_in_v2)} shown): {report.only_in_v2}\n")

    stdout.write("\n  Field parity:\n")
    for field_name, fr in report.field_reports.items():
        total = fr.matches + len(fr.mismatches)
        status = "✓" if not fr.mismatches else "✗"
        stdout.write(f"    {status} {field_name:<28} {fr.matches:>6,}/{total:<6,}")
        if fr.mismatches:
            stdout.write(f"  ({len(fr.mismatches)} mismatches)")
        stdout.write("\n")
        if fr.mismatches:
            shown = fr.mismatches if verbose else fr.mismatches[:3]
            for row_key, v1_val, v2_val in shown:
                stdout.write(f"        key={row_key} v1={_format_value(v1_val)} v2={_format_value(v2_val)}\n")
            if not verbose and len(fr.mismatches) > 3:
                stdout.write(f"        ... and {len(fr.mismatches) - 3} more (pass --verbose to see all)\n")

    stdout.write(f"\n  Result: {'✅ PARITY OK' if report.ok else '❌ PARITY FAILED'}\n")


# --------------------------------------------------------------------------
# Command
# --------------------------------------------------------------------------


class Command(BaseCommand):
    help = "Compare v1 CSV vs v2 JSON export outputs for a given opportunity (parity check)."

    def add_arguments(self, parser):
        parser.add_argument("--opportunity-id", type=int, required=True)
        parser.add_argument(
            "--endpoint",
            choices=list(ENDPOINTS.keys()) + ["all"],
            default="all",
            help="Which endpoint to compare (default: all)",
        )
        parser.add_argument(
            "--sample",
            type=int,
            default=None,
            help="Only compare the first N rows (after both fetches — fetches are always full)",
        )
        parser.add_argument(
            "--access-token",
            default=None,
            help="OAuth token. Falls back to CONNECT_ACCESS_TOKEN env var, then TokenManager.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show every mismatch, not just the first 3 per field.",
        )
        parser.add_argument(
            "--base-url",
            default=None,
            help="Override CONNECT_PRODUCTION_URL (useful for staging).",
        )
        parser.add_argument(
            "--cache-dir",
            default=None,
            help="Directory to cache raw v1/v2 fetches. If set, skips network "
            "on subsequent runs with the same opp_id + endpoint. Use for iterating "
            "on the normalizer config without refetching large datasets.",
        )

    def handle(self, *args, **options):
        access_token = self._resolve_access_token(options["access_token"])
        if not access_token:
            self.stderr.write(
                "No access token available. Provide --access-token, set CONNECT_ACCESS_TOKEN, "
                "or run `python manage.py get_cli_token` to cache one at ~/.commcare-connect/token.json."
            )
            sys.exit(2)

        base_url = options["base_url"] or settings.CONNECT_PRODUCTION_URL
        opp_id = options["opportunity_id"]
        sample = options["sample"]
        verbose = options["verbose"]

        endpoint_choice = options["endpoint"]
        endpoint_names = list(ENDPOINTS.keys()) if endpoint_choice == "all" else [endpoint_choice]

        self.stdout.write(f"Parity check: opportunity {opp_id} against {base_url}")
        if sample:
            self.stdout.write(f"  (comparing first {sample:,} rows only)")

        cache_dir = options.get("cache_dir")
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

        any_failed = False
        for name in endpoint_names:
            cfg = ENDPOINTS[name]
            try:
                v1_rows, v1_elapsed = self._fetch_v1_cached(base_url, access_token, cfg, opp_id, cache_dir)
            except httpx.HTTPStatusError as e:
                self.stderr.write(f"\n[{name}] v1 fetch failed: HTTP {e.response.status_code}")
                self.stderr.write(f"  {e.response.text[:500]}")
                any_failed = True
                continue
            except Exception as e:
                self.stderr.write(f"\n[{name}] v1 fetch failed: {type(e).__name__}: {e}")
                any_failed = True
                continue

            try:
                v2_rows, v2_elapsed, v2_pages = self._fetch_v2_cached(base_url, access_token, cfg, opp_id, cache_dir)
            except ExportAPIError as e:
                self.stderr.write(f"\n[{name}] v2 fetch failed: {e}")
                any_failed = True
                continue
            except Exception as e:
                self.stderr.write(f"\n[{name}] v2 fetch failed: {type(e).__name__}: {e}")
                any_failed = True
                continue

            report = _compare(v1_rows, v2_rows, cfg, sample)
            report.v1_elapsed = v1_elapsed
            report.v2_elapsed = v2_elapsed
            report.v2_pages = v2_pages
            _print_report(self.stdout, report, cfg, verbose)
            if not report.ok:
                any_failed = True

        self.stdout.write(f"\n{'=' * 78}\n")
        if any_failed:
            self.stdout.write(self.style.ERROR("❌ At least one endpoint had parity issues."))
            sys.exit(1)
        else:
            self.stdout.write(self.style.SUCCESS("✅ All compared endpoints match between v1 and v2."))

    @staticmethod
    def _fetch_v1_cached(base_url, access_token, cfg, opp_id, cache_dir):
        if cache_dir:
            cache_file = os.path.join(cache_dir, f"{opp_id}_{cfg.name}_v1.json")
            if os.path.exists(cache_file):
                with open(cache_file) as f:
                    payload = json.load(f)
                return payload["rows"], payload["elapsed"]
        rows, elapsed = _fetch_v1(base_url, access_token, cfg, opp_id)
        if cache_dir:
            with open(cache_file, "w") as f:
                json.dump({"rows": rows, "elapsed": elapsed}, f)
        return rows, elapsed

    @staticmethod
    def _fetch_v2_cached(base_url, access_token, cfg, opp_id, cache_dir):
        if cache_dir:
            cache_file = os.path.join(cache_dir, f"{opp_id}_{cfg.name}_v2.json")
            if os.path.exists(cache_file):
                with open(cache_file) as f:
                    payload = json.load(f)
                return payload["rows"], payload["elapsed"], payload["pages"]
        rows, elapsed, pages = _fetch_v2(base_url, access_token, cfg, opp_id)
        if cache_dir:
            with open(cache_file, "w") as f:
                json.dump({"rows": rows, "elapsed": elapsed, "pages": pages}, f)
        return rows, elapsed, pages

    @staticmethod
    def _resolve_access_token(cli_token: str | None) -> str | None:
        if cli_token:
            return cli_token
        env_token = os.environ.get("CONNECT_ACCESS_TOKEN")
        if env_token:
            return env_token
        try:
            from commcare_connect.labs.integrations.connect.cli import TokenManager

            tm = TokenManager()
            data = tm.load_token()
            if data and not tm.is_expired():
                return data.get("access_token")
        except Exception:
            pass
        return None
