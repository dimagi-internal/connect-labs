"""R2 Revisit Analysis — which work areas a second revisit round (R2) should send workers to.

A programme-owned, multi-opportunity report for a study that already ran one
revisit round (R1) as separate opportunities. For each organisation it reads the
original opportunity and its R1 opportunity (``config.orgs``: ``[{org, main, r1}]``)
and shows two tabs:

  Table — per organisation and ward: work areas in the original and in the R1
          opportunity; R2 Work-Areas (R1's own thresholds, not already in R1);
          work areas of suspended workers, approved inaccessible work areas and
          work areas with a rejected HSD/NCF visit (each also "not in R1"); and a
          de-duplicated ward total.
  Map   — the selected ward's boundary and its work areas, coloured by the first
          reason that applies, with a legend that hides categories and a popup
          with each work area's numbers.
  Download CSV — one organisation's R2 work areas, for the chosen wards and rules,
          in Connect's work-area upload format (microplans' CSV_HEADERS), with
          a new Expected Visit Count: buildings × the ward's EVC per building.

R1 work areas carry no link to their original (new slug, no case id) but were
uploaded with the original boundary unchanged, so "in R1" is an exact centroid
match (7 decimal places).

The render loads its eager sources itself, per organisation and opportunity,
through the pipeline-rows stream (``noPipelineStream``), so the four
organisations' sources load a few at a time with progress. The map's two sources
are meant to be on demand (``load: "on_demand"`` on the workflow's sources) and
are queried one ward at a time with ``actions.queryPipelineRows``.
"""

from pathlib import Path

COLUMNS = [
    [
        "main",
        "No. of WAs in main opp",
        "Work areas in the original RCT opportunity (Connect’s work-area export) in this ward, "
        "leaving out the ones Connect marks Excluded.",
    ],
    [
        "r1",
        "No. of WAs in R1 opp",
        "Work areas in this ward in the R1 revisit opportunity, including new ones R1 added.",
    ],
    [
        "algo_add",
        "R2 Work-Area",
        "Work areas of the primary opportunity, not already in R1 and in a ward with no work areas in R1 "
        "(R1 already applied these thresholds in its wards), that none of these rules exclude: "
        "HSD visits over 40% of EVC (or EVC of 0); MUAC recorded on over 70% of HSD visits; deworming "
        "delivered on over 40% of HSD visits; an NCF visit with only 1 building. Visits count at every "
        "review status except duplicate and trial.",
    ],
    [
        "susp",
        "WAs of suspended workers (main opp)",
        "Work areas whose assigned worker is suspended on the original opportunity (Connect’s worker "
        "export), including ones already in R1.",
    ],
    [
        "susp_not_r1",
        "WAs of suspended workers, not in R1",
        "Suspended workers’ work areas that are not already in the R1 opportunity.",
    ],
    [
        "inacc_not_r1",
        "Approved inaccessible, not in R1",
        "Work areas Connect has marked Inaccessible (approved; a pending request is not counted) that "
        "are not already in R1.",
    ],
    [
        "rejected",
        "WAs with any rejected HSD/NCF visit",
        "Work areas with at least one rejected Health Service Delivery/No Children Found visit in the "
        "original opportunity, even if they also have approved visits. Includes ones already in R1.",
    ],
    [
        "rejected_not_r1",
        "Rejected-visit WAs, not in R1",
        "Rejected-visit work areas that are not already in the R1 opportunity.",
    ],
    [
        "total_r2",
        "Ward total R2 WAs (de-duplicated, not in R1)",
        "Every work area not in R1 that is an R2 Work-Area, belongs to a suspended worker, is approved "
        "inaccessible, or has a rejected visit, each counted once.",
    ],
    [
        "evc_ratio",
        "Ward EVC per building (main opp)",
        "The ward’s total Expected Visit Count divided by its total building count, over the original "
        "opportunity’s work areas (Excluded ones left out). Organisation and overall rows divide their "
        "own totals.",
    ],
    [
        "evc_r2",
        "Expected visits for R2 WAs",
        "The new Expected Visit Count of the ward’s R2 work areas (the ward-total column), added up. Each "
        "work area’s is its building count × the ward’s EVC per building, rounded. The Download CSV tab "
        "writes the same per-work-area value.",
    ],
]

DEFINITION = {
    "name": "R2 Revisit Analysis",
    "description": "Per organisation and ward: work areas in the original and R1 revisit opportunities, "
    "and the work areas a second revisit round should cover, as a table and on a map.",
    "version": 1,
    "templateType": "r2_revisit_analysis",
    "statuses": [{"id": "active", "label": "Active", "color": "green"}],
    "config": {
        "auth_requires": ["connect"],
        "noPipelineStream": True,
        "renderWhileLoading": True,
        "showFilters": False,
        "showSummaryCards": False,
        # Each organisation's original and R1 opportunity: [{org, main, r1}].
        "orgs": [],
        "rows_limit": 20000,
        "concurrency": 3,
        # R1's thresholds: a work area is NOT an R2 Work-Area when HSD visits /
        # EVC, MUAC / HSD or deworming / HSD is over these, or it had an NCF
        # visit with at most `ncf_max_bld` buildings.
        "algo": {"hsd_evc": 0.4, "muac": 0.7, "dw": 0.4, "ncf_max_bld": 1},
        # [kind, alias, label, read from the R1 opportunity (1) or the main one (0)]
        "sources": [
            ["workers", "workers", "Workers (suspended)", 0],
            ["rejected", "rejected", "Rejected visits", 0],
            ["visits", "visits", "Visit rollup (HSD/MUAC/DW/NCF)", 0],
            ["wa_main", "work_areas", "Work areas (main opp)", 0],
            ["wa_r1", "work_areas", "Work areas (R1 opp)", 1],
        ],
        "columns": COLUMNS,
        "text": {
            "intro": "Work areas missed in R1 that are likely to have children and that workers can visit "
            "again, by organisation and ward. Click the ⓘ next to a column for what it counts, or the map "
            "icon next to a ward to see it on the Map tab. The Download CSV tab saves an organisation’s R2 "
            "work areas in Connect’s work-area upload format. “Not in R1” means the work area is not already "
            "in the R1 revisit opportunity (matched on its exact location).",
            "reload": "Reload, reusing data Labs fetched in the last hour",
            "hard": "Ignore cached data and download everything again (takes several minutes)",
            "r1tag": "This ward has work areas in the R1 revisit opportunity",
            "notes": [
                "Rows tinted amber with an R1 tag are wards that have work areas in the R1 revisit " "opportunity.",
                "R2 Work-Area thresholds (40% of EVC, 70% MUAC, 40% deworming, 1 building) are in the "
                "workflow settings (config.algo). They are not applied in wards with R1 work areas; the "
                "Download CSV tab can treat a ward as not done in R1.",
                "Not in R1: the work area’s exact location does not appear in the R1 opportunity (R1 work "
                "areas were uploaded with the original boundary unchanged).",
            ],
        },
    },
    "pipeline_sources": [],
}

# ---------------------------------------------------------------------------
# Pipeline schemas (Connect exports and visits; nothing from CommCare HQ)
# ---------------------------------------------------------------------------

WORK_AREAS_SCHEMA = {
    "data_source": {"type": "connect_export", "endpoint": "work_areas"},
    "grouping_key": "entity_id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "wa_case_id", "path": "work_area.case_id", "aggregation": "first"},
        {"name": "ward", "path": "work_area.ward", "aggregation": "first"},
        {"name": "wa_status", "path": "work_area.status", "aggregation": "first"},
        {"name": "owner", "path": "work_area.username", "aggregation": "first"},
        {"name": "centroid", "path": "work_area.centroid", "aggregation": "first"},
        {"name": "evc", "path": "work_area.expected_visit_count", "aggregation": "first"},
        {"name": "bld", "path": "work_area.building_count", "aggregation": "first"},
    ],
}

WORKERS_SCHEMA = {
    "data_source": {"type": "connect_export", "endpoint": "user_data"},
    "grouping_key": "entity_id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "uname", "path": "user_data.username", "aggregation": "first"},
        {"name": "worker_name", "path": "user_data.name", "aggregation": "first"},
        {"name": "suspended", "path": "user_data.suspended", "aggregation": "first"},
    ],
}

_WA_CASE_ID = {
    "name": "wa_case_id",
    "paths": ["form.work_area_info.wa_caseid", "form.wa_case_id"],
    "aggregation": "first",
}

REJECTED_SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "linking_field": "wa_case_id",
    "terminal_stage": "entity",
    "filters": {"status": ["rejected"]},
    "fields": [
        _WA_CASE_ID,
        {"name": "rejected_count", "path": "form.meta.instanceID", "aggregation": "count"},
    ],
}

_HSD = "Health Service Delivery"

VISITS_SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "linking_field": "wa_case_id",
    "terminal_stage": "entity",
    # Every review status except duplicate/trial, as WA Revisit counts them.
    "filters": {"status": ["approved", "pending", "rejected", "over_limit"]},
    "fields": [
        _WA_CASE_ID,
        {
            "name": "hsd",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": _HSD,
        },
        {
            "name": "ncf",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": "No Children Found",
        },
        {
            "name": "muac",
            "path": "form.case.update.soliciter_muac_cm",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": _HSD,
        },
        {
            "name": "dw",
            "path": "form.case.update.dw_meds_delivery_status",
            "aggregation": "count",
            "filter_path": "form.case.update.dw_meds_delivery_status",
            "filter_value": "DW Delivered",
        },
    ],
}

WA_SHAPES_SCHEMA = {
    "data_source": {"type": "connect_export", "endpoint": "work_areas"},
    "grouping_key": "entity_id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "wa_case_id", "path": "work_area.case_id", "aggregation": "first"},
        {"name": "ward", "path": "work_area.ward", "aggregation": "first"},
        {"name": "slug", "path": "work_area.slug", "aggregation": "first"},
        {"name": "boundary", "path": "work_area.boundary", "aggregation": "first"},
        {"name": "centroid", "path": "work_area.centroid", "aggregation": "first"},
        {"name": "lga", "path": "work_area.case_properties.lga", "aggregation": "first"},
        {"name": "state", "path": "work_area.case_properties.state", "aggregation": "first"},
        {"name": "grp", "path": "work_area.work_area_group_name", "aggregation": "first"},
    ],
}

WARD_BOUNDS_SCHEMA = {
    "data_source": {"type": "connect_export", "endpoint": "implementation_areas"},
    "grouping_key": "entity_id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "name", "path": "implementation_area.name", "aggregation": "first"},
        {"name": "boundary", "path": "implementation_area.boundary", "aggregation": "first"},
    ],
}

PIPELINE_SCHEMAS = [
    {
        "alias": "work_areas",
        "name": "R2 Revisit Analysis — Work Areas (Connect)",
        "description": "One row per work area: ward, status, assigned worker, centroid, EVC, buildings",
        "schema": WORK_AREAS_SCHEMA,
    },
    {
        "alias": "workers",
        "name": "R2 Revisit Analysis — Workers (Connect)",
        "description": "One row per worker, with the suspended flag",
        "schema": WORKERS_SCHEMA,
    },
    {
        "alias": "rejected",
        "name": "R2 Revisit Analysis — Rejected Visit Rollup (Connect)",
        "description": "One row per work area with at least one rejected visit",
        "schema": REJECTED_SCHEMA,
    },
    {
        "alias": "visits",
        "name": "R2 Revisit Analysis — Visit Rollup per WA (Connect)",
        "description": "One row per work area: HSD, NCF, MUAC and deworming counts",
        "schema": VISITS_SCHEMA,
    },
    {
        "alias": "wa_shapes",
        "name": "R2 Revisit Analysis — Work-area shapes (Connect)",
        "description": "One row per work area with its boundary, centroid, LGA/State and group; read on demand "
        "by the Map and Download CSV tabs",
        "schema": WA_SHAPES_SCHEMA,
    },
    {
        "alias": "ward_bounds",
        "name": "R2 Revisit Analysis — Ward boundaries (Connect)",
        "description": "One row per implementation area (ward) with its boundary; read on demand by the Map tab",
        "schema": WARD_BOUNDS_SCHEMA,
    },
]

RENDER_CODE = (Path(__file__).parent / "r2_revisit_analysis_render.js").read_text(encoding="utf-8")

TEMPLATE = {
    "key": "r2_revisit_analysis",
    "name": "R2 Revisit Analysis",
    "description": "Programme report for a second revisit round: per organisation and ward, the work areas "
    "R1 missed (R1 thresholds, suspended workers, inaccessible, rejected visits), as a table and on a map.",
    "icon": "fa-map-location-dot",
    "color": "indigo",
    "multi_opp": True,
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
