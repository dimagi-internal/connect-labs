"""
Integration test for workflows using real OAuth and LabsRecords.

Creates workflow definitions from templates, verifies they load correctly,
and optionally cleans up after.

Usage:
    # First, ensure you have a valid token:
    python manage.py get_cli_token --settings=config.settings.local

    # Run the integration test:
    python manage.py test_workflow_integration --settings=config.settings.local

    # Test all templates with cleanup:
    python manage.py test_workflow_integration --all --cleanup --settings=config.settings.local
"""

import logging
import sys

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

DEFAULT_OPPORTUNITY_ID = 879


class Command(BaseCommand):
    help = "Run integration tests for workflow system using real OAuth"

    def add_arguments(self, parser):
        parser.add_argument(
            "--template",
            type=str,
            default="performance_review",
            help="Which template to test (default: performance_review)",
        )
        parser.add_argument(
            "--cleanup",
            action="store_true",
            help="Delete created records after test",
        )
        parser.add_argument(
            "--opportunity-id",
            type=int,
            default=DEFAULT_OPPORTUNITY_ID,
            help=f"Opportunity ID for scoping records (default: {DEFAULT_OPPORTUNITY_ID})",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List available templates",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Test all templates",
        )

    def handle(self, *args, **options):
        from connect_labs.labs.integrations.connect.cli.token_manager import TokenManager
        from connect_labs.workflow.templates import TEMPLATES, list_templates

        # List templates
        if options["list"]:
            self.stdout.write("\nAvailable workflow templates:\n")
            for template in list_templates():
                self.stdout.write(f"  {template['key']}: {template['name']}")
            return

        # Get OAuth token
        token_manager = TokenManager()
        access_token = token_manager.get_valid_token()
        if not access_token:
            raise CommandError(
                "No valid OAuth token. Run: python manage.py get_cli_token --settings=config.settings.local"
            )

        opportunity_id = options["opportunity_id"]
        self.stdout.write(f"Using opportunity_id: {opportunity_id}")

        # Determine templates to test
        templates_to_test = list(TEMPLATES.keys()) if options["all"] else [options["template"]]

        # Run tests
        results = []
        for template_key in templates_to_test:
            if template_key not in TEMPLATES:
                self.stdout.write(self.style.ERROR(f"Unknown template: {template_key}"))
                continue

            self.stdout.write(f"\n{'=' * 50}")
            self.stdout.write(f"Testing: {template_key}")
            self.stdout.write("=" * 50)

            result = self._test_template(
                template_key=template_key,
                access_token=access_token,
                opportunity_id=opportunity_id,
                cleanup=options["cleanup"],
            )
            results.append((template_key, result))

        # Summary
        self.stdout.write(f"\n{'=' * 50}")
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 50)

        passed = sum(1 for _, r in results if r["success"])
        failed = len(results) - passed

        for template_key, result in results:
            if result["success"]:
                self.stdout.write(self.style.SUCCESS(f"  [PASS] {template_key}"))
            else:
                self.stdout.write(self.style.ERROR(f"  [FAIL] {template_key}: {result['error']}"))

        self.stdout.write(f"\nTotal: {passed} passed, {failed} failed")

        if failed > 0:
            sys.exit(1)

    def _test_template(self, template_key: str, access_token: str, opportunity_id: int, cleanup: bool) -> dict:
        """Test a single template - create, verify, optionally cleanup."""
        from connect_labs.workflow.data_access import WorkflowDataAccess
        from connect_labs.workflow.templates import create_workflow_from_template

        result = {"success": False, "error": None, "definition_id": None, "pipeline_id": None}
        data_access = None

        try:
            # Initialize
            data_access = WorkflowDataAccess(access_token=access_token, opportunity_id=opportunity_id)

            # Create workflow from template
            # Note: pipeline won't be created in CLI context (no request object)
            self.stdout.write("  Creating workflow...")
            definition, render_code, pipeline = create_workflow_from_template(data_access, template_key)
            result["definition_id"] = definition.id
            if pipeline:
                result["pipeline_id"] = pipeline.id
                self.stdout.write(f"  Created definition ID: {definition.id} with pipeline ID: {pipeline.id}")
            else:
                self.stdout.write(f"  Created definition ID: {definition.id} (no pipeline - CLI mode)")

            # Verify definition loads
            self.stdout.write("  Verifying definition...")
            loaded = data_access.get_definition(definition.id)
            if not loaded:
                raise Exception("Could not load definition after creation")
            self.stdout.write(self.style.SUCCESS("  Definition OK"))

            # Verify render code loads
            self.stdout.write("  Verifying render code...")
            render = data_access.get_render_code(definition.id)
            if not render or not render.component_code:
                raise Exception("Could not load render code after creation")
            if "function WorkflowUI" not in render.component_code:
                raise Exception("Render code missing WorkflowUI function")
            self.stdout.write(self.style.SUCCESS("  Render code OK"))

            result["success"] = True

        except Exception as e:
            result["error"] = str(e)
            self.stdout.write(self.style.ERROR(f"  FAILED: {e}"))
            logger.exception("Integration test failed")

        finally:
            if cleanup and result["definition_id"] and data_access:
                self.stdout.write("  Cleaning up...")
                try:
                    data_access.delete_definition(result["definition_id"])
                    self.stdout.write("  Deleted")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  Cleanup failed: {e}"))

            if data_access:
                data_access.close()

        return result
