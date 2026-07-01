"""
Management command to test all tasks URLs.
Run with: python manage.py test_tasks_urls
"""
from connect_labs.labs.management.commands.base_labs_url_test import BaseLabsURLTest


class Command(BaseLabsURLTest):
    help = "Test all tasks URLs"

    project_name = "tasks"
    base_urls = [
        "/tasks/",
        "/tasks/create/",
    ]
