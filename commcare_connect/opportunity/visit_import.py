"""Minimal stub for migration compatibility.

The full visit_import module was removed during labs simplification.
This stub provides get_exchange_rate which is referenced by migrations
0059 and 0067 (data migrations that have already run on production).
"""

from django.utils.timezone import now


def get_exchange_rate(currency_code, date=None):
    from commcare_connect.opportunity.models import ExchangeRate

    if currency_code is None:
        raise Exception("Opportunity must have specified currency to import payments")

    currency_code = currency_code.upper()

    if currency_code == "USD":
        return 1

    rate_date = date or now().date()
    rate_obj = (
        ExchangeRate.objects.filter(currency_code=currency_code, rate_date__lte=rate_date)
        .order_by("-rate_date")
        .first()
    )

    if not rate_obj or not rate_obj.rate:
        raise Exception("Rate not found for opportunity currency")

    return rate_obj.rate
