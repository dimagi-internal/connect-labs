"""Write `tailwind/safelist-generated.txt` from `connect_labs.labs.tailwind_safelist`."""

from django.conf import settings
from django.core.management.base import BaseCommand

from connect_labs.labs.tailwind_safelist import generate_safelist, safelist_path


class Command(BaseCommand):
    help = "Regenerate tailwind/safelist-generated.txt (Tailwind safelist for DB-stored render_code)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit non-zero if the committed file is stale, without writing it.",
        )

    def handle(self, *args, **options):
        path = safelist_path(settings.BASE_DIR)
        expected = generate_safelist()
        current = path.read_text(encoding="utf-8") if path.exists() else None

        if options["check"]:
            if current == expected:
                self.stdout.write(self.style.SUCCESS(f"{path} is up to date."))
                return
            raise SystemExit(f"{path} is stale. Run: python manage.py generate_tailwind_safelist")

        if current == expected:
            self.stdout.write(f"{path} already up to date.")
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {path} ({expected.count(chr(10))} lines)."))
