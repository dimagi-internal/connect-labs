"""Serializers used only for OpenAPI documentation of the export envelope.

The endpoints return fixture data verbatim, so these describe the *envelope*
shape (next/results) rather than constraining row fields. Mirrors production
Connect's keyset envelope exactly (no ``count``).
"""
from rest_framework import serializers


class ExportPageSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True, help_text="URL of the next page, or null on the last page.")
    results = serializers.ListField(child=serializers.DictField(), help_text="Fixture rows for this page.")
