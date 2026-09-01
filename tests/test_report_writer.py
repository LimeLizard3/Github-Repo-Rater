"""
Unit tests for report_writer.py's pure text-processing functions
(_strip_garbled_artifacts, and the PDF/HTML redesign helpers added
2026-08-31). No live API calls, no Chrome -- pure string in, string out.
"""

from __future__ import annotations

from server.Report_Writing.report_writer import (
    _apply_badges,
    _format_generated_at,
    _quality_score_badge,
    _strip_garbled_artifacts,
    _strip_redundant_header_lines,
    render_html,
)


def test_strips_literal_backslash_n_sequence():
    garbled = "First part." + "\\n\\n" + "Second part."
    result = _strip_garbled_artifacts(garbled)
    assert "\\n" not in result
    assert "First part." in result
    assert "Second part." in result


def test_strips_literal_backslash_r_sequence():
    garbled = "First part." + "\\r\\n" + "Second part."
    result = _strip_garbled_artifacts(garbled)
    assert "\\r" not in result
    assert "\\n" not in result


def test_strips_stray_closing_tag():
    garbled = "Some justification text.</justification>"
    result = _strip_garbled_artifacts(garbled)
    assert "</justification>" not in result
    assert "Some justification text." in result


def test_strips_two_different_tags_in_one_string():
    garbled = "<justification>The repo is well structured.</justification>"
    result = _strip_garbled_artifacts(garbled)
    assert "<justification>" not in result
    assert "</justification>" not in result
    assert "The repo is well structured." in result


def test_real_newlines_are_untouched():
    text = "Paragraph one.\nParagraph two.\n\nParagraph three."
    assert _strip_garbled_artifacts(text) == text


def test_underscores_are_untouched():
    text = "Naming uses __init__.py and snake_case_names throughout."
    assert _strip_garbled_artifacts(text) == text


def test_ordinary_comparison_operators_are_untouched():
    text = "if x < y and z > w, this should survive untouched"
    assert _strip_garbled_artifacts(text) == text


def test_empty_string_returns_empty_string():
    assert _strip_garbled_artifacts("") == ""


# --- _apply_badges ---------------------------------------------------------


def test_severity_badge_gets_wrapped_and_uppercased():
    html = "<li>[critical] something broke</li>"
    result = _apply_badges(html)
    assert '<span class="badge badge-critical">CRITICAL</span>' in result
    assert "[critical]" not in result


def test_all_four_severities_get_their_own_class():
    for severity, css_class in [
        ("critical", "badge-critical"),
        ("major", "badge-major"),
        ("minor", "badge-minor"),
        ("inferred", "badge-inferred"),
    ]:
        result = _apply_badges(f"<li>[{severity}] x</li>")
        assert f'class="badge {css_class}"' in result


def test_dimension_badge_gets_wrapped():
    html = "<li>[Architecture] Clear separation of concerns</li>"
    result = _apply_badges(html)
    assert '<span class="badge badge-dim-arch">Architecture</span>' in result
    assert "[Architecture]" not in result


def test_not_available_paragraph_gets_callout_wrapper():
    html = "<p><strong>Not available</strong> — subagent timed out</p>"
    result = _apply_badges(html)
    assert result.startswith('<div class="callout-fail">')
    assert "subagent timed out" in result


def test_plain_text_without_badge_patterns_is_untouched():
    html = "<p>This is ordinary prose with no severity or dimension tags.</p>"
    assert _apply_badges(html) == html


# --- _strip_redundant_header_lines ------------------------------------------


def test_strips_leading_h1_and_quality_score_paragraph():
    html = (
        "<h1>owner/repo</h1>\n"
        "<p><strong>Quality score:</strong> 7.4/10</p>\n"
        "<h2>Architecture:</h2>\n"
        "<p>Some text.</p>"
    )
    result = _strip_redundant_header_lines(html)
    assert "<h1>" not in result
    assert "Quality score" not in result
    assert "<h2>Architecture:</h2>" in result
    assert "Some text." in result


def test_strips_na_quality_score_line_too():
    html = "<h1>owner/repo</h1>\n<p><strong>Quality score:</strong> N/A (Architecture unavailable)</p>\n<h2>x</h2>"
    result = _strip_redundant_header_lines(html)
    assert "Quality score" not in result
    assert "<h2>x</h2>" in result


# --- _quality_score_badge / _format_generated_at ----------------------------


def test_quality_score_badge_bands():
    assert "qs-good" in _quality_score_badge(9.0)
    assert "qs-mid" in _quality_score_badge(6.5)
    assert "qs-low" in _quality_score_badge(2.0)
    assert "qs-na" in _quality_score_badge(None)


def test_quality_score_badge_shows_one_decimal():
    assert "7.4/10" in _quality_score_badge(7.4)


def test_format_generated_at_parses_iso8601():
    assert _format_generated_at("2026-08-31T14:23:00Z") == "August 31, 2026"


def test_format_generated_at_falls_back_on_unparseable_input():
    assert _format_generated_at("not-a-real-timestamp") == "not-a-real-timestamp"


# --- render_html end-to-end (no Chrome involved, just the HTML string) -----


def test_render_html_has_no_duplicate_title_or_quality_score():
    md = "# owner/repo\n\n**Quality score:** 8.0/10\n\n## Architecture:\n\nSome text."
    html = render_html(md, title="owner/repo", quality_score=8.0, generated_at="2026-08-31T00:00:00Z")
    assert html.count("<h1") == 1  # only the cover page's h1, not a second one in the body
    assert html.count("Quality score") == 0  # replaced entirely by the cover page's badge
    assert "qs-badge" in html
    assert "cover-page" in html
    assert "August 31, 2026" in html
