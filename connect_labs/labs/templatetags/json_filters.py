"""Template filters for JSON serialization."""
import json

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name="tojson")
def tojson(value):
    """Convert a Python object to JSON for use in JavaScript.

    Handles proper conversion of Python types to JavaScript types:
    - None -> null
    - True/False -> true/false
    - etc.
    """
    return mark_safe(json.dumps(value))
