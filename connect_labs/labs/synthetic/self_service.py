"""Self-service synthetic data generation.

Given a user + opportunity, automatically:
  1. Fetches the app structure from Connect (via /export/opportunity/<id>/app_structure/)
  2. Parses the deliver form schema
  3. Builds a default Manifest with 4 FLW personas and sensible field distributions
  4. Runs the existing generator
  5. Saves fixtures to UserSyntheticDataset (expires in 24 hours)
"""

from __future__ import annotations

import datetime
import logging
import random
from typing import Any

import httpx
from django.conf import settings
from django.utils import timezone

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labs.integrations.connect.export_client import ExportAPIClient
from connect_labs.labs.synthetic.generator.fixtures.engine import generate
from connect_labs.labs.synthetic.generator.fixtures.manifest import (
    BeneficiaryCohort,
    FlwPersona,
    KpiSpec,
    Manifest,
    MeanStddev,
    NormalDistribution,
    Timeline,
)
from connect_labs.labs.synthetic.generator.fixtures.schema_loader import FormSchema, parse_form_schema_from_app_json
from connect_labs.labs.synthetic.models import UserSyntheticDataset

logger = logging.getLogger(__name__)

_APP_STRUCTURE_TIMEOUT = 120.0
_EXPIRY_HOURS = 24

_DEFAULT_PERSONAS: list[dict] = [
    {
        "id": "flw_rockstar",
        "display_name": "Top performer",
        "archetype": "rockstar",
        "accuracy_distribution": {"mean": 0.93, "stddev": 0.04},
        "completeness_distribution": {"mean": 0.95, "stddev": 0.03},
        "flag_rate": 0.03,
    },
    {
        "id": "flw_steady_a",
        "display_name": "Steady worker A",
        "archetype": "steady",
        "accuracy_distribution": {"mean": 0.78, "stddev": 0.07},
        "completeness_distribution": {"mean": 0.80, "stddev": 0.06},
        "flag_rate": 0.10,
    },
    {
        "id": "flw_steady_b",
        "display_name": "Steady worker B",
        "archetype": "steady",
        "accuracy_distribution": {"mean": 0.75, "stddev": 0.08},
        "completeness_distribution": {"mean": 0.77, "stddev": 0.07},
        "flag_rate": 0.12,
    },
    {
        "id": "flw_struggling",
        "display_name": "Needs support",
        "archetype": "struggling",
        "accuracy_distribution": {"mean": 0.55, "stddev": 0.12},
        "completeness_distribution": {"mean": 0.58, "stddev": 0.10},
        "flag_rate": 0.28,
    },
    {
        "id": "flw_new_hire",
        "display_name": "New hire",
        "archetype": "new_hire",
        "accuracy_distribution": {"mean": 0.65, "stddev": 0.10},
        "completeness_distribution": {"mean": 0.68, "stddev": 0.09},
        "flag_rate": 0.18,
    },
]


class SyntheticGenerationError(Exception):
    pass


def _fetch_app_structure(opportunity_id: int, access_token: str) -> dict[str, Any]:
    client = LabsRecordAPIClient(access_token=access_token)
    try:
        url = f"{client.base_url}/export/opportunity/{opportunity_id}/app_structure/"
        resp = client.http_client.get(url, params={"app_type": "deliver"}, timeout=_APP_STRUCTURE_TIMEOUT)
        if resp.status_code == 404:
            raise SyntheticGenerationError(f"Opportunity {opportunity_id} not found or no app linked.")
        if resp.status_code >= 400:
            raise SyntheticGenerationError(f"Could not fetch app structure from Connect ({resp.status_code}).")
        return resp.json()
    except httpx.RequestError as e:
        raise SyntheticGenerationError(f"Network error fetching app structure: {e}") from e
    finally:
        client.close()


def _fetch_opportunity_detail(opportunity_id: int, access_token: str) -> dict[str, Any]:
    client = LabsRecordAPIClient(access_token=access_token)
    try:
        url = f"{client.base_url}/export/opportunity/{opportunity_id}/"
        resp = client.http_client.get(url, timeout=30.0)
        if resp.status_code >= 400:
            return {"id": opportunity_id, "name": f"Opportunity {opportunity_id}"}
        return resp.json()
    except httpx.RequestError:
        return {"id": opportunity_id, "name": f"Opportunity {opportunity_id}"}
    finally:
        client.close()


def _build_field_distributions(schema: FormSchema) -> dict:
    """Build sensible field distributions from the form schema's question types."""
    distributions = {}
    for q in schema.questions:
        if q.kind in ("decimal", "int"):
            distributions[q.json_path] = NormalDistribution(mean=50.0, stddev=15.0)
    return distributions


def _build_default_kpi(schema: FormSchema) -> KpiSpec:
    """Pick the first numeric field as a default KPI, fall back to a count."""
    for q in schema.questions:
        if q.kind in ("decimal", "int"):
            return KpiSpec(
                kpi="data_quality",
                field_path=q.json_path,
                aggregation="non_null_rate",
                threshold_underperform=0.6,
                threshold_target=0.85,
            )
    return KpiSpec(
        kpi="visit_count",
        field_path="id",
        aggregation="count",
        threshold_underperform=10,
        threshold_target=20,
    )


def _build_manifest(
    opportunity_id: int,
    opportunity_name: str,
    visit_count: int,
    schema: FormSchema,
) -> Manifest:
    n_flws = len(_DEFAULT_PERSONAS)
    visits_per_flw_per_week = 5
    total_weeks = max(4, round(visit_count / (n_flws * visits_per_flw_per_week)))

    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(weeks=total_weeks)
    # Adjust end_date so it's exactly total_weeks*7 days after start
    end_date = start_date + datetime.timedelta(weeks=total_weeks)

    personas = [FlwPersona(**p) for p in _DEFAULT_PERSONAS]
    field_distributions = _build_field_distributions(schema)

    cohort = BeneficiaryCohort(
        id="primary_cohort",
        size=max(20, visit_count // n_flws),
        field_distributions=field_distributions,
        progression="flat",
    )

    return Manifest(
        opportunity_id=opportunity_id,
        opportunity_name=opportunity_name,
        random_seed=random.randint(0, 2**31),
        timeline=Timeline(
            start_date=start_date,
            end_date=end_date,
            weeks=total_weeks,
            visit_cadence_per_week_per_flw=MeanStddev(mean=float(visits_per_flw_per_week), stddev=0.2),
        ),
        flw_personas=personas,
        beneficiary_cohorts=[cohort],
        kpi_config=[_build_default_kpi(schema)],
    )


def _fetch_real_form_jsons(opportunity_id: int, access_token: str, max_samples: int = 150) -> list[dict]:
    """Fetch a sample of real visit form_jsons from Connect to use as templates.

    Returns an empty list on any error — callers fall back to schema-generated values.
    """
    try:
        with ExportAPIClient(settings.CONNECT_PRODUCTION_URL, access_token, timeout=30.0) as client:
            for page in client.paginate(
                f"/export/opportunity/{opportunity_id}/user_visits/",
                params={"page_size": max_samples},
            ):
                result = [v["form_json"] for v in page if isinstance(v.get("form_json"), dict)]
                return result[:max_samples]
    except Exception:
        logger.debug("Could not fetch real form_jsons for opp %s; using generated values", opportunity_id)
    return []


def _overlay_form_jsons(visits: list[dict], real_form_jsons: list[dict], rng: random.Random) -> None:
    """Replace each synthetic visit's form_json with a randomly sampled real one.

    Preserves form.meta so userID / timeEnd from the real visit don't leak in.
    The visit-level username / visit_date fields (outside form_json) are kept
    exactly as the generator set them — only the payload is swapped.
    """
    if not real_form_jsons:
        return
    for visit in visits:
        real_fj = rng.choice(real_form_jsons)
        visit["form_json"] = real_fj


def generate_and_save(
    *,
    user,
    opportunity_id: int,
    visit_count: int,
    access_token: str,
) -> UserSyntheticDataset:
    """Full pipeline: fetch schema → build manifest → generate → save to DB.

    Replaces any existing dataset for this user+opportunity.
    Raises SyntheticGenerationError on upstream failures.
    """
    app_json = _fetch_app_structure(opportunity_id, access_token)
    schema = parse_form_schema_from_app_json(app_json, app_type="deliver")

    opp_detail = _fetch_opportunity_detail(opportunity_id, access_token)
    opp_name = opp_detail.get("name") or f"Opportunity {opportunity_id}"

    manifest = _build_manifest(opportunity_id, opp_name, visit_count, schema)
    fixtures = generate(manifest=manifest, opportunity_detail=opp_detail, form_schema=schema)

    # Overlay real form_jsons so field values are always realistic (correct types,
    # choices, case-update blocks, etc.) regardless of how the app schema is defined.
    real_form_jsons = _fetch_real_form_jsons(opportunity_id, access_token)
    rng = random.Random(manifest.random_seed)
    _overlay_form_jsons(fixtures.get("user_visits", []), real_form_jsons, rng)

    expires_at = timezone.now() + datetime.timedelta(hours=_EXPIRY_HOURS)

    dataset, _ = UserSyntheticDataset.objects.update_or_create(
        user=user,
        opportunity_id=opportunity_id,
        defaults={
            "visit_count": len(fixtures.get("user_visits", [])),
            "fixtures": fixtures,
            "expires_at": expires_at,
        },
    )
    logger.info(
        "Generated synthetic dataset for user=%s opp=%s visits=%s expires=%s",
        user.pk,
        opportunity_id,
        dataset.visit_count,
        expires_at.isoformat(),
    )
    return dataset
