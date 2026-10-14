"""Add a person to the team of the organisation that runs a program.

    make manage CMD="supply_team_add 10135 someone@example.org --role admin"

Platform support's way to add a running organisation's FIRST admin, who then
adds everyone else at /supply/team/. It runs the same `programme_team_add`
operation as that screen, as the system caller, so the change is recorded.
"""

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("program_id", type=int)
        parser.add_argument("email")
        parser.add_argument("--role", choices=("admin", "member"), default="member")

    def handle(self, *args, program_id, email, role, **options):
        access = SupplyDataAccess(program_id=program_id, caller=SYSTEM)
        try:
            team = call_operation("programme_team_add", access, {"email": email, "role": role})
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        # Ids and roles only: this prints into a public Actions log.
        self.stdout.write(f"{team['org_name']} (org {team['org_id']}) team for program {program_id}:")
        for member in team["members"]:
            self.stdout.write(f"  user {member['user_id']}: {member['role']}")
