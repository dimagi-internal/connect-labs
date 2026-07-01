"""Measure where the campaign tool's load-everything bootstrap breaks at scale.

Builds the national synthetic campaign at increasing worker counts and reports the
metrics that drive the cliff: how big the single ``window.CUT_DATA`` JSON gets, how
long it takes to serialize, and how many DB queries it costs. Run locally after
seeding dev boundaries; in labs it runs against the real GeoPoDe geography.

    manage.py measure_campaign_scale --seed-boundaries --scales 1000,5000,25000,50000
"""
from __future__ import annotations

import json
import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.test.utils import CaptureQueriesContext

from connect_labs.campaign.services import dev_boundaries, geography, serializers, synthetic_campaign


class Command(BaseCommand):
    help = "Build the national synthetic campaign at increasing scales and report bootstrap-payload cliffs."

    def add_arguments(self, parser):
        parser.add_argument("--scales", type=str, default="1000,5000,25000,50000")
        parser.add_argument(
            "--seed-boundaries",
            action="store_true",
            help="Seed dev NGA boundaries first (LOCAL only — never where real GeoPoDe data is loaded).",
        )

    def handle(self, *args, **options):
        if options["seed_boundaries"]:
            info = dev_boundaries.seed_demo_boundaries()
            self.stdout.write(
                self.style.WARNING(
                    f"Seeded DEV boundaries: {info['states']} states / {info['lgas']} LGAs / {info['wards']} wards."
                )
            )
        if not geography.is_loaded():
            self.stderr.write(self.style.ERROR("No NGA boundaries loaded. Pass --seed-boundaries (local) first."))
            return

        scales = [int(s) for s in options["scales"].split(",") if s.strip()]
        self.stdout.write("")
        header = "{:>9} | {:>8} | {:>11} | {:>7} | {:>10} | {:>9}".format(
            "workers", "build s", "serialize s", "queries", "payload MB", "KB/worker"
        )
        self.stdout.write(header)
        self.stdout.write("-" * 76)
        for n in scales:
            t0 = time.perf_counter()
            campaign = synthetic_campaign.build_synthetic_campaign(worker_count=n)
            build_s = time.perf_counter() - t0

            t1 = time.perf_counter()
            with CaptureQueriesContext(connection) as ctx:
                payload = serializers.bootstrap_payload(campaign)
            serialize_s = time.perf_counter() - t1
            blob = json.dumps(payload, ensure_ascii=False)
            mb = len(blob.encode("utf-8")) / 1_048_576
            kb_per = (len(blob.encode("utf-8")) / 1024) / max(1, len(payload["WORKERS"]))
            self.stdout.write(
                f"{n:>9} | {build_s:>8.1f} | {serialize_s:>11.2f} | {len(ctx):>7} | {mb:>10.2f} | {kb_per:>9.2f}"
            )
        self.stdout.write("")
        self.stdout.write(
            self.style.NOTICE(
                "Cliff = the single bootstrap JSON the browser downloads + parses + holds, plus the "
                "client-side render of every worker row. Mitigation: summary-only bootstrap + "
                "server-side paginated/filtered worker+microplan endpoints + virtualized tables."
            )
        )
