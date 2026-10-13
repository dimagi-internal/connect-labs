"""Seed an organisation running its own procurement on labs (#2458).

    make manage CMD="supply_seed_org_procurement oes"
    make manage CMD="supply_seed_org_procurement llo --local-admin"

Registers a labs-only programme, declares its running organisation, and seeds
its sourcing and orders through the same operations a person uses. See
`supply_chain/demo/org_procurement.py` for the two organisations and why.

`--local-admin` also makes a LOCAL-ONLY admin (a `dev-` handle on a `.example`
address, no usable password) for walking it with `/supply/dev-login/?as=`.
It is refused unless DEBUG is on: on labs the real admin joins by invitation.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from connect_labs.supply_chain.demo import org_procurement


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("org", choices=sorted(org_procurement.SPECS))
        parser.add_argument("--local-admin", action="store_true")

    def handle(self, *args, org, local_admin, **options):
        admin = None
        if local_admin:
            if not settings.DEBUG:
                raise CommandError("--local-admin makes a sign-in-able local user, so it runs only with DEBUG on")
            admin = org_procurement.ensure_admin(org_procurement.SPECS[org])
        result = org_procurement.seed(org, admin=admin)
        for key, value in result.items():
            self.stdout.write(f"{key}: {value}")
