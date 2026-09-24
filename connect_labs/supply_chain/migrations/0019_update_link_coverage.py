from django.db import migrations, models


class Migration(migrations.Migration):
    """An update link can follow its organisation instead of naming rows.

    Every existing link keeps "listed": what it named when it was issued, and
    nothing that appeared later.
    """

    dependencies = [
        ("supply_chain", "0018_approver_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="updatelink",
            name="coverage",
            field=models.CharField(
                choices=[
                    ("listed", "Only the orders, supply points and approvals named"),
                    ("organisation", "Everything involving the organisation, including new orders"),
                ],
                default="listed",
                max_length=16,
            ),
        ),
    ]
