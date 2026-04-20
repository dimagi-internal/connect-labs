"""Invalidate the registry cache when SyntheticOpportunity rows change."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.labs.synthetic.registry import invalidate_cache


@receiver(post_save, sender=SyntheticOpportunity)
def _invalidate_on_save(sender, **kwargs):
    invalidate_cache()


@receiver(post_delete, sender=SyntheticOpportunity)
def _invalidate_on_delete(sender, **kwargs):
    invalidate_cache()
