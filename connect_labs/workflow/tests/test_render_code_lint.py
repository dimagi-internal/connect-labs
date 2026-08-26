"""Tests for the render_code write-time class check.

NOTE for anyone editing this file: Tailwind's automatic source detection scans
this repo's Python files, so a literal utility class name written here would
end up safelisted into the real bundle and could mask the very failure a test
is asserting. Every class name below is therefore either built by string
concatenation or a deliberately non-Tailwind placeholder.
"""

from connect_labs.workflow.render_code_lint import (
    available_classes,
    extract_class_candidates,
    find_unresolved_classes,
    render_code_warning,
)

# Built by concatenation so no literal utility token appears in this file.
ARBITRARY = "min-w-" + "[52px]"
PRESENT = "zzzprobe-present"
ABSENT = "zzzprobe-absent"

STYLESHEET = f"""
.{PRESENT} {{ color: red; }}
.flex {{ display: flex; }}
.hover\\:{PRESENT}:hover {{ color: blue; }}
.{PRESENT}\\/20 {{ color: rgb(0 0 0 / 20%); }}
.min-w-\\[40px\\] {{ min-width: 40px; }}
"""


def _bundle(tmp_path):
    directory = tmp_path / "css"
    directory.mkdir()
    (directory / "tailwind.css").write_text(STYLESHEET, encoding="utf-8")
    return directory


class TestAvailableClasses:
    def test_parses_escaped_selectors(self, tmp_path):
        classes = available_classes(_bundle(tmp_path))
        assert PRESENT in classes
        assert f"hover:{PRESENT}" in classes
        assert f"{PRESENT}/20" in classes
        assert "min-w-" + "[40px]" in classes

    def test_unions_every_bundle_in_the_directory(self, tmp_path):
        directory = _bundle(tmp_path)
        (directory / "vendors.css").write_text(".zzzprobe-vendor { color: teal; }", encoding="utf-8")
        assert "zzzprobe-vendor" in available_classes(directory)

    def test_returns_none_when_nothing_has_been_built(self, tmp_path):
        """CI runs no npm step, so the bundles are absent there. That must read
        as 'no opinion', never as 'every class is missing'."""
        empty = tmp_path / "css"
        empty.mkdir()
        assert available_classes(empty) is None
        assert available_classes(tmp_path / "does-not-exist") is None


class TestExtractClassCandidates:
    def test_reads_class_and_classname_attributes(self):
        source = f'<div class="{PRESENT}"><span className="{ABSENT} flex" /></div>'
        assert extract_class_candidates(source) == {PRESENT, ABSENT, "flex"}

    def test_reads_string_literals_inside_a_className_expression(self):
        source = f'<div className={{clsx("{PRESENT}", active && "{ABSENT}")}} />'
        assert extract_class_candidates(source) == {PRESENT, ABSENT}

    def test_skips_tokens_broken_by_a_template_interpolation(self):
        """`text-${color}-500` is not a class anyone can judge — the fragments
        around the interpolation are partial tokens, not utilities."""
        source = "<div className={`text-${color}-500 flex`} />"
        assert extract_class_candidates(source) == {"flex"}

    def test_ignores_non_class_looking_tokens(self):
        source = '<div className="Foo /bar/baz 123 {}" />'
        assert extract_class_candidates(source) == set()


class TestFindUnresolvedClasses:
    def test_flags_a_class_no_stylesheet_defines(self, tmp_path):
        source = f'<div className="{PRESENT} {ABSENT}" />'
        assert find_unresolved_classes(source, bundle_dir=_bundle(tmp_path)) == [ABSENT]

    def test_flags_an_arbitrary_value_utility_the_safelist_cannot_cover(self, tmp_path):
        """The residual `tailwind/safelist-generated.txt` structurally cannot
        reach: an arbitrary bracketed value has to appear verbatim in a scanned
        source, and DB-stored render_code is never scanned. This is the case
        that collapsed a 12-week bar chart to zero height (labs#1294)."""
        source = f'<div className="{ARBITRARY}" />'
        assert find_unresolved_classes(source, bundle_dir=_bundle(tmp_path)) == [ARBITRARY]

    def test_silent_when_everything_resolves(self, tmp_path):
        source = f'<div className="{PRESENT} flex hover:{PRESENT}" />'
        assert find_unresolved_classes(source, bundle_dir=_bundle(tmp_path)) == []

    def test_fails_open_when_bundles_are_absent(self, tmp_path):
        source = f'<div className="{ABSENT} {ARBITRARY}" />'
        assert find_unresolved_classes(source, bundle_dir=tmp_path / "nope") == []


class TestRenderCodeWarning:
    def test_none_when_clean(self, tmp_path):
        assert render_code_warning(f'<div className="{PRESENT}" />', bundle_dir=_bundle(tmp_path)) is None

    def test_names_the_class_and_the_remedy(self, tmp_path):
        warning = render_code_warning(f'<div className="{ABSENT}" />', bundle_dir=_bundle(tmp_path))
        assert warning["unresolved_classes"] == [ABSENT]
        assert warning["unresolved_class_count"] == 1
        assert ABSENT in warning["message"]
        assert "style=" in warning["message"]

    def test_caps_the_reported_list(self, tmp_path):
        classes = " ".join(f"zzzprobe-many-{i}" for i in range(60))
        warning = render_code_warning(f'<div className="{classes}" />', bundle_dir=_bundle(tmp_path))
        assert warning["unresolved_class_count"] == 60
        assert len(warning["unresolved_classes"]) == 25
