from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    """A movement may now also be cancelled by a removal: an adjustment naming it in `reverses`."""

    dependencies = [
        ("supply_chain", "0073_stock_plan_target_by_partner"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="movement",
            name="movement_reversal_is_consumption_back_in",
        ),
        migrations.AddConstraint(
            model_name="movement",
            constraint=models.CheckConstraint(
                condition=Q(reverses__isnull=True)
                | Q(kind="consumption", from_supply_point__isnull=True, to_supply_point__isnull=False)
                | Q(kind="adjustment"),
                name="movement_reversal_is_visit_put_back_or_removal",
            ),
        ),
    ]
