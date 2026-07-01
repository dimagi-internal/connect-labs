"""
Management command to test all explorer URLs.
Run with: python manage.py test_explorer_urls
"""
from connect_labs.labs.management.commands.base_labs_url_test import BaseLabsURLTest


class Command(BaseLabsURLTest):
    help = "Test all explorer URLs"

    project_name = "explorer"
    base_urls = [
        "/labs/explorer/",
    ]
