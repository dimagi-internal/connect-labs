from django.core.management.base import BaseCommand, CommandError

from connect_labs.users.models import User


class Command(BaseCommand):
    help = "Promotes the given user to a superuser and provides admin access."

    def add_arguments(self, parser):
        parser.add_argument("email", type=str)

    def handle(self, email, **options):
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(f"No user with email {email} found!")
        user.is_superuser = True
        user.is_staff = True
        user.save()
        print(f"{email} successfully promoted to superuser and can now access the admin site")
