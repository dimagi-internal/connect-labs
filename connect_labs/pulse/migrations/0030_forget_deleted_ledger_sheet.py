"""Forget the costs ledger sheet: it was deleted, and the invoices sheet that replaces it is adopted
by id (``costs_ledger_sheet_create`` with ``spreadsheet_id``)."""

from django.db import migrations


def forget(apps, schema_editor):
    apps.get_model("pulse", "PulseScalar").objects.filter(key="costs_ledger_sheet").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pulse", "0029_drop_ledger_lines"),
    ]

    operations = [migrations.RunPython(forget, migrations.RunPython.noop, hints={"run_on_secondary": False})]
