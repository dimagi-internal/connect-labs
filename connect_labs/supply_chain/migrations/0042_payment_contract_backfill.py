"""Every payment so far was made against an invoice, so its order is the invoice's.

Its own migration, apart from the column change on either side: Postgres
refuses to ALTER a table with deferred foreign-key checks still pending from an
UPDATE in the same transaction.
"""

from django.db import migrations
from django.db.models import OuterRef, Subquery


def payment_contract_from_invoice(apps, schema_editor):
    Payment = apps.get_model("supply_chain", "Payment")
    Invoice = apps.get_model("supply_chain", "Invoice")
    Payment.objects.filter(contract__isnull=True).update(
        contract_id=Subquery(Invoice.objects.filter(pk=OuterRef("invoice_id")).values("contract_id")[:1])
    )


class Migration(migrations.Migration):
    dependencies = [("supply_chain", "0041_tracking_reality")]

    operations = [migrations.RunPython(
            payment_contract_from_invoice, migrations.RunPython.noop, hints={"run_on_secondary": False}
        )]
