"""Tidy imported suppliers: a country in "City", notes inside an email cell.

    make manage CMD="supply_tidy_suppliers --program-id 10172"           # show what would change
    make manage CMD="supply_tidy_suppliers --program-id 10172 --apply"   # make the changes

Shows every change first and writes only with --apply, one attributed
`supplier_update` per supplier. What the rules do and do not touch:
`supply_chain/reference/tidy.py`. A country is the company's own fact, so
setting one shows in every programme that buys from the company.
"""

from django.core.management.base import BaseCommand

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.reference.tidy import tidy_supplier


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--program-id", type=int, required=True)
        parser.add_argument("--apply", action="store_true", help="make the changes (the default only shows them)")

    def handle(self, *args, program_id, apply, **options):
        access = SupplyDataAccess(access_token="tidy", program_id=program_id, caller=SYSTEM)
        changed = 0
        for supplier in call_operation("supplier_list", access, {}):
            tidied = tidy_supplier(supplier.get("city"), supplier.get("country"), supplier.get("contacts"))
            for line in tidied.left_alone:
                self.stdout.write(f"  left alone  {supplier['name']}: {line}")
            if not tidied.any:
                continue
            changed += 1
            for line in tidied.changes:
                self.stdout.write(f"  {'changed' if apply else 'would change'}  {supplier['name']}: {line}")
            if apply:
                call_operation("supplier_update", access, {"supplier_id": supplier["id"], "data": tidied.data})
        verb = "changed" if apply else "would change"
        self.stdout.write(
            f"{verb} {changed} supplier(s)." + ("" if apply else " Run again with --apply to make them.")
        )
