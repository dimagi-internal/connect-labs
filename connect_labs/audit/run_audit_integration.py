#!/usr/bin/env python
"""
Integration test for optimized audit flow.

Tests the new AuditDataAccess class that uses the analysis pipeline
for memory-efficient visit processing.

Usage:
    python connect_labs/audit/run_audit_integration.py [config]

Available configs:
    opp385_last10          - Opp 385, last 10 visits total (default)
    opp385                 - Opp 385, last 10 per FLW
    opp772                 - Opp 772, last 10 per FLW
    opp772_zero            - Opp 772, wrong FLWs (demonstrates 0 visits)
    opp772_date            - Opp 772, wrong date range (demonstrates 0 visits)
    opp772_lastN_with_dates - Opp 772, last 10 per FLW with old dates in criteria
                             (dates should be ignored for lastN audit types)
    opp524_related_fields  - Opp 524, last 100 visits with related fields
                             (tests related_fields extraction for weight images)
    opp874_related_fields  - Opp 874, last 100 visits with related fields
                             (tests related_fields extraction for weight images)
"""

import os
import sys
from dataclasses import dataclass


@dataclass
class TestConfig:
    """Configuration for audit integration test."""

    name: str
    search_query: str
    select_count: int = 1
    audit_type: str = "last_n_across_all"
    count_across_all: int = 10
    count_per_flw: int | None = None
    sample_percentage: int = 100
    selected_flw_user_ids: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    related_fields: list[dict] | None = None  # List of {image_path, field_path, label}


TEST_CONFIGS = {
    "opp385_last10": TestConfig(
        name="Opp 385 - Last 10 Total",
        search_query="385",
        select_count=1,
        audit_type="last_n_across_all",
        count_across_all=10,
    ),
    "opp385": TestConfig(
        name="Opp 385 - Last 10 per FLW",
        search_query="385",
        select_count=1,
        audit_type="last_n_per_flw",
        count_per_flw=10,
    ),
    "opp772": TestConfig(
        name="Opp 772 - Last 10 per FLW",
        search_query="772",
        select_count=1,
        audit_type="last_n_per_flw",
        count_per_flw=10,
    ),
    "opp772_zero": TestConfig(
        name="Opp 772 - Zero Visits (Wrong FLWs)",
        search_query="772",
        select_count=1,
        audit_type="last_n_per_flw",
        count_per_flw=10,
        selected_flw_user_ids=["nonexistent_flw1", "nonexistent_flw2"],
    ),
    "opp772_date": TestConfig(
        name="Opp 772 - Zero Visits (Wrong Date Range)",
        search_query="772",
        select_count=1,
        audit_type="date_range",
        start_date="2024-01-01",
        end_date="2024-01-31",
    ),
    "opp772_lastN_with_dates": TestConfig(
        name="Opp 772 - Last 10 per FLW (ignoring old dates in criteria)",
        search_query="772",
        select_count=1,
        audit_type="last_n_per_flw",
        count_per_flw=10,
        # These dates should be IGNORED since audit_type is last_n_per_flw
        start_date="2024-01-01",
        end_date="2024-01-31",
    ),
    "opp874_related_fields": TestConfig(
        name="Opp 874 - Last 20 with Related Fields",
        search_query="874",
        select_count=1,
        audit_type="last_n_across_all",
        count_across_all=20,
        related_fields=[
            {
                "image_path": "anthropometric/upload_weight_image",
                "field_path": "child_weight_visit",
                "label": "Child Weight",
            },
        ],
    ),
}


class MockRequest:
    """Mock request object for CLI testing."""

    def __init__(self, access_token: str, opportunity_id: int | None = None):
        self.session = {
            "labs_oauth": {
                "access_token": access_token,
            }
        }
        self.labs_context = {
            "opportunity_id": opportunity_id,
            "organization_id": None,
            "program_id": None,
        }
        self.GET = {}


def test_optimized_audit_flow(config_name="opp385_last10"):
    """Test complete audit flow with new optimized data access."""

    if config_name not in TEST_CONFIGS:
        print(f"[ERROR] Unknown config: {config_name}")
        print(f"Available configs: {', '.join(TEST_CONFIGS.keys())}")
        return

    config = TEST_CONFIGS[config_name]

    print("=" * 80)
    print("OPTIMIZED AUDIT INTEGRATION TEST")
    print("=" * 80)
    print(f"Configuration: {config.name}")
    print(f"Search: '{config.search_query}'")
    print(f"Audit Type: {config.audit_type}")
    if config.start_date or config.end_date:
        print(f"Date Range: {config.start_date or 'any'} to {config.end_date or 'any'}")
    if config.selected_flw_user_ids:
        print(f"Selected FLWs: {', '.join(config.selected_flw_user_ids)}")
    if config.related_fields:
        print(f"Related Fields: {len(config.related_fields)} rule(s)")
        for rf in config.related_fields:
            print(f"  - {rf.get('image_path')} -> {rf.get('field_path')} ({rf.get('label')})")
    print("=" * 80)

    # Step 1: Get OAuth token
    print("\n[1] Getting OAuth token...")
    from django.conf import settings

    from connect_labs.labs.integrations.connect.cli import TokenManager
    from connect_labs.labs.integrations.connect.oauth import introspect_token

    access_token = os.getenv("CONNECT_OAUTH_TOKEN")
    if access_token:
        print("[OK] Using token from CONNECT_OAUTH_TOKEN environment variable")
    else:
        token_manager = TokenManager()
        access_token = token_manager.get_valid_token()
        if access_token:
            info = token_manager.get_token_info()
            if info and "expires_in_seconds" in info:
                minutes = info["expires_in_seconds"] // 60
                print(f"[OK] Using saved token (expires in {minutes} minutes)")
        else:
            print("[ERROR] No valid OAuth token found")
            print("[INFO] Please run: python manage.py get_cli_token")
            return

    # Introspect token
    print("[INFO] Introspecting token...")
    user_profile = introspect_token(
        access_token=access_token,
        client_id=settings.CONNECT_OAUTH_CLIENT_ID,
        client_secret=settings.CONNECT_OAUTH_CLIENT_SECRET,
        production_url=settings.CONNECT_PRODUCTION_URL,
    )

    if not user_profile:
        print("[ERROR] Could not introspect token")
        return

    username = user_profile["username"]
    print(f"[OK] User: {username}")

    # Step 2: Initialize optimized data access
    print("\n[2] Initializing optimized data access...")
    from connect_labs.audit.data_access import AuditCriteria, AuditDataAccess

    # Create mock request
    request = MockRequest(access_token=access_token)
    data_access = AuditDataAccess(request=request)
    print("[OK] AuditDataAccess initialized")

    try:
        # Step 3: Search opportunities
        print(f"\n[3] Searching for '{config.search_query}' opportunities...")
        opportunities = data_access.search_opportunities(query=config.search_query, limit=5)

        if not opportunities:
            print(f"[WARNING] No opportunities found matching '{config.search_query}'")
            return

        opportunities = opportunities[: config.select_count]
        print(f"[OK] Found {len(opportunities)} opportunities")
        for opp in opportunities:
            print(f"     - {opp.get('id')}: {opp.get('name')}")

        test_opp_id = opportunities[0]["id"]

        # Reinitialize data_access with opportunity context
        data_access.close()
        request.labs_context["opportunity_id"] = test_opp_id
        data_access = AuditDataAccess(opportunity_id=test_opp_id, request=request)

        # Step 4: Filter visits (SLIM mode - single fetch for both IDs and visit info)
        print("\n[4] Fetching and filtering visits (SLIM - no form_json)...")
        import time

        criteria = AuditCriteria(
            audit_type=config.audit_type,
            count_across_all=config.count_across_all,
            count_per_flw=config.count_per_flw or 10,
            sample_percentage=config.sample_percentage,
            selected_flw_user_ids=config.selected_flw_user_ids,
            start_date=config.start_date,
            end_date=config.end_date,
            related_fields=config.related_fields,
        )

        start = time.time()
        # Single call returns both IDs and filtered visits (like UI preview+create flow)
        visit_ids, filtered_visits = data_access.get_visit_ids_for_audit(
            opportunity_ids=[test_opp_id],
            criteria=criteria,
            return_visits=True,
        )
        elapsed = time.time() - start
        print(f"[OK] Fetched and filtered to {len(visit_ids)} visits in {elapsed:.2f}s (slim mode)")
        print(f"     Visit IDs: {visit_ids[:5]}{'...' if len(visit_ids) > 5 else ''}")

        # Show FLW breakdown
        if filtered_visits:
            from collections import Counter

            sample = filtered_visits[0]
            print(f"     Sample visit keys: {list(sample.keys())}")
            has_form_json = "form_json" in sample and sample["form_json"]
            print(f"     Has form_json: {has_form_json} (should be empty dict or False)")

            # Show FLW distribution
            flw_counts = Counter(v.get("username") for v in filtered_visits if v.get("username"))
            print(f"     FLW breakdown ({len(flw_counts)} unique FLWs):")
            for flw, count in sorted(flw_counts.items(), key=lambda x: -x[1])[:10]:
                print(f"       - {flw}: {count} visits")

            # Show date range of filtered visits
            dates = [v.get("visit_date") for v in filtered_visits if v.get("visit_date")]
            if dates:
                print(f"     Date range: {min(dates)} to {max(dates)}")

        if not visit_ids:
            print("[WARNING] No visits match criteria")
            return

        # Step 5: Extract images (USES PIPELINE - chunked parsing for form_json)
        print("\n[5] Extracting images with question IDs (using analysis pipeline)...")
        start = time.time()
        visit_images = data_access.extract_images_for_visits(
            visit_ids, test_opp_id, related_fields=config.related_fields
        )
        elapsed = time.time() - start
        print(f"[OK] Extracted images for {len(visit_images)} visits in {elapsed:.2f}s")

        total_images = sum(len(imgs) for imgs in visit_images.values())
        print(f"     Total images: {total_images}")

        # Count images with related fields
        if config.related_fields:
            images_with_related = sum(1 for imgs in visit_images.values() for img in imgs if img.get("related_fields"))
            print(f"     Images with related fields: {images_with_related}")

        # Show sample
        for visit_id_str, images in list(visit_images.items())[:3]:
            print(f"     Visit {visit_id_str}: {len(images)} images")
            if images:
                img = images[0]
                print(f"       - blob_id: {img.get('blob_id', '')[:20]}...")
                print(f"         question_id: {img.get('question_id')}")
                print(f"         name: {img.get('name')}")
                # Show related fields if present
                related_fields = img.get("related_fields", [])
                if related_fields:
                    print("         related_fields:")
                    for rf in related_fields:
                        print(f"           - {rf.get('label')}: {rf.get('value')}")

        # Step 6: Create session (passing pre-extracted images to avoid redundant fetch)
        print("\n[6] Creating audit session...")
        start = time.time()
        session = data_access.create_audit_session(
            username=username,
            visit_ids=visit_ids,
            title="Optimized Integration Test",
            tag="optimized_test",
            opportunity_id=test_opp_id,
            criteria=criteria,
            visit_images=visit_images,  # Pass pre-extracted images (optimization)
            workflow_run_id=None,  # Not created from a workflow
        )
        elapsed = time.time() - start
        print(f"[OK] Created session ID: {session.id} in {elapsed:.2f}s")
        print(f"     Title: {session.title}")
        print(f"     Status: {session.status}")
        print(f"     Visit count: {len(session.visit_ids)}")

        # Verify visit_images in session
        session_images = session.data.get("visit_images", {})
        session_total_images = sum(len(imgs) for imgs in session_images.values())
        print(f"     Images in session: {session_total_images}")

        # Step 7: Complete session
        print("\n[7] Completing audit session...")
        session = data_access.complete_audit_session(
            session=session,
            overall_result="pass",
            notes="Optimized integration test completed",
            kpi_notes="Memory-efficient processing verified",
        )
        print("[OK] Session completed")
        print(f"     Status: {session.status}")
        print(f"     Overall result: {session.overall_result}")

        print("\n" + "=" * 80)
        print("INTEGRATION TEST COMPLETE")
        print("=" * 80)
        print(f"\nSession ID: {session.id}")
        print(f"Total visits: {len(session.visit_ids)}")
        print(f"Total images: {session_total_images}")

    finally:
        data_access.close()


if __name__ == "__main__":
    import django

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    django.setup()

    config_name = sys.argv[1] if len(sys.argv) > 1 else "opp385_last10"
    test_optimized_audit_flow(config_name)
