"""
Management command to test all audit URLs.
Run with: python manage.py test_audit_urls
"""
from connect_labs.labs.management.commands.base_labs_url_test import BaseLabsURLTest


class Command(BaseLabsURLTest):
    help = "Test all audit URLs"

    project_name = "audit"
    base_urls = [
        "/audit/",
        "/audit/create/",
    ]
