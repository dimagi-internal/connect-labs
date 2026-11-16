from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from connect_labs.hqdata.access import (
    MEMBERSHIP_TTL,
    ProjectAccessDenied,
    create_project,
    may_use_project,
    refresh_memberships,
    require_project,
    visible_projects,
)
from connect_labs.hqdata.models import HQProject, HQProjectMember
from connect_labs.users.tests.factories import UserFactory

HOST = "www.commcarehq.org"


@pytest.fixture
def alice(db):
    return UserFactory()


@pytest.fixture
def bob(db):
    return UserFactory()


def test_creating_a_project_needs_hq_to_say_yes_and_grants_nothing_otherwise(alice):
    with pytest.raises(ProjectAccessDenied):
        create_project(alice, "demo", hq_host=HOST, can_read_domain=lambda: False)
    assert not HQProject.objects.exists() and not HQProjectMember.objects.exists()


def test_the_first_person_in_is_admin_and_sync_owner_the_next_is_a_member(alice, bob):
    project = create_project(alice, " Demo ", hq_host=HOST, can_read_domain=lambda: True)
    assert project.domain == "demo" and project.sync_owner == alice and project.created_by == alice
    assert HQProjectMember.objects.get(project=project, user=alice).role == HQProjectMember.ROLE_ADMIN

    same = create_project(bob, "demo", hq_host=HOST, can_read_domain=lambda: True)
    assert same.pk == project.pk and same.sync_owner == alice
    assert HQProjectMember.objects.get(project=project, user=bob).role == HQProjectMember.ROLE_MEMBER


def test_joining_again_does_not_demote_an_admin(alice):
    project = create_project(alice, "demo", hq_host=HOST, can_read_domain=lambda: True)
    create_project(alice, "demo", hq_host=HOST, can_read_domain=lambda: True)
    assert HQProjectMember.objects.get(project=project, user=alice).role == HQProjectMember.ROLE_ADMIN


def test_a_blank_domain_is_refused(alice):
    with pytest.raises(ValueError):
        create_project(alice, "  ", hq_host=HOST, can_read_domain=lambda: True)


def test_access_is_membership_proven_recently(alice, bob):
    project = create_project(alice, "demo", hq_host=HOST, can_read_domain=lambda: True)
    now = timezone.now()
    assert may_use_project(alice, project, now)
    assert not may_use_project(bob, project, now)
    assert not may_use_project(AnonymousUser(), project, now)
    assert not may_use_project(None, project, now)
    assert may_use_project(alice, project, now + MEMBERSHIP_TTL - timedelta(minutes=1))
    assert not may_use_project(alice, project, now + MEMBERSHIP_TTL + timedelta(minutes=1))
    with pytest.raises(ProjectAccessDenied):
        require_project(bob, project, now)


def test_one_project_is_not_access_to_another(alice):
    mine = create_project(alice, "mine", hq_host=HOST, can_read_domain=lambda: True)
    other = HQProject.objects.create(domain="other")
    assert may_use_project(alice, mine) and not may_use_project(alice, other)
    assert list(visible_projects(alice)) == [mine]


def test_refreshing_restamps_listed_domains_and_deletes_unlisted_ones(alice):
    keep = create_project(alice, "keep", hq_host=HOST, can_read_domain=lambda: True)
    gone = create_project(alice, "gone", hq_host=HOST, can_read_domain=lambda: True)
    HQProjectMember.objects.filter(project=keep).update(verified_at=timezone.now() - timedelta(days=3))
    assert not may_use_project(alice, keep)

    result = refresh_memberships(alice, ["KEEP", "not-registered"], hq_host=HOST)
    assert result == {"verified": ["keep"], "removed": ["gone"]}
    assert may_use_project(alice, keep) and not may_use_project(alice, gone)
    assert not HQProject.objects.filter(domain="not-registered").exists()  # never creates


def test_refreshing_one_host_leaves_another_hosts_projects_alone(alice):
    create_project(alice, "demo", hq_host=HOST, can_read_domain=lambda: True)
    other_host = create_project(alice, "demo", hq_host="india.commcarehq.org", can_read_domain=lambda: True)
    refresh_memberships(alice, [], hq_host=HOST)
    assert may_use_project(alice, other_host)
