"""Tests for the walkthrough HTML presentation generator."""

import json

# Minimal 1x1 red JPEG for testing (44 bytes)
TINY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
    "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
    "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf"
    "/EABQQAQAAAAAAAAAAAAAAAAAAAAD/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAA"
    "AAAAAAAAAAA//aAAwDAQACEQMRAD8AKwA//9k="
)


def _make_run_data(slides=None, name="Test Demo", narrative="Test narrative"):
    """Build minimal run data for testing."""
    return {
        "name": name,
        "narrative": narrative,
        "generated_at": "2026-03-23T14:30:00Z",
        "duration_seconds": 120,
        "personas": {
            "sarah": {
                "name": "Sarah Chen",
                "role": "Program Manager",
                "color": "#2563eb",
                "intro": "Sarah manages programs.",
            }
        },
        "slides": slides
        or [
            {"type": "title"},
            {"type": "persona_intro", "persona_key": "sarah"},
            {
                "type": "scene",
                "scene_index": 1,
                "scene_total": 1,
                "persona_key": "sarah",
                "title": "Test Scene",
                "narration": "This is a test scene.",
                "screenshot_b64": TINY_JPEG_B64,
                "ai_evaluation": None,
            },
            {
                "type": "summary",
                "scenes_completed": 1,
                "scenes_total": 1,
                "ai_scores": [],
                "issues": [],
                "previous_run": None,
            },
        ],
    }


class TestGeneratePresentation:
    """Tests for generate_presentation module."""

    def test_generates_html_file(self, tmp_path):
        """Generator produces an HTML file at the specified output path."""
        from tools.walkthrough.generate_presentation import generate

        output_path = tmp_path / "output.html"
        run_data = _make_run_data()

        generate(run_data, str(output_path))

        assert output_path.exists()
        content = output_path.read_text()
        assert content.startswith("<!DOCTYPE html>")

    def test_html_contains_demo_name(self, tmp_path):
        """The demo name appears in the HTML output."""
        from tools.walkthrough.generate_presentation import generate

        output_path = tmp_path / "output.html"
        generate(_make_run_data(name="My Custom Demo"), str(output_path))

        content = output_path.read_text()
        assert "My Custom Demo" in content

    def test_html_contains_narrative(self, tmp_path):
        """The narrative text appears in the HTML output."""
        from tools.walkthrough.generate_presentation import generate

        output_path = tmp_path / "output.html"
        generate(_make_run_data(narrative="AI helps everyone"), str(output_path))

        content = output_path.read_text()
        assert "AI helps everyone" in content

    def test_html_contains_persona_info(self, tmp_path):
        """Persona names and roles appear in the output."""
        from tools.walkthrough.generate_presentation import generate

        output_path = tmp_path / "output.html"
        generate(_make_run_data(), str(output_path))

        content = output_path.read_text()
        assert "Sarah Chen" in content
        assert "Program Manager" in content

    def test_html_embeds_screenshot_as_base64(self, tmp_path):
        """Screenshots are embedded as base64 data URIs."""
        from tools.walkthrough.generate_presentation import generate

        output_path = tmp_path / "output.html"
        generate(_make_run_data(), str(output_path))

        content = output_path.read_text()
        assert "data:image/png;base64," in content

    def test_html_contains_scene_title(self, tmp_path):
        """Scene titles appear in the output."""
        from tools.walkthrough.generate_presentation import generate

        output_path = tmp_path / "output.html"
        generate(_make_run_data(), str(output_path))

        content = output_path.read_text()
        assert "Test Scene" in content

    def test_html_contains_ai_evaluation(self, tmp_path):
        """AI quality evaluations appear when present."""
        from tools.walkthrough.generate_presentation import generate

        slides = [
            {"type": "title"},
            {"type": "persona_intro", "persona_key": "sarah"},
            {
                "type": "scene",
                "scene_index": 1,
                "scene_total": 1,
                "persona_key": "sarah",
                "title": "AI Scene",
                "narration": "Testing AI eval.",
                "screenshot_b64": TINY_JPEG_B64,
                "ai_evaluation": {
                    "score": 4,
                    "max_score": 5,
                    "commentary": "Criteria are relevant and well-weighted.",
                },
            },
            {
                "type": "summary",
                "scenes_completed": 1,
                "scenes_total": 1,
                "ai_scores": [{"feature": "Criteria", "score": 4, "max_score": 5}],
                "issues": [],
                "previous_run": None,
            },
        ]
        output_path = tmp_path / "output.html"
        generate(_make_run_data(slides=slides), str(output_path))

        content = output_path.read_text()
        assert "Criteria are relevant and well-weighted." in content

    def test_html_contains_keyboard_nav_js(self, tmp_path):
        """The output includes keyboard navigation JavaScript."""
        from tools.walkthrough.generate_presentation import generate

        output_path = tmp_path / "output.html"
        generate(_make_run_data(), str(output_path))

        content = output_path.read_text()
        assert "ArrowRight" in content
        assert "ArrowLeft" in content

    def test_html_has_print_css(self, tmp_path):
        """The output includes print media styles."""
        from tools.walkthrough.generate_presentation import generate

        output_path = tmp_path / "output.html"
        generate(_make_run_data(), str(output_path))

        content = output_path.read_text()
        assert "@media print" in content

    def test_html_is_self_contained(self, tmp_path):
        """The output has no external resource references (no <link href> or <script src>)."""
        from tools.walkthrough.generate_presentation import generate

        output_path = tmp_path / "output.html"
        generate(_make_run_data(), str(output_path))

        content = output_path.read_text()
        assert '<link rel="stylesheet" href=' not in content
        assert "<script src=" not in content

    def test_summary_slide_shows_issues(self, tmp_path):
        """Issues appear in the summary slide."""
        from tools.walkthrough.generate_presentation import generate

        slides = [
            {"type": "title"},
            {
                "type": "summary",
                "scenes_completed": 2,
                "scenes_total": 2,
                "ai_scores": [],
                "issues": [
                    {"scene": 1, "severity": "warning", "description": "Map had no markers"},
                    {"scene": 2, "severity": "error", "description": "Button not visible"},
                ],
                "previous_run": None,
            },
        ]
        output_path = tmp_path / "output.html"
        generate(_make_run_data(slides=slides), str(output_path))

        content = output_path.read_text()
        assert "Map had no markers" in content
        assert "Button not visible" in content

    def test_summary_slide_shows_previous_run_comparison(self, tmp_path):
        """Previous run comparison appears when provided."""
        from tools.walkthrough.generate_presentation import generate

        slides = [
            {"type": "title"},
            {
                "type": "summary",
                "scenes_completed": 1,
                "scenes_total": 1,
                "ai_scores": [{"feature": "Criteria", "score": 4, "max_score": 5}],
                "issues": [],
                "previous_run": {
                    "generated_at": "2026-03-22T10:00:00Z",
                    "ai_scores": [{"feature": "Criteria", "score": 3, "max_score": 5}],
                },
            },
        ]
        output_path = tmp_path / "output.html"
        generate(_make_run_data(slides=slides), str(output_path))

        content = output_path.read_text()
        # Should show the improvement
        assert "Previous Run" in content or "previous" in content.lower()


class TestWriteSidecar:
    """Tests for JSON sidecar (run history) generation."""

    def test_writes_sidecar_json(self, tmp_path):
        """Generator writes a JSON sidecar file alongside the HTML."""
        from tools.walkthrough.generate_presentation import generate

        output_path = tmp_path / "output.html"
        run_data = _make_run_data()

        generate(run_data, str(output_path))

        sidecar_path = tmp_path / "output.json"
        assert sidecar_path.exists()
        sidecar = json.loads(sidecar_path.read_text())
        assert sidecar["name"] == "Test Demo"
        assert sidecar["generated_at"] == "2026-03-23T14:30:00Z"

    def test_sidecar_contains_ai_scores(self, tmp_path):
        """Sidecar JSON contains AI quality scores for comparison."""
        from tools.walkthrough.generate_presentation import generate

        slides = [
            {"type": "title"},
            {
                "type": "summary",
                "scenes_completed": 1,
                "scenes_total": 1,
                "ai_scores": [{"feature": "Criteria", "score": 4, "max_score": 5}],
                "issues": [],
                "previous_run": None,
            },
        ]
        output_path = tmp_path / "output.html"
        generate(_make_run_data(slides=slides), str(output_path))

        sidecar = json.loads((tmp_path / "output.json").read_text())
        assert len(sidecar["ai_scores"]) == 1
        assert sidecar["ai_scores"][0]["feature"] == "Criteria"


class TestFailedScenes:
    """Tests for graceful handling of failed/skipped scenes."""

    def test_scene_with_no_screenshot_renders_placeholder(self, tmp_path):
        """A scene with screenshot_b64=None shows a placeholder, not a broken image."""
        from tools.walkthrough.generate_presentation import generate

        slides = [
            {"type": "title"},
            {"type": "persona_intro", "persona_key": "sarah"},
            {
                "type": "scene",
                "scene_index": 1,
                "scene_total": 1,
                "persona_key": "sarah",
                "title": "Failed Scene",
                "narration": "This scene could not be captured.",
                "screenshot_b64": None,
                "ai_evaluation": None,
                "error": "Element not found: .dashboard",
            },
            {
                "type": "summary",
                "scenes_completed": 0,
                "scenes_total": 1,
                "ai_scores": [],
                "issues": [{"scene": 1, "severity": "error", "description": "Element not found"}],
                "previous_run": None,
            },
        ]
        output_path = tmp_path / "output.html"
        generate(_make_run_data(slides=slides), str(output_path))

        content = output_path.read_text()
        assert "Failed Scene" in content
        # Should NOT have a broken image tag with empty src
        assert 'src="data:image/png;base64,"' not in content
        # Should show the error
        assert "Element not found" in content


class TestCLIInterface:
    """Tests for the command-line interface."""

    def test_cli_reads_json_and_produces_html(self, tmp_path):
        """CLI reads a JSON input file and produces an HTML output file."""
        from tools.walkthrough.generate_presentation import main as cli_main

        input_path = tmp_path / "input.json"
        output_path = tmp_path / "output.html"
        input_path.write_text(json.dumps(_make_run_data()))

        cli_main(["--input", str(input_path), "--output", str(output_path)])

        assert output_path.exists()
        assert output_path.read_text().startswith("<!DOCTYPE html>")
