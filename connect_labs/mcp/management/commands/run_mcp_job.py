"""Run one long-running MCP job on the standalone ECS task launched for it.

Not for people: ``connect_labs.mcp.jobs.launch_standalone`` starts a Fargate task with
this as its command, for a tool registered with ``job_runner="standalone"``.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run a launched long-running MCP job (the command of its standalone ECS task)."

    def add_arguments(self, parser):
        parser.add_argument("job_id")

    def handle(self, *args, **opts):
        import os

        from connect_labs.mcp import jobs

        # The task's container sets this already; set it so nothing here launches another.
        os.environ[jobs.KIND.runner_env] = "1"
        outcome = jobs.run_standalone(opts["job_id"])
        self.stdout.write(f"MCP job {opts['job_id']}: {outcome}")
