"""
Management command to test all Labs Admin URLs.
Run with: python manage.py test_admin_urls
"""
from connect_labs.labs.management.commands.base_labs_url_test import BaseLabsURLTest


class Command(BaseLabsURLTest):
    help = "Test all Labs Admin URLs"

    project_name = "admin"
    base_urls = [
        "/labs/admin/",
    ]
