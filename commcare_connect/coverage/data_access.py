"""
Data access layer for fetching DUs from CommCare.

Note: User visits should be fetched via AnalysisPipeline.fetch_raw_visits()
which uses efficient CSV caching (same pathway as UI views).
"""

import logging

import httpx
from django.conf import settings

from commcare_connect.coverage.models import FLW, CoverageData, DeliveryUnit, ServiceArea

logger = logging.getLogger(__name__)


class CoverageDataAccess:
    """Fetch DUs from CommCare. User visits are fetched via AnalysisPipeline."""

    def __init__(self, request):
        self.request = request
        self.access_token = request.session.get("labs_oauth", {}).get("access_token")
        self.opportunity_id = getattr(request, "labs_context", {}).get("opportunity_id")

        # Get CommCare OAuth token from session
        self.commcare_oauth = request.session.get("commcare_oauth", {})
        self.commcare_access_token = self.commcare_oauth.get("access_token")
        self.commcare_domain = None
        self.commcare_hq_url = getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org")

        # Debug logging
        if not self.commcare_access_token:
            logger.warning("[Coverage] No CommCare OAuth token in session")

    def get_opportunity_metadata(self) -> dict:
        """Fetch opportunity metadata from Connect export API"""
        if not self.access_token:
            raise ValueError("No OAuth access token found. Please log in at /labs/login/")

        url = f"{settings.CONNECT_PRODUCTION_URL}/export/opportunity/{self.opportunity_id}/"

        try:
            response = httpx.get(url, headers={"Authorization": f"Bearer {self.access_token}"}, timeout=30.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ValueError(
                    f"Opportunity {self.opportunity_id} not found or you don't have access to it. "
                    f"Please verify the opportunity ID and your permissions."
                )
            raise

        opp_data = response.json()

        # Extract deliver_app domain
        deliver_app = opp_data.get("deliver_app")
        if deliver_app:
            self.commcare_domain = deliver_app.get("cc_domain")

        if not self.commcare_domain:
            raise ValueError("CommCare domain not found in opportunity data")

        if not self.commcare_access_token:
            raise ValueError(
                "CommCare OAuth not configured. Please authorize CommCare access at /labs/commcare/initiate/"
            )

        # Check if token is expired
        from django.utils import timezone

        expires_at = self.commcare_oauth.get("expires_at", 0)
        if timezone.now().timestamp() >= expires_at:
            logger.warning(f"CommCare OAuth token expired (expired at {expires_at})")
            raise ValueError("CommCare OAuth token has expired. Please re-authorize at /labs/commcare/initiate/")

        return opp_data

    def fetch_delivery_units_from_commcare(self) -> list[dict]:
        """Fetch DU cases from CommCare Case API v2 using OAuth"""
        endpoint = f"{self.commcare_hq_url}/a/{self.commcare_domain}/api/case/v2/"

        headers = {
            "Authorization": f"Bearer {self.commcare_access_token}",
            "Content-Type": "application/json",
        }

        params = {"case_type": "deliver-unit", "limit": 1000}

        all_cases = []
        next_url = endpoint

        # Paginate through results
        page = 0
        try:
            while next_url:
                page += 1
                response = httpx.get(
                    next_url, params=params if next_url == endpoint else None, headers=headers, timeout=60.0
                )
                response.raise_for_status()

                data = response.json()
                cases = data.get("cases", [])
                all_cases.extend(cases)

                next_url = data.get("next")
                params = None  # Don't send params for next page URLs
        except httpx.HTTPStatusError as e:
            logger.error(f"[Coverage] HTTP {e.response.status_code} fetching DUs from CommCare: {e}")
            return all_cases
        except httpx.RequestError as e:
            logger.error(f"[Coverage] Request error fetching DUs from CommCare: {e}")
            return all_cases

        logger.info(f"[Coverage] Fetched {len(all_cases)} DUs from CommCare ({page} pages)")
        return all_cases

    def build_coverage_dus_only(self) -> CoverageData:
        """
        Build CoverageData with DUs only (no visits).

        Visits should be fetched separately via the analysis framework
        for consistent caching behavior.
        """
        from commcare_connect.coverage.field_mappings import get_unmapped_properties

        coverage = CoverageData()

        # Get opportunity metadata
        opp_data = self.get_opportunity_metadata()
        coverage.opportunity_id = self.opportunity_id
        coverage.opportunity_name = opp_data.get("name")
        coverage.commcare_domain = self.commcare_domain

        logger.info(f"[Coverage] Building DU data for: {coverage.opportunity_name} (opp {self.opportunity_id})")

        # Fetch DUs from CommCare
        du_cases = self.fetch_delivery_units_from_commcare()

        # Track unmapped properties across all DUs
        all_unmapped_properties = set()

        for case_data in du_cases:
            try:
                du = DeliveryUnit.from_commcare_case(case_data)
                coverage.delivery_units[du.du_name] = du

                # Track unmapped properties for debugging
                properties = case_data.get("properties", {})
                unmapped = get_unmapped_properties(properties)
                all_unmapped_properties.update(unmapped)

                # Group by service area
                sa_id = du.service_area_id
                if sa_id and sa_id not in coverage.service_areas:
                    coverage.service_areas[sa_id] = ServiceArea(id=sa_id)
                if sa_id:
                    coverage.service_areas[sa_id].delivery_units.append(du)

                # Track FLWs from DU data (only have CommCare ID at this point)
                if du.flw_commcare_id:
                    if du.flw_commcare_id not in coverage.flws:
                        coverage.flws[du.flw_commcare_id] = FLW(commcare_id=du.flw_commcare_id)

                    flw = coverage.flws[du.flw_commcare_id]
                    flw.assigned_units += 1
                    if du.status == "completed":
                        flw.completed_units += 1
                    if sa_id and sa_id not in flw.service_areas:
                        flw.service_areas.append(sa_id)
                    flw.delivery_units.append(du)

            except Exception as e:
                logger.warning(f"Failed to process delivery unit {case_data.get('case_id')}: {e}")
                continue

        # Populate SA metadata from DUs
        for sa in coverage.service_areas.values():
            sa.aggregate_metadata_from_dus()

        # Log unmapped properties for future schema updates
        if all_unmapped_properties:
            logger.info(
                f"[Coverage] Found {len(all_unmapped_properties)} unmapped properties: "
                f"{sorted(all_unmapped_properties)}"
            )

        logger.info(
            f"[Coverage] DU data complete: {len(coverage.delivery_units)} DUs, " f"{len(coverage.service_areas)} SAs"
        )
        if coverage.service_areas:
            sample_sas = list(coverage.service_areas.keys())[:5]
            logger.info(f"[Coverage] Sample service area IDs: {sample_sas}")
            # Log sample SA metadata
            if sample_sas:
                first_sa = coverage.service_areas[sample_sas[0]]
                logger.info(
                    f"[Coverage] Sample SA metadata - ID: {first_sa.id}, Name: {first_sa.name}, "
                    f"Unlock Order: {first_sa.unlock_order}, Ward: {first_sa.ward_name}"
                )
        else:
            logger.warning("[Coverage] No service areas found! Check if DUs have service_area_id set.")

        return coverage
