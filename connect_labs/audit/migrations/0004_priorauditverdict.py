"""Create PriorAuditVerdict -- the table 0001_initial was supposed to create.

It did not, and the reason is worth keeping. The `audit` app's migration FILES
are absent from this repo (they did not survive the commcare_connect ->
connect_labs package rename, #797), but their RECORDS are still in
django_migrations under app_label "audit":

    audit | 0001_initial            | 2025-11-07
    audit | 0002_delete_old_models  | 2025-11-11
    audit | 0003_initial            | 2025-11-11

So `makemigrations` saw an empty directory and produced `0001_initial`, whose
name already existed in that table. Django marks a migration applied by NAME, so
it considered mine already run and skipped it -- silently, with the deploy's
migrate step reporting success. The sibling 0002_priorauditprojectionstate had a
name nobody had used, so it applied, and production ended up with the state table
and no verdict table.

Numbered 0004 because that is the first index the recorded history does not
already claim.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Depends on the state migration rather than on 0001_initial, because
        # 0002_priorauditprojectionstate is ALREADY APPLIED in production. An
        # applied migration may not depend on an unapplied one -- Django raises
        # InconsistentMigrationHistory -- so this has to come after it, not
        # before.
        ("audit", "0002_priorauditprojectionstate"),
    ]

    operations = [
        migrations.CreateModel(
            name='PriorAuditVerdict',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('opportunity_id', models.IntegerField(db_index=True)),
                ('session_id', models.IntegerField(db_index=True)),
                ('session_title', models.CharField(blank=True, default='', max_length=255)),
                ('visit_id', models.CharField(max_length=64)),
                ('blob_id', models.CharField(max_length=255)),
                ('result', models.CharField(max_length=32)),
                ('completed_at', models.DateTimeField(blank=True, db_index=True, null=True)),
            ],
            options={
                'indexes': [models.Index(fields=['opportunity_id', 'visit_id', 'blob_id', '-completed_at'], name='idx_prior_audit_lookup')],
                'constraints': [models.UniqueConstraint(fields=('session_id', 'visit_id', 'blob_id'), name='uniq_prior_audit_session_visit_blob')],
            },
        ),
    ]
