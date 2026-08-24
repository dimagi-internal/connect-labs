"""Tests for check_migration_drift.

The regression these pin is #1264: the `audit` app's migration files were lost in
the #797 rename while their django_migrations rows survived, so `makemigrations`
re-generated `0001_initial`, `migrate` skipped it by name, and the table was never
created — with every layer reporting success. The classifier below is what makes
that state loud, so its severity boundaries are the thing worth testing.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.migrations.recorder import MigrationRecorder

from connect_labs.multidb.management.commands.check_migration_drift import (
    ARMED,
    AT_RISK,
    COLLIDABLE_NOW,
    SHADOWED,
    app_has_models,
    classify_ghost,
    parse_index,
)


class TestParseIndex:
    def test_reads_the_leading_number(self):
        assert parse_index("0004_priorauditverdict") == 4

    def test_unnumbered_name_has_no_index(self):
        assert parse_index("initial_squashed") is None


class TestClassifyGhost:
    def test_empty_app_with_models_and_recorded_0001_is_collidable_now(self):
        """The #1264 shape: no files at all, 0001_initial recorded, models present.

        makemigrations emits exactly `0001_initial` for an app's first migration,
        so this is not a risk of collision — it is the next thing that happens.
        """
        assert classify_ghost("0001_initial", set(), has_models=True) == COLLIDABLE_NOW

    def test_empty_app_without_models_is_armed_not_collidable(self):
        """Measured on prod 2026-08-24: solicitations, tasks and flags all sit here.

        makemigrations writes nothing for an app with no models, so the collision
        cannot happen today — but the first model added to one produces exactly
        `0001_initial`. That is the state `audit` was in until #1252 added a model
        to it, so this is reported as a defect now rather than a footnote.
        """
        assert classify_ghost("0001_initial", set(), has_models=False) == ARMED

    def test_models_only_change_the_0001_case(self):
        # has_models is about whether makemigrations writes anything at all; it
        # does not reorder the numbering logic the other severities rest on.
        assert classify_ghost("0003_initial", set(), has_models=False) == AT_RISK
        assert classify_ghost("0002_x", {"0001_a", "0004_b"}, has_models=False) == SHADOWED

    def test_empty_app_ghosts_above_0001_are_at_risk(self):
        # Reachable, but only once numbering climbs to it, and only if the
        # fragment matches too — so it is not the certain case above.
        assert classify_ghost("0003_initial", set()) == AT_RISK

    def test_ghost_at_the_next_number_is_at_risk(self):
        assert classify_ghost("0003_initial", {"0001_initial", "0002_state"}) == AT_RISK

    def test_ghost_below_the_next_number_is_shadowed(self):
        """Inert for collisions: makemigrations counts up from the max on disk."""
        assert classify_ghost("0002_delete_old_models", {"0001_initial", "0004_verdict"}) == SHADOWED

    def test_unnumbered_ghost_is_shadowed_not_guessed(self):
        # No predictable successor, so it is never claimed to be collidable.
        assert classify_ghost("manual_backfill", {"0001_initial"}) == SHADOWED

    def test_real_audit_app_state_after_1264(self):
        """Post-fix reality: 0001/0002/0004 on disk, 0002_delete_old_models and
        0003_initial recorded with no file. Both sit below the next number (5),
        so the app is no longer a live trap — just unreproducible from zero."""
        on_disk = {"0001_initial", "0002_priorauditprojectionstate", "0004_priorauditverdict"}
        assert classify_ghost("0002_delete_old_models", on_disk) == SHADOWED
        assert classify_ghost("0003_initial", on_disk) == SHADOWED


class TestAppHasModels:
    def test_true_for_an_app_with_models(self):
        # audit gained PriorAuditVerdict in #1252 — that is what sprang the trap.
        assert app_has_models("audit") is True

    def test_false_for_an_app_with_none(self):
        assert app_has_models("multidb") is False

    def test_false_for_an_app_that_is_not_installed(self):
        assert app_has_models("an_app_that_was_deleted") is False


@pytest.mark.django_db
class TestCommand:
    def test_passes_on_a_clean_database(self, capsys):
        # The test DB is migrated from the repo's own files, so every recorded
        # name has a file by construction.
        call_command("check_migration_drift", "--database", "default")
        assert "no drift" in capsys.readouterr().out

    def test_fails_on_a_recorded_name_with_no_file(self, capsys):
        # multidb has a migrations package, no migration files and no models, so
        # a recorded 0001_initial for it is the armed form of the audit-app trap.
        MigrationRecorder.Migration.objects.create(app="multidb", name="0001_initial")

        with pytest.raises(CommandError, match="Migration drift detected"):
            call_command("check_migration_drift", "--database", "default")

        out = capsys.readouterr().out
        assert ARMED in out
        assert "multidb.0001_initial" in out

    def test_shadowed_ghost_passes_by_default_and_fails_under_strict(self, capsys):
        # Below the next number for an app that does have files, so it cannot be
        # re-generated — reported, but not a deploy blocker unless asked for.
        app = "audit"
        MigrationRecorder.Migration.objects.create(app=app, name="0003_gone_from_disk")

        call_command("check_migration_drift", "--database", "default")
        assert SHADOWED in capsys.readouterr().out

        with pytest.raises(CommandError, match="Migration drift detected"):
            call_command("check_migration_drift", "--database", "default", "--strict")

    def test_rejects_an_unknown_alias_instead_of_silently_checking_nothing(self):
        with pytest.raises(CommandError, match="Unknown database alias"):
            call_command("check_migration_drift", "--database", "does-not-exist")

    def test_retired_app_is_reported_but_never_fails(self, capsys):
        # No longer in INSTALLED_APPS => makemigrations never runs for it, so its
        # recorded names cannot be re-generated.
        MigrationRecorder.Migration.objects.create(app="an_app_that_was_deleted", name="0001_initial")

        call_command("check_migration_drift", "--database", "default")

        out = capsys.readouterr().out
        assert "no longer installed" in out
        assert "an_app_that_was_deleted" in out
