import datetime as dt

from commcare_connect.labs.synthetic.generator.ocs_templates import TEMPLATES, render_transcript


def test_templates_exist():
    assert len(TEMPLATES) >= 5
    for key, tmpl in TEMPLATES.items():
        assert len(tmpl) >= 3, f"template {key} has fewer than 3 messages"
        for msg in tmpl:
            assert msg["role"] in ("bot", "flw")
            assert len(msg["text"]) > 0


def test_render_transcript_fills_placeholders():
    base_ts = dt.datetime(2026, 1, 15, 9, 0)
    result = render_transcript(template_key="high_flag_rate", flw_name="Nuhu D.", base_timestamp=base_ts)
    assert len(result) >= 3
    assert all("ts" in msg for msg in result)
    assert any("Nuhu" in msg["text"] for msg in result)
    timestamps = [dt.datetime.fromisoformat(msg["ts"]) for msg in result]
    assert timestamps == sorted(timestamps)


def test_render_unknown_template_falls_back():
    base_ts = dt.datetime(2026, 1, 15, 9, 0)
    result = render_transcript(template_key="nonexistent", flw_name="Test", base_timestamp=base_ts)
    assert len(result) >= 3
