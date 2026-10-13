"""Which organisation runs a programme's supply chain.

One row per programme, naming the `LabsOrg` whose people tender, award,
contract, order, pay and receive for it -- what `records.BUYER_OF_RECORD`'s
`programme_org` has always meant, finally written down. Before it existed that
organisation was Dimagi by construction (`identity.DIMAGI_ORG_SLUG`), so no
other organisation could run procurement as itself.

Declared, never derived from anything a caller can influence, and changed only
by Django staff or the system caller: changing it changes who "we" are in the
programme, which is the authority every provenance rule protects. See
`docs/superpowers/specs/2026-10-12-supply-running-organisation-design.md`.
"""

from django.conf import settings
from django.db import models


class ProgrammeRunner(models.Model):
    program_id = models.IntegerField(unique=True)
    org = models.ForeignKey("labs.LabsOrg", on_delete=models.PROTECT, related_name="+")
    declared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    declared_at = models.DateTimeField(auto_now=True)
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["program_id"]

    def __str__(self) -> str:
        return f"program {self.program_id} run by {self.org}"
