from django import template

register = template.Library()


@register.filter
def dictkey(mapping, key):
    return (mapping or {}).get(key)
