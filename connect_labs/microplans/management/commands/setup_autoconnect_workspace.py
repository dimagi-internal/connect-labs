"""Create (or update) the Auto-Connect workspace: a labs-only organisation with
one labs-only microplans program, run with roles (``labs.workspaces``).

    manage.py setup_autoconnect_workspace
    manage.py setup_autoconnect_workspace --connect-program-id 9001 --connect-program-id 9002
    manage.py setup_autoconnect_workspace --remove-connect-program-id 9002
    manage.py setup_autoconnect_workspace --admin some.username

Idempotent: a second run finds the workspace by its organisation and program
names and only applies the changes asked for. Nothing is hard-coded: the ids it
prints are whatever this database allocated.

  * ``--connect-program-id`` (repeatable) links a REAL Connect program; anyone in a
    Connect organisation delivering an opportunity in a linked program is a
    Network Manager, their organisation their team. Usually one program per
    partner, created over time -- a Program Manager can also change the list on
    the workspace settings page. None linked is valid: only Program Managers can
    use it until one is.
  * ``--admin`` (repeatable) makes an existing labs user a Program Manager though
    they are not Dimagi staff (an admin of the organisation). They must have
    signed in to labs once.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


def ensure_workspace(org_name: str, program_name: str):
    """The workspace's (organisation, synthetic opp), made if missing.

    Refuses to turn an organisation that already holds other programs into a
    workspace: that would change who can open those programs."""
    from connect_labs.labs.models import Workspace
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    existing = (
        SyntheticOpportunity.objects.filter(
            labs_only=True, program_name=program_name, labs_org__name=org_name, labs_org__workspace__isnull=False
        )
        .select_related("labs_org")
        .order_by("pk")
        .first()
    )
    if existing is not None:
        return existing.labs_org, existing, False

    with transaction.atomic():
        # Created directly, as the study seeder does, rather than through
        # `register_labs_only_opp`: there are no fixtures to point at (plans live in
        # labs-local records), and registering would go and count visits in Drive.
        # save() files it under a new labs-only organisation (org_tree).
        new_id = SyntheticOpportunity.next_labs_only_opp_id()
        # The opp is its own program, so its id must not already name another
        # program (an id other opps were filed under explicitly).
        while SyntheticOpportunity.objects.filter(program_id=new_id).exists():
            new_id += 1
        opp = SyntheticOpportunity.objects.create(
            opportunity_id=new_id,
            labs_only=True,
            enabled=True,
            label=program_name,
            org_name=org_name,
            program_name=program_name,
            gdrive_folder_id="",
            # Dimagi only; the workspace roles replace this rule for its programs.
            allowed_domains=["@dimagi.com"],
        )
        org = opp.labs_org
        others = SyntheticOpportunity.objects.filter(labs_org=org).exclude(pk=opp.pk)
        if org is None or others.exists():
            raise CommandError(
                f"the labs-only organisation {org_name!r} already holds other programs; "
                "pick another --org-name rather than turning them into a workspace"
            )
        Workspace.objects.get_or_create(org=org)
    return org, opp, True


class Command(BaseCommand):
    help = "Create or update the Auto-Connect workspace (labs-only org + microplans program with roles)"

    def add_arguments(self, parser):
        parser.add_argument("--org-name", default="Auto-Connect")
        parser.add_argument("--program-name", default="CLP Stage 4")
        parser.add_argument(
            "--connect-program-id",
            type=int,
            action="append",
            default=[],
            help="a real Connect program whose delivering organisations are Network Managers (repeatable)",
        )
        parser.add_argument(
            "--remove-connect-program-id",
            type=int,
            action="append",
            default=[],
            help="unlink a Connect program (repeatable)",
        )
        parser.add_argument(
            "--admin",
            action="append",
            default=[],
            help="labs username to make a Program Manager of the workspace (repeatable)",
        )

    def handle(self, *args, **opts):
        from connect_labs.labs.synthetic.org_tree import synthetic_program_id
        from connect_labs.marketplace.models import OrgMembership

        org, opp, created = ensure_workspace(opts["org_name"], opts["program_name"])
        workspace = org.workspace

        linked = set(workspace.connect_program_ids)
        linked |= {p for p in opts["connect_program_id"] if p > 0}
        linked -= set(opts["remove_connect_program_id"])
        if sorted(linked) != sorted(workspace.connect_program_ids):
            workspace.connect_program_ids = sorted(linked)
            workspace.save(update_fields=["connect_program_ids", "updated_at"])

        User = get_user_model()
        for username in opts["admin"]:
            user = User.objects.filter(username=username).first()
            if user is None:
                raise CommandError(f"no labs user {username!r} -- they must sign in to labs once first")
            membership, _ = OrgMembership.objects.get_or_create(org=org, user=user, defaults={"role": "admin"})
            if membership.role != "admin":
                membership.role = "admin"
                membership.save(update_fields=["role"])

        program_id = synthetic_program_id(opp)
        self.stdout.write(("Created" if created else "Found") + f" workspace {org.name!r}")
        self.stdout.write(f"  organisation slug:      {org.slug}")
        self.stdout.write(f"  labs program id:        {program_id}  ({opp.program_name})")
        self.stdout.write(f"  labs opportunity id:    {opp.opportunity_id}")
        self.stdout.write(
            "  linked Connect programs: " + (", ".join(map(str, workspace.connect_program_ids)) or "(none yet)")
        )
        self.stdout.write(f"  portfolio:              /microplans/program/{program_id}/")
        self.stdout.write(f"  workspace settings:     /microplans/program/{program_id}/workspace/")
