"""
Service layer for loading admin boundaries from multiple sources.

Supported sources:
- geoBoundaries (https://www.geoboundaries.org/) - CC BY 4.0
- OpenStreetMap (https://www.openstreetmap.org/) - ODbL
- GRID3 (https://grid3.org/) - varies by country
- HDX/OCHA COD (https://data.humdata.org/) - varies by dataset
"""

import io
import json
import logging
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.core.files.uploadedfile import UploadedFile

from commcare_connect.labs.admin_boundaries.models import (
    AdminBoundary,
    AdminBoundarySourceConfig,
    AdminBoundaryStaticLoadRecord,
)

logger = logging.getLogger(__name__)

# Path to the fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@dataclass
class LoadResult:
    """Result of a boundary loading operation."""

    iso_code: str
    level: int
    success: bool
    count: int = 0
    message: str = ""
    error: str = ""


@dataclass
class CountryLoadResult:
    """Result of loading all levels for a country."""

    iso_code: str
    source: str = ""
    total_loaded: int = 0
    levels: list[LoadResult] = field(default_factory=list)
    cleared: int = 0

    @property
    def success(self) -> bool:
        return any(r.success for r in self.levels)


class GeoBoundariesLoader:
    """
    Service for loading administrative boundaries from geoBoundaries API.

    Usage:
        loader = GeoBoundariesLoader()

        # Load with progress callback
        def on_progress(msg):
            print(msg)

        result = loader.load_country("KEN", levels=[0, 1, 2], on_progress=on_progress)
        print(f"Loaded {result.total_loaded} boundaries")
    """

    GEOBOUNDARIES_API = "https://www.geoboundaries.org/api/current/gbOpen"
    DEFAULT_LEVELS = [0, 1, 2]  # Country, State/Province, District
    MAX_LEVEL = 5  # geoBoundaries goes up to ADM5 for some countries
    SOURCE = AdminBoundary.Source.GEOBOUNDARIES

    def __init__(self):
        self.http_client = httpx.Client(timeout=120.0, follow_redirects=True)

    def __del__(self):
        if hasattr(self, "http_client"):
            self.http_client.close()

    def load_country(
        self,
        iso_code: str,
        levels: list[int] | None = None,
        clear: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> CountryLoadResult:
        """
        Load boundaries for a country at specified admin levels.

        Args:
            iso_code: ISO 3166-1 alpha-3 country code (e.g., "KEN")
            levels: Admin levels to load (default: [0, 1, 2])
            clear: If True, delete existing boundaries before loading
            on_progress: Optional callback for progress messages

        Returns:
            CountryLoadResult with details of what was loaded
        """
        iso_code = iso_code.upper()
        levels = levels if levels is not None else self.DEFAULT_LEVELS

        result = CountryLoadResult(iso_code=iso_code, source=self.SOURCE)

        def progress(msg: str):
            if on_progress:
                on_progress(msg)
            logger.info(f"[geoBoundaries/{iso_code}] {msg}")

        progress(f"Processing {iso_code} from geoBoundaries...")

        # Clear existing data if requested (only for this source)
        if clear:
            deleted, _ = AdminBoundary.objects.filter(iso_code=iso_code, source=self.SOURCE).delete()
            result.cleared = deleted
            progress(f"Cleared {deleted} existing geoBoundaries entries")

        # Load each level
        for level in levels:
            level_result = self.load_boundary_level(iso_code, level, on_progress=on_progress)
            result.levels.append(level_result)
            if level_result.success:
                result.total_loaded += level_result.count

        progress(f"Complete - loaded {result.total_loaded} boundaries for {iso_code}")
        return result

    def load_boundary_level(
        self,
        iso_code: str,
        level: int,
        on_progress: Callable[[str], None] | None = None,
    ) -> LoadResult:
        """Load boundaries for a specific country and admin level."""
        iso_code = iso_code.upper()

        def progress(msg: str):
            if on_progress:
                on_progress(msg)

        api_url = f"{self.GEOBOUNDARIES_API}/{iso_code}/ADM{level}/"

        try:
            # First, get the metadata to find the GeoJSON download URL
            progress(f"ADM{level}: Fetching metadata...")
            response = self.http_client.get(api_url, timeout=30.0)

            if response.status_code == 404:
                return LoadResult(
                    iso_code=iso_code,
                    level=level,
                    success=False,
                    message=f"ADM{level} not available for {iso_code}",
                )

            response.raise_for_status()
            metadata = response.json()

            # Get the simplified GeoJSON URL
            geojson_url = metadata.get("gjDownloadURL")
            if not geojson_url:
                return LoadResult(
                    iso_code=iso_code,
                    level=level,
                    success=False,
                    error="No GeoJSON URL in API response",
                )

            # Download the GeoJSON
            progress(f"ADM{level}: Downloading GeoJSON...")
            geojson_response = self.http_client.get(geojson_url)
            geojson_response.raise_for_status()
            geojson_data = geojson_response.json()

            features = geojson_data.get("features", [])
            if not features:
                return LoadResult(
                    iso_code=iso_code,
                    level=level,
                    success=False,
                    message=f"ADM{level}: No features found",
                )

            # Process and save boundaries
            progress(f"ADM{level}: Processing {len(features)} features...")
            boundaries_to_create = []
            skipped = 0

            for feature in features:
                try:
                    boundary = self._feature_to_boundary(feature, iso_code, level, geojson_url)
                    if boundary:
                        boundaries_to_create.append(boundary)
                except Exception as e:
                    shape_name = feature.get("properties", {}).get("shapeName", "unknown")
                    logger.warning(f"Skipping {shape_name}: {e}")
                    skipped += 1

            # Delete existing for this country/level/source and bulk create
            AdminBoundary.objects.filter(iso_code=iso_code, admin_level=level, source=self.SOURCE).delete()
            AdminBoundary.objects.bulk_create(boundaries_to_create, batch_size=100)

            message = f"ADM{level}: Loaded {len(boundaries_to_create)} boundaries"
            if skipped:
                message += f" (skipped {skipped})"
            progress(message)

            return LoadResult(
                iso_code=iso_code,
                level=level,
                success=True,
                count=len(boundaries_to_create),
                message=message,
            )

        except httpx.HTTPStatusError as e:
            error = f"HTTP error: {e}"
            logger.error(f"[geoBoundaries/{iso_code}] ADM{level}: {error}")
            return LoadResult(iso_code=iso_code, level=level, success=False, error=error)

        except httpx.RequestError as e:
            error = f"Request error: {e}"
            logger.error(f"[geoBoundaries/{iso_code}] ADM{level}: {error}")
            return LoadResult(iso_code=iso_code, level=level, success=False, error=error)

        except Exception as e:
            error = f"Error: {e}"
            logger.error(f"[geoBoundaries/{iso_code}] ADM{level}: {error}")
            return LoadResult(iso_code=iso_code, level=level, success=False, error=error)

    def _feature_to_boundary(self, feature: dict, iso_code: str, level: int, source_url: str) -> AdminBoundary | None:
        """Convert a GeoJSON feature to an AdminBoundary model instance."""
        properties = feature.get("properties", {})
        geometry_data = feature.get("geometry")

        if not geometry_data:
            return None

        geom = GEOSGeometry(json.dumps(geometry_data))

        # Ensure it's a MultiPolygon
        if isinstance(geom, Polygon):
            geom = MultiPolygon(geom)
        elif not isinstance(geom, MultiPolygon):
            raise ValueError(f"Unexpected geometry type: {geom.geom_type}")

        # Extract properties - geoBoundaries uses consistent field names
        shape_id = properties.get("shapeID", "")
        shape_name = properties.get("shapeName", "")

        if not shape_id:
            # Generate an ID if not present
            shape_id = f"gb-{iso_code}-ADM{level}-{shape_name}".replace(" ", "_")

        return AdminBoundary(
            iso_code=iso_code,
            admin_level=level,
            name=shape_name,
            name_local=properties.get("shapeNameLocal", ""),
            boundary_id=shape_id,
            geometry=geom,
            source=self.SOURCE,
            source_url=source_url,
        )


class OSMLoader:
    """
    Service for loading administrative boundaries from OpenStreetMap via Overpass API.

    OSM admin_level values vary by country. Common mappings:
    - admin_level 2: Country
    - admin_level 4: State/Province/Region
    - admin_level 6: County/District
    - admin_level 8: Municipality/City

    Usage:
        loader = OSMLoader()
        result = loader.load_country("KEN", levels=[0, 1, 2], on_progress=print)
    """

    OVERPASS_API = "https://overpass-api.de/api/interpreter"
    SOURCE = AdminBoundary.Source.OSM

    # Default mapping from our ADM levels to OSM admin_level
    # This can be overridden per-country
    DEFAULT_LEVEL_MAPPING = {
        0: 2,  # ADM0 (country) -> OSM admin_level 2
        1: 4,  # ADM1 (state/province) -> OSM admin_level 4
        2: 6,  # ADM2 (district/county) -> OSM admin_level 6
        3: 8,  # ADM3 (municipality) -> OSM admin_level 8
        4: 10,  # ADM4 (ward/neighborhood) -> OSM admin_level 10
    }

    # Country-specific overrides where OSM levels differ
    COUNTRY_LEVEL_MAPPING = {
        # Some countries use different OSM levels
        # Add overrides here as needed
    }

    def __init__(self):
        self.http_client = httpx.Client(timeout=300.0, follow_redirects=True)

    def __del__(self):
        if hasattr(self, "http_client"):
            self.http_client.close()

    def get_osm_level(self, iso_code: str, adm_level: int) -> int:
        """Get the OSM admin_level for a given ADM level and country."""
        if iso_code in self.COUNTRY_LEVEL_MAPPING:
            mapping = self.COUNTRY_LEVEL_MAPPING[iso_code]
            if adm_level in mapping:
                return mapping[adm_level]
        return self.DEFAULT_LEVEL_MAPPING.get(adm_level, 2 + (adm_level * 2))

    def load_country(
        self,
        iso_code: str,
        levels: list[int] | None = None,
        clear: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> CountryLoadResult:
        """
        Load boundaries for a country at specified admin levels from OSM.

        Args:
            iso_code: ISO 3166-1 alpha-3 country code (e.g., "KEN")
            levels: Admin levels to load (default: [0, 1, 2])
            clear: If True, delete existing OSM boundaries before loading
            on_progress: Optional callback for progress messages

        Returns:
            CountryLoadResult with details of what was loaded
        """
        iso_code = iso_code.upper()
        levels = levels if levels is not None else [0, 1, 2]

        result = CountryLoadResult(iso_code=iso_code, source=self.SOURCE)

        def progress(msg: str):
            if on_progress:
                on_progress(msg)
            logger.info(f"[OSM/{iso_code}] {msg}")

        progress(f"Processing {iso_code} from OpenStreetMap...")

        # Clear existing OSM data if requested
        if clear:
            deleted, _ = AdminBoundary.objects.filter(iso_code=iso_code, source=self.SOURCE).delete()
            result.cleared = deleted
            progress(f"Cleared {deleted} existing OSM entries")

        # Load each level
        for level in levels:
            level_result = self.load_boundary_level(iso_code, level, on_progress=on_progress)
            result.levels.append(level_result)
            if level_result.success:
                result.total_loaded += level_result.count

        progress(f"Complete - loaded {result.total_loaded} boundaries for {iso_code}")
        return result

    def load_boundary_level(
        self,
        iso_code: str,
        level: int,
        on_progress: Callable[[str], None] | None = None,
    ) -> LoadResult:
        """Load boundaries for a specific country and admin level from OSM."""
        iso_code = iso_code.upper()
        osm_level = self.get_osm_level(iso_code, level)

        def progress(msg: str):
            if on_progress:
                on_progress(msg)

        progress(f"ADM{level}: Querying OSM (admin_level={osm_level})...")

        # Build Overpass query
        # We use ISO3166-1:alpha3 tag to find the country area, then query admin boundaries within it
        query = f"""
        [out:json][timeout:180];
        area["ISO3166-1:alpha3"="{iso_code}"]->.country;
        (
          relation["boundary"="administrative"]["admin_level"="{osm_level}"](area.country);
        );
        out geom;
        """

        # Retry logic for flaky Overpass API
        max_retries = 3
        retry_delay = 5  # seconds

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    import time

                    progress(f"ADM{level}: Retry {attempt}/{max_retries - 1} after {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff

                response = self.http_client.post(
                    self.OVERPASS_API,
                    data={"data": query},
                    timeout=180.0,
                )
                response.raise_for_status()
                data = response.json()
                break  # Success, exit retry loop

            except (httpx.RequestError, httpx.TimeoutException) as e:
                logger.warning(f"[OSM/{iso_code}] ADM{level}: Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    # Last attempt failed
                    if isinstance(e, httpx.TimeoutException):
                        error = "Request timed out after retries - OSM query may be too large"
                    else:
                        error = f"Request error after {max_retries} retries: {e}"
                    logger.error(f"[OSM/{iso_code}] ADM{level}: {error}")
                    return LoadResult(iso_code=iso_code, level=level, success=False, error=error)
                continue

        # Process the response
        try:
            elements = data.get("elements", [])
            if not elements:
                return LoadResult(
                    iso_code=iso_code,
                    level=level,
                    success=False,
                    message=f"ADM{level}: No OSM boundaries found (admin_level={osm_level})",
                )

            progress(f"ADM{level}: Processing {len(elements)} OSM relations...")
            boundaries_to_create = []
            skipped = 0

            for element in elements:
                try:
                    boundary = self._element_to_boundary(element, iso_code, level)
                    if boundary:
                        boundaries_to_create.append(boundary)
                except Exception as e:
                    name = element.get("tags", {}).get("name", f"relation/{element.get('id', 'unknown')}")
                    logger.warning(f"Skipping {name}: {e}")
                    skipped += 1

            # Delete existing for this country/level/source and bulk create
            AdminBoundary.objects.filter(iso_code=iso_code, admin_level=level, source=self.SOURCE).delete()
            AdminBoundary.objects.bulk_create(boundaries_to_create, batch_size=100)

            message = f"ADM{level}: Loaded {len(boundaries_to_create)} boundaries from OSM"
            if skipped:
                message += f" (skipped {skipped})"
            progress(message)

            return LoadResult(
                iso_code=iso_code,
                level=level,
                success=True,
                count=len(boundaries_to_create),
                message=message,
            )

        except Exception as e:
            error = f"Error processing OSM data: {e}"
            logger.error(f"[OSM/{iso_code}] ADM{level}: {error}", exc_info=True)
            return LoadResult(iso_code=iso_code, level=level, success=False, error=error)

    def _element_to_boundary(self, element: dict, iso_code: str, level: int) -> AdminBoundary | None:
        """Convert an OSM relation element to an AdminBoundary model instance."""
        if element.get("type") != "relation":
            return None

        tags = element.get("tags", {})
        osm_id = element.get("id")

        if not osm_id:
            return None

        # Build geometry from members
        geometry = self._build_geometry(element)
        if geometry is None:
            return None

        # Extract name
        name = tags.get("name", tags.get("name:en", f"OSM-{osm_id}"))
        name_local = tags.get("name:local", tags.get("official_name", ""))

        return AdminBoundary(
            iso_code=iso_code,
            admin_level=level,
            name=name,
            name_local=name_local,
            boundary_id=f"osm-{osm_id}",
            geometry=geometry,
            source=self.SOURCE,
            source_url=f"https://www.openstreetmap.org/relation/{osm_id}",
        )

    def _build_geometry(self, element: dict) -> MultiPolygon | None:
        """Build a MultiPolygon geometry from OSM relation members."""
        # When using 'out geom', the geometry is included directly
        members = element.get("members", [])

        # Collect all outer and inner ways
        outer_rings = []
        inner_rings = []

        for member in members:
            role = member.get("role", "")
            geom = member.get("geometry", [])

            if not geom:
                continue

            # Convert geometry points to coordinate list
            coords = [(point["lon"], point["lat"]) for point in geom if "lon" in point and "lat" in point]

            if len(coords) < 4:  # Need at least 4 points for a valid ring
                continue

            # Ensure ring is closed
            if coords[0] != coords[-1]:
                coords.append(coords[0])

            if role == "outer":
                outer_rings.append(coords)
            elif role == "inner":
                inner_rings.append(coords)

        if not outer_rings:
            return None

        # Build polygons - for simplicity, create one polygon per outer ring
        # A more sophisticated approach would match inner rings to their containing outer ring
        polygons = []
        for outer in outer_rings:
            try:
                # Create polygon with just the outer ring
                poly_coords = [outer]
                poly = Polygon(*poly_coords)
                if poly.valid:
                    polygons.append(poly)
            except Exception as e:
                logger.debug(f"Failed to create polygon: {e}")
                continue

        if not polygons:
            return None

        return MultiPolygon(polygons)


class URLBasedLoader:
    """
    Service for loading administrative boundaries from direct GeoJSON URLs.

    Used for sources like GRID3 and HDX that don't have standardized APIs
    but provide downloadable GeoJSON files.

    Usage:
        loader = URLBasedLoader("grid3")
        result = loader.load_boundary_level(
            iso_code="NGA",
            level=3,
            level_config={"url": "https://...", "name_field": "wardname", "id_field": "wardcode"},
            on_progress=print,
        )
    """

    def __init__(self, source: str):
        """Initialize with source identifier.

        Args:
            source: Source identifier ("grid3" or "hdx")
        """
        self.source = source
        self.http_client = httpx.Client(timeout=300.0, follow_redirects=True)

    def __del__(self):
        if hasattr(self, "http_client"):
            self.http_client.close()

    def load_boundary_level(
        self,
        iso_code: str,
        level: int,
        level_config: dict,
        on_progress: Callable[[str], None] | None = None,
    ) -> LoadResult:
        """Load boundaries for a specific country and admin level from a URL.

        Args:
            iso_code: ISO 3166-1 alpha-3 country code
            level: Admin level to load
            level_config: Configuration dict with url, name_field, id_field
            on_progress: Optional callback for progress messages

        Returns:
            LoadResult with details of what was loaded
        """
        iso_code = iso_code.upper()

        def progress(msg: str):
            if on_progress:
                on_progress(msg)
            logger.info(f"[{self.source}/{iso_code}] {msg}")

        url = level_config.get("url", "")
        name_field = level_config.get("name_field", "name")
        id_field = level_config.get("id_field", "id")

        if not url:
            return LoadResult(
                iso_code=iso_code,
                level=level,
                success=False,
                error=f"No URL configured for ADM{level}",
            )

        # Check if this is a placeholder URL
        if "PLACEHOLDER" in level_config.get("note", "") or not url.endswith(".geojson"):
            return LoadResult(
                iso_code=iso_code,
                level=level,
                success=False,
                error=f"ADM{level} URL not yet configured for {self.source}",
            )

        progress(f"ADM{level}: Downloading from {self.source}...")

        try:
            response = self.http_client.get(url, timeout=180.0)
            response.raise_for_status()
            geojson_data = response.json()

            features = geojson_data.get("features", [])
            if not features:
                return LoadResult(
                    iso_code=iso_code,
                    level=level,
                    success=False,
                    message=f"ADM{level}: No features found in GeoJSON",
                )

            progress(f"ADM{level}: Processing {len(features)} features...")
            boundaries_to_create = []
            skipped = 0

            for feature in features:
                try:
                    boundary = self._feature_to_boundary(feature, iso_code, level, url, name_field, id_field)
                    if boundary:
                        boundaries_to_create.append(boundary)
                except Exception as e:
                    props = feature.get("properties", {})
                    name = props.get(name_field, props.get("name", "unknown"))
                    logger.warning(f"Skipping {name}: {e}")
                    skipped += 1

            # Delete existing for this country/level/source and bulk create
            AdminBoundary.objects.filter(iso_code=iso_code, admin_level=level, source=self.source).delete()
            AdminBoundary.objects.bulk_create(boundaries_to_create, batch_size=100)

            message = f"ADM{level}: Loaded {len(boundaries_to_create)} boundaries from {self.source}"
            if skipped:
                message += f" (skipped {skipped})"
            progress(message)

            return LoadResult(
                iso_code=iso_code,
                level=level,
                success=True,
                count=len(boundaries_to_create),
                message=message,
            )

        except httpx.HTTPStatusError as e:
            error = f"HTTP error: {e}"
            logger.error(f"[{self.source}/{iso_code}] ADM{level}: {error}")
            return LoadResult(iso_code=iso_code, level=level, success=False, error=error)

        except httpx.RequestError as e:
            error = f"Request error: {e}"
            logger.error(f"[{self.source}/{iso_code}] ADM{level}: {error}")
            return LoadResult(iso_code=iso_code, level=level, success=False, error=error)

        except Exception as e:
            error = f"Error: {e}"
            logger.error(f"[{self.source}/{iso_code}] ADM{level}: {error}", exc_info=True)
            return LoadResult(iso_code=iso_code, level=level, success=False, error=error)

    def _feature_to_boundary(
        self,
        feature: dict,
        iso_code: str,
        level: int,
        source_url: str,
        name_field: str,
        id_field: str,
    ) -> AdminBoundary | None:
        """Convert a GeoJSON feature to an AdminBoundary model instance."""
        properties = feature.get("properties", {})
        geometry_data = feature.get("geometry")

        if not geometry_data:
            return None

        geom = GEOSGeometry(json.dumps(geometry_data))

        # Ensure it's a MultiPolygon
        if isinstance(geom, Polygon):
            geom = MultiPolygon(geom)
        elif not isinstance(geom, MultiPolygon):
            raise ValueError(f"Unexpected geometry type: {geom.geom_type}")

        # Extract name and ID from configured fields
        name = properties.get(name_field, properties.get("name", ""))
        feature_id = properties.get(id_field, properties.get("id", ""))

        if not feature_id:
            # Generate an ID if not present
            feature_id = f"{self.source}-{iso_code}-ADM{level}-{name}".replace(" ", "_")

        boundary_id = f"{self.source}-{feature_id}"

        return AdminBoundary(
            iso_code=iso_code,
            admin_level=level,
            name=name,
            name_local=properties.get("name_local", ""),
            boundary_id=boundary_id,
            geometry=geom,
            source=self.source,
            source_url=source_url,
        )


class GeoPoDELoader:
    """
    Service for loading administrative boundaries from GeoPoDe file uploads.

    GeoPoDe (https://geopode.world/) provides high-quality admin boundary data
    exported as ZIP files containing GeoJSON files per admin level.

    File naming pattern: boundary_{level}_{source}.json
    Level names vary by country:
    - country -> ADM0
    - state/districts/counties/provinces/regions -> ADM1
    - lga/regions/subcounties/districts/departments -> ADM2
    - ward/communes/subdistricts -> ADM3

    Usage:
        loader = GeoPoDELoader()

        # Load from uploaded ZIP file
        result = loader.load_from_zip(uploaded_file, on_progress=print)

        # Load from individual GeoJSON file
        result = loader.load_from_geojson(json_content, "NGA", 1, on_progress=print)
    """

    SOURCE = AdminBoundary.Source.GEOPODE

    # Map level names from filenames to admin levels
    # Only used for unambiguous cases - hierarchical inference takes priority
    LEVEL_NAME_MAPPING = {
        # ADM0 - only country is unambiguous
        "country": 0,
        # ADM3 - these are usually the deepest level
        "ward": 3,
        "wards": 3,
        "communes": 3,
        "villages": 3,
        # ADM4
        "settlements": 4,
    }
    # Note: districts, regions, counties, provinces, subcounties, lga, etc.
    # are ambiguous - their level depends on context (hierarchy depth)

    def __init__(self):
        pass

    def load_from_zip(
        self,
        zip_file: UploadedFile | io.BytesIO,
        clear: bool = False,
        on_progress: Callable[[str], None] | None = None,
        filename: str = "",
        iso_override: str = "",
    ) -> CountryLoadResult:
        """
        Load boundaries from a GeoPoDe ZIP file.

        Args:
            zip_file: Uploaded ZIP file or BytesIO containing GeoJSON files
            clear: If True, delete existing GeoPoDe boundaries before loading
            on_progress: Optional callback for progress messages
            filename: Optional filename to use for ISO extraction (if zip_file.name not available)
            iso_override: Force this ISO-3 for every file (the manifest's canonical code).
                Use when the filename is unreliable (e.g. ``GeoPoDe_DRC_...`` whose real
                ISO is ``COD``) or the data carries no nested ISO.

        Returns:
            CountryLoadResult with details of what was loaded
        """

        def progress(msg: str):
            if on_progress:
                on_progress(msg)
            logger.info(f"[GeoPoDe] {msg}")

        progress("Opening ZIP file...")

        try:
            # Try to extract ISO code from filename (e.g., GeoPoDe_NGA_Geometry_*.zip)
            zip_filename = filename
            if not zip_filename and hasattr(zip_file, "name"):
                zip_filename = str(zip_file.name)
            iso_from_filename = (iso_override or "").upper() or self._extract_iso_from_filename(zip_filename)

            # Handle both UploadedFile and BytesIO
            if hasattr(zip_file, "read"):
                zip_content = zip_file.read()
                if hasattr(zip_file, "seek"):
                    zip_file.seek(0)
            else:
                zip_content = zip_file

            with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
                # Find all JSON files
                json_files = [name for name in zf.namelist() if name.endswith(".json")]

                if not json_files:
                    return CountryLoadResult(
                        iso_code="",
                        source=self.SOURCE,
                        levels=[LoadResult(iso_code="", level=0, success=False, error="No JSON files found in ZIP")],
                    )

                progress(f"Found {len(json_files)} JSON files in ZIP")

                # Process each JSON file
                all_results = []
                iso_code = iso_from_filename  # Start with filename-derived ISO
                total_loaded = 0

                for json_filename in sorted(json_files):
                    progress(f"Processing {json_filename}...")

                    try:
                        with zf.open(json_filename) as jf:
                            geojson_data = json.load(jf)

                        result = self._process_geojson(
                            geojson_data,
                            json_filename,
                            fallback_iso=iso_code,  # Pass fallback ISO from filename
                            clear=clear and len(all_results) == 0,  # Only clear on first file
                            on_progress=on_progress,
                            iso_override=iso_override,
                        )

                        all_results.append(result)

                        if result.success:
                            total_loaded += result.count
                            # An explicit override is authoritative; otherwise trust the
                            # data's ISO over the filename when it looks valid.
                            if not iso_override and result.iso_code and len(result.iso_code) == 3:
                                iso_code = result.iso_code

                    except Exception as e:
                        logger.error(f"[GeoPoDe] Error processing {json_filename}: {e}")
                        all_results.append(
                            LoadResult(
                                iso_code=iso_code or "",
                                level=-1,
                                success=False,
                                error=f"Error processing {json_filename}: {e}",
                            )
                        )

                if iso_override:
                    iso_code = iso_override.upper()
                # Now that every level is loaded, link each unit to its immediate
                # parent by matching the parent's denormalized code (so callers can
                # narrow children by an indexed key instead of a spatial join).
                linked = self._link_parents(iso_code) if iso_code else 0
                if linked:
                    progress(f"Linked {linked} units to their parents")

                progress(f"Complete - loaded {total_loaded} boundaries from {len(json_files)} files")

                return CountryLoadResult(
                    iso_code=iso_code or "",
                    source=self.SOURCE,
                    total_loaded=total_loaded,
                    levels=all_results,
                )

        except zipfile.BadZipFile:
            return CountryLoadResult(
                iso_code="",
                source=self.SOURCE,
                levels=[LoadResult(iso_code="", level=0, success=False, error="Invalid ZIP file")],
            )
        except Exception as e:
            logger.error(f"[GeoPoDe] Error loading ZIP: {e}", exc_info=True)
            return CountryLoadResult(
                iso_code="",
                source=self.SOURCE,
                levels=[LoadResult(iso_code="", level=0, success=False, error=f"Error: {e}")],
            )

    def _process_geojson(
        self,
        geojson_data: dict,
        filename: str,
        fallback_iso: str = "",
        clear: bool = False,
        on_progress: Callable[[str], None] | None = None,
        iso_override: str = "",
    ) -> LoadResult:
        """Process a single GeoJSON file from GeoPoDe."""

        def progress(msg: str):
            if on_progress:
                on_progress(msg)

        features = geojson_data.get("features", [])
        if not features:
            return LoadResult(iso_code="", level=0, success=False, message=f"No features in {filename}")

        # Check for CRS in the GeoJSON
        source_srid = self._detect_crs(geojson_data)
        if source_srid and source_srid != 4326:
            progress(f"Detected CRS: EPSG:{source_srid} - will transform to WGS84")

        # Extract level from filename: boundary_{level}_{source}.json
        level_name = self._extract_level_from_filename(filename)

        # Get ISO code and infer admin level from first feature
        first_props = features[0].get("properties", {})

        # ISO precedence: explicit override (manifest) > nested data (WHO files,
        # case varies: Iso_3_code / ISO_3_CODE) > filename fallback.
        nested_props = first_props.get("properties", {}) or {}
        nested_iso = nested_props.get("Iso_3_code") or nested_props.get("ISO_3_CODE") or ""
        iso_code = (iso_override or "").upper() or nested_iso or ""

        # Infer admin level from the level name and property structure
        admin_level = self._infer_admin_level(level_name, first_props)

        # If no ISO yet, use fallback from ZIP filename
        if not iso_code and fallback_iso:
            iso_code = fallback_iso

        progress(f"{filename}: {len(features)} features, ADM{admin_level}, ISO={iso_code}")

        if clear:
            deleted, _ = AdminBoundary.objects.filter(
                iso_code=iso_code, admin_level=admin_level, source=self.SOURCE
            ).delete()
            if deleted:
                progress(f"Cleared {deleted} existing GeoPoDe entries for {iso_code} ADM{admin_level}")

        # Determine the name field based on level_name
        name_field = f"{level_name}_name"

        # Process features
        boundaries_to_create = []
        skipped = 0

        for feature in features:
            try:
                boundary = self._feature_to_boundary(
                    feature, iso_code, admin_level, level_name, filename, source_srid=source_srid
                )
                if boundary:
                    boundaries_to_create.append(boundary)
            except Exception as e:
                props = feature.get("properties", {})
                name = props.get(name_field, props.get("global_id", "unknown"))
                logger.warning(f"[GeoPoDe] Skipping {name}: {e}")
                skipped += 1

        # Delete existing for this country/level/source and bulk create
        AdminBoundary.objects.filter(iso_code=iso_code, admin_level=admin_level, source=self.SOURCE).delete()
        AdminBoundary.objects.bulk_create(boundaries_to_create, batch_size=100)

        message = f"ADM{admin_level}: Loaded {len(boundaries_to_create)} boundaries"
        if skipped:
            message += f" (skipped {skipped})"

        return LoadResult(
            iso_code=iso_code,
            level=admin_level,
            success=True,
            count=len(boundaries_to_create),
            message=message,
        )

    def _extract_iso_from_filename(self, filename: str) -> str:
        """Extract ISO code from GeoPoDe ZIP filename.

        Pattern: GeoPoDe_{ISO}_Geometry_*.zip
        """
        if not filename:
            return ""

        basename = filename.split("/")[-1].split("\\")[-1]  # Handle paths

        # Try to match GeoPoDe_{ISO}_Geometry pattern
        match = re.search(r"GeoPoDe_([A-Z]{3})_", basename)
        if match:
            return match.group(1)

        return ""

    def _extract_level_from_filename(self, filename: str) -> str:
        """Extract the level token from a GeoPoDe member filename.

        Files are ``boundary_<level>_<suffix>.json`` where <suffix> is
        ``default`` or ``<provider>_boundaries`` and <level> may itself contain
        underscores (e.g. ``sub_prefectures``, ``senatorial_divisions``). We strip
        the leading ``boundary_`` and the trailing suffix so the full level token
        survives — earlier code split on the first ``_`` and mislabeled multi-word
        levels (``sub_prefectures`` -> ``sub``), which then matched the parent's
        ``*_name`` field instead of the leaf's.
        """
        basename = filename.split("/")[-1]
        if basename.endswith(".json"):
            basename = basename[: -len(".json")]
        if basename.startswith("boundary_"):
            basename = basename[len("boundary_") :]
        # Strip trailing source descriptor: "_<provider>_boundaries", "_boundaries", or "_default".
        basename = re.sub(r"_(?:[a-z0-9]+_)?boundaries$", "", basename)
        if basename.endswith("_default"):
            basename = basename[: -len("_default")]
        return basename

    def _infer_admin_level(self, level_name: str, properties: dict) -> int:
        """Infer admin level from level name and property structure.

        The hierarchy is determined by counting *_name fields in the properties.
        GeoPoDe includes all parent levels' names, so deeper levels have more fields:
        - country file: 0 non-country _name fields -> ADM0
        - state/province/counties file: 1 field -> ADM1
        - district/lga/subcounties file: 2 fields -> ADM2
        - ward file: 3 fields -> ADM3
        """
        # First, check for unambiguous level names (country, ward, etc.)
        if level_name.lower() in self.LEVEL_NAME_MAPPING:
            return self.LEVEL_NAME_MAPPING[level_name.lower()]

        # Count *_name fields to infer depth from hierarchy
        # More _name fields = deeper level
        name_fields = [k for k in properties.keys() if k.endswith("_name") and k != "country_name"]
        if len(name_fields) == 0:
            return 0  # Country level
        elif len(name_fields) == 1:
            return 1  # ADM1
        elif len(name_fields) == 2:
            return 2  # ADM2
        elif len(name_fields) >= 3:
            return 3  # ADM3+

        return 1  # Default to ADM1

    def _detect_crs(self, geojson_data: dict) -> int | None:
        """Detect the CRS/SRID from GeoJSON data.

        GeoJSON files may include CRS in various formats:
        - crs.properties.name: "urn:ogc:def:crs:EPSG::32632" or "EPSG:32632"
        - crs.properties.code: 32632

        Returns:
            EPSG SRID code if found, None otherwise
        """
        crs = geojson_data.get("crs")
        if not crs:
            return None

        props = crs.get("properties", {})

        # Try to get from 'code' property
        code = props.get("code")
        if code:
            try:
                return int(code)
            except (ValueError, TypeError):
                pass

        # Try to extract from 'name' property
        name = props.get("name", "")
        if name:
            # Handle various formats:
            # "urn:ogc:def:crs:EPSG::32632"
            # "EPSG:32632"
            # "urn:ogc:def:crs:OGC:1.3:CRS84" (WGS84)
            if "CRS84" in name or "CRS:84" in name:
                return 4326

            # Extract EPSG code
            match = re.search(r"EPSG[:\s]*:*(\d+)", name, re.IGNORECASE)
            if match:
                return int(match.group(1))

            # Try urn:ogc format
            match = re.search(r"EPSG::(\d+)", name)
            if match:
                return int(match.group(1))

        return None

    def _feature_to_boundary(
        self,
        feature: dict,
        iso_code: str,
        level: int,
        level_name: str,
        source_filename: str,
        source_srid: int | None = None,
    ) -> AdminBoundary | None:
        """Convert a GeoPoDe GeoJSON feature to an AdminBoundary."""
        properties = feature.get("properties", {})
        geometry_data = feature.get("geometry")

        if not geometry_data:
            return None

        geom = GEOSGeometry(json.dumps(geometry_data))

        # If source SRID is specified and not WGS84, transform to WGS84
        if source_srid and source_srid != 4326:
            geom.srid = source_srid
            geom.transform(4326)
        elif source_srid is None:
            # Auto-detect if coordinates are in projected CRS (not WGS84)
            # WGS84 lat/long: lat -90 to 90, lon -180 to 180
            # Projected CRS: coordinates typically in thousands or millions
            extent = geom.extent  # (xmin, ymin, xmax, ymax)
            if abs(extent[0]) > 180 or abs(extent[2]) > 180 or abs(extent[1]) > 90 or abs(extent[3]) > 90:
                logger.warning(
                    f"[GeoPoDe] Coordinates appear to be in projected CRS "
                    f"(extent: {extent[0]:.0f}, {extent[1]:.0f}). "
                    f"Attempting auto-transform from common African projections..."
                )
                # Try common projected CRS for African countries
                # UTM zones for Africa (both N and S hemispheres)
                # Africa spans roughly UTM zones 28-38
                common_african_srids = [
                    # UTM South (common for sub-Saharan Africa)
                    32732,
                    32733,
                    32734,
                    32735,
                    32736,
                    32737,
                    32738,
                    # UTM North (common for North/Central Africa)
                    32632,
                    32633,
                    32634,
                    32635,
                    32636,
                    32637,
                    32638,
                    # Africa-specific projections
                    4210,  # Arc 1960 (East Africa)
                    4269,  # NAD83
                    102022,  # Africa Albers Equal Area
                    102024,  # Africa Lambert Conformal Conic
                ]
                transformed = False
                for try_srid in common_african_srids:
                    try:
                        test_geom = GEOSGeometry(json.dumps(geometry_data))
                        test_geom.srid = try_srid
                        test_geom.transform(4326)
                        test_extent = test_geom.extent
                        # Check if transformed coords are valid WGS84 and make geographic sense
                        # Africa roughly spans: lon -20 to 55, lat -35 to 40
                        if (
                            -180 <= test_extent[0] <= 180
                            and -180 <= test_extent[2] <= 180
                            and -90 <= test_extent[1] <= 90
                            and -90 <= test_extent[3] <= 90
                            # Additional check: result should be roughly in Africa
                            and -30 <= test_extent[0] <= 60
                            and -40 <= test_extent[1] <= 45
                        ):
                            geom = test_geom
                            transformed = True
                            logger.info(f"[GeoPoDe] Successfully transformed from SRID {try_srid}")
                            break
                    except Exception:
                        continue

                if not transformed:
                    logger.error(
                        f"[GeoPoDe] Could not auto-transform coordinates. "
                        f"Data may not display correctly. Extent: {extent}"
                    )

        # Ensure SRID is set to WGS84
        geom.srid = 4326

        # Ensure it's a MultiPolygon
        if isinstance(geom, Polygon):
            geom = MultiPolygon(geom)
        elif not isinstance(geom, MultiPolygon):
            raise ValueError(f"Unexpected geometry type: {geom.geom_type}")

        # Denormalized hierarchy: every "<token>_name"/"<token>_code" pair on the
        # feature, split into this unit's own token vs its parents'. ("_name"/"_code"
        # are both 5 chars, so [:-5] yields the token.)
        all_names = {k[:-5]: v for k, v in properties.items() if k.endswith("_name") and v}
        all_codes = {k[:-5]: v for k, v in properties.items() if k.endswith("_code") and v}

        # The leaf name is the level token's own "<token>_name". Fall back to the
        # deepest non-country name if the token field is missing.
        name = all_names.get(level_name) or ""
        if not name:
            for token, value in all_names.items():
                if token != "country":
                    name = value  # any non-country name beats nothing
            name = name or all_names.get("country", "")

        own_code = all_codes.get(level_name, "")
        parent_codes = {t: c for t, c in all_codes.items() if t != level_name}
        parent_names = {t: n for t, n in all_names.items() if t != level_name}

        global_id = properties.get("global_id", "")
        if not global_id:
            global_id = f"{iso_code}-{level}-{name}".replace(" ", "_")
        boundary_id = f"geopode-{global_id}"

        # Local/display name from nested viz_n (WHO files only; best-effort).
        nested_props = properties.get("properties", {}) or {}
        name_local = ""
        for key, value in nested_props.items():
            if "_viz_n" in key.lower() and key != "Adm0_viz_n" and value:
                name_local = value
                break

        population = properties.get("population_1")
        try:
            population = float(population) if population not in (None, "") else None
        except (TypeError, ValueError):
            population = None

        extra = {
            "provider": properties.get("source", ""),  # WHO / HDX / GRID3 / Other
            "source_date": properties.get("source_date", ""),
            "global_id": global_id,
            "level_token": level_name,
            "own_code": own_code,
            "parent_codes": parent_codes,
            "parent_names": parent_names,
        }

        return AdminBoundary(
            iso_code=iso_code,
            admin_level=level,
            name=name,
            name_local=name_local,
            boundary_id=boundary_id,
            geometry=geom,
            source=self.SOURCE,
            source_url=f"geopode:{source_filename}",
            population=population,
            extra=extra,
        )

    def _link_parents(self, iso_code: str) -> int:
        """Set parent_boundary_id on this country's GeoPoDe units.

        Each unit stores its own code and its parents' codes (denormalized in
        ``extra``). We map ``(level_token, own_code) -> boundary_id`` and, for each
        unit, look up the parent one admin level up by its code. Returns the number
        of units linked. Idempotent — safe to re-run.
        """
        boundaries = list(
            AdminBoundary.objects.filter(iso_code=iso_code, source=self.SOURCE).only(
                "id", "boundary_id", "admin_level", "parent_boundary_id", "extra"
            )
        )
        code_to_id: dict[tuple, str] = {}
        level_token: dict[int, str] = {}
        for b in boundaries:
            token = (b.extra or {}).get("level_token")
            own = (b.extra or {}).get("own_code")
            if token:
                level_token[b.admin_level] = token
                if own:
                    code_to_id[(token, str(own))] = b.boundary_id

        updates = []
        for b in boundaries:
            parent_token = level_token.get(b.admin_level - 1)
            if not parent_token:
                continue
            parent_code = ((b.extra or {}).get("parent_codes") or {}).get(parent_token)
            parent_id = code_to_id.get((parent_token, str(parent_code))) if parent_code else None
            if parent_id and parent_id != b.parent_boundary_id:
                b.parent_boundary_id = parent_id
                updates.append(b)
        if updates:
            AdminBoundary.objects.bulk_update(updates, ["parent_boundary_id"], batch_size=200)
        return len(updates)


class CountrySourceRegistry:
    """
    Registry of available data sources per country.

    Loads country configurations from the JSON fixture and provides
    access to source configurations.

    Usage:
        registry = CountrySourceRegistry()

        # Get all configured countries
        countries = registry.get_all_countries()

        # Get config for a specific country
        nigeria = registry.get_country("NGA")
        if nigeria:
            sources = nigeria.get_available_sources()
            recommended = nigeria.get_recommended_source(3)

        # Check if a country supports a source/level
        config = registry.get_source_config("NGA", "grid3")
    """

    _instance = None
    _records: dict[str, AdminBoundaryStaticLoadRecord]

    def __new__(cls):
        """Singleton pattern to avoid reloading fixture multiple times."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._records = {}
            cls._instance._load_fixture()
        return cls._instance

    def _load_fixture(self):
        """Load country configurations from JSON fixture."""
        fixture_path = FIXTURES_DIR / "country_sources.json"

        if not fixture_path.exists():
            logger.warning(f"Country sources fixture not found: {fixture_path}")
            return

        try:
            with open(fixture_path) as f:
                data = json.load(f)

            for country_data in data:
                record = AdminBoundaryStaticLoadRecord(country_data)
                self._records[record.iso_code] = record

            logger.info(f"Loaded {len(self._records)} country configurations from fixture")

        except Exception as e:
            logger.error(f"Failed to load country sources fixture: {e}", exc_info=True)

    def reload(self):
        """Force reload of fixture data."""
        self._records = {}
        self._load_fixture()

    def get_country(self, iso_code: str) -> AdminBoundaryStaticLoadRecord | None:
        """Get configuration for a country by ISO code."""
        return self._records.get(iso_code.upper())

    def get_all_countries(self) -> list[AdminBoundaryStaticLoadRecord]:
        """Get all configured countries."""
        return list(self._records.values())

    def get_all_iso_codes(self) -> list[str]:
        """Get list of all configured ISO codes."""
        return sorted(self._records.keys())

    def get_source_config(self, iso_code: str, source: str) -> AdminBoundarySourceConfig | None:
        """Get source configuration for a country."""
        country = self.get_country(iso_code)
        if country:
            return country.get_source_config(source)
        return None

    def is_country_configured(self, iso_code: str) -> bool:
        """Check if a country has any configuration."""
        return iso_code.upper() in self._records


# Convenience function to get the appropriate loader
def get_loader(source: str):
    """Get the appropriate loader for the given source."""
    if source == AdminBoundary.Source.OSM or source == "osm":
        return OSMLoader()
    elif source == AdminBoundary.Source.GRID3 or source == "grid3":
        return URLBasedLoader("grid3")
    elif source == AdminBoundary.Source.HDX or source == "hdx":
        return URLBasedLoader("hdx")
    else:
        return GeoBoundariesLoader()


def get_source_display_name(source: str) -> str:
    """Get human-readable name for a source."""
    names = {
        "geoboundaries": "geoBoundaries",
        "osm": "OpenStreetMap",
        "grid3": "GRID3",
        "hdx": "HDX (OCHA COD)",
        "geopode": "GeoPoDe",
    }
    return names.get(source, source)


def stream_load_country(
    iso_code: str,
    levels: list[int],
    source: str,
    clear: bool = False,
):
    """
    Generator that yields progress events while loading boundaries for a single country.

    This is the primary loading function for the single-country UI workflow.
    It uses the CountrySourceRegistry to get configuration for URL-based sources.

    Event types:
    - "status": Progress message {"message": str}
    - "result": Final result {"total_loaded": int, ...}
    - "error": Error occurred {"error": str}

    Args:
        iso_code: ISO 3166-1 alpha-3 country code
        levels: List of admin levels to load
        source: Data source ("geoboundaries", "osm", "grid3", "hdx")
        clear: Whether to clear existing data first

    Yields:
        Tuples of (event_type, event_data)
    """
    EVENT_STATUS = "status"
    EVENT_RESULT = "result"
    EVENT_ERROR = "error"

    iso_code = iso_code.upper()
    source_name = get_source_display_name(source)

    try:
        yield EVENT_STATUS, {"message": f"Starting download from {source_name}..."}

        # Get registry and source config for URL-based sources
        registry = CountrySourceRegistry()
        source_config = registry.get_source_config(iso_code, source)

        # Get the loader
        loader = get_loader(source)
        is_url_based = isinstance(loader, URLBasedLoader)

        yield EVENT_STATUS, {"message": f"[{iso_code}] Processing {iso_code} from {source_name}..."}

        # Clear if requested
        if clear:
            deleted, _ = AdminBoundary.objects.filter(iso_code=iso_code, source=source).delete()
            if deleted:
                yield EVENT_STATUS, {"message": f"[{iso_code}] Cleared {deleted} existing entries"}

        result = {
            "iso_code": iso_code,
            "source": source,
            "total_loaded": 0,
            "cleared": 0,
            "success": False,
            "levels": [],
        }

        # Load each level
        for level in levels:
            yield EVENT_STATUS, {"message": f"[{iso_code}] ADM{level}: Fetching data..."}

            # For URL-based loaders, we need to get the level config from registry
            if is_url_based:
                if source_config:
                    level_config = source_config.get_level_config(level)
                    if level_config:
                        level_result = loader.load_boundary_level(
                            iso_code,
                            level,
                            level_config,
                            on_progress=lambda msg: None,
                        )
                    else:
                        level_result = LoadResult(
                            iso_code=iso_code,
                            level=level,
                            success=False,
                            error=f"ADM{level} not configured for {source} in {iso_code}",
                        )
                else:
                    level_result = LoadResult(
                        iso_code=iso_code,
                        level=level,
                        success=False,
                        error=f"{source} not configured for {iso_code}",
                    )
            else:
                # API-based loaders (geoBoundaries, OSM)
                level_result = loader.load_boundary_level(
                    iso_code,
                    level,
                    on_progress=lambda msg: None,
                )

            level_data = {
                "level": level_result.level,
                "success": level_result.success,
                "count": level_result.count,
                "message": level_result.message,
                "error": level_result.error,
            }
            result["levels"].append(level_data)

            if level_result.success:
                result["total_loaded"] += level_result.count
                yield EVENT_STATUS, {"message": f"[{iso_code}] {level_result.message}"}
            elif level_result.error:
                yield EVENT_STATUS, {"message": f"[{iso_code}] ADM{level}: {level_result.error}"}
            else:
                yield EVENT_STATUS, {"message": f"[{iso_code}] {level_result.message}"}

        result["success"] = result["total_loaded"] > 0

        yield EVENT_STATUS, {"message": f"[{iso_code}] Complete - loaded {result['total_loaded']} boundaries"}

        yield EVENT_STATUS, {"message": f"All done! Loaded {result['total_loaded']} boundaries from {source_name}"}

        yield EVENT_RESULT, {
            "success": True,
            "source": source,
            "total_loaded": result["total_loaded"],
            "result": result,
        }

    except Exception as e:
        logger.error(f"[stream_load_country] Error: {e}", exc_info=True)
        yield EVENT_ERROR, {"error": str(e)}


def stream_load_boundaries(
    iso_codes: list[str],
    levels: list[int],
    source: str,
    clear: bool = False,
):
    """
    Generator that yields progress events while loading boundaries.

    This is designed for SSE streaming - yields tuples of (event_type, data).
    For single-country loading, prefer stream_load_country() instead.

    Event types:
    - "status": Progress message {"message": str}
    - "result": Final result {"results": list, "total_loaded": int}
    - "error": Error occurred {"error": str}

    Usage:
        for event_type, data in stream_load_boundaries(["KEN"], [0,1,2], "geoboundaries"):
            if event_type == "status":
                print(data["message"])
            elif event_type == "result":
                print(f"Loaded {data['total_loaded']} boundaries")

    Args:
        iso_codes: List of ISO 3166-1 alpha-3 country codes
        levels: List of admin levels to load
        source: Data source ("geoboundaries", "osm", "grid3", "hdx")
        clear: Whether to clear existing data first

    Yields:
        Tuples of (event_type, event_data)
    """
    EVENT_STATUS = "status"
    EVENT_RESULT = "result"
    EVENT_ERROR = "error"

    try:
        source_name = get_source_display_name(source)
        registry = CountrySourceRegistry()
        loader = get_loader(source)
        is_url_based = isinstance(loader, URLBasedLoader)

        yield EVENT_STATUS, {"message": f"Starting download from {source_name}..."}

        results = []
        total_loaded = 0

        for iso_code in iso_codes:
            iso_code = iso_code.upper()
            yield EVENT_STATUS, {"message": f"[{iso_code}] Processing {iso_code} from {source_name}..."}

            # Get source config for URL-based sources
            source_config = registry.get_source_config(iso_code, source) if is_url_based else None

            # Clear if requested
            if clear:
                deleted, _ = AdminBoundary.objects.filter(iso_code=iso_code, source=source).delete()
                if deleted:
                    yield EVENT_STATUS, {"message": f"[{iso_code}] Cleared {deleted} existing entries"}

            country_result = {
                "iso_code": iso_code,
                "source": source,
                "total_loaded": 0,
                "cleared": 0,
                "success": False,
                "levels": [],
            }

            # Load each level
            for level in levels:
                yield EVENT_STATUS, {"message": f"[{iso_code}] ADM{level}: Fetching data..."}

                # For URL-based loaders, we need level config from registry
                if is_url_based:
                    if source_config:
                        level_config = source_config.get_level_config(level)
                        if level_config:
                            level_result = loader.load_boundary_level(
                                iso_code,
                                level,
                                level_config,
                                on_progress=lambda msg: None,
                            )
                        else:
                            level_result = LoadResult(
                                iso_code=iso_code,
                                level=level,
                                success=False,
                                error=f"ADM{level} not configured for {source}",
                            )
                    else:
                        level_result = LoadResult(
                            iso_code=iso_code,
                            level=level,
                            success=False,
                            error=f"{source} not configured for {iso_code}",
                        )
                else:
                    level_result = loader.load_boundary_level(
                        iso_code,
                        level,
                        on_progress=lambda msg: None,
                    )

                level_data = {
                    "level": level_result.level,
                    "success": level_result.success,
                    "count": level_result.count,
                    "message": level_result.message,
                    "error": level_result.error,
                }
                country_result["levels"].append(level_data)

                if level_result.success:
                    country_result["total_loaded"] += level_result.count
                    yield EVENT_STATUS, {"message": f"[{iso_code}] {level_result.message}"}
                elif level_result.error:
                    yield EVENT_STATUS, {"message": f"[{iso_code}] ADM{level}: {level_result.error}"}
                else:
                    yield EVENT_STATUS, {"message": f"[{iso_code}] {level_result.message}"}

            country_result["success"] = country_result["total_loaded"] > 0
            results.append(country_result)
            total_loaded += country_result["total_loaded"]

            yield EVENT_STATUS, {
                "message": f"[{iso_code}] Complete - loaded {country_result['total_loaded']} boundaries"
            }

        yield EVENT_STATUS, {"message": f"All done! Loaded {total_loaded} total boundaries from {source_name}"}

        yield EVENT_RESULT, {
            "success": True,
            "source": source,
            "total_loaded": total_loaded,
            "results": results,
        }

    except Exception as e:
        logger.error(f"[stream_load_boundaries] Error: {e}", exc_info=True)
        yield EVENT_ERROR, {"error": str(e)}


def stream_load_geopode(
    zip_file: UploadedFile | io.BytesIO,
    clear: bool = False,
):
    """
    Generator that yields progress events while loading GeoPoDe boundaries from a ZIP file.

    This is designed for SSE streaming - yields tuples of (event_type, data).

    Event types:
    - "status": Progress message {"message": str}
    - "result": Final result {"total_loaded": int, ...}
    - "error": Error occurred {"error": str}

    Args:
        zip_file: Uploaded ZIP file containing GeoPoDe GeoJSON files
        clear: Whether to clear existing GeoPoDe data first

    Yields:
        Tuples of (event_type, event_data)
    """
    EVENT_STATUS = "status"
    EVENT_RESULT = "result"
    EVENT_ERROR = "error"

    try:
        yield EVENT_STATUS, {"message": "Processing GeoPoDe ZIP file..."}

        loader = GeoPoDELoader()
        messages = []

        def on_progress(msg: str):
            messages.append(msg)

        result = loader.load_from_zip(zip_file, clear=clear, on_progress=on_progress)

        # Yield all progress messages
        for msg in messages:
            yield EVENT_STATUS, {"message": msg}

        if result.total_loaded > 0:
            yield EVENT_STATUS, {"message": f"Complete! Loaded {result.total_loaded} boundaries for {result.iso_code}"}
        else:
            # Check if there were any errors
            errors = [r.error for r in result.levels if r.error]
            if errors:
                yield EVENT_STATUS, {"message": f"Completed with errors: {errors[0]}"}

        yield EVENT_RESULT, {
            "success": result.success,
            "source": "geopode",
            "iso_code": result.iso_code,
            "total_loaded": result.total_loaded,
            "levels": [
                {
                    "level": r.level,
                    "success": r.success,
                    "count": r.count,
                    "message": r.message,
                    "error": r.error,
                }
                for r in result.levels
            ],
        }

    except Exception as e:
        logger.error(f"[stream_load_geopode] Error: {e}", exc_info=True)
        yield EVENT_ERROR, {"error": str(e)}


# Backwards compatibility alias
BoundaryLoader = GeoBoundariesLoader


# =============================================================================
# Opportunity Boundary Coverage - Spatial Query Service
# =============================================================================


@dataclass
class BoundaryMatch:
    """Single admin boundary with visit count."""

    boundary_id: str
    name: str
    admin_level: int
    visit_count: int


@dataclass
class BoundaryMatchResult:
    """Result of matching opportunity visits to admin boundaries."""

    opportunity_id: int
    iso_code: str
    total_visits: int
    visits_with_gps: int
    visits_matched: int
    visits_unmatched: int  # Has GPS but outside all known boundaries
    boundaries_by_level: dict[int, list[BoundaryMatch]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "opportunity_id": self.opportunity_id,
            "iso_code": self.iso_code,
            "total_visits": self.total_visits,
            "visits_with_gps": self.visits_with_gps,
            "visits_matched": self.visits_matched,
            "visits_unmatched": self.visits_unmatched,
            "boundaries_by_level": {
                str(level): [
                    {
                        "boundary_id": b.boundary_id,
                        "name": b.name,
                        "admin_level": b.admin_level,
                        "visit_count": b.visit_count,
                    }
                    for b in boundaries
                ]
                for level, boundaries in self.boundaries_by_level.items()
            },
        }


def get_opp_boundary_coverage(
    opportunity_id: int,
    iso_code: str,
    admin_levels: list[int] | None = None,
) -> BoundaryMatchResult:
    """
    Get admin boundary coverage for an opportunity using efficient SQL join.

    Performs a spatial join between cached visit GPS coordinates and admin
    boundaries using PostGIS ST_Contains. Returns summary of which boundaries
    contain visits and how many.

    Cache Handling:
        - Queries ComputedVisitCache which may have multiple entries per visit
          (from different pipeline configs)
        - Uses DISTINCT ON to deduplicate - each visit counted once
        - The `location` field is a base field, identical across all configs
        - Raises ValueError if no cached data exists for the opportunity

    Args:
        opportunity_id: Opportunity to analyze
        iso_code: Country ISO code to filter boundaries (e.g., "KEN", "NGA")
        admin_levels: Which admin levels to match (default [1, 2] for ADM1/ADM2)

    Returns:
        BoundaryMatchResult with counts per boundary and unmatched visit count

    Raises:
        ValueError: If no cached visit data exists for the opportunity.
                   Caller must ensure pipeline has been run first.

    Example:
        # In a view where pipeline has already run:
        result = get_opp_boundary_coverage(
            opportunity_id=814,
            iso_code="KEN",
            admin_levels=[1, 2, 3]
        )
        print(f"Matched {result.visits_matched} visits to boundaries")
    """
    from django.db import connection

    from commcare_connect.labs.analysis.backends.sql.models import ComputedVisitCache

    if admin_levels is None:
        admin_levels = [1, 2]

    # Check if cache exists for this opportunity
    cache_exists = ComputedVisitCache.objects.filter(opportunity_id=opportunity_id).exists()

    if not cache_exists:
        raise ValueError(
            f"No cached visit data exists for opportunity {opportunity_id}. "
            "Run the analysis pipeline first to populate the cache."
        )

    # Get total visit count and visits with GPS (deduplicated)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COUNT(DISTINCT visit_id) as total_visits,
                COUNT(DISTINCT CASE WHEN location IS NOT NULL AND location != '' THEN visit_id END) as visits_with_gps
            FROM labs_computed_visit_cache
            WHERE opportunity_id = %s
            """,
            [opportunity_id],
        )
        row = cursor.fetchone()
        total_visits = row[0] or 0
        visits_with_gps = row[1] or 0

    if visits_with_gps == 0:
        return BoundaryMatchResult(
            opportunity_id=opportunity_id,
            iso_code=iso_code,
            total_visits=total_visits,
            visits_with_gps=0,
            visits_matched=0,
            visits_unmatched=0,
            boundaries_by_level={},
        )

    # Check if admin boundaries exist for this country
    boundaries_exist = AdminBoundary.objects.filter(iso_code=iso_code, admin_level__in=admin_levels).exists()

    if not boundaries_exist:
        return BoundaryMatchResult(
            opportunity_id=opportunity_id,
            iso_code=iso_code,
            total_visits=total_visits,
            visits_with_gps=visits_with_gps,
            visits_matched=0,
            visits_unmatched=visits_with_gps,
            boundaries_by_level={},
        )

    # Run the spatial join query
    # This query:
    # 1. Deduplicates visits (DISTINCT ON visit_id) to handle multiple cache entries
    # 2. Parses location string "lat lon alt acc" into PostGIS point
    # 3. Joins with admin boundaries using ST_Contains
    # 4. Groups by boundary to get visit counts
    with connection.cursor() as cursor:
        cursor.execute(
            r"""
            WITH unique_visits AS (
                -- Deduplicate visits (may have multiple cache entries from different configs)
                SELECT DISTINCT ON (visit_id)
                    visit_id,
                    location
                FROM labs_computed_visit_cache
                WHERE opportunity_id = %s
                  AND location IS NOT NULL
                  AND location != ''
            ),
            visit_points AS (
                -- Parse location string "lat lon alt acc" into PostGIS point
                SELECT
                    visit_id,
                    ST_SetSRID(ST_MakePoint(
                        CAST(split_part(location, ' ', 2) AS float),  -- lon (2nd part)
                        CAST(split_part(location, ' ', 1) AS float)   -- lat (1st part)
                    ), 4326) AS geom
                FROM unique_visits
                WHERE split_part(location, ' ', 1) ~ '^-?[0-9]+\.?[0-9]*$'
                  AND split_part(location, ' ', 2) ~ '^-?[0-9]+\.?[0-9]*$'
            )
            SELECT
                ab.boundary_id,
                ab.name,
                ab.admin_level,
                COUNT(vp.visit_id) AS visit_count
            FROM visit_points vp
            JOIN labs_admin_boundary ab
                ON ST_Contains(ab.geometry, vp.geom)
            WHERE ab.iso_code = %s
              AND ab.admin_level = ANY(%s)
            GROUP BY ab.boundary_id, ab.name, ab.admin_level
            ORDER BY ab.admin_level, visit_count DESC;
            """,
            [opportunity_id, iso_code, admin_levels],
        )
        boundary_rows = cursor.fetchall()

    # Build result
    boundaries_by_level: dict[int, list[BoundaryMatch]] = {}

    for boundary_id, name, admin_level, visit_count in boundary_rows:
        if admin_level not in boundaries_by_level:
            boundaries_by_level[admin_level] = []

        boundaries_by_level[admin_level].append(
            BoundaryMatch(
                boundary_id=boundary_id,
                name=name,
                admin_level=admin_level,
                visit_count=visit_count,
            )
        )

    # Calculate total matched visits (need a separate query to get unique visit count)
    # because a visit can be in multiple boundaries at different levels
    with connection.cursor() as cursor:
        cursor.execute(
            r"""
            WITH unique_visits AS (
                SELECT DISTINCT ON (visit_id)
                    visit_id,
                    location
                FROM labs_computed_visit_cache
                WHERE opportunity_id = %s
                  AND location IS NOT NULL
                  AND location != ''
            ),
            visit_points AS (
                SELECT
                    visit_id,
                    ST_SetSRID(ST_MakePoint(
                        CAST(split_part(location, ' ', 2) AS float),
                        CAST(split_part(location, ' ', 1) AS float)
                    ), 4326) AS geom
                FROM unique_visits
                WHERE split_part(location, ' ', 1) ~ '^-?[0-9]+\.?[0-9]*$'
                  AND split_part(location, ' ', 2) ~ '^-?[0-9]+\.?[0-9]*$'
            )
            SELECT COUNT(DISTINCT vp.visit_id)
            FROM visit_points vp
            WHERE EXISTS (
                SELECT 1 FROM labs_admin_boundary ab
                WHERE ST_Contains(ab.geometry, vp.geom)
                  AND ab.iso_code = %s
                  AND ab.admin_level = ANY(%s)
            );
            """,
            [opportunity_id, iso_code, admin_levels],
        )
        visits_matched = cursor.fetchone()[0] or 0

    visits_unmatched = visits_with_gps - visits_matched

    return BoundaryMatchResult(
        opportunity_id=opportunity_id,
        iso_code=iso_code,
        total_visits=total_visits,
        visits_with_gps=visits_with_gps,
        visits_matched=visits_matched,
        visits_unmatched=visits_unmatched,
        boundaries_by_level=boundaries_by_level,
    )


# ---------------------------------------------------------------------------
# resolve_many — paste-a-list ward-name resolution
#
# The bulk-create flow's safety belt: the lead pastes a list of ward names; for
# each name we return the resolved boundary (id + LGA + population), and for any
# name that doesn't resolve we return an explicit `unresolved_reason` so the
# front-end can render an "unknown ward" row the lead must fix or skip before
# Create Plans is enabled. See spine item `name-match-and-confirm` in the
# microplans-10-wards why-brief.
# ---------------------------------------------------------------------------


@dataclass
class NameResolution:
    """Per-name resolution returned by `resolve_many_by_name`.

    Exactly one of (matched_id, unresolved_reason) is non-empty.

    When ``unresolved_reason`` is ``ambiguous: N candidates``, ``candidates``
    lists each candidate boundary so the front-end can render a pick-the-LGA
    affordance per row instead of forcing Skip-and-lose-the-row. Without this,
    Dana pastes 10 wards, 4 come back ambiguous, and "Create 6 plans" is the
    only path forward — a task-completion failure. With the candidate list
    surfaced, she picks "Madobi (Madobi LGA)" or "Tofa (Bagwai LGA)" inline
    and proceeds with all 10 wards.

    ``candidates`` is ALWAYS present (empty list for ``not found`` / clean
    matches) so the front-end can iterate unconditionally.
    """

    name: str
    matched_id: str = ""
    matched_name: str = ""
    lga: str = ""
    population: float | None = None
    unresolved_reason: str = ""
    candidates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "matched_id": self.matched_id,
            "matched_name": self.matched_name,
            "lga": self.lga,
            "population": self.population,
            "unresolved_reason": self.unresolved_reason,
            "candidates": self.candidates,
        }


def resolve_many_by_name(
    names: list[str],
    iso_code: str,
    admin_level: int,
    source: str | None = None,
) -> list[NameResolution]:
    """Resolve a list of ward names against the AdminBoundary table.

    For each input name:
    - If exactly one boundary matches (case-insensitive, exact match) at the
      given iso_code + admin_level (+ optional source) → return the match with
      its boundary_id, name, parent name (LGA), and population.
    - If no boundary matches → return ``unresolved_reason="not found"``.
    - If multiple boundaries match (ambiguous name across LGAs) → return
      ``unresolved_reason="ambiguous: N candidates"`` so the front-end can
      surface a disambiguation step.

    Returns one NameResolution per input name, in the same order.
    """
    # Imported here so this module is import-light for non-DB callers.
    from commcare_connect.labs.admin_boundaries.models import AdminBoundary

    results: list[NameResolution] = []
    cleaned = [(raw, (raw or "").strip()) for raw in names]
    nonempty_cleaned = [name for _, name in cleaned if name]
    if not nonempty_cleaned:
        return [NameResolution(name=raw, unresolved_reason="empty name") for raw, _ in cleaned]

    qs = AdminBoundary.objects.filter(
        iso_code=iso_code,
        admin_level=admin_level,
        name__in=nonempty_cleaned,  # case-sensitive prefilter; broaden below
    )
    if source:
        qs = qs.filter(source=source)

    # Bucket candidates by lowercase name so we can do case-insensitive match
    # and disambiguation in one pass. Most callers will hit ≤ a few hundred
    # candidates so an in-memory bucket is cheap and avoids N+1.
    by_lower: dict[str, list[AdminBoundary]] = {}
    for b in qs:
        by_lower.setdefault(b.name.lower(), []).append(b)
    # Fall back to a case-insensitive search for any names that didn't match
    # the case-sensitive __in (e.g. the boundary stored as "Madobi" vs paste
    # "madobi"). One extra query, OR'd across the misses.
    misses = [n for n in nonempty_cleaned if n.lower() not in by_lower]
    if misses:
        from django.db.models import Q

        q = Q()
        for n in misses:
            q |= Q(name__iexact=n)
        ci_qs = AdminBoundary.objects.filter(iso_code=iso_code, admin_level=admin_level).filter(q)
        if source:
            ci_qs = ci_qs.filter(source=source)
        for b in ci_qs:
            by_lower.setdefault(b.name.lower(), []).append(b)

    # Parent lookup (LGA = the admin_level - 1 parent for ADM3 wards in NGA).
    parent_ids = {b.parent_boundary_id for matches in by_lower.values() for b in matches if b.parent_boundary_id}
    parent_name_by_id: dict[str, str] = {}
    if parent_ids:
        parent_qs = AdminBoundary.objects.filter(iso_code=iso_code, boundary_id__in=parent_ids).only(
            "boundary_id", "name"
        )
        for p in parent_qs:
            parent_name_by_id[p.boundary_id] = p.name

    for raw, cleaned_name in cleaned:
        if not cleaned_name:
            results.append(NameResolution(name=raw, unresolved_reason="empty name"))
            continue
        matches = by_lower.get(cleaned_name.lower(), [])
        if not matches:
            results.append(NameResolution(name=raw, unresolved_reason="not found"))
            continue
        if len(matches) > 1:
            # Surface each candidate so the front-end can render a picker
            # ("Madobi (Madobi LGA, pop 7,452)" / "Madobi (Kano Municipal LGA,
            # pop 23K)") instead of forcing Skip. Order by population desc
            # then name asc so the highest-signal candidate is first — gives
            # Dana a sensible default to compare against.
            cands = [
                {
                    "matched_id": b.boundary_id,
                    "matched_name": b.name,
                    "lga": parent_name_by_id.get(b.parent_boundary_id, ""),
                    "population": b.population,
                }
                for b in matches
            ]
            cands.sort(key=lambda c: (-(c["population"] or 0), c["matched_name"]))
            results.append(
                NameResolution(
                    name=raw,
                    unresolved_reason=f"ambiguous: {len(matches)} candidates",
                    candidates=cands,
                )
            )
            continue
        b = matches[0]
        results.append(
            NameResolution(
                name=raw,
                matched_id=b.boundary_id,
                matched_name=b.name,
                lga=parent_name_by_id.get(b.parent_boundary_id, ""),
                population=b.population,
            )
        )
    return results
