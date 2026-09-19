"""Read every real opportunity's invoices now, rather than 25 per slow sweep.

python manage.py pulse_sync_invoices            # all real opportunities
python manage.py pulse_sync_invoices --opp 2063
"""

from django.core.management.base import BaseCommand

from connect_labs.pulse import ingest
from connect_labs.pulse.client import get_client
from connect_labs.pulse.models import PulseOpportunity


class Command(BaseCommand):
    help = "Mirror Connect's invoices for every real opportunity."

    def add_arguments(self, parser):
        parser.add_argument("--opp", type=int, action="append", default=[])

    def handle(self, *args, **opts):
        opps = PulseOpportunity.objects.filter(is_test=False).order_by("opportunity_id")
        if opts["opp"]:
            opps = opps.filter(opportunity_id__in=opts["opp"])
        read = failed = invoices = 0
        with get_client() as client:
            for opp in opps:
                try:
                    invoices += ingest.refresh_invoices(client, opp)
                    read += 1
                except Exception as exc:  # noqa: BLE001 — report and carry on
                    failed += 1
                    self.stdout.write(f"  opp {opp.opportunity_id}: {exc}")
        self.stdout.write(f"{read} opportunities read, {invoices} invoices mirrored, {failed} failed")
