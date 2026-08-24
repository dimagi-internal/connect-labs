"""Placeholder for a migration that ran but whose file this repo no longer has.

`tasks` has 6 migrations recorded in production's django_migrations, applied
before the commcare_connect -> connect_labs rename (#797), and NONE of their
files survived it. Django marks a migration applied by NAME, so those names are
claimed: the first model added to this app would make makemigrations generate
`0001_initial`, a name already recorded, and migrate would SKIP it while every
layer reported success. That is exactly #1264, where audit -- which sat in this
same state until #1252 gave it a model -- ended up with no table in production.

This file claims the HIGHEST recorded name, so makemigrations numbers from the
next free index instead of restarting at 0001. It has no operations because the
app has no models; it exists to occupy a number, not to change a schema.

Do not renumber it, and do not "tidy up" the empty operations list: on
production this name is already applied and will be skipped, and on a fresh
database it applies as a no-op. Both are correct. See connect-labs #1267.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = []

    operations = []
