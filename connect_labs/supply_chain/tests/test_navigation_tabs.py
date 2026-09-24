"""Every supply screen keeps the nav tab it belongs under highlighted.

A view missing from `TAB_FOR_VIEW` un-highlights every tab, so the nav reads
as though you had left the domain. That was found and fixed once for a quote's
own page, and the same hole was still open on the approval-request screen the
test-kit walkthrough films. This closes the set rather than the one instance.
"""

from django.urls import get_resolver

from connect_labs.supply_chain.navigation import SUPPLY_TABS, TAB_FOR_VIEW, VIEWS_WITHOUT_TABS

TABS = {name for name, _ in SUPPLY_TABS}


def _view_names() -> set[str]:
    namespace = get_resolver().namespace_dict.get("supply_chain")
    assert namespace, "supply_chain urls are not mounted"
    _, resolver = namespace
    return {f"supply_chain:{name}" for name in resolver.reverse_dict if isinstance(name, str)}


def test_every_supply_view_lands_under_a_tab():
    unaccounted = sorted(_view_names() - TABS - set(TAB_FOR_VIEW) - VIEWS_WITHOUT_TABS)
    assert not unaccounted, (
        "these supply views highlight no nav tab, so the nav reads as if you had left "
        f"the domain: {unaccounted}. Add each to TAB_FOR_VIEW, or to VIEWS_WITHOUT_TABS "
        "with the reason it renders no nav."
    )


def test_each_mapping_points_at_a_real_tab():
    assert not sorted(set(TAB_FOR_VIEW.values()) - TABS)


def test_the_exempt_set_names_only_real_views():
    # `dev_login` is mounted only under DEBUG, so it is absent here by design.
    assert not sorted(VIEWS_WITHOUT_TABS - _view_names() - {"supply_chain:dev_login"})


def test_nothing_is_both_exempt_and_mapped():
    assert not sorted(VIEWS_WITHOUT_TABS & set(TAB_FOR_VIEW))


class TestSupplyTextStaysLegible:
    """No supply screen writes body text in a colour that fails WCAG AA.

    Measured off the rendered pages (the walkthrough's visual-geometry lens):
    `text-gray-400` is 2.54:1 on white and `text-[11px] text-gray-500` is
    4.43:1 on the cards it sits on -- both under the 4.5:1 floor for normal
    text. Every use was real prose a reader has to parse ("nothing dated",
    "not set", a spec verdict, a rationale), never decoration, so both were
    swept to `text-gray-600`. This keeps them swept: text presence and text
    legibility are different properties, and every other template test sees
    only the first.
    """

    def _templates(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "templates" / "supply_chain"
        assert root.is_dir(), root
        return sorted(root.rglob("*.html"))

    def test_no_supply_template_uses_the_palest_grey_for_text(self):
        offenders = [str(f) for f in self._templates() if "text-gray-400" in f.read_text()]
        assert not offenders, (
            f"text-gray-400 measures 2.54:1 on white, below the 4.5:1 AA floor: {offenders}. "
            "Use text-gray-600 for secondary text."
        )

    def test_no_supply_template_sets_eleven_pixel_text_in_gray_500(self):
        offenders = [
            str(f)
            for f in self._templates()
            if "text-[11px] text-gray-500" in (t := f.read_text()) or "text-gray-500 text-[11px]" in t
        ]
        assert not offenders, (
            f"11px text-gray-500 measures 4.43:1 on a card, below the 4.5:1 AA floor: {offenders}. "
            "Use text-gray-600 at that size."
        )


class TestTheOneDateRuleReachesEveryScreen:
    """Every date a supply screen prints goes through `|day`.

    The rule is stated once in values.DAY_FORMAT, but a template that prints
    an operation's ISO string straight out bypasses it, and one page then
    reads "2026-09-24" beside "24 Sep 2026". Two renders in a row were capped
    partly on exactly that. This is a source check, not a render check: it is
    the only way to catch the screens no walkthrough happens to film.
    """

    #: Context keys that hold a date. Anything printed from one must be filtered.
    DATE_KEYS = (
        "decided_on",
        "requested_on",
        "signed_on",
        "received_on",
        "confirmed_on",
        "issued_on",
        "expiry",
        "validity_until",
    )

    def _templates(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "templates" / "supply_chain"
        return sorted(root.rglob("*.html"))

    def test_no_supply_template_prints_a_raw_date(self):
        import re

        offenders = []
        for f in self._templates():
            for number, line in enumerate(f.read_text().splitlines(), 1):
                for key in self.DATE_KEYS:
                    # `{{ x.<key> }}` or `{{ x.<key>|default:… }}` with no `|day`
                    for match in re.finditer(r"\{\{\s*[\w.]*\b" + key + r"\b([^}]*)\}\}", line):
                        if "|day" not in match.group(0) and "value=" not in line:
                            offenders.append(f"{f.name}:{number}: {match.group(0)}")
        assert (
            not offenders
        ), "these print a date without the one date rule (values.DAY_FORMAT); add `|day`: " + "; ".join(offenders)
