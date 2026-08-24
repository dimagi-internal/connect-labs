"""Compare the migration names a database has RECORDED against the files on disk.

Why this exists
---------------
Django marks a migration applied by NAME, not by content. If ``django_migrations``
holds a row whose file is absent from the repo, that name is a landmine: a later
``makemigrations`` can generate the same name, and ``migrate`` will skip it as
already-applied. Nothing errors. The deploy's migrate step reports success, CI
passes (its DB is fresh, so the collision cannot occur there), and the model
imports fine -- while the table was never created.

That is not hypothetical. The ``audit`` app lost its migration FILES in the
``commcare_connect`` -> ``connect_labs`` rename (#797) but kept its RECORDS::

    audit | 0001_initial           | 2025-11-07
    audit | 0002_delete_old_models | 2025-11-11
    audit | 0003_initial           | 2025-11-11

``makemigrations`` then saw an empty directory, generated ``0001_initial``, and
Django skipped it. ``PriorAuditVerdict`` did not exist in production for three
days across two "successful" deploys (#1246, fixed in #1264). The bulk-data view
catches exceptions around the prior-audit index and falls back to an EMPTY index,
which renders as "no image was previously audited" -- so the failure mode of this
class is a silent wrong answer, not a crash.

This command makes that state visible, and is meant to be run against a real
database (the one whose recorded history matters), not a fresh test DB.
"""

import re

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

# Django names generated migrations "%04i_%s" % (number, fragment), so the
# leading digits are the ordering key. A name without them (hand-written, or
# renamed) has no predictable successor, so it is never classed as collidable.
_INDEX_RE = re.compile(r"^(\d+)")

# Severities, worst first. All but SHADOWED fail the command by default: a
# SHADOWED ghost cannot be re-generated under its own name, so it is a
# reproducibility problem rather than a live trap.
COLLIDABLE_NOW = "COLLIDABLE_NOW"
ARMED = "ARMED"
AT_RISK = "AT_RISK"
SHADOWED = "SHADOWED"

FAILING = (COLLIDABLE_NOW, ARMED, AT_RISK)


def app_has_models(app_label):
    """Whether the app defines any concrete model right now.

    An app with none has nothing for makemigrations to write, so its recorded
    ghosts cannot be re-generated until a model shows up.
    """
    try:
        return bool(list(apps.get_app_config(app_label).get_models()))
    except LookupError:
        return False


def parse_index(name):
    """Leading migration number, or None when the name is not numbered."""
    match = _INDEX_RE.match(name)
    return int(match.group(1)) if match else None


def classify_ghost(name, disk_names, has_models=True):
    """Rate one recorded-but-fileless migration name.

    ``disk_names`` is every migration name that app currently has on disk.
    ``has_models`` is whether the app defines any concrete model today --
    ``makemigrations`` generates nothing for an app that defines none, which is
    the difference between a trap that is sprung and one that is merely loaded.

    - COLLIDABLE_NOW: the app has no files, the ghost is ``0001_initial``, and
      the app has models. ``makemigrations`` emits exactly that name for a first
      migration, so the collision is not a possibility -- it is the next thing
      that happens. This is the #1264 shape.
    - ARMED: same, but the app has no models yet, so nothing is generated today.
      It fires on the day someone adds the first model. This is precisely the
      state ``audit`` sat in until #1252 added PriorAuditVerdict, so it is
      reported as a defect to fix now rather than a note to remember later.
    - AT_RISK: the ghost's number is one ``makemigrations`` could still reach
      (>= the next number it would assign). Whether it actually collides then
      depends on the name fragment, which is not predictable.
    - SHADOWED: the ghost sits below the next number, so no generated name can
      reach it. Inert for collisions -- but it still means a migrate from zero
      produces a different schema than this database has.
    """
    index = parse_index(name)
    if not disk_names and name == "0001_initial":
        return COLLIDABLE_NOW if has_models else ARMED

    if index is None:
        return SHADOWED

    disk_indexes = [i for i in (parse_index(n) for n in disk_names) if i is not None]
    next_index = (max(disk_indexes) if disk_indexes else 0) + 1
    return AT_RISK if index >= next_index else SHADOWED


class Command(BaseCommand):
    help = "Report migrations a database records as applied but which have no file on disk."

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            action="append",
            dest="databases",
            help=(
                "Database alias to check. Repeatable. Defaults to every configured "
                "database, matching what migrate_multi migrates."
            ),
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Also exit non-zero on SHADOWED ghosts (recorded history the repo cannot rebuild).",
        )

    def handle(self, *args, **options):
        aliases = options["databases"] or list(settings.DATABASES.keys())
        unknown = [a for a in aliases if a not in settings.DATABASES]
        if unknown:
            raise CommandError(f"Unknown database alias(es): {', '.join(unknown)}")

        # load=False + load_disk() reads the migration FILES without building the
        # graph and without touching a database. build_graph() would raise on a
        # dangling dependency -- and a repo in exactly that state is one this
        # command needs to be able to describe rather than crash on.
        loader = MigrationLoader(None, load=False)
        loader.load_disk()
        project_apps = loader.migrated_apps | loader.unmigrated_apps

        failing = False
        for alias in aliases:
            if self.check_database(alias, loader, project_apps, options["strict"]):
                failing = True

        if failing:
            raise CommandError(
                "Migration drift detected. A recorded name with no file can be silently skipped by "
                "migrate -- see connect-labs #1246/#1264 before deploying."
            )

    def check_database(self, alias, loader, project_apps, strict):
        """Report drift for one alias. Returns True if it should fail the command."""
        self.stdout.write(self.style.MIGRATE_HEADING(f"\nDatabase: {alias}"))

        recorder = MigrationRecorder(connections[alias])
        if not recorder.has_table():
            self.stdout.write("  no django_migrations table — nothing recorded yet, nothing to compare.")
            return False

        applied = recorder.applied_migrations()

        disk_by_app = {}
        for app_label, name in loader.disk_migrations:
            disk_by_app.setdefault(app_label, set()).add(name)

        ghosts_by_app = {}
        retired = []
        for app_label, name in sorted(applied):
            if name in disk_by_app.get(app_label, set()):
                continue
            if app_label not in project_apps:
                # The app itself is gone from INSTALLED_APPS. makemigrations never
                # runs for it, so it cannot collide; report the count only.
                retired.append((app_label, name))
                continue
            ghosts_by_app.setdefault(app_label, []).append(name)

        if not ghosts_by_app:
            self.stdout.write(self.style.SUCCESS("  no drift: every recorded migration has a file on disk."))
        fail = False

        for app_label, names in sorted(ghosts_by_app.items()):
            disk_names = disk_by_app.get(app_label, set())
            has_models = app_has_models(app_label)
            self.stdout.write(f"\n  {app_label} — {len(disk_names)} file(s) on disk, {len(names)} recorded with none:")
            for name in names:
                severity = classify_ghost(name, disk_names, has_models)
                style = self.style.ERROR if severity in FAILING else self.style.WARNING
                self.stdout.write(style(f"    [{severity}] {app_label}.{name}"))
                if severity == COLLIDABLE_NOW:
                    self.stdout.write(
                        "      This app has no migration files and does have models. The next "
                        "`makemigrations` emits this exact name and `migrate` skips it as applied."
                    )
                if severity == ARMED:
                    self.stdout.write(
                        "      This app has no migration files and no models yet, so nothing is "
                        "generated today — but the first model added to it produces exactly this "
                        "name, which `migrate` will skip. `audit` sat here until #1252."
                    )
                if severity in FAILING or strict:
                    fail = True

        if retired:
            apps_retired = sorted({app for app, _ in retired})
            self.stdout.write(
                f"\n  ({len(retired)} recorded migration(s) belong to app(s) no longer installed — "
                f"{', '.join(apps_retired)}. Not a collision risk; makemigrations never runs for them.)"
            )

        return fail
