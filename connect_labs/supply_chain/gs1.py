"""GS1 identifier helpers — check digits, key construction, Digital Link.

Ported from connect_labs/supply/gs1.py (the OES satellite site, being retired).
Copied rather than imported because that app is going away.

Its RUTF unit-ladder constants were deliberately NOT ported: a carton holding 150
sachets is a fact about one commodity, not about GS1, and it lives on
commodity.base_per_pack. The conversions live in values.py.
"""


def check_digit(payload):
    """Mod-10 check digit for a GS1 key payload (the key without its last digit)."""
    if not payload.isdigit():
        raise ValueError("GS1 payloads must be numeric")
    total = 0
    for i, ch in enumerate(reversed(payload)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - (total % 10)) % 10


def _with_check_digit(payload, length):
    if len(payload) != length - 1:
        raise ValueError(f"expected {length - 1} digits before the check digit, got {len(payload)}")
    return f"{payload}{check_digit(payload)}"


def make_sscc(company_prefix, serial, extension=3):
    """Build an 18-digit SSCC from an extension digit, company prefix and serial."""
    body = f"{extension}{company_prefix}"
    body = f"{body}{str(serial).zfill(17 - len(body))}"
    return _with_check_digit(body[:17], 18)


def make_gtin(company_prefix, item_ref, indicator=0):
    """Build a 14-digit GTIN (indicator digit + prefix + item reference)."""
    body = f"{indicator}{company_prefix}"
    body = f"{body}{str(item_ref).zfill(13 - len(body))}"
    return _with_check_digit(body[:13], 14)


def make_gln(company_prefix, location_ref):
    body = f"{company_prefix}{str(location_ref).zfill(12 - len(company_prefix))}"
    return _with_check_digit(body[:12], 13)


def is_valid(key):
    """True when a GS1 key's trailing check digit is correct."""
    return bool(key) and key.isdigit() and check_digit(key[:-1]) == int(key[-1])


def digital_link(key_ai, key):
    """GS1 Digital Link URI — the modern EPCIS 2.0 identifier form."""
    return f"https://id.gs1.org/{key_ai}/{key}"


def parse_digital_link(uri):
    """Return (application_identifier, key) from a GS1 Digital Link URI.

    Also accepts legacy ``urn:epc:id:sscc:...`` / ``sgtin`` forms so a supplier
    on either identifier style can post events.
    """
    if not uri:
        return None, None
    text = str(uri)
    if text.startswith("https://id.gs1.org/"):
        parts = text[len("https://id.gs1.org/") :].strip("/").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
        return None, None
    if text.startswith("urn:epc:id:sscc:"):
        return "00", text.rsplit(":", 1)[-1].replace(".", "")
    if text.startswith("urn:epc:id:sgtin:"):
        return "01", text.rsplit(":", 1)[-1].replace(".", "")
    if text.startswith("urn:epc:id:sgln:"):
        return "414", text.rsplit(":", 1)[-1].replace(".", "")
    return None, text
