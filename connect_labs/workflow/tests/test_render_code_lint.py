"""Tests for the render_code write-time class check.

NOTE for anyone editing this file: Tailwind's automatic source detection scans
this repo's Python files, so a literal utility class name written here would
end up safelisted into the real bundle and could mask the very failure a test
is asserting. Every class name below is therefore either built by string
concatenation or a deliberately non-Tailwind placeholder.
"""

from unittest.mock import MagicMock, patch

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

    def test_unions_every_stylesheet_the_runner_page_loads(self, tmp_path):
        directory = _bundle(tmp_path)
        (directory / "vendors.css").write_text(".zzzprobe-vendor { color: teal; }", encoding="utf-8")
        assert "zzzprobe-vendor" in available_classes(directory)

    def test_ignores_bundles_the_runner_page_does_not_load(self, tmp_path):
        """`webpack/build-supply.js` writes supply-bundle.css into the same
        directory, but `templates/base.html` never links it. Reading it would
        let a render_code using one of its classes (`btn`, `card`, `field`)
        pass as available while rendering completely unstyled — a MISSED
        warning of exactly the class this module exists to catch."""
        directory = _bundle(tmp_path)
        (directory / "supply-bundle.css").write_text(".zzzprobe-supply { color: teal; }", encoding="utf-8")
        assert "zzzprobe-supply" not in available_classes(directory)

    def test_cache_is_published_as_one_entry(self, tmp_path):
        """Two writes left a window where a reader saw a fresh key with a stale
        (or absent) class set — a live race under gunicorn gthread."""
        from connect_labs.workflow import render_code_lint

        directory = _bundle(tmp_path)
        available_classes(directory)
        assert set(render_code_lint._cache) == {"state"}
        key, classes = render_code_lint._cache["state"]
        assert PRESENT in classes

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

    def test_ignores_comparison_operands_in_a_className_expression(self):
        """The dominant idiom in this repo's render code puts non-class string
        literals inside className, and they are all hyphen-free:

            className={'px-2 ' + (session.status === 'completed' ? ... : ...)}

        Measured over the 34 templates in connect_labs/workflow/templates/,
        that shape produced 15 distinct bogus candidates at 19 sites. Warning
        about those would make this noise an author learns to skip, which costs
        exactly the signal labs#1294 needs."""
        source = "<div className={'px-2 py-1 ' + (s.status === 'completed' ? 'font-bold' : 'italic')} />"
        assert extract_class_candidates(source) == {"px-2", "py-1", "font-bold", "italic"}

    def test_keeps_hyphen_free_utilities_that_are_real(self):
        source = '<div className="flex hidden truncate border rounded" />'
        assert extract_class_candidates(source) == {"flex", "hidden", "truncate", "border", "rounded"}

    def test_drops_tokens_left_dangling_by_string_concatenation(self):
        """The `${...}` sentinel handles template literals; `"text-" + colour`
        is the same partial-token problem via concatenation."""
        source = '<div className={"text-" + colour} />'
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


class TestWriteBoundaryWiring:
    """The check is only worth anything if the write paths actually call it,
    and only safe if it can never fail a write that already succeeded."""

    def test_mcp_write_result_carries_the_warning(self, tmp_path):
        from connect_labs.mcp.tools.workflows import _attach_render_code_warning

        with patch(
            "connect_labs.workflow.render_code_lint.available_classes",
            return_value=available_classes(_bundle(tmp_path)),
        ):
            result = _attach_render_code_warning({"workflow_id": 1}, f'<div className="{ABSENT}" />')
        assert result["render_code_warning"]["unresolved_classes"] == [ABSENT]

    def test_mcp_write_result_is_clean_when_everything_resolves(self, tmp_path):
        from connect_labs.mcp.tools.workflows import _attach_render_code_warning

        with patch(
            "connect_labs.workflow.render_code_lint.available_classes",
            return_value=available_classes(_bundle(tmp_path)),
        ):
            result = _attach_render_code_warning({"workflow_id": 1}, f'<div className="{PRESENT}" />')
        assert "render_code_warning" not in result

    def test_an_exception_in_the_check_cannot_break_an_mcp_write(self):
        """The record is already written by the time this runs. An advisory
        check must never turn a successful write into an error."""
        from connect_labs.mcp.tools.workflows import _attach_render_code_warning

        with patch(
            "connect_labs.workflow.render_code_lint.render_code_warning",
            side_effect=RuntimeError("boom"),
        ):
            result = _attach_render_code_warning({"workflow_id": 1}, "<div />")
        assert result == {"workflow_id": 1}

    def test_an_exception_in_the_check_cannot_break_the_view_save(self, rf, tmp_path):
        """Same contract on the Django save API, where the consequence is worse:
        that view's `except Exception` returns a 500, so an unguarded check
        would report a failed save on a save that already landed, and the author
        would retry a write that succeeded."""
        import json

        from connect_labs.workflow.views import save_render_code_api

        request = rf.post(
            "/labs/workflow/api/5230/render-code/",
            data=json.dumps({"component_code": "<div />"}),
            content_type="application/json",
        )
        request.user = MagicMock(is_authenticated=True)

        record = MagicMock(id=42)
        with (
            patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA,
            patch(
                "connect_labs.workflow.render_code_lint.render_code_warning",
                side_effect=RuntimeError("boom"),
            ),
        ):
            MockWDA.return_value.save_render_code.return_value = record
            response = save_render_code_api(request, 5230)

        assert response.status_code == 200
        assert json.loads(response.content)["success"] is True
