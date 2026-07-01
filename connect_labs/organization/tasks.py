from django.contrib.sites.models import Site
from django.urls import reverse

from connect_labs.organization.models import UserOrganizationMembership
from connect_labs.users.models import User
from connect_labs.utils.tasks import send_mail_async


def send_org_invite(membership_id, host_user_id):
    membership = UserOrganizationMembership.objects.get(pk=membership_id)
    host_user = User.objects.get(pk=host_user_id)
    if not membership.user.email:
        return
    location = reverse("organization:accept_invite", args=(membership.organization.slug, membership.invite_id))
    try:
        site = Site.objects.get_current()
        domain = site.domain
    except Exception:
        domain = "localhost"
    invite_url = f"https://{domain}{location}"
    message = f"""Hi,

You have been invited to join {membership.organization.name} on Connect by {host_user.name}.
The invite can be accepted by visiting the link.

{invite_url}

Thank You,
Connect

This inbox is not monitored. Please do not respond to this email."""

    send_mail_async.delay(
        subject=f"{host_user.name} has invited you to join '{membership.organization.name}' on Connect",
        message=message,
        recipient_list=[membership.user.email],
    )
