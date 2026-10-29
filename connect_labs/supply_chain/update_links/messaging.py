"""The text of a message that carries an update link to a person's PersonalID chat.

Sent through Open Chat Studio (`OCSDataAccess.trigger_bot(platform="commcare_connect",
message_text=...)`), which delivers it to the Connect app. The app renders message
text as Markdown with no auto-linking, so a bare URL is NOT tappable: the link has
to be a Markdown link. See docs/personalid-authorization.md.
"""


def link_message(url: str, *, org_name: str = "", label: str = "") -> str:
    """A short message with the link as a tappable Markdown link."""
    what = label or (f"{org_name} stock" if org_name else "stock")
    return (
        f"[Update {what}]({url})\n\n"
        "Tap the link to record stock, receipts and counts. It is for you only; please do not forward it."
    )
