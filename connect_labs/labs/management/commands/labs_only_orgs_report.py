"""List every labs-only organisation with its admins, opps and allowed domains. Read-only.

For checking, after a deploy, the admin grants migration `synthetic/0010` made.
Writes nothing.

    python manage.py labs_only_orgs_report

One line per labs-only organisation:

    <pk> <key> "<name>" admins=<usernames, or -> opps=<n> enabled=<n> domains=<n> open=<n>

`admins` are the admin memberships that count (granted for this organisation).
A membership that sits on it but was granted for another row (moved there by a
merge) is listed as `moved-in=` and is NOT an admin. `domains` counts the
distinct allowed email domains across its opps; `open` counts opps with no
allowed domains at all (visible to anyone with synthetic opps turned on).
Opps with no organisation are listed last.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from connect_labs.labs.models import LABS_ONLY_ORG_PREFIX, LabsOrg
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.marketplace.models import OrgMembership


class Command(BaseCommand):
    help = "Read-only: each labs-only organisation, its admins, opp count and allowed-domain count."

    def handle(self, *args, **options):
        rows = LabsOrg.objects.filter(Q(labs_only=True) | Q(slug__startswith=LABS_ONLY_ORG_PREFIX)).order_by("slug")
        for org in rows:
            members = OrgMembership.objects.filter(org=org).select_related("user").order_by("user__username")
            admins = [m.user.username for m in members if m.role == "admin" and m.granted_for_org_id == org.pk]
            moved = [m.user.username for m in members if m.granted_for_org_id != org.pk]
            opps = list(SyntheticOpportunity.objects.filter(labs_org=org, labs_only=True))
            domains = {d.strip().lower() for o in opps for d in (o.allowed_domains or []) if d.strip()}
            line = (
                f'{org.pk} {org.slug} "{org.name}" admins={",".join(admins) or "-"} '
                f"opps={len(opps)} enabled={sum(1 for o in opps if o.enabled)} "
                f"domains={len(domains)} open={sum(1 for o in opps if not o.allowed_domains)}"
            )
            if not org.labs_only:
                line += " NOT-FLAGGED-LABS-ONLY"
            if org.connect_organization_id is not None:
                line += f" LINKED-TO-CONNECT={org.connect_organization_id}"
            if moved:
                line += f" moved-in={','.join(moved)}"
            self.stdout.write(line)

        unfiled = SyntheticOpportunity.objects.filter(labs_only=True, labs_org__isnull=True).order_by("opportunity_id")
        for opp in unfiled:
            self.stdout.write(f'unfiled opp {opp.opportunity_id} org_name="{opp.org_name}"')
        self.stdout.write(f"{rows.count()} labs-only organisation(s), {unfiled.count()} unfiled opp(s)")
