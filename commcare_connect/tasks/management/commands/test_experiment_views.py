"""
Management command to test all task views by making actual HTTP requests.

This simulates real user navigation to catch runtime errors.

Usage:
    python manage.py test_experiment_views
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client, override_settings

User = get_user_model()


class Command(BaseCommand):
    help = "Test all task views by making actual HTTP requests"

    def handle(self, *args, **options):
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("TASKS VIEWS RUNTIME TEST"))
        self.stdout.write("=" * 80)

        errors = []
        tests_passed = 0

        # Create test user
        self.stdout.write("\n[Setup] Creating test user...")
        try:
            # Try to get existing user first
            try:
                user = User.objects.get(username="test_tasks_user")
            except User.DoesNotExist:
                user = User.objects.create_user(
                    username="test_tasks_user",
                    email=f"test_tasks_{User.objects.count()}@example.com",
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
            errors, tests_passed = self._run_all_tests(client, errors, tests_passed)

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

    def _run_all_tests(self, client, errors, tests_passed):
        """Run all test sections and return updated error and test counts."""

        # Test GET views
        self.stdout.write("\n[1] Testing GET views...")
        get_views = [
            ("Task List", "/tasks/"),
            ("Task Create", "/tasks/create/"),
            ("Task Detail (no data)", "/tasks/1/"),  # Will 404 if no task exists
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

        # Test Opportunity API endpoints (used by wizard)
        self.stdout.write("\n[2] Testing Opportunity API endpoints...")
        api_get_endpoints = [
            ("Opportunity Search", "/tasks/opportunities/search/?q=test&limit=10"),
            ("Opportunity Workers (no opp)", "/tasks/opportunities/999/workers/"),
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

        # Test AI Assistant endpoints
        self.stdout.write("\n[3] Testing AI Assistant endpoints...")
        test_task_id = 1  # Using task ID 1 for testing (will return 404 if no task exists)
        ai_endpoints = [
            ("AI Sessions (GET)", f"/tasks/{test_task_id}/ai/sessions/", "get"),
            ("AI Add Session (POST)", f"/tasks/{test_task_id}/ai/add-session/", "post"),
            ("AI Transcript (GET)", f"/tasks/{test_task_id}/ai/transcript/", "get"),
        ]

        for view_name, url, method in ai_endpoints:
            try:
                if method == "get":
                    response = client.get(url)
                else:
                    response = client.post(url, data={"session_id": "test-session"})

                # Should work or return 302 (redirect to login if needed) or 404 (no AI session yet)
                if response.status_code in [200, 302, 404]:
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

        # Test Database Management endpoints
        self.stdout.write("\n[4] Testing Database Management endpoints...")
        db_endpoints = [
            ("Database Stats (GET)", "/tasks/api/database/stats/", "get"),
            ("Database Reset (POST)", "/tasks/api/database/reset/", "post"),
        ]

        for view_name, url, method in db_endpoints:
            try:
                if method == "get":
                    response = client.get(url)
                else:
                    response = client.post(url, data={}, content_type="application/json")

                # Should work or return 302 (redirect to login if needed)
                if response.status_code in [200, 302]:
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

        return errors, tests_passed
