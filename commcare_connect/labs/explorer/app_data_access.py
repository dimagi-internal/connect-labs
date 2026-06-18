"""
Data Access Layer for App Downloader.

This layer fetches opportunity and app data from the Connect production
Data Export APIs. It does NOT use local database access.
"""

import datetime
import logging
import zipfile
from io import BytesIO

import httpx
from django.conf import settings
from django.http import HttpRequest

logger = logging.getLogger(__name__)


class AppDownloaderDataAccess:
    """
    Data access layer for downloading CommCare apps.

    Fetches opportunity data from Connect production APIs and
    downloads CCZ files from CommCare HQ.
    """

    def __init__(
        self,
        request: HttpRequest | None = None,
        access_token: str | None = None,
    ):
        """
        Initialize the app downloader data access layer.

        Args:
            request: HttpRequest object (for extracting OAuth token from session)
            access_token: OAuth token for Connect production APIs (optional if request provided)
        """
        self.request = request

        # Get OAuth token from session if not provided
        if not access_token and request:
            if hasattr(request, "session") and "labs_oauth" in request.session:
                access_token = request.session["labs_oauth"].get("access_token")

        if not access_token:
            raise ValueError("OAuth access token required for app downloader")

        self.access_token = access_token
        self.production_url = settings.CONNECT_PRODUCTION_URL.rstrip("/")

        # Initialize HTTP client with Bearer token for Connect API
        self.http_client = httpx.Client(
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=120.0,
        )

    def close(self):
        """Close HTTP client."""
        if self.http_client:
            self.http_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _call_connect_api(self, endpoint: str) -> httpx.Response:
        """Call Connect production API with OAuth token."""
        url = f"{self.production_url}{endpoint}"
        response = self.http_client.get(url)
        response.raise_for_status()
        return response

    def get_active_opportunities(self) -> list[dict]:
        """
        Fetch active opportunities from the data export API.

        Returns opportunities where is_active=True and end_date >= today,
        enriched with organization and program names.

        Returns:
            List of opportunity dicts with organization_name and program_name added
        """
        response = self._call_connect_api("/export/opp_org_program_list/")
        data = response.json()

        # Build lookup maps for orgs and programs
        orgs = {o["slug"]: o["name"] for o in data.get("organizations", [])}
        programs = {p["id"]: p["name"] for p in data.get("programs", [])}

        # Filter for active opportunities
        today = datetime.date.today().isoformat()
        active = []

        for opp in data.get("opportunities", []):
            # Check is_active flag and end_date
            if not opp.get("is_active"):
                continue
            end_date = opp.get("end_date", "")
            if not end_date or end_date < today:
                continue

            # Enrich with org/program names
            opp["organization_name"] = orgs.get(opp.get("organization"), opp.get("organization", ""))
            if opp.get("program"):
                opp["program_name"] = programs.get(opp["program"], "")
            else:
                opp["program_name"] = ""

            active.append(opp)

        # Sort by name
        active.sort(key=lambda x: x.get("name", "").lower())

        logger.info(f"Found {len(active)} active opportunities")
        return active

    def get_opportunity_details(self, opp_id: int) -> dict | None:
        """
        Fetch detailed opportunity info including app details.

        Args:
            opp_id: Opportunity ID

        Returns:
            Opportunity dict with learn_app and deliver_app details, or None
        """
        # Delegate to the single canonical owner of GET /export/opportunity/{id}/
        # (see labs.analysis.data_access) so this fetch isn't re-implemented here.
        from commcare_connect.labs.analysis.data_access import fetch_opportunity_detail

        try:
            return fetch_opportunity_detail(self.access_token, opp_id)
        except ValueError as e:
            logger.error(f"Failed to fetch opportunity {opp_id}: {e}")
            return None

    def download_ccz(self, hq_server_url: str, domain: str, app_id: str) -> bytes | None:
        """
        Download CCZ file from CommCare HQ.

        Tries release, build, then save versions in order.

        Args:
            hq_server_url: Base URL of CommCare HQ server (e.g., https://www.commcarehq.org)
            domain: CommCare domain name
            app_id: CommCare app ID

        Returns:
            CCZ file bytes or None if download failed
        """
        # Use a separate client without the Bearer token for CommCare HQ
        for latest in ["release", "build", "save"]:
            url = f"{hq_server_url}/a/{domain}/apps/api/download_ccz/"
            params = {"app_id": app_id, "latest": latest}

            try:
                logger.info(f"Attempting CCZ download: {url} with latest={latest}")
                response = httpx.get(url, params=params, timeout=120)

                if response.is_success:
                    logger.info(f"Successfully downloaded CCZ for app {app_id} (latest={latest})")
                    return response.content
                else:
                    logger.warning(f"CCZ download failed with status {response.status_code} for latest={latest}")
            except httpx.TimeoutException:
                logger.warning(f"CCZ download timed out for app {app_id} with latest={latest}")
            except Exception as e:
                logger.error(f"CCZ download error for app {app_id}: {e}")

        logger.error(f"All CCZ download attempts failed for app {app_id}")
        return None

    def download_apps_as_zip(
        self,
        opportunities: list[dict],
        app_type: str,  # "learn" or "deliver"
    ) -> tuple[BytesIO, list[str]]:
        """
        Download multiple apps and package them in a ZIP file.

        Args:
            opportunities: List of opportunity dicts (from get_active_opportunities)
            app_type: "learn" or "deliver"

        Returns:
            Tuple of (BytesIO containing ZIP, list of error messages)
        """
        errors = []
        zip_buffer = BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for opp in opportunities:
                opp_id = opp.get("id")
                opp_name = opp.get("name", f"opp_{opp_id}")

                # Fetch full opportunity details to get app info
                details = self.get_opportunity_details(opp_id)
                if not details:
                    errors.append(f"Could not fetch details for opportunity: {opp_name}")
                    continue

                # Get the appropriate app
                app_key = "learn_app" if app_type == "learn" else "deliver_app"
                app_data = details.get(app_key)

                if not app_data:
                    errors.append(f"No {app_type} app configured for: {opp_name}")
                    continue

                # Extract app details
                domain = app_data.get("cc_domain")
                cc_app_id = app_data.get("cc_app_id")
                app_name = app_data.get("name", f"{app_type}_app")

                # Get HQ server URL - default to commcarehq.org if not specified
                hq_server_url = "https://www.commcarehq.org"
                if app_data.get("hq_server"):
                    hq_server_url = app_data["hq_server"].get("url", hq_server_url)

                if not domain or not cc_app_id:
                    errors.append(f"Missing domain or app_id for {app_type} app in: {opp_name}")
                    continue

                # Download the CCZ
                ccz_content = self.download_ccz(hq_server_url, domain, cc_app_id)

                if ccz_content:
                    # Create a safe filename
                    safe_opp_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in opp_name)
                    safe_app_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in app_name)
                    filename = f"{safe_opp_name}/{safe_app_name}.ccz"

                    zf.writestr(filename, ccz_content)
                    logger.info(f"Added {filename} to ZIP")
                else:
                    errors.append(f"Failed to download {app_type} app CCZ for: {opp_name}")

        zip_buffer.seek(0)
        return zip_buffer, errors
