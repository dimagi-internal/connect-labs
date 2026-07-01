from commcare_connect.pages.providers import base, get_provider, list_providers, register


def test_register_and_get_provider():
    @register
    class DummyProvider(base.CardProvider):
        key = "dummy"
        label = "Dummy"
        target_kind = "opportunity"

        def get_card_data(self, request, target, options):
            return base.CardPayload(title="hi", card_type="stat")

    prov = get_provider("dummy")
    assert prov is not None
    assert prov.key == "dummy"
    assert prov in list_providers()

    payload = prov.get_card_data(request=None, target={}, options={})
    assert payload.to_dict()["title"] == "hi"
    assert payload.to_dict()["card_type"] == "stat"


def test_get_provider_unknown_returns_none():
    assert get_provider("does-not-exist-xyz") is None
