"""Join the three 0064 migrations that three parallel changes each added after 0063."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0064_buying_model_and_needed_by"),
        ("supply_chain", "0064_document_stock_issue"),
        ("supply_chain", "0064_handover_plan_confirm_receive"),
    ]

    operations = []
