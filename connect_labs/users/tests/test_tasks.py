import pytest
from celery.result import EagerResult

from connect_labs.users.models import User
from connect_labs.users.tasks import get_users_count
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_user_count(settings):
    """A basic test to execute the get_users_count Celery task.

    Counted against what was already there: the task counts every user, and a
    reused test database can carry rows left by an earlier run that was cut
    short, which made this assert on the database's history rather than on
    the task.
    """
    before = User.objects.count()
    UserFactory.create_batch(3)
    settings.CELERY_TASK_ALWAYS_EAGER = True
    task_result = get_users_count.delay()
    assert isinstance(task_result, EagerResult)
    assert task_result.result == before + 3
