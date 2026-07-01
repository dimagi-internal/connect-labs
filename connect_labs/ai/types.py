from dataclasses import dataclass

from django.http import HttpRequest

from connect_labs.users.models import User


@dataclass
class UserDependencies:
    user: User
    program_id: int | None = None
    request: HttpRequest | None = None
