from django.core.management.base import BaseCommand, CommandError

from connect_labs.hqdata.models import HQProject, HQSyncRun
from connect_labs.hqdata.sync import sync_project


class Command(BaseCommand):
    help = "Sync a CommCare project now (all of its enabled sources)."

    def add_arguments(self, parser):
        parser.add_argument("project_id", type=int)
        parser.add_argument("--mode", choices=[HQSyncRun.MODE_DELTA, HQSyncRun.MODE_FULL, HQSyncRun.MODE_RECONCILE])

    def handle(self, *args, project_id, mode=None, **options):
        try:
            project = HQProject.objects.get(pk=project_id)
        except HQProject.DoesNotExist:
            raise CommandError(f"No project {project_id}")
        for source_id, outcome in sync_project(project, mode=mode).items():
            if isinstance(outcome, Exception):
                self.stderr.write(f"source {source_id}: {type(outcome).__name__}: {outcome}")
            else:
                self.stdout.write(
                    f"source {source_id}: {outcome.mode} fetched={outcome.fetched} deleted={outcome.deleted}"
                )
