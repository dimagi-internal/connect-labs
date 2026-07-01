"""
Management command to enrich opportunities with admin boundary coverage data.

Processes opportunities from a CSV file, runs the analysis pipeline to populate
visit cache (if needed), then performs spatial queries to determine which admin
boundaries contain visits for each opportunity.

Usage:
    # Enrich next 10 opps without boundaries (lowest opp_id first)
    python manage.py enrich_opp_boundaries --count 10

    # Enrich specific opps
    python manage.py enrich_opp_boundaries --opp-ids 814,822,830

    # Dry run to see what would be enriched
    python manage.py enrich_opp_boundaries --count 10 --dry-run

    # Use custom CSV file
    python manage.py enrich_opp_boundaries --count 10 --csv-file path/to/file.csv
"""

import csv
import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


# Country name to ISO 3166-1 alpha-3 code mapping
# Based on countries in the CSV file
COUNTRY_TO_ISO = {
    "Nigeria": "NGA",
    "Uganda": "UGA",
    "Tanzania": "TZA",
    "Malawi": "MWI",
    "India": "IND",
    "Sierra Leone": "SLE",
    "Liberia": "LBR",
    "DRC": "COD",
    "Kenya": "KEN",
    "CAR": "CAF",
    "Ethiopia": "ETH",
    "Mozambique": "MOZ",
    "Zambia": "ZMB",
    # Additional common mappings
    "South Africa": "ZAF",
    "Rwanda": "RWA",
    "Bangladesh": "BGD",
    "Pakistan": "PAK",
    "Philippines": "PHL",
    "Ghana": "GHA",
    "Senegal": "SEN",
    "Mali": "MLI",
    "Niger": "NER",
    "Burkina Faso": "BFA",
    "Cameroon": "CMR",
    "Chad": "TCD",
    "Sudan": "SDN",
    "South Sudan": "SSD",
    "Somalia": "SOM",
    "Madagascar": "MDG",
    "Zimbabwe": "ZWE",
    "Botswana": "BWA",
    "Namibia": "NAM",
    "Angola": "AGO",
    "Congo": "COG",
    "Gabon": "GAB",
    "Equatorial Guinea": "GNQ",
    "Benin": "BEN",
    "Togo": "TGO",
    "Cote d'Ivoire": "CIV",
    "Guinea": "GIN",
    "Guinea-Bissau": "GNB",
    "Gambia": "GMB",
    "Mauritania": "MRT",
    "Central African Republic": "CAF",
    "Democratic Republic of the Congo": "COD",
}


class Command(BaseCommand):
    help = "Enrich opportunities with admin boundary coverage data"

    # Default paths
    DEFAULT_CSV_PATH = Path(__file__).parent.parent.parent.parent / "admin_boundaries/examples/Opp Country Funder.csv"
    ENRICHMENT_FIXTURE_PATH = (
        Path(__file__).parent.parent.parent.parent.parent / "solicitations/fixtures/opp_org_enrichment.json"
    )

    def add_arguments(self, parser):
        # Selection arguments (mutually exclusive)
        selection_group = parser.add_mutually_exclusive_group(required=True)
        selection_group.add_argument(
            "--count",
            type=int,
            help="Enrich next N opps without admin_boundaries (lowest opp_id first)",
        )
        selection_group.add_argument(
            "--opp-ids",
            type=str,
            help="Comma-separated list of specific opportunity IDs to enrich",
        )

        # Optional arguments
        parser.add_argument(
            "--csv-file",
            type=str,
            help=f"Path to CSV file with opp_id,enriched_country,funder columns (default: {self.DEFAULT_CSV_PATH})",
        )
        parser.add_argument(
            "--admin-levels",
            type=str,
            default="0,1,2,3",
            help="Comma-separated admin levels to query (default: 0,1,2,3)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be enriched without making changes",
        )
        parser.add_argument(
            "--skip-pipeline",
            action="store_true",
            help="Skip running the analysis pipeline (only query existing cache)",
        )
        parser.add_argument(
            "--include-skipped",
            action="store_true",
            help="Include previously skipped opps (those with enrichment_skipped_reason)",
        )
        parser.add_argument(
            "--clear-skipped",
            type=str,
            help="Clear enrichment_skipped_reason for specific opp IDs (comma-separated) to allow retry",
        )

    def handle(self, *args, **options):
        self.dry_run = options["dry_run"]
        self.skip_pipeline = options["skip_pipeline"]
        self.include_skipped = options["include_skipped"]
        self.admin_levels = [int(x.strip()) for x in options["admin_levels"].split(",")]

        # Load CSV data
        csv_path = Path(options["csv_file"]) if options["csv_file"] else self.DEFAULT_CSV_PATH
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        opp_data = self._load_csv(csv_path)
        self.stdout.write(f"Loaded {len(opp_data)} opportunities from CSV")

        # Load existing enrichment fixture
        enrichment_data = self._load_enrichment_fixture()
        existing_enrichments = {e["opportunity_id"]: e for e in enrichment_data.get("enrichments", [])}
        self.stdout.write(f"Loaded {len(existing_enrichments)} existing enrichment records")

        # Handle --clear-skipped: remove enrichment_skipped_reason for specified opps
        if options["clear_skipped"]:
            clear_ids = [int(x.strip()) for x in options["clear_skipped"].split(",")]
            cleared = 0
            for oid in clear_ids:
                if oid in existing_enrichments and existing_enrichments[oid].get("enrichment_skipped_reason"):
                    existing_enrichments[oid].pop("enrichment_skipped_reason", None)
                    cleared += 1
            if cleared > 0:
                enrichment_data["enrichments"] = list(existing_enrichments.values())
                self._save_enrichment_fixture(enrichment_data)
                self.stdout.write(self.style.SUCCESS(f"Cleared skip reason for {cleared} opps"))

        # Determine which opps to process
        if options["opp_ids"]:
            opp_ids = [int(x.strip()) for x in options["opp_ids"].split(",")]
            opps_to_process = [opp_data[oid] for oid in opp_ids if oid in opp_data]
        else:
            # Find opps without admin_boundaries AND not previously skipped, sorted by opp_id ascending
            opps_needing_enrichment = []
            skipped_count = 0
            for opp_id, data in opp_data.items():
                existing = existing_enrichments.get(opp_id, {})
                # Skip if already has admin_boundaries
                if "admin_boundaries" in existing:
                    continue
                # Skip if previously attempted but failed (has enrichment_skipped_reason)
                # Unless --include-skipped is set
                if existing.get("enrichment_skipped_reason") and not self.include_skipped:
                    skipped_count += 1
                    continue
                opps_needing_enrichment.append((opp_id, data))

            if skipped_count > 0:
                self.stdout.write(
                    f"Skipping {skipped_count} previously attempted opps (use --include-skipped to retry)"
                )

            # Sort by opp_id ascending and take first N
            opps_needing_enrichment.sort(key=lambda x: x[0])
            count = options["count"]
            opps_to_process = [data for _, data in opps_needing_enrichment[:count]]

        self.stdout.write(f"Will process {len(opps_to_process)} opportunities")
        self.stdout.write(f"Admin levels: {self.admin_levels}")
        self.stdout.write("")

        if self.dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))
            self.stdout.write("")

        # Process each opportunity (save incrementally to preserve progress)
        results = []
        for i, opp_info in enumerate(opps_to_process, 1):
            opp_id = opp_info["opp_id"]
            country = opp_info["country"]
            funder = opp_info["funder"]
            iso_code = COUNTRY_TO_ISO.get(country, "")

            self.stdout.write("=" * 70)
            self.stdout.write(f"[{i}/{len(opps_to_process)}] Processing Opportunity {opp_id}")
            self.stdout.write(f"  Country: {country} ({iso_code})")
            self.stdout.write(f"  Funder: {funder}")

            if not iso_code:
                self.stdout.write(self.style.WARNING(f"  Unknown country: {country} - skipping boundary query"))
                result = self._create_enrichment_record(
                    opp_id, country, "", funder, None, skip_reason="unknown_country"
                )
                results.append(result)
                # Save incrementally
                if not self.dry_run:
                    self._save_enrichment_results(enrichment_data, [result])
                continue

            if self.dry_run:
                self.stdout.write(self.style.SUCCESS("  Would enrich this opportunity"))
                result = self._create_enrichment_record(opp_id, country, iso_code, funder, None)
                results.append(result)
                continue

            # Run the enrichment
            result = self._enrich_opportunity(opp_id, country, iso_code, funder)
            results.append(result)

            # Save incrementally after each successful enrichment
            self._save_enrichment_results(enrichment_data, [result])
            self.stdout.write(self.style.SUCCESS(f"  Saved enrichment for opp {opp_id}"))

        # Note: Results are now saved incrementally, so no batch save needed here

        # Summary
        self.stdout.write("")
        self.stdout.write("=" * 70)
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 70)

        success_count = sum(1 for r in results if r.get("admin_boundaries"))
        skipped_results = [r for r in results if r.get("enrichment_skipped_reason")]

        self.stdout.write(f"Processed: {len(results)}")
        self.stdout.write(self.style.SUCCESS(f"Successfully enriched with boundaries: {success_count}"))

        if skipped_results:
            self.stdout.write(self.style.WARNING(f"Skipped (will not retry automatically): {len(skipped_results)}"))
            # Group by reason
            reasons = {}
            for r in skipped_results:
                reason = r.get("enrichment_skipped_reason", "unknown")
                reasons[reason] = reasons.get(reason, 0) + 1
            for reason, count in sorted(reasons.items()):
                self.stdout.write(f"  - {reason}: {count}")

        if self.dry_run:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("DRY RUN COMPLETE - no changes made"))

    def _load_csv(self, csv_path: Path) -> dict:
        """Load opportunity data from CSV file."""
        opp_data = {}
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                opp_id = int(row["opp_id"])
                opp_data[opp_id] = {
                    "opp_id": opp_id,
                    "country": row["enriched_country"],
                    "funder": row["funder"],
                }
        return opp_data

    def _load_enrichment_fixture(self) -> dict:
        """Load existing enrichment fixture."""
        if not self.ENRICHMENT_FIXTURE_PATH.exists():
            return {"enrichments": []}

        with open(self.ENRICHMENT_FIXTURE_PATH) as f:
            return json.load(f)

    def _save_enrichment_fixture(self, enrichment_data: dict):
        """Save enrichment fixture."""
        with open(self.ENRICHMENT_FIXTURE_PATH, "w") as f:
            json.dump(enrichment_data, f, indent=2)

    def _enrich_opportunity(self, opp_id: int, country: str, iso_code: str, funder: str) -> dict:
        """Run analysis pipeline and spatial query for an opportunity."""
        from connect_labs.coverage.analysis import COVERAGE_BASE_CONFIG
        from connect_labs.labs.admin_boundaries.services import get_opp_boundary_coverage
        from connect_labs.labs.analysis.pipeline import AnalysisPipeline
        from connect_labs.labs.integrations.connect.cli import create_cli_request

        # Create CLI request with auth context
        self.stdout.write("  Creating CLI request...")
        request = create_cli_request(opportunity_id=opp_id)

        if not request:
            raise CommandError(f"Failed to authenticate for opp {opp_id}. Run: python manage.py get_cli_token")

        # Check opportunity info
        opp = request.labs_context.get("opportunity", {})
        visit_count = opp.get("visit_count", 0)
        self.stdout.write(f"  Opportunity: {opp.get('name', 'Unknown')}")
        self.stdout.write(f"  Visit count: {visit_count}")

        if visit_count == 0:
            self.stdout.write(self.style.WARNING("  No visits for this opportunity"))
            return self._create_enrichment_record(opp_id, country, iso_code, funder, None, skip_reason="no_visits")

        # Run analysis pipeline to populate cache (unless skip_pipeline)
        if not self.skip_pipeline:
            self.stdout.write("  Running analysis pipeline to populate cache...")
            try:
                pipeline = AnalysisPipeline(request)
                pipeline.stream_analysis_ignore_events(COVERAGE_BASE_CONFIG, opportunity_id=opp_id)
                self.stdout.write(self.style.SUCCESS("  Cache populated"))
            except Exception as e:
                raise CommandError(f"Pipeline error for opp {opp_id}: {e}") from e

        # Verify cache has data (detect empty API responses)
        from connect_labs.labs.analysis.backends.sql.models import ComputedVisitCache

        cached_count = ComputedVisitCache.objects.filter(opportunity_id=opp_id).count()
        if cached_count == 0 and visit_count > 0:
            raise CommandError(
                f"Pipeline completed but cache is empty for opp {opp_id}. "
                f"Expected {visit_count} visits but got 0. "
                f"This usually means the API returned an empty response (possibly due to timeout "
                f"for large exports). Try again later or check the API directly."
            )

        self.stdout.write(f"  Cache verified: {cached_count} visits")

        # Run spatial query
        self.stdout.write(f"  Running spatial query for {iso_code} ADM{self.admin_levels}...")
        try:
            boundary_result = get_opp_boundary_coverage(
                opportunity_id=opp_id,
                iso_code=iso_code,
                admin_levels=self.admin_levels,
            )

            # Format results
            admin_boundaries = {}
            for level, boundaries in boundary_result.boundaries_by_level.items():
                admin_boundaries[str(level)] = [
                    {
                        "name": b.name,
                        "boundary_id": b.boundary_id,
                        "visit_count": b.visit_count,
                    }
                    for b in boundaries
                ]

            boundary_coverage = {
                "total_visits": boundary_result.total_visits,
                "visits_with_gps": boundary_result.visits_with_gps,
                "visits_matched": boundary_result.visits_matched,
                "visits_unmatched": boundary_result.visits_unmatched,
            }

            matched = boundary_result.visits_matched
            with_gps = boundary_result.visits_with_gps
            self.stdout.write(self.style.SUCCESS(f"  Matched {matched}/{with_gps} visits to boundaries"))

            # Log boundary counts per level
            for level in sorted(admin_boundaries.keys()):
                count = len(admin_boundaries[level])
                self.stdout.write(f"    ADM{level}: {count} boundaries")

            return self._create_enrichment_record(
                opp_id, country, iso_code, funder, admin_boundaries, boundary_coverage
            )

        except ValueError as e:
            # No cached data - this shouldn't happen if pipeline ran successfully
            raise CommandError(f"No cached data for opp {opp_id}: {e}") from e
        except Exception as e:
            raise CommandError(f"Spatial query error for opp {opp_id}: {e}") from e

    def _create_enrichment_record(
        self,
        opp_id: int,
        country: str,
        iso_code: str,
        funder: str,
        admin_boundaries: dict | None,
        boundary_coverage: dict | None = None,
        skip_reason: str | None = None,
    ) -> dict:
        """Create an enrichment record for an opportunity.

        Args:
            opp_id: Opportunity ID
            country: Country name
            iso_code: ISO 3166-1 alpha-3 code
            funder: Funder name
            admin_boundaries: Dict of admin level -> boundary list, or None if not enriched
            boundary_coverage: Coverage stats dict, or None
            skip_reason: If set, marks this opp as attempted but not enrichable.
                        Valid reasons: "no_visits", "unknown_country", "too_large"
                        (other errors should fail loudly, not skip silently)
        """
        record = {
            "opportunity_id": opp_id,
            "opp_country": country,
            "iso_code": iso_code,
            "funder": funder,
        }

        if admin_boundaries is not None:
            record["admin_boundaries"] = admin_boundaries
            # Clear any previous skip reason if we now have boundaries
            record["enrichment_skipped_reason"] = None
        elif skip_reason:
            # Mark as attempted but skipped - won't be retried automatically
            record["enrichment_skipped_reason"] = skip_reason

        if boundary_coverage is not None:
            record["boundary_coverage"] = boundary_coverage

        return record

    def _save_enrichment_results(self, enrichment_data: dict, results: list, verbose: bool = False):
        """Merge results into enrichment fixture and save.

        Called incrementally after each enrichment to preserve progress.
        """
        # Build lookup of existing records
        existing_by_id = {e["opportunity_id"]: e for e in enrichment_data.get("enrichments", [])}

        # Merge new results
        for result in results:
            opp_id = result["opportunity_id"]
            if opp_id in existing_by_id:
                # Update existing record
                existing_by_id[opp_id].update(result)
            else:
                # Add new record
                existing_by_id[opp_id] = result

        # Remove the placeholder record with opp_id=0 if it exists
        if 0 in existing_by_id and len(existing_by_id) > 1:
            del existing_by_id[0]

        # Sort by opp_id
        enrichment_data["enrichments"] = sorted(existing_by_id.values(), key=lambda x: x["opportunity_id"])

        # Save
        with open(self.ENRICHMENT_FIXTURE_PATH, "w") as f:
            json.dump(enrichment_data, f, indent=2)

        if verbose:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Saved {len(enrichment_data['enrichments'])} enrichment records to {self.ENRICHMENT_FIXTURE_PATH}"
                )
            )
