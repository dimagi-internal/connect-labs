"""A supplier is a company, part two: the company columns leave `Supplier`.

0022 moved every company fact to the organisation and its supplier profile.
What stays is the program's own: its status and notes, one link per company
per program.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0022_supplier_profile_and_fold"),
    ]

    operations = [
        migrations.RemoveField(model_name="supplier", name="name"),
        migrations.RemoveField(model_name="supplier", name="type"),
        migrations.RemoveField(model_name="supplier", name="country"),
        migrations.RemoveField(model_name="supplier", name="city"),
        migrations.RemoveField(model_name="supplier", name="contacts"),
        migrations.RemoveField(model_name="supplier", name="qualifications"),
        migrations.RemoveField(model_name="supplier", name="connect_organization_id"),
        migrations.AlterField(
            model_name="supplier",
            name="org",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="supplier_links", to="labs.labsorg"
            ),
        ),
        migrations.AlterModelOptions(name="supplier", options={"ordering": ["org__name"]}),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.UniqueConstraint(fields=("scope_key", "org"), name="supply_supplier_one_link_per_program"),
        ),
    ]
