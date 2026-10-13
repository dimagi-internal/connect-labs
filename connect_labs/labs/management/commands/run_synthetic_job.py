"""Run one synthetic job on the standalone ECS task launched for it.

Not for people: ``connect_labs.labs.synthetic.ecs_runner.launch`` starts a Fargate
task with this as its command. See that module for why.
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run a launched synthetic job (the command of its standalone ECS task)."

    def add_arguments(self, parser):
        parser.add_argument("job_id")

    def handle(self, *args, **opts):
        from connect_labs.labs.synthetic.ecs_runner import run_job

        try:
            run_job(opts["job_id"])
        except Exception as exc:
            # Recorded as the job's FAILURE already; a non-zero exit marks the task too.
            raise CommandError(f"synthetic job {opts['job_id']} failed: {exc}") from exc
