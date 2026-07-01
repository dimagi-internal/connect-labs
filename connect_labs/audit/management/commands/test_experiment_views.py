"""
Management command to test all audit views by making actual HTTP requests.

This simulates real user navigation to catch runtime errors.

Usage:
    python manage.py test_experiment_views
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client, override_settings

User = get_user_model()


class Command(BaseCommand):
    help = "Test all audit views by making actual HTTP requests"

    def handle(self, *args, **options):
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("AUDIT VIEWS RUNTIME TEST"))
        self.stdout.write("=" * 80)

        errors = []
        tests_passed = 0

        # Create test user
        self.stdout.write("\n[Setup] Creating test user...")
        try:
            # Try to get existing user first
            try:
                user = User.objects.get(username="test_audit_user")
            except User.DoesNotExist:
                user = User.objects.create_user(
                    username="test_audit_user",
                    email=f"test_audit_{User.objects.count()}@example.com",
                    password="testpass123",
                )
            self.stdout.write("    [OK] Test user ready")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"    [FAIL] Could not create test user: {e}"))
            return

        # Allow test host so the Client() requests pass ALLOWED_HOSTS.
        with override_settings(ALLOWED_HOSTS=["*"]):
            client = Client()
            client.force_login(user)

            # Test GET views
            self.stdout.write("\n[1] Testing GET views...")
            get_views = [
                ("Session List", "/audit/"),
                ("Create Wizard", "/audit/create/"),
                ("Session Detail (no data)", "/audit/1/"),  # Will 404 if no session exists
                ("Legacy Session Export (no data)", "/audit/sessions/1/export/"),  # Legacy route
                ("Export All", "/audit/export-all/"),
            ]

            for view_name, url in get_views:
                try:
                    response = client.get(url, follow=True)
                    if response.status_code in [200, 302, 404]:  # 404 is ok for detail views without data
                        self.stdout.write(f"    [OK] {view_name} ({url}) -> {response.status_code}")
                        tests_passed += 1
                    else:
                        error_msg = f"{view_name} ({url}) returned {response.status_code}"
                        errors.append(error_msg)
                        self.stdout.write(self.style.ERROR(f"    [FAIL] {error_msg}"))
                        # Show response content for debugging
                        if hasattr(response, "content"):
                            content = response.content.decode("utf-8")[:200]
                            self.stdout.write(self.style.ERROR(f"           Response preview: {content}..."))
                except Exception as e:
                    import traceback

                    error_msg = f"{view_name} ({url}) raised exception"
                    errors.append(error_msg)
                    self.stdout.write(self.style.ERROR(f"    [FAIL] {error_msg}"))
                    self.stdout.write(self.style.ERROR(f"           Exception: {e}"))
                    self.stdout.write(self.style.ERROR("           Traceback:"))
                    for line in traceback.format_exc().split("\n"):
                        if line.strip():
                            self.stdout.write(self.style.ERROR(f"           {line}"))

            # Test API endpoints (GET)
            self.stdout.write("\n[2] Testing API GET endpoints...")
            api_get_endpoints = [
                ("Opportunity Search", "/audit/api/opportunities/search/?q=test&limit=10"),
                ("Database Stats", "/audit/api/database/stats/"),
                ("Progress Tracker (no task)", "/audit/api/audit/progress/?task_id=test123"),
                ("Image Serve (no blob)", "/audit/image/test_blob_id/"),
            ]

            for view_name, url in api_get_endpoints:
                try:
                    response = client.get(url)
                    # 200=success, 302=redirect, 400=bad request, 404=not found are all OK
                    if response.status_code in [200, 302, 400, 404]:
                        self.stdout.write(f"    [OK] {view_name} ({url}) -> {response.status_code}")
                        tests_passed += 1
                    else:
                        error_msg = f"{view_name} ({url}) returned {response.status_code}"
                        errors.append(error_msg)
                        self.stdout.write(self.style.ERROR(f"    [FAIL] {error_msg}"))
                except Exception as e:
                    import traceback

                    error_msg = f"{view_name} ({url}) raised exception"
                    errors.append(error_msg)
                    self.stdout.write(self.style.ERROR(f"    [FAIL] {error_msg}"))
                    self.stdout.write(self.style.ERROR(f"           Exception: {e}"))
                    self.stdout.write(self.style.ERROR("           Traceback:"))
                    for line in traceback.format_exc().split("\n"):
                        if line.strip():
                            self.stdout.write(self.style.ERROR(f"           {line}"))

            # Test POST API endpoints (will fail with 400 for missing data, but should not error)
            self.stdout.write("\n[3] Testing POST API endpoints (expect 400 for missing data)...")
            api_post_endpoints = [
                ("Audit Preview API", "/audit/api/audit/preview/"),
                ("Audit Create API", "/audit/api/audit/create/"),
            ]

            for view_name, url in api_post_endpoints:
                try:
                    response = client.post(url, data={}, content_type="application/json")
                    # 400=bad request expected for missing data, 302=redirect OK
                    if response.status_code in [400, 302]:
                        self.stdout.write(f"    [OK] {view_name} ({url}) -> {response.status_code}")
                        tests_passed += 1
                    else:
                        error_msg = f"{view_name} ({url}) returned unexpected {response.status_code}"
                        errors.append(error_msg)
                        self.stdout.write(self.style.ERROR(f"    [FAIL] {error_msg}"))
                except Exception as e:
                    import traceback

                    error_msg = f"{view_name} ({url}) raised exception"
                    errors.append(error_msg)
                    self.stdout.write(self.style.ERROR(f"    [FAIL] {error_msg}"))
                    self.stdout.write(self.style.ERROR(f"           Exception: {e}"))
                    self.stdout.write(self.style.ERROR("           Traceback:"))
                    for line in traceback.format_exc().split("\n"):
                        if line.strip():
                            self.stdout.write(self.style.ERROR(f"           {line}"))

        # Summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("TEST SUMMARY")
        self.stdout.write("=" * 80)
        self.stdout.write(f"Tests passed: {tests_passed}")
        self.stdout.write(f"Errors: {len(errors)}")

        if errors:
            self.stdout.write("\n" + self.style.ERROR("ERRORS FOUND:"))
            for error in errors:
                self.stdout.write(self.style.ERROR(f"  - {error}"))
            self.stdout.write("\n" + self.style.ERROR("[FAIL] Some views have errors"))
        else:
            self.stdout.write("\n" + self.style.SUCCESS("[SUCCESS] All views responded correctly!"))
