"""Read a campaign's fields out of its forms ONCE, into a narrow table every count then reads.

On a project of millions of forms the slow part of any count is reading each form's JSON. Reading it again for every
count (and again after every sync) took most of an hour on ``echis-niger``. So the fields a campaign's coverage needs
(child, cycle, health centre, district, their names, whether the dose was given, and where the form was filled in) are
copied into ``HQCampaignRecord`` once per form, by the database (``INSERT ... SELECT``), in chunks ordered by when each
form was synced. A later run reads only what was synced since (``HQCampaignExtraction.watermark``), so after the first
run the counts take seconds. The work is time-boxed: a run that reaches its budget stops cleanly with its watermark
saved and the next run carries on.

Only for a project kept in labs (a customer's database keeps its own data; there the counts read the forms directly).
"""

from __future__ import annotations

import hashlib
import json
import time

from django.db import connection

from connect_labs.hqdata.computed import ComputedContinues
from connect_labs.hqdata.models import HQCampaignExtraction, HQCampaignRecord, HQForm, HQProject

#: Forms copied per statement: big enough to be efficient, small enough that progress is saved often.
CHUNK = 50_000
#: How long one run extracts before it stops and lets the next run carry on (under the fill's 40-minute limit).
BUDGET_SECONDS = 25 * 60


class ExtractionContinues(ComputedContinues):
    """The run used its time budget; what it extracted is saved and the next run carries on from there."""

    def __init__(self, done: int, remaining: int):
        super().__init__(f"Extracted {done:,} records so far; {remaining:,} still to read.")
        self.done = done
        self.remaining = remaining


def spec_key(spec) -> str:
    """The extraction a spec needs: the same paths read the same records, whatever else the spec changes."""
    fields = {
        "xmlns": spec.distribution_xmlns,
        "child": spec.child_path,
        "cycle": spec.cycle_path,
        "csi": spec.csi_path,
        "district": spec.district_path,
        "csi_name": spec.csi_name_path,
        "district_name": spec.district_name_path,
        "served": [spec.served_path, spec.served_value],
        "point": list(spec.point_paths),
    }
    return hashlib.sha1(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


def applies(project: HQProject) -> bool:
    return project.store == HQProject.STORE_LABS


def _insert_sql(spec) -> str:
    from connect_labs.microplans.coverage.campaign import _json_path

    pt = "COALESCE(" + ", ".join(f"form_json #>> {_json_path(p)}" for p in spec.point_paths) + ")"
    num = "'^-?[0-9]+([.][0-9]+)?$'"
    two = "'^-?[0-9]+([.][0-9]+)? -?[0-9]+([.][0-9]+)?( |$)'"
    return f"""
        INSERT INTO {HQCampaignRecord._meta.db_table}
            (project_id, spec_key, form_id, username, received_on, child, cycle, csi, district, csi_name,
             district_name, served, lat, lon, accuracy_m, excluded)
        SELECT project_id, %(key)s, form_id, username, received_on,
               child, cyc, csi, COALESCE(district, ''), COALESCE(csi_name, ''), COALESCE(district_name, ''),
               COALESCE(dose = %(served)s, false),
               CASE WHEN pt IS NULL OR pt !~ {two} THEN NULL
                    WHEN split_part(pt, ' ', 1)::float = 0 AND split_part(pt, ' ', 2)::float = 0 THEN NULL
                    ELSE split_part(pt, ' ', 1)::float END,
               CASE WHEN pt IS NULL OR pt !~ {two} THEN NULL
                    WHEN split_part(pt, ' ', 1)::float = 0 AND split_part(pt, ' ', 2)::float = 0 THEN NULL
                    ELSE split_part(pt, ' ', 2)::float END,
               CASE WHEN pt IS NULL OR pt !~ {two} THEN NULL
                    WHEN split_part(pt, ' ', 4) !~ {num} THEN NULL
                    ELSE split_part(pt, ' ', 4)::float END,
               archived OR deleted_at IS NOT NULL
        FROM (
            SELECT project_id, form_id, username, received_on, archived, deleted_at,
                   form_json #>> {_json_path(spec.child_path)} AS child,
                   form_json #>> {_json_path(spec.cycle_path)} AS cyc,
                   form_json #>> {_json_path(spec.csi_path)} AS csi,
                   form_json #>> {_json_path(spec.district_path)} AS district,
                   form_json #>> {_json_path(spec.csi_name_path)} AS csi_name,
                   form_json #>> {_json_path(spec.district_name_path)} AS district_name,
                   form_json #>> {_json_path(spec.served_path)} AS dose,
                   {pt} AS pt
            FROM {HQForm._meta.db_table}
            WHERE project_id = %(project)s AND synced_at > %(lower)s AND synced_at <= %(upper)s
              AND (%(xmlns)s = '' OR xmlns = %(xmlns)s)
        ) f
        WHERE child IS NOT NULL AND cyc IS NOT NULL AND csi IS NOT NULL
        ON CONFLICT (project_id, spec_key, form_id) DO UPDATE SET
            username = EXCLUDED.username, received_on = EXCLUDED.received_on, child = EXCLUDED.child,
            cycle = EXCLUDED.cycle, csi = EXCLUDED.csi, district = EXCLUDED.district, csi_name = EXCLUDED.csi_name,
            district_name = EXCLUDED.district_name, served = EXCLUDED.served, lat = EXCLUDED.lat,
            lon = EXCLUDED.lon, accuracy_m = EXCLUDED.accuracy_m, excluded = EXCLUDED.excluded
    """


def extract(project: HQProject, spec, report=None, budget_seconds: float = BUDGET_SECONDS) -> HQCampaignExtraction:
    """Bring the project's campaign records up to date with what has been synced; raise ``ExtractionContinues`` if
    the budget runs out first (the work done is kept)."""
    key = spec_key(spec)
    # One extraction of a project's campaign at a time: the coverage figures and the satellite check both ask for it,
    # and the second waits for the first rather than reading the same forms alongside it.
    lock = (74_200, int(hashlib.sha1(f"{project.pk}:{key}".encode()).hexdigest()[:7], 16))
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, %s)", lock)
    try:
        return _extract(project, spec, key, report, budget_seconds)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", lock)


def _extract(project: HQProject, spec, key: str, report, budget_seconds: float) -> HQCampaignExtraction:
    from datetime import datetime, timezone

    state, _ = HQCampaignExtraction.objects.get_or_create(project=project, spec_key=key)
    floor = datetime(1970, 1, 1, tzinfo=timezone.utc)
    forms = HQForm.objects.filter(project_id=project.pk)
    if spec.distribution_xmlns:
        forms = forms.filter(xmlns=spec.distribution_xmlns)
    started = time.monotonic()
    remaining = forms.filter(synced_at__gt=state.watermark or floor).count()
    done = 0
    sql = _insert_sql(spec)
    while remaining > done:
        lower = state.watermark or floor
        upper = (
            forms.filter(synced_at__gt=lower)
            .order_by("synced_at")
            .values_list("synced_at", flat=True)[CHUNK : CHUNK + 1]
        )
        upper = (
            upper[0]
            if upper
            else forms.filter(synced_at__gt=lower).order_by("-synced_at").values_list("synced_at", flat=True).first()
        )
        if upper is None:
            break
        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                {
                    "key": key,
                    "served": spec.served_value,
                    "project": project.pk,
                    "lower": lower,
                    "upper": upper,
                    "xmlns": spec.distribution_xmlns,
                },
            )
        done += forms.filter(synced_at__gt=lower, synced_at__lte=upper).count()
        state.watermark = upper
        state.records = HQCampaignRecord.objects.filter(project_id=project.pk, spec_key=key).count()
        state.save(update_fields=["watermark", "records", "updated_at"])
        if report:
            report(
                f"Reading the campaign's records once: {done:,} of {remaining:,} forms",
                stage_name="extract",
                processed=min(done, remaining),
                total=remaining,
            )
        if time.monotonic() - started > budget_seconds and remaining > done:
            raise ExtractionContinues(done, remaining - done)
    return state


def scan_records(project: HQProject, spec, with_points: bool = False) -> dict:
    """``campaign.scan``'s answer read from the extracted records (seconds, not an hour)."""
    from connect_labs.microplans.coverage import check

    key = spec_key(spec)
    prefixes = [p.replace("%", "") + "%" for p in spec.exclude_username_prefixes] or ["\\x00never%"]
    dec = int(spec.point_decimals)
    place = (
        f"CASE WHEN lat IS NOT NULL AND (accuracy_m IS NULL OR accuracy_m <= %(acc)s) "
        f"THEN round(lat::numeric, {dec}) END"
    )
    place_lon = (
        f"CASE WHEN lon IS NOT NULL AND (accuracy_m IS NULL OR accuracy_m <= %(acc)s) "
        f"THEN round(lon::numeric, {dec}) END"
    )
    point_sets = ", (csi, cycle, lat_r, lon_r)" if with_points else ""
    place_cols = "lat_r, lon_r" if with_points else "NULL::numeric, NULL::numeric"
    sql = f"""
        WITH g AS (
            SELECT child, cycle, csi, district, csi_name, district_name, served,
                   {place} AS lat_r, {place_lon} AS lon_r
            FROM {HQCampaignRecord._meta.db_table}
            WHERE project_id = %(project)s AND spec_key = %(key)s AND NOT excluded
              AND NOT (username LIKE ANY(%(prefixes)s))
        )
        SELECT GROUPING(district) AS by_place, district, csi, cycle, {place_cols},
               max(csi_name), max(district_name),
               count(DISTINCT child), count(DISTINCT child) FILTER (WHERE served)
        FROM g
        GROUP BY GROUPING SETS ((district, csi, cycle){point_sets})
    """
    counted: dict = {}
    names: dict = {}
    cells: list = []
    with connection.cursor() as cursor:
        cursor.execute(
            sql, {"project": project.pk, "key": key, "prefixes": prefixes, "acc": float(spec.max_accuracy_m)}
        )
        while True:
            batch = cursor.fetchmany(5000)
            if not batch:
                break
            for by_place, district, csi, cyc, lat, lon, csi_name, district_name, seen, served in batch:
                cycle = check.cycle_number(cyc)
                if cycle is None:
                    continue
                if not by_place:
                    slot = counted.setdefault((district or "", csi, cycle), {"seen": 0, "served": 0})
                    slot["seen"] += seen
                    slot["served"] += served
                    if csi_name:
                        names.setdefault(csi, csi_name)
                    if district and district_name:
                        names.setdefault(district, district_name)
                elif lat is not None and lon is not None:
                    cells.append((csi, cycle, float(lat), float(lon), seen, served))
    return {"counts": counted, "names": names, "cells": cells}
