"""A request for quotes is a tender: the model and every link to it are renamed.

A table rename and column renames, no data rewrite. The legacy LabsRecord type
string "supply_round" (records.ROUND_TYPE) is stored data and keeps its value.

The RenameModel sits inside SeparateDatabaseAndState on purpose. A bare
RenameModel makes Django inject its own RenameContentType operation, which is a
RunPython with no hints -- and this repo refuses every RunPython without
`hints={"run_on_secondary": ...}` (the validator on pre_migrate), so every test
database failed to build. The content type is renamed here instead, hinted.
"""

from django.db import migrations, models


def rename_content_type(apps, schema_editor, old="round", new="tender"):
    ContentType = apps.get_model("contenttypes", "ContentType")
    if ContentType.objects.filter(app_label="supply_chain", model=new).exists():
        return
    ContentType.objects.filter(app_label="supply_chain", model=old).update(model=new)


def rename_content_type_back(apps, schema_editor):
    rename_content_type(apps, schema_editor, old="tender", new="round")


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0026_supplier_market"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RenameModel(old_name="Round", new_name="Tender")],
            state_operations=[migrations.RenameModel(old_name="Round", new_name="Tender")],
        ),
        migrations.RunPython(rename_content_type, rename_content_type_back, hints={"run_on_secondary": False}),
        # The two indexes that name the field are dropped before it is renamed
        # and rebuilt after: RenameField does not carry an index's field list,
        # so an index left in place is recreated against `round` on rollback.
        migrations.RemoveIndex(model_name="outreach", name="supply_chai_round_i_4a8f26_idx"),
        migrations.RemoveIndex(model_name="quote", name="supply_chai_round_i_f205c7_idx"),
        migrations.RenameField(model_name="outreach", old_name="round", new_name="tender"),
        migrations.RenameField(model_name="quote", old_name="round", new_name="tender"),
        migrations.RenameField(model_name="award", old_name="round", new_name="tender"),
        migrations.RenameField(model_name="contract", old_name="round", new_name="tender"),
        migrations.RenameField(model_name="document", old_name="round", new_name="tender"),
        migrations.AddIndex(
            model_name="outreach",
            index=models.Index(fields=["tender", "supplier"], name="supply_chai_tender__5c1340_idx"),
        ),
        migrations.AddIndex(
            model_name="quote",
            index=models.Index(fields=["tender", "commodity"], name="supply_chai_tender__fd9ca4_idx"),
        ),
    ]
