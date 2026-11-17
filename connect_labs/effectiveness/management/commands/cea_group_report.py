"""Rerun a scenario group and say what moved: the way to pick the work up again.

    python manage.py cea_group_report "Reaching 1M Kangaroo Care cases"            # by name (or id), compare, print
    python manage.py cea_group_report 3 --rebaseline                               # and make today's numbers the
    baseline python manage.py cea_group_report 3 --owner ace --json                         # the comparison as JSON

Every scenario in the group is run through the same sizing the pages use, with today's data, models and code, and set
beside the headline it had when it was last baselined: a figure that has moved is printed with what it was. Read the
group's summary first (it is printed above the table); after a change you meant to make, ``--rebaseline`` and update
the summary with what you decided.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from connect_labs.effectiveness import compare, saved, scenario_groups
from connect_labs.effectiveness.models import ScenarioGroup
from connect_labs.effectiveness.views.groups import _jsonable, show


class Command(BaseCommand):
    help = "Run a scenario group, print its summary and the scenarios side by side, and what moved since the baseline."

    def add_arguments(self, parser):
        parser.add_argument("group", help="The group's id or its name.")
        parser.add_argument("--owner", help="The group's owner's username (needed only when the name is ambiguous).")
        parser.add_argument(
            "--rebaseline", action="store_true", help="Make what each scenario gives now its baseline."
        )
        parser.add_argument("--json", action="store_true", dest="as_json", help="Print the comparison as JSON.")

    def handle(self, *args, group, owner=None, rebaseline=False, as_json=False, **options):
        found = ScenarioGroup.objects.select_related("owner")
        found = found.filter(pk=int(group)) if group.isdigit() else found.filter(name=group)
        if owner:
            found = found.filter(owner__username=owner)
        rows = list(found)
        if len(rows) != 1:
            raise CommandError(f"{len(rows)} groups match {group!r}; give an id, or --owner.")
        g = rows[0]
        try:
            data = scenario_groups.compare_group(g.owner, g, rebaseline=rebaseline)
        except saved.SavedError as exc:
            raise CommandError(exc.message) from None
        if as_json:
            self.stdout.write(json.dumps(_jsonable(data), indent=2, default=str))
            return
        self.stdout.write(f"{g.name}\n{g.question}\n\n{g.summary or '(no summary yet)'}\n")
        scenarios = data["scenarios"]
        for r in compare.RESULTS:
            if not r.headline:
                continue
            self.stdout.write(f"\n{r.label}")
            for s in scenarios:
                h = (s.get("headline") or {}).get(r.key)
                moved = (s.get("drift") or {}).get(r.key)
                note = f"   (was {show(r.unit, moved[0])})" if moved else ""
                self.stdout.write(f"  {show(r.unit, h):>10}  {s['name']}{note}")
        if rebaseline:
            self.stdout.write("\nBaselined: these are now the numbers later runs are compared with.")
