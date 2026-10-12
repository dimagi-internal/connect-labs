"""File labs-only opps that have no organisation yet. Idempotent; run after each deploy of #2435's work.

Migration `synthetic/0010` files every labs-only opp that exists when it runs.
During the deploy, the previous code keeps serving requests for a few minutes
and can make labs-only opps without an organisation; this files them the same
way (their name's old bucket). Until it runs they are keyed by an unfiled key of
their own, which only staff can configure, so nothing leaks while they wait.

    python manage.py labs_only_orgs_backfill
"""

from django.core.management.base import BaseCommand

from connect_labs.labs.synthetic.org_tree import file_unfiled_opps


class Command(BaseCommand):
    help = "File labs-only synthetic opps that have no labs-only organisation yet (idempotent)."

    def handle(self, *args, **options):
        report = file_unfiled_opps()
        self.stdout.write(f"filed {report['filed']} opp(s)")
        for key in report["made"]:
            self.stdout.write(f"made labs-only organisation {key}")
        if report["left_unfiled"]:
            self.stdout.write(
                "left unfiled (a non-labs-only organisation holds their old key; staff to decide): "
                + ", ".join(str(i) for i in report["left_unfiled"])
            )
