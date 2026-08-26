"""The generated Tailwind safelist is the guaranteed utility floor for
DB-stored workflow render_code. See connect_labs/labs/tailwind_safelist.py.
"""

from pathlib import Path

from django.conf import settings

from connect_labs.labs.tailwind_safelist import (
    COLOR_PREFIXES,
    NEGATABLE_PREFIXES,
    PALETTE_FAMILIES,
    SAFELIST_RELPATH,
    SHADES,
    SIZE_PREFIXES,
    SPACING,
    STRUCTURAL_UTILITIES,
    generate_safelist,
    safelist_path,
)


def _committed():
    return safelist_path(settings.BASE_DIR).read_text(encoding="utf-8")


def _entries(text):
    return {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}


class TestGeneratedSafelistIsCommitted:
    def test_committed_file_matches_the_generator(self):
        """Staleness gate: the file Tailwind scans must be what the generator
        produces. Regenerate with `python manage.py generate_tailwind_safelist`."""
        assert (
            _committed() == generate_safelist()
        ), "tailwind/safelist-generated.txt is stale. Run: python manage.py generate_tailwind_safelist"

    def test_file_warns_against_hand_editing(self):
        assert _committed().startswith("# GENERATED FILE - DO NOT EDIT BY HAND.")


class TestSafelistIsActuallyWiredIntoTheBuild:
    """The file only does anything because tailwind.css `@source`s it. Delete
    that one line and every other test here still passes while the entire fix
    silently stops working — which is precisely the failure mode labs#1294 is
    about."""

    def test_tailwind_css_sources_the_generated_safelist(self):
        css = (Path(settings.BASE_DIR) / "tailwind" / "tailwind.css").read_text(encoding="utf-8")
        expected = f'@source "./{SAFELIST_RELPATH.name}";'
        assert expected in css, (
            f"tailwind/tailwind.css must contain {expected!r}, otherwise "
            f"{SAFELIST_RELPATH} is never scanned and the safelist has no effect."
        )


class TestFullPaletteIsCovered:
    def test_every_family_shade_and_colour_prefix_is_present(self):
        entries = _entries(_committed())
        missing = [
            f"{prefix}-{family}-{shade}"
            for prefix in COLOR_PREFIXES
            for family in PALETTE_FAMILIES
            for shade in SHADES
            if f"{prefix}-{family}-{shade}" not in entries
        ]
        assert missing == []

    def test_every_sizing_prefix_covers_the_whole_spacing_scale(self):
        entries = _entries(_committed())
        missing = [
            f"{prefix}-{value}" for prefix in SIZE_PREFIXES for value in SPACING if f"{prefix}-{value}" not in entries
        ]
        assert missing == []

    def test_negative_spacing_is_generated(self):
        """The repo's own render code uses `-mb-px` and `-mx-4`; the positive
        matrix alone does not emit them."""
        entries = _entries(_committed())
        for prefix in NEGATABLE_PREFIXES:
            assert f"-{prefix}-4" in entries
        assert "-mb-px" in entries

    def test_structural_utilities_are_generated(self):
        entries = _entries(_committed())
        missing = [u for u in STRUCTURAL_UTILITIES if u not in entries]
        assert missing == []

    def test_the_specific_utilities_that_rendered_invisible_are_covered(self):
        """Every utility labs#1294 measured as purged from the bundle. The
        purge is per-utility, not per-family — its sibling resolving is not
        evidence that it does."""
        entries = _entries(_committed())
        for utility in ("bg-slate-400", "bg-emerald-600", "text-rose-700", "bg-rose-400", "h-28"):
            assert utility in entries, f"{utility} is not covered by the generated safelist"
