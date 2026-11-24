"""One Labs user per person, whichever sign-in made the row first.

Labs users are created by two sign-ins that name people differently:

* the Connect sign-in (labs/integrations/connect) uses the **Connect username**;
* the campaign sign-in (campaign/auth) uses the **CommCare HQ username**, which for
  an HQ web user is their email address.

Both carry an email the identity provider stands behind: Connect's userinfo email is
the Connect account's own email, which Connect only lets a person change to an
address they have verified (ACCOUNT_EMAIL_VERIFICATION = "mandatory"), and the
campaign sign-in only trusts the HQ login name (campaign/auth/whitelist.py
``verified_email``). Emails are unique across Labs users (``unique_user_email``).

So the rule for a Connect sign-in is:

1. a row with the Connect username is theirs;
2. otherwise a row the HQ sign-in made for the same address is theirs too -- it
   is keyed by that address (username == email), so it is the same person.
   Adopt it: rename it to the Connect username, so it is found by step 1 from
   then on. The campaign sign-in still finds it by the HQ-vouched email;
3. otherwise any other row holding the email belongs to a different Connect
   username. Which person that address belongs to is an admin's call, so refuse
   with ``EmailHeldByAnotherAccount`` rather than guess;
4. otherwise create the row.

Before this, step 2 did not exist and step 3 was an IntegrityError: a person who
had used the campaign tool first got a 500 on every Labs sign-in.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction

from connect_labs.users.models import User

logger = logging.getLogger(__name__)


class EmailHeldByAnotherAccount(Exception):
    def __init__(self, username: str, email: str, holder: User):
        super().__init__(f"{email} is held by user {holder.id} ({holder.username!r}), not {username!r}")
        self.username = username
        self.email = email
        self.holder = holder


def _made_by_hq_sign_in(user: User) -> bool:
    return bool(user.email) and (user.username or "").strip().lower() == user.email.strip().lower()


def _resolve(username: str, email: str | None, name: str) -> User:
    user = User.objects.select_for_update().filter(username=username).first()
    holder = (
        User.objects.select_for_update().filter(email__iexact=email).exclude(username=username).first()
        if email
        else None
    )

    if user is not None:
        user.name = name
        if email and holder is None:
            user.email = email
        elif email and holder is not None:
            # Their own row exists; only the new address is contested. Sign them in and
            # keep the email on file rather than lock them out over it.
            logger.warning(
                "Connect user %r now has email %s, held by user id=%s (%r); keeping %s on file",
                username,
                email,
                holder.id,
                holder.username,
                user.email,
            )
        user.save()
        return user

    if holder is not None:
        if not _made_by_hq_sign_in(holder):
            raise EmailHeldByAnotherAccount(username, email, holder)
        logger.info("Adopting user id=%s made by the HQ sign-in as Connect user %r", holder.id, username)
        holder.username = username
        holder.name = name or holder.name
        holder.save(update_fields=["username", "name"])
        return holder

    return User.objects.create(username=username, email=email or None, name=name)


def resolve_connect_user(username: str, email: str | None, name: str) -> User:
    """The Labs user for a Connect sign-in. See the module docstring for the rule.

    ``email`` may be empty: a login that carries none never erases the one on file.
    Raises ``EmailHeldByAnotherAccount`` when the email belongs to a different person.
    """
    email = (email or "").strip() or None
    for attempt in range(2):
        try:
            with transaction.atomic():
                return _resolve(username, email, name)
        except IntegrityError:
            # Another request wrote the same username or email between our read and
            # write. The second pass sees that row.
            if attempt:
                raise
    raise AssertionError("unreachable")
