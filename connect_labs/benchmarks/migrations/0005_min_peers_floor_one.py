import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("benchmarks", "0004_benchmarkcohort_require_complete_series"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="benchmarkcohort",
            name="benchmark_cohort_min_peers_floor",
        ),
        migrations.AlterField(
            model_name="benchmarkcohort",
            name="min_peers",
            field=models.PositiveIntegerField(default=5, validators=[django.core.validators.MinValueValidator(1)]),
        ),
        migrations.AddConstraint(
            model_name="benchmarkcohort",
            constraint=models.CheckConstraint(
                condition=models.Q(("min_peers__gte", 1)), name="benchmark_cohort_min_peers_floor"
            ),
        ),
    ]
