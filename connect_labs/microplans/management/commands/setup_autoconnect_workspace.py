"""Create (or update) the Auto-Connect workspace: a labs-only organisation with
one labs-only microplans program, run with roles (``labs.workspaces``).

    manage.py setup_autoconnect_workspace
    manage.py setup_autoconnect_workspace --program-id 10120   # adopt an existing labs-only program
    manage.py setup_autoconnect_workspace --connect-program-id 9001 --connect-program-id 9002
    manage.py setup_autoconnect_workspace --remove-connect-program-id 9002
    manage.py setup_autoconnect_workspace --admin some.username

Never makes a second one. ``--program-id`` ADOPTS an existing labs-only program
(e.g. one made earlier with the ``synthetic_create_labs_only`` MCP tool) and flags
its organisation as a workspace; without it, a program with these organisation
and program names is adopted if there is exactly one, and only otherwise is a
new one made. A second run finds the workspace and only applies the changes
asked for. Plans already in an adopted program carry no team, so they are
Program Managers' only until moved. Nothing is hard-coded: the ids it prints
are whatever this database holds.

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


def _adopt(program_id):
    """`labs.workspaces.adopt_program` -- the same function the workspace settings
    page's "Turn on workspace roles" button runs -- with its refusals as command errors."""
    from connect_labs.labs.workspaces import AdoptRefused, adopt_program

    try:
        workspace, opp, created = adopt_program(program_id)
    except AdoptRefused as e:
        raise CommandError(str(e)) from None
    return workspace.org, opp, created


def ensure_workspace(org_name: str, program_name: str, program_id: int | None = None):
    """The workspace's (organisation, synthetic opp, what happened).

    In order, never making a second one:
      1. ``program_id`` given: adopt that existing labs-only program (it must exist);
      2. a workspace already holding a program with these names: use it;
      3. exactly one existing labs-only program with these names: adopt it;
      4. otherwise make the organisation and program.
    "What happened" is "adopted", "found" or "created"."""
    from django.db.models import Q

    from connect_labs.labs.synthetic.models import SyntheticOpportunity
    from connect_labs.labs.synthetic.org_tree import synthetic_program_id

    if program_id is not None:
        org, opp, created = _adopt(program_id)
        return org, opp, "adopted" if created else "found"

    named = (
        SyntheticOpportunity.objects.filter(labs_only=True, program_name=program_name)
        .filter(Q(labs_org__name=org_name) | Q(org_name=org_name))
        .select_related("labs_org")
        .order_by("pk")
    )
    flagged = named.filter(labs_org__workspace__isnull=False).first()
    if flagged is not None:
        return flagged.labs_org, flagged, "found"
    programs = {synthetic_program_id(o): o for o in reversed(list(named))}
    if len(programs) > 1:
        raise CommandError(
            f"{len(programs)} labs-only programs are called {program_name!r} under {org_name!r} "
            f"({sorted(programs)}); say which with --program-id"
        )
    if programs:
        org, opp, _created = _adopt(next(iter(programs)))
        return org, opp, "adopted"

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
        org, opp, _created = _adopt(synthetic_program_id(opp))
    return org, opp, "created"


class Command(BaseCommand):
    help = "Create or update the Auto-Connect workspace (labs-only org + microplans program with roles)"

    def add_arguments(self, parser):
        parser.add_argument("--org-name", default="Auto-Connect")
        parser.add_argument("--program-name", default="CLP Stage 4")
        parser.add_argument(
            "--program-id",
            type=int,
            default=None,
            help="adopt this existing labs-only program (and its organisation) as the workspace",
        )
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

        org, opp, outcome = ensure_workspace(opts["org_name"], opts["program_name"], opts["program_id"])
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
        self.stdout.write(f"{outcome.capitalize()} workspace {org.name!r}")
        self.stdout.write(f"  organisation slug:      {org.slug}")
        self.stdout.write(f"  labs program id:        {program_id}  ({opp.program_name})")
        self.stdout.write(f"  labs opportunity id:    {opp.opportunity_id}")
        self.stdout.write(
            "  linked Connect programs: " + (", ".join(map(str, workspace.connect_program_ids)) or "(none yet)")
        )
        self.stdout.write(f"  portfolio:              /microplans/program/{program_id}/")
        self.stdout.write(f"  workspace settings:     /microplans/program/{program_id}/workspace/")
