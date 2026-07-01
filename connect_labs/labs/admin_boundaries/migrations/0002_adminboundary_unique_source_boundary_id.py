from django.db import migrations, models


class Migration(migrations.Migration):
    """Replace global unique=True on boundary_id with a composite unique
    constraint on (source, boundary_id) so IDs need only be unique per source."""

    dependencies = [
        ("admin_boundaries", "0001_initial"),
    ]

    operations = [
        # Remove global uniqueness from boundary_id
        migrations.AlterField(
            model_name="adminboundary",
            name="boundary_id",
            field=models.CharField(help_text="Unique ID from source (shapeID or OSM ID)", max_length=100),
        ),
        # Add composite unique constraint: (source, boundary_id)
        migrations.AddConstraint(
            model_name="adminboundary",
            constraint=models.UniqueConstraint(
                fields=["source", "boundary_id"],
                name="labs_admin_boundary_source_boundary_id_uniq",
            ),
        ),
    ]
