"""Register (or refresh) the reference example as an embed client in labs.

The labs side of the example: what a Dimagi superuser would otherwise do in Django admin
for any embedding system, from the example's own settings (embed_example/config.py).
Idempotent: run it after changing the example's key, address or resources.

The client may assert users (``identity_authority``: it only ever names the person signed
in to labs) and is limited to generated data (``generated_data_only``), which labs
enforces at token time over everything the resource reads. This command also refuses up
front any resource whose scope is not a generated opportunity.

    python manage.py embed_example_register [--dry-run]
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from connect_labs.embed.models import EmbedClient
from connect_labs.embed_example import config
from connect_labs.labs.synthetic.provenance import all_generated


class Command(BaseCommand):
    help = "Register the reference embedding example as an embed client (generated data only)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print what would be saved; save nothing.")

    def handle(self, *args, dry_run=False, **options):
        if not config.configured():
            raise CommandError("EMBED_EXAMPLE_PRIVATE_KEY and a labs URL must be set first.")
        resources = []
        for resource in config.resources():
            entry = {k: v for k, v in resource.entry.items() if k != "label"}
            opportunity_id = entry.get("opportunity_id")
            if entry.get("program_id") is not None or opportunity_id is None:
                raise CommandError(f"{resource.key}: the example names an opportunity_id, never a programme.")
            if not all_generated([opportunity_id]):
                raise CommandError(f"{resource.key}: opportunity {opportunity_id} is not generated data.")
            resources.append(entry)
        if not resources:
            raise CommandError("EMBED_EXAMPLE_RESOURCES names nothing to show.")

        base = urlsplit(config.base_url())
        origin = f"{base.scheme}://{base.netloc}"
        embedder = config.embedder()
        fields = {
            "name": "Labs reference embedder (live example)",
            "active": True,
            "identity_authority": True,
            "generated_data_only": True,
            "allowed_origins": [origin],
            "resources": resources,
            "session_seconds": 3600,
        }
        if base.scheme == "https":
            # The real path: labs fetches the example's published keys, as for any system.
            fields.update(jwks_uri=origin + reverse("embed_example:jwks"), jwks=None)
        else:
            # A local http build: labs only fetches https, so the keys go in directly.
            fields.update(jwks_uri="", jwks=embedder.jwks())

        if dry_run:
            self.stdout.write(json.dumps({"client_id": config.client_id(), **fields}, indent=2))
            return
        client = EmbedClient.objects.filter(client_id=config.client_id()).first() or EmbedClient(
            client_id=config.client_id()
        )
        for name, value in fields.items():
            setattr(client, name, value)
        client.full_clean()
        client.save()
        self.stdout.write(self.style.SUCCESS(f"Registered {client.client_id}: {len(resources)} resource(s), {origin}"))
