"""
Management command to test LabsRecord creation locally with production data.

Reads opportunities/visits from production but calls the LabsRecord view directly
to test if the bug exists in our local code.

Usage:
    python manage.py test_labs_record_local
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.test import APIRequestFactory

from connect_labs.labs.integrations.connect.cli import TokenManager
from connect_labs.opportunity.models import Opportunity


class Command(BaseCommand):
    help = "Test LabsRecord creation locally with production data"

    def handle(self, *args, **options):
        self.stdout.write(self.style.ERROR("This command is no longer available: data_export app was removed."))
        return

        # Step 1: Get OAuth token for production
        self.stdout.write("\n[1] Getting OAuth token...")
        access_token = os.getenv("CONNECT_OAUTH_TOKEN")
        if not access_token:
            token_manager = TokenManager()
            access_token = token_manager.get_valid_token()
            if not access_token:
                self.stdout.write(self.style.ERROR("No OAuth token found. Run: python manage.py get_cli_token"))
                return
        self.stdout.write(self.style.SUCCESS("[OK] Token retrieved"))

        # Step 2: Use test data (simulating what production sends)
        self.stdout.write("\n[2] Using test data from production...")
        opp = {"id": 385, "name": "[Kenya] Readers-C1"}
        self.stdout.write(self.style.SUCCESS(f"[OK] Using opportunity data: {opp['id']} - {opp['name']}"))

        # Step 3: Get local opportunity for the view (any opportunity will do for testing)
        self.stdout.write("\n[3] Getting local opportunity object...")
        local_opportunity = Opportunity.objects.first()
        if not local_opportunity:
            self.stdout.write(self.style.ERROR("No opportunities found in local database"))
            return
        self.stdout.write(
            self.style.SUCCESS(f"[OK] Using local opportunity: {local_opportunity.id} - {local_opportunity.name}")
        )

        # Step 4: Prepare payload (simulating what client sends)
        self.stdout.write("\n[4] Preparing test payload...")
        payload = [
            {
                "experiment": "audit",
                "type": "AuditTemplate",
                "data": {
                    "opportunity_ids": [opp["id"]],
                    "audit_type": "last_n_across_all",
                    "granularity": "combined",
                    "preview_data": [{"total_visits": 10}],
                    "sample_percentage": 100,
                    "count_across_all": 10,
                },
                # Note: NO 'id' field - this is what causes the bug
            }
        ]
        self.stdout.write(f"Payload: {payload}")

        # Step 5: Call the view directly
        self.stdout.write("\n[5] Calling LabsRecordDataView.create() directly...")

        try:
            # Create a fake request
            factory = APIRequestFactory()
            request = factory.post("/export/opportunity/764/labs_record/", payload, format="json")

            # Mock the user
            User = get_user_model()
            request.user = User.objects.first()

            # Create view instance
            view = LabsRecordDataView()
            view.opportunity = local_opportunity
            view.request = request
            view.format_kwarg = None
            view.kwargs = {"opp_id": local_opportunity.id}

            # Call create with the request data
            request.data = payload

            self.stdout.write(self.style.WARNING("[TEST] Calling view.create() with id=None (PRODUCTION BUG)..."))
            response = view.create(request)

            self.stdout.write(self.style.SUCCESS(f"[SUCCESS] Response status: {response.status_code}"))
            self.stdout.write(f"Response data: {response.data}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[EXPECTED ERROR] {type(e).__name__}: {e}"))
            import traceback

            traceback.print_exc()

            # Now test with the fix
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write("Testing with a workaround (adding fake ID)...")
            self.stdout.write("=" * 80)

            payload_with_id = [
                {
                    "experiment": "audit",
                    "type": "AuditTemplate",
                    "data": {
                        "opportunity_ids": [opp["id"]],
                        "audit_type": "last_n_across_all",
                        "granularity": "combined",
                        "preview_data": [{"total_visits": 10}],
                        "sample_percentage": 100,
                        "count_across_all": 10,
                    },
                    "id": 99999,  # Add fake ID
                }
            ]

            try:
                request.data = payload_with_id
                response = view.create(request)
                self.stdout.write(self.style.SUCCESS(f"[SUCCESS WITH ID] Response status: {response.status_code}"))
                self.stdout.write(f"Response data: {response.data}")
            except Exception as e2:
                self.stdout.write(self.style.ERROR(f"[STILL FAILED] {type(e2).__name__}: {e2}"))
                import traceback

                traceback.print_exc()

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("TEST COMPLETE")
        self.stdout.write("=" * 80)
