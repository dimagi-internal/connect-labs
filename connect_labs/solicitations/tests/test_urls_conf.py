"""Minimal URL configuration for URL resolution tests.

This avoids loading the full project urlconf which requires
optional dependencies like django_weasyprint.
"""
from django.urls import include, path

urlpatterns = [
    path("solicitations/", include("connect_labs.solicitations.urls", namespace="solicitations")),
]
