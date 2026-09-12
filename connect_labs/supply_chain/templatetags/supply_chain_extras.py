from django import template

register = template.Library()


@register.filter
def dictkey(mapping, key):
    return (mapping or {}).get(key)


@register.filter
def questions_for(questions, audience):
    """Split a row's outstanding questions by who they are for -- design doc
    section 7's audience split. A comparison row's `questions` list mixes
    supplier-facing facts with our own internal gaps (a misconfigured round,
    a missing course definition); this is what lets the comparison screen
    show "ask the supplier" and "our own outstanding work" as two lists
    instead of one undifferentiated one.
    """
    return [q for q in (questions or []) if q.get("audience") == audience]
