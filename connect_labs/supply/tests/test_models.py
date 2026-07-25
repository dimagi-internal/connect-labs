import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_no_missing_migrations():
    # Fails if models.py has drifted from the committed migrations.
    call_command("makemigrations", "supply", "--check", "--dry-run")
