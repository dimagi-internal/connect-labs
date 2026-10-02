"""Write a Playwright storage state that signs a demo persona in on a LOCAL labs build.

    make manage CMD="ddd_demo_session --username demo-sophie --base-url http://localhost:8000 --out state.json"

Local DEBUG builds only (`connect_labs.labs.demo_sessions`); refused anywhere else.
The walkthrough seeders' `--local` mode calls the same module, so a recipe's
`auth.personas` files come out of the seed without this; it exists for signing in
a persona by hand (the agent account, say) on a build that is already seeded.
"""

import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs import demo_sessions


class Command(BaseCommand):
    help = "Mint a session for a demo persona on a local DEBUG build and write it as Playwright storage state."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--base-url", default="http://localhost:8000")
        parser.add_argument("--out", required=True, help="where to write the storage state JSON (0600)")
        parser.add_argument("--hours", type=int, default=12)

    def handle(self, *args, username, base_url, out, hours, **options):
        try:
            demo_sessions.require_local()
        except demo_sessions.LocalOnly as exc:
            raise CommandError(str(exc))
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"no user {username!r} in this database; run the walkthrough's seed first")
        try:
            state = demo_sessions.storage_state(user, base_url, hours=hours)
        except demo_sessions.LocalOnly as exc:
            raise CommandError(str(exc))
        path = Path(out)
        path.write_text(json.dumps(state))
        os.chmod(path, 0o600)
        self.stdout.write(f"{username} signed in for {hours} h on {base_url} -> {path}")
