"""Drop the accounting lines labs kept from the costs ledger sheet.

They included staff pay, consultants and contractors from Intacct. Labs now imports only the
reconstructed invoices (``pulse.ledger``), built outside labs from a filtered pull, and keeps no
accounting line. Dropping the tables removes every stored line.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pulse", "0028_ledger_and_reconstructed_invoices"),
    ]

    operations = [
        migrations.DeleteModel(name="PulseReconstructedInvoiceSource"),
        migrations.DeleteModel(name="PulseLedgerLine"),
    ]
