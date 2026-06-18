"""Serializers used only for OpenAPI documentation of the export envelope.

The endpoints return fixture data verbatim, so these describe the *envelope*
shape (results/next/count) rather than constraining row fields.
"""
from rest_framework import serializers


class ExportPageSerializer(serializers.Serializer):
    results = serializers.ListField(child=serializers.DictField(), help_text="Fixture rows for this page.")
    next = serializers.CharField(allow_null=True, help_text="URL of the next page, or null on the last page.")
    count = serializers.IntegerField(help_text="Total rows across all pages.")
