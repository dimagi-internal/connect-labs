"""
Management command to test async audit creation functionality.

Uses Django's test infrastructure to call the actual view endpoints,
ensuring maximum fidelity with the real UX flow.

Usage:
    # Quick diagnostic (no OAuth required)
    python manage.py test_async_audit --diagnostic

    # Full test with real audit task
    python manage.py test_async_audit --opportunity-id=874

    # Test with cleanup
    python manage.py test_async_audit --opportunity-id=874 --cleanup
"""

import json
import time
import uuid

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import BaseCommand
from django.test import RequestFactory


class Command(BaseCommand):
    help = "Test async audit creation functionality"

    def add_arguments(self, parser):
        parser.add_argument(
            "--diagnostic",
            action="store_true",
            help="Run diagnostic tests only (no OAuth required)",
        )
        parser.add_argument(
            "--opportunity-id",
            type=int,
            default=874,
            help="Opportunity ID for real audit test (default: 874)",
        )
        parser.add_argument(
            "--username",
            type=str,
            help="Override username (e.g., jjackson@dimagi.com) if OAuth introspection returns wrong value",
        )
        parser.add_argument(
            "--cleanup",
            action="store_true",
            help="Clean up test records after running",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=60,
            help="Max seconds to wait for async task completion (default: 60)",
        )

    def handle(self, *args, **options):
        diagnostic_only = options.get("diagnostic", False)
        opportunity_id = options["opportunity_id"]
        username_override = options.get("username")
        cleanup = options.get("cleanup", False)
        timeout = options.get("timeout", 60)

        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("ASYNC AUDIT CREATION TEST")
        self.stdout.write("=" * 70)

        # Section 1: Celery Configuration
        is_eager = self._test_celery_config()

        # Section 2: Broker Connectivity
        broker_ok = self._test_broker_connectivity()

        # Section 3: Simple Async Task Test
        async_ok = self._test_simple_async_task(is_eager)

        if diagnostic_only:
            self._print_summary(is_eager, broker_ok, async_ok)
            return

        # Section 4: OAuth Token & User Profile
        access_token, user_profile = self._get_oauth_token_and_profile(username_override)
        if not access_token or not user_profile:
            self._print_summary(is_eager, broker_ok, async_ok, oauth_ok=False)
            return

        # Section 5: Full Audit Creation Test via Django View
        audit_ok = self._test_audit_creation_via_view(
            access_token, user_profile, opportunity_id, is_eager, timeout, cleanup
        )

        self._print_summary(is_eager, broker_ok, async_ok, oauth_ok=True, audit_ok=audit_ok)

    def _test_celery_config(self):
        """Test Celery configuration and display settings."""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("[1] CELERY CONFIGURATION")
        self.stdout.write("-" * 70)

        eager = getattr(settings, "CELERY_TASK_ALWAYS_EAGER", None)
        broker = getattr(settings, "CELERY_BROKER_URL", None)
        result_backend = getattr(settings, "CELERY_RESULT_BACKEND", None)

        self.stdout.write(f"    CELERY_TASK_ALWAYS_EAGER: {eager}")
        self.stdout.write(f"    CELERY_BROKER_URL: {broker or '(not set)'}")
        self.stdout.write(f"    CELERY_RESULT_BACKEND: {result_backend or '(not set)'}")

        if eager:
            self.stdout.write(
                self.style.WARNING(
                    "\n    [!] EAGER MODE ENABLED - Tasks run synchronously!\n"
                    "    To test true async behavior:\n"
                    "    1. Add to .env: CELERY_TASK_ALWAYS_EAGER=False\n"
                    "    2. Start Redis: docker run -p 6379:6379 redis\n"
                    "    3. Start Celery worker:\n"
                    "       celery -A config.celery_app worker --loglevel=info --pool=solo"
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("\n    [OK] ASYNC MODE - Tasks will run via Celery worker"))

        return eager

    def _test_broker_connectivity(self):
        """Test Redis/broker connectivity."""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("[2] BROKER CONNECTIVITY")
        self.stdout.write("-" * 70)

        broker = getattr(settings, "CELERY_BROKER_URL", None)
        if not broker:
            self.stdout.write(self.style.WARNING("    [SKIP] No broker URL configured"))
            return False

        try:
            import redis

            r = redis.from_url(broker)
            r.ping()
            self.stdout.write(self.style.SUCCESS("    [OK] Redis connection successful"))
            return True
        except ImportError:
            self.stdout.write(self.style.WARNING("    [SKIP] redis package not installed"))
            return False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"    [FAIL] Redis connection failed: {e}"))
            return False

    def _test_simple_async_task(self, is_eager):
        """Test async behavior with a simple task."""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("[3] SIMPLE ASYNC TASK TEST")
        self.stdout.write("-" * 70)

        # Use the registered test task from audit.tasks
        from commcare_connect.audit.tasks import test_async_simple

        task_id = str(uuid.uuid4())
        self.stdout.write(f"    Task ID: {task_id}")
        self.stdout.write(f"    Starting task (should {'block' if is_eager else 'return immediately'})...")

        start_time = time.time()
        test_async_simple.apply_async(kwargs={"sleep_seconds": 3}, task_id=task_id)
        queue_time = time.time() - start_time

        self.stdout.write(f"    Queue call returned in: {queue_time:.3f}s")

        if is_eager:
            if queue_time > 2:
                self.stdout.write(self.style.SUCCESS("    [OK] Task ran synchronously (eager mode)"))
            else:
                self.stdout.write(self.style.WARNING("    [?] Faster than expected for eager mode"))
        else:
            if queue_time < 0.5:
                self.stdout.write(self.style.SUCCESS("    [OK] Task queued immediately (async mode)"))

                # Poll for completion
                self.stdout.write("    Polling for completion...")
                from celery.result import AsyncResult

                async_result = AsyncResult(task_id)

                for i in range(20):
                    time.sleep(0.5)
                    state = async_result.state
                    # result.info may be an exception object if task failed
                    info = async_result.info if isinstance(async_result.info, dict) else {}
                    msg = info.get("message", "")
                    self.stdout.write(f"      [{i*0.5:.1f}s] State: {state}, Message: {msg}")

                    if state == "SUCCESS":
                        self.stdout.write(self.style.SUCCESS("    [OK] Task completed successfully"))
                        return True
                    elif state == "FAILURE":
                        self.stdout.write(self.style.ERROR(f"    [FAIL] Task failed: {info}"))
                        return False

                self.stdout.write(self.style.WARNING("    [WARN] Task did not complete in expected time"))
                return False
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"    [FAIL] Task did not return immediately ({queue_time:.2f}s)\n"
                        "    This may indicate the Celery worker is not running."
                    )
                )
                return False

        return True

    def _get_oauth_token_and_profile(self, username_override=None):
        """Get OAuth token and user profile from TokenManager."""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("[4] OAUTH TOKEN & USER PROFILE")
        self.stdout.write("-" * 70)

        try:
            from commcare_connect.labs.integrations.connect.cli import TokenManager

            token_manager = TokenManager()
            access_token = token_manager.get_valid_token()

            if not access_token:
                self.stdout.write(
                    self.style.ERROR("    [FAIL] No valid OAuth token\n" "    Run: python manage.py get_cli_token")
                )
                return None, None

            info = token_manager.get_token_info()
            if info and "expires_in_seconds" in info:
                minutes = info["expires_in_seconds"] // 60
                self.stdout.write(self.style.SUCCESS(f"    [OK] Token valid (expires in {minutes} min)"))

            # Use username override if provided, otherwise try cached profile
            if username_override:
                self.stdout.write(f"    Using provided username: {username_override}")
                user_profile = {
                    "id": 0,
                    "username": username_override,
                    "email": username_override,
                }
            else:
                # Try to get cached user_profile from token file
                token_data = token_manager.load_token()
                user_profile = token_data.get("user_profile") if token_data else None

                if user_profile:
                    self.stdout.write(self.style.SUCCESS(f"    [OK] User: {user_profile.get('username')}"))
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            "    [FAIL] No user profile cached and no --username provided\n"
                            "    Please provide: --username=jjackson@dimagi.com"
                        )
                    )
                    return None, None

            return access_token, user_profile

        except Exception as e:
            import traceback

            self.stdout.write(self.style.ERROR(f"    [FAIL] Error: {e}"))
            traceback.print_exc()
            return None, None

    def _create_authenticated_request(
        self, access_token, user_profile, opportunity_id, method="POST", path="/", data=None
    ):
        """
        Create a Django request that mimics the real UX authentication flow.

        This sets up the request exactly as the Labs middleware would:
        - Session with labs_oauth token
        - labs_context with opportunity_id
        - Authenticated user via LabsOAuthBackend
        """
        from commcare_connect.users.models import User

        factory = RequestFactory()

        if method == "POST":
            request = factory.post(
                path,
                data=json.dumps(data) if data else "{}",
                content_type="application/json",
            )
        else:
            request = factory.get(path)

        # Set up session like the OAuth callback does
        session = SessionStore()
        session["labs_oauth"] = {
            "access_token": access_token,
            "expires_at": time.time() + 3600,
            "user_profile": user_profile,
            "organization_data": {
                "opportunities": [{"id": opportunity_id, "name": "Test Opportunity"}],
            },
        }
        session.create()
        request.session = session

        # Set up labs_context like LabsContextMiddleware does
        request.labs_context = {
            "opportunity_id": opportunity_id,
        }

        # Create a Django User with real user profile
        user, _ = User.objects.update_or_create(
            username=user_profile.get("username", "test"),
            defaults={
                "email": user_profile.get("email", ""),
                "name": f"{user_profile.get('first_name', '')} {user_profile.get('last_name', '')}".strip(),
            },
        )
        request.user = user

        return request

    def _test_audit_creation_via_view(self, access_token, user_profile, opportunity_id, is_eager, timeout, cleanup):
        """Test audit creation by calling the actual Django view."""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("[5] AUDIT CREATION VIA DJANGO VIEW")
        self.stdout.write("-" * 70)

        from commcare_connect.audit.views import ExperimentAuditCreateAsyncAPIView

        # Build request data exactly like the frontend would
        request_data = {
            "opportunities": [opportunity_id],
            "criteria": {
                "audit_type": "last_n_per_flw",
                "countPerFlw": 2,
                "title": "Async Test Audit",
            },
            "visit_ids": [],
            "flw_visit_ids": {},
        }

        self.stdout.write(f"    Opportunity ID: {opportunity_id}")
        self.stdout.write(f"    User: {user_profile.get('username')}")
        self.stdout.write(f"    Criteria: {request_data['criteria']}")

        # Create authenticated request with real user profile
        request = self._create_authenticated_request(
            access_token=access_token,
            user_profile=user_profile,
            opportunity_id=opportunity_id,
            method="POST",
            path="/audit/api/create-async/",
            data=request_data,
        )

        self.stdout.write("    Calling ExperimentAuditCreateAsyncAPIView.post()...")

        start_time = time.time()
        try:
            view = ExperimentAuditCreateAsyncAPIView()
            view.request = request
            response = view.post(request)
            queue_time = time.time() - start_time

            self.stdout.write(f"    View returned in: {queue_time:.3f}s")
            self.stdout.write(f"    Response status: {response.status_code}")

            response_data = json.loads(response.content)
            self.stdout.write(f"    Response: {json.dumps(response_data, indent=6)}")

            if response.status_code != 200:
                self.stdout.write(self.style.ERROR("    [FAIL] View returned error"))
                return False

            task_id = response_data.get("task_id")
            job_id = response_data.get("job_id")

            if not task_id:
                self.stdout.write(self.style.ERROR("    [FAIL] No task_id in response"))
                return False

            self.stdout.write(f"    Task ID: {task_id}")
            self.stdout.write(f"    Job ID: {job_id}")

            # Check immediate return
            if not is_eager and queue_time < 1.0:
                self.stdout.write(self.style.SUCCESS("    [OK] View returned immediately (async)"))
            elif is_eager:
                self.stdout.write("    (Eager mode: task ran synchronously)")

            # Poll for task completion
            return self._poll_task_completion(access_token, opportunity_id, task_id, job_id, timeout, cleanup)

        except Exception as e:
            import traceback

            self.stdout.write(self.style.ERROR(f"    [FAIL] Error: {e}"))
            traceback.print_exc()
            return False

    def _poll_task_completion(self, access_token, opportunity_id, task_id, job_id, timeout, cleanup):
        """Poll for task completion using both Celery result and job record."""
        self.stdout.write("\n    Polling for completion...")

        from celery.result import AsyncResult

        from commcare_connect.audit.data_access import AuditDataAccess, create_mock_request

        # Create data access to check job records
        mock_request = create_mock_request(access_token, opportunity_id)
        data_access = AuditDataAccess(opportunity_id=opportunity_id, request=mock_request)

        try:
            async_result = AsyncResult(task_id)
            final_status = None
            final_result = None

            for i in range(timeout):
                time.sleep(1)

                # Check Celery state
                celery_state = async_result.state
                _ = async_result.info or {}  # Intentionally unused, kept for potential debugging

                # Check job record
                job_data = data_access.get_audit_creation_job_by_task_id(task_id)
                job_status = job_data["data"].get("status") if job_data else "not_found"
                job_progress = job_data["data"].get("progress", {}) if job_data else {}
                stage = job_progress.get("stage_name", "")

                self.stdout.write(f"      [{i+1}s] Celery: {celery_state}, Job: {job_status}, Stage: {stage}")

                if job_status in ("completed", "failed", "cancelled"):
                    final_status = job_status
                    final_result = job_data["data"] if job_data else {}
                    break

                if celery_state == "SUCCESS" and job_status == "pending":
                    self.stdout.write(self.style.WARNING("      [WARN] Celery done but job not updated"))

            # Report results
            if final_status == "completed":
                result = final_result.get("result", {})
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\n    [OK] Audit creation completed!\n"
                        f"         Sessions: {len(result.get('sessions', []))}\n"
                        f"         Visits: {result.get('total_visits')}\n"
                        f"         Images: {result.get('total_images')}"
                    )
                )
                success = True
            elif final_status == "failed":
                error = final_result.get("error", "Unknown")
                self.stdout.write(self.style.ERROR(f"    [FAIL] Task failed: {error}"))
                success = False
            elif final_status == "pending":
                self.stdout.write(
                    self.style.ERROR(
                        "    [FAIL] Job still PENDING after task!\n" "    Task could not update the job record."
                    )
                )
                success = False
            else:
                self.stdout.write(self.style.WARNING(f"    [WARN] Task did not complete in {timeout}s"))
                success = False

            # Cleanup
            if cleanup and job_id:
                self.stdout.write("    Cleaning up...")
                if data_access.delete_audit_creation_job(job_id):
                    self.stdout.write("    Deleted job record")

            return success

        finally:
            data_access.close()

    def _print_summary(self, is_eager, broker_ok, async_ok, oauth_ok=None, audit_ok=None):
        """Print test summary."""
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 70)

        mode = "SYNCHRONOUS (eager)" if is_eager else "ASYNCHRONOUS"
        self.stdout.write(f"    Celery Mode: {mode}")
        self.stdout.write(f"    Broker Connection: {'OK' if broker_ok else 'FAILED/SKIPPED'}")
        self.stdout.write(f"    Simple Task Test: {'PASS' if async_ok else 'FAIL'}")

        if oauth_ok is not None:
            self.stdout.write(f"    OAuth Token: {'OK' if oauth_ok else 'MISSING'}")
        if audit_ok is not None:
            self.stdout.write(f"    Audit Creation: {'PASS' if audit_ok else 'FAIL'}")

        self.stdout.write("")

        if is_eager:
            self.stdout.write(
                self.style.WARNING(
                    "To enable TRUE async behavior:\n"
                    "  1. Add to .env: CELERY_TASK_ALWAYS_EAGER=False\n"
                    "  2. Start Redis: docker run -p 6379:6379 redis\n"
                    "  3. Start worker: celery -A config.celery_app worker -l info --pool=solo\n"
                    "  4. Re-run this test"
                )
            )

        all_passed = async_ok and (audit_ok is None or audit_ok)
        if all_passed:
            self.stdout.write(self.style.SUCCESS("\n[SUCCESS] All tests passed!"))
        else:
            self.stdout.write(self.style.ERROR("\n[FAILURE] Some tests failed - see details above"))

        self.stdout.write("=" * 70 + "\n")
