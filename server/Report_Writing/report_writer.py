"""
Phase 5 report writer: validate the coordinator's raw dict, persist it as
JSON, and render Markdown/PDF views from the validated object.

HTML is still generated FROM the rendered Markdown (never by walking the
RepoRating object a second time), but it's now only an intermediate step --
it gets converted to PDF via headless Chrome and is never itself written to
reports/ratings/. JSON stays as an internal record, not a user-facing view.

2026-08-31: render_html() gained two extra scalar params (quality_score,
generated_at) to build a styled header bar -- a small, deliberate exception
to "never re-walk the RepoRating object": the header is presentational
chrome, not content, and the body below it still comes exclusively from
the markdown text. No second parallel content template was introduced.
"""

from __future__ import annotations

import base64
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import markdown as markdown_lib

from server.Report_Writing.report_schema import DimensionStatus, RepoRating

REPORTS_DIR = Path("reports/ratings")
_LOGO_PATH = Path(__file__).parent / "assets" / "logo.png"

# Hardcoded to the standard Windows install location -- this project runs
# locally on Windows only for now. Revisit if this ever needs to run
# somewhere Chrome isn't at this path (e.g. a server, Phase 7+).
CHROME_PATH = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_filename_component(s: str) -> str:
    return _UNSAFE_FILENAME_CHARS.sub("_", s)


_TAG_PATTERN = re.compile(r"</?[A-Za-z_][A-Za-z0-9_]*>")


def _strip_garbled_artifacts(text: str) -> str:
    """Strip artifacts occasionally present in raw Anthropic API output:
    literal backslash-n/backslash-r sequences (the two characters, not a
    real newline) used as a line-break stand-in, and stray XML/HTML-style
    tags (e.g. a leaked "</justification>"). This is a structural backstop
    -- the SYSTEM_PROMPT guard in results_subagent.py doesn't reliably
    prevent this on its own, so this closes the failure mode in code
    instead of depending on the prompt working every time. [[results-subagent-garbled-justification-text]]
    """
    if not text:
        return text
    text = text.replace("\\n", " ").replace("\\r", " ")
    text = _TAG_PATTERN.sub("", text)
    return re.sub(r" {2,}", " ", text)


_ITEM_TAG_PATTERN = re.compile(r"<item>(.*?)</item>", re.DOTALL)


def _coerce_str_list(value: Any) -> list[str]:
    """Defends against a subagent occasionally returning strengths/weaknesses
    as one glued-together string (sometimes with pseudo <item> tags) instead
    of a real JSON array. Iterating a bare string with a plain `for s in
    value` walks it character-by-character -- Python strings are iterable at
    the character level -- producing hundreds of single-character "items"
    that Pydantic happily accepts (each character IS a valid str on its
    own). That corruption already reached a real generated report, ballooning
    it to 150 pages / ~3MB, slow enough on mobile data to look like a hang.

    If we get a string, first try to recover the real intended items from
    any <item>...</item> wrapping (the shape the model seems to reach for
    when it collapses an array into a string); otherwise treat the whole
    string as a single item rather than shredding it into characters.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        items = _ITEM_TAG_PATTERN.findall(value)
        return items if items else [value]
    return []


def _sanitize_dimension(dim: dict[str, Any]) -> None:
    if dim.get("justification"):
        dim["justification"] = _strip_garbled_artifacts(dim["justification"])
    for key in ("strengths", "weaknesses"):
        if dim.get(key):
            dim[key] = [_strip_garbled_artifacts(s) for s in _coerce_str_list(dim[key])]
    if dim.get("issues_found"):
        for issue in dim["issues_found"]:
            issue["description"] = _strip_garbled_artifacts(issue["description"])


def _sanitize_raw(raw: dict[str, Any]) -> dict[str, Any]:
    # Mutates the dimension sub-dicts in place -- raw is a freshly built
    # dict from rate_repo(), not reused elsewhere, so this is safe.
    for dim_name in ("architecture", "results_functionality", "documentation"):
        dim = raw.get(dim_name)
        if isinstance(dim, dict):
            _sanitize_dimension(dim)
    for key in ("strengths", "weaknesses"):
        if raw.get(key):
            raw[key] = [_strip_garbled_artifacts(s) for s in _coerce_str_list(raw[key])]
    return raw


def parse_rating(raw: dict[str, Any], generated_at: str) -> RepoRating:
    raw = _sanitize_raw(raw)
    return RepoRating.model_validate({**raw, "generated_at": generated_at, "schema_version": "1"})
    #model_validate comes from Pydantic's BaseModel class itself, and RepoRating gets it via inheritance


def _base_filename(rating: RepoRating, tag: str | None = None) -> str:
    owner, _, repo = rating.repo.partition("/")
    # Date-only, colon-free (':' is illegal in Windows filenames). Coarser
    # than the old full timestamp -- a rerun on the same repo on the same
    # day overwrites the previous report unless a --tag is given.
    # rating.generated_at (the field, not the filename) keeps real
    # ISO-8601 with colons and seconds; that's data, not a path.
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    base = (
        f"{_sanitize_filename_component(owner)}__"
        f"{_sanitize_filename_component(repo)}__{date}_report"
    )
    if tag:
        base += f"_{_sanitize_filename_component(tag)}"
    return base


def _escape_markdown(text: str) -> str:
    """Escape characters that Markdown would otherwise treat as formatting.

    Model-generated text routinely contains things like "__init__.py" or
    "snake_case_name" -- Markdown reads "__x__" as bold, mangling them into
    "<strong>x</strong>.py" unless escaped. Only escaping _ and * (not every
    special character) keeps the raw .md file itself still readable.
    """
    return text.replace("_", "\\_").replace("*", "\\*")


def _plain_items(items: list[str]) -> list[str]:
    # Blank line before the list is required -- without it, Markdown treats
    # the "- item" lines as part of the preceding paragraph instead of a
    # separate list block, and they render as plain text with literal dashes.
    if not items:
        return []
    return [""] + [f"- {_escape_markdown(s)}" for s in items]


def _bullet_list(label: str, items: list[str]) -> list[str]:
    if not items:
        return []
    return ["", f"**{label}:**"] + _plain_items(items)


def render_markdown(rating: RepoRating) -> str:
    lines: list[str] = [f"# {_escape_markdown(rating.repo)}", ""] #2nd empty string is there to add a newline after title

    if rating.quality_score is not None:
        lines.append(f"**Quality score:** {rating.quality_score}/10")
    else:
        failed = []
        if rating.architecture.status == DimensionStatus.FAILED:
            failed.append("Architecture")
        if rating.results_functionality.status == DimensionStatus.FAILED:
            failed.append("Results/Functionality")
        reason = " and ".join(failed) if failed else "a required dimension"
        lines.append(f"**Quality score:** N/A ({reason} unavailable)")
    lines.append("")

    lines.append("## Popularity:")
    lines.append("")
    p = rating.popularity
    if p.status == DimensionStatus.FAILED:
        lines.append(f"**Not available** — {_escape_markdown(p.error or '')}")
    else:
        lines.append("_Informational only — does not affect Quality Score._")
        lines.append("")
        lines.append(f"- **Stars:** {p.stars:,}" if p.stars is not None else "- **Stars:** unknown")
        lines.append(f"- **Forks:** {p.forks:,}" if p.forks is not None else "- **Forks:** unknown")
        lines.append(f"- **Watchers:** {p.watchers:,}" if p.watchers is not None else "- **Watchers:** unknown")
        if p.has_releases:
            lines.append(f"- **Release downloads:** {p.release_downloads:,}")
        else:
            lines.append("- **Release downloads:** no releases published")
    lines.append("")

    lines.append("## Architecture:")
    lines.append("")
    a = rating.architecture
    if a.status == DimensionStatus.FAILED:
        lines.append(f"**Not available** — {_escape_markdown(a.error or '')}")
    else:
        lines.append(f"**Score:** {a.score}/10")
        lines.append("")
        lines.append(_escape_markdown(a.justification or ""))
        lines += _bullet_list("Strengths", a.strengths)
        lines += _bullet_list("Weaknesses", a.weaknesses)
    lines.append("")

    lines.append("## Results/Functionality:")
    lines.append("")
    r = rating.results_functionality
    if r.status == DimensionStatus.FAILED:
        lines.append(f"**Not available** — {_escape_markdown(r.error or '')}")
    else:
        lines.append(f"**Score:** {r.score}/10")
        lines.append("")
        lines.append(_escape_markdown(r.justification or ""))
        issue_lines = [f"[{issue.severity}] {issue.description}" for issue in r.issues_found]
        lines += _bullet_list("Issues found", issue_lines)
        lines += _bullet_list("Strengths", r.strengths)
        lines += _bullet_list("Weaknesses", r.weaknesses)
    lines.append("")

    lines.append("## Design/Docs:")
    lines.append("")
    d = rating.documentation
    if d.status == DimensionStatus.FAILED:
        lines.append(f"**Not available** — {_escape_markdown(d.error or '')}")
    else:
        if d.present:
            lines.append(f"**Documentation completeness:** {d.completeness}/10")
        else:
            lines.append("**Documentation present:** No")
        lines.append("")
        lines.append(_escape_markdown(d.justification or ""))
        lines += _bullet_list("Strengths", d.strengths)
        lines += _bullet_list("Weaknesses", d.weaknesses)
    lines.append("")

    if rating.strengths:
        lines.append("## Overall Strengths:")
        lines += _plain_items(rating.strengths)
        lines.append("")
    if rating.weaknesses:
        lines.append("## Overall Weaknesses:")
        lines += _plain_items(rating.weaknesses)
        lines.append("")

    lines.append("## Recommendations:")
    lines.append("")
    rec = rating.recommendations
    if rec.status == DimensionStatus.FAILED:
        lines.append(f"**Not available** — {_escape_markdown(rec.error or '')}")
    else:
        lines += _plain_items(rec.recommendations)
    lines.append("")

    return "\n".join(lines)


_SEVERITY_BADGE_PATTERN = re.compile(r"\[(critical|major|minor|inferred)\]")
_SEVERITY_BADGE_CLASSES = {
    "critical": "badge-critical",
    "major": "badge-major",
    "minor": "badge-minor",
    "inferred": "badge-inferred",
}

_DIMENSION_BADGE_PATTERN = re.compile(r"\[(Architecture|Results/Functionality|Design/Docs)\]")
_DIMENSION_BADGE_CLASSES = {
    "Architecture": "badge-dim-arch",
    "Results/Functionality": "badge-dim-results",
    "Design/Docs": "badge-dim-docs",
}

_NOT_AVAILABLE_PATTERN = re.compile(r"<p><strong>Not available</strong>.*?</p>", re.DOTALL)

# Wraps the Popularity section's own content (between its <h2> and the next
# one) in a distinct box -- keeps it visually separate from the 3 graded
# dimensions below it, since it's informational only and never scored.
_POPULARITY_SECTION_PATTERN = re.compile(
    r"<h2>Popularity:?</h2>\s*(.*?)(?=<h2>|\Z)", re.DOTALL
)


def _wrap_popularity_section(body_html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return f'<div class="popularity-box"><h3>Popularity</h3>{match.group(1)}</div>'

    return _POPULARITY_SECTION_PATTERN.sub(repl, body_html, count=1)

# The markdown source's "# repo" title and "**Quality score:** ..." line
# are still needed in the .md file, but they'd render a second time right
# below the styled header bar in the HTML/PDF, which already shows both --
# stripped from the HTML body only, never from the markdown source itself.
_TITLE_H1_PATTERN = re.compile(r"^<h1>.*?</h1>\s*", re.DOTALL)
_QUALITY_SCORE_LINE_PATTERN = re.compile(r"^<p><strong>Quality score:</strong>.*?</p>\s*", re.DOTALL)


def _strip_redundant_header_lines(body_html: str) -> str:
    body_html = _TITLE_H1_PATTERN.sub("", body_html, count=1)
    body_html = _QUALITY_SCORE_LINE_PATTERN.sub("", body_html, count=1)
    return body_html


def _apply_badges(body_html: str) -> str:
    """Post-process the already-markdown-derived HTML to turn recognizable
    plain-text patterns -- "[critical]", "[Architecture]", "Not available"
    paragraphs -- into styled badges/callouts. This transforms text that's
    already there, it doesn't add new content, so it doesn't reintroduce a
    second content template."""
    body_html = _SEVERITY_BADGE_PATTERN.sub(
        lambda m: f'<span class="badge {_SEVERITY_BADGE_CLASSES[m.group(1)]}">{m.group(1).upper()}</span>',
        body_html,
    )
    body_html = _DIMENSION_BADGE_PATTERN.sub(
        lambda m: f'<span class="badge {_DIMENSION_BADGE_CLASSES[m.group(1)]}">{m.group(1)}</span>',
        body_html,
    )
    body_html = _NOT_AVAILABLE_PATTERN.sub(
        lambda m: f'<div class="callout-fail">{m.group(0)}</div>', body_html
    )
    return body_html


def _quality_score_badge(quality_score: float | None) -> str:
    if quality_score is None:
        return '<span class="qs-badge qs-na">N/A</span>'
    if quality_score >= 8:
        css_class = "qs-good"
    elif quality_score >= 5:
        css_class = "qs-mid"
    else:
        css_class = "qs-low"
    return f'<span class="qs-badge {css_class}">{quality_score:.1f}/10</span>'


def _format_generated_at(generated_at: str) -> str:
    try:
        return datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").strftime("%B %d, %Y")
    except ValueError:
        return generated_at


def _logo_data_uri() -> str | None:
    """Base64-embeds the app icon directly into the HTML so the cover page
    doesn't depend on an external file existing at render time -- same
    reasoning web pages sometimes inline small images as data URIs.
    Returns None (cover page omits the logo entirely) if the asset is
    missing, rather than letting a missing decorative image break report
    generation -- same graceful-degradation spirit as CHROME_PATH being
    checked lazily in render_pdf() instead of at import time."""
    try:
        data = _LOGO_PATH.read_bytes()
    except FileNotFoundError:
        return None
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


_STYLE = """
<style>
:root {
  --navy: #16213e; --accent: #2c5aa0; --text: #1a1a1a; --text-muted: #55607a;
}
* { box-sizing: border-box; }

/* Page margins reserve room at the bottom for the footer/page-number
   margin box below; left/right/top stay at 0 since .report-body's own
   padding already provides that spacing -- avoids doubling up on both. */
@page {
  margin: 0 0 1.4cm 0;
  @bottom-center {
    content: "GitHub Repo-Rater  \\2022  Page " counter(page) " of " counter(pages);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 10px; font-weight: 500; letter-spacing: 0.01em; color: #6b7690;
  }
}
/* Cover page gets its own footer, not the base one -- the base footer's
   gray text would be unreadable against the navy background, so this
   repeats just the page number in a light color that reads on navy. */
@page cover {
  margin: 0 0 1.4cm 0;
  background: var(--navy);
  @bottom-center {
    content: "Page " counter(page) " of " counter(pages);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 10px; font-weight: 500; letter-spacing: 0.01em; color: #b7c4e0;
  }
}

html, body { height: 100%; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  margin: 0; color: var(--text); line-height: 1.55; background: #fff;
}
.cover-page {
  page: cover;
  break-after: page; page-break-after: always;
  background: var(--navy); color: #fff;
  /* 100% (of the page's own content box) instead of 100vh -- vh measures
     against the wrong box once the cover page has a nonzero margin
     (needed for its footer), leaving a gap of unstyled white space
     between this div and the actual page edge. */
  min-height: 100%; padding: 3.6em 3.4em 3em;
  display: flex; flex-direction: column; justify-content: space-between;
  position: relative; overflow: hidden;
}
/* A large, soft, off-canvas glow in the app's own brand gradient (teal to
   blue, matching the logo) -- fills what would otherwise be a big empty
   navy void with something intentional instead of more text. */
.cover-page::before {
  content: ""; position: absolute; top: -260px; right: -260px;
  width: 780px; height: 780px; border-radius: 50%; pointer-events: none;
  background: radial-gradient(circle, rgba(45,212,191,0.26) 0%, rgba(59,130,246,0.17) 42%, rgba(22,33,62,0) 70%);
}
/* Smaller, mirrored glow bottom-left -- bookends the page so both empty
   bands (above and below the hero text) get some visual weight instead
   of just one corner being treated. */
.cover-page::after {
  content: ""; position: absolute; bottom: -220px; left: -220px;
  width: 520px; height: 520px; border-radius: 50%; pointer-events: none;
  background: radial-gradient(circle, rgba(59,130,246,0.16) 0%, rgba(45,212,191,0.10) 45%, rgba(22,33,62,0) 72%);
}
.cover-header { display: flex; align-items: center; gap: 0.85em; position: relative; z-index: 1; }
.cover-logo { width: 46px; height: 46px; border-radius: 11px; display: block; }
.cover-eyebrow { font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.12em; color: #8fa0c7; margin: 0; }
.cover-hero { position: relative; z-index: 1; }
.cover-title { font-size: 2.75em; font-weight: 700; margin: 0 0 1em; word-break: break-word; max-width: 12em; line-height: 1.15; }
.cover-title-rule { width: 68px; height: 4px; border-radius: 2px; margin-bottom: 1.5em; background: linear-gradient(90deg, #2dd4bf, #3b82f6); }
.cover-score-block .qs-badge { font-size: 2.1em; padding: 0.4em 1.15em; }
.cover-footer-meta { position: relative; z-index: 1; }
.cover-generated { color: #d7deee; font-size: 1.05em; margin-bottom: 0.5em; }
.cover-methodology { color: #8fa0c7; font-size: 0.85em; max-width: 22em; }
.meta-label { font-size: 0.72em; text-transform: uppercase; letter-spacing: 0.06em; color: #8fa0c7; margin-bottom: 0.35em; }
.report-body { padding: 1.8em 2.2em 2.5em; max-width: 54em; }
h2 { color: var(--navy); border-bottom: 2px solid var(--accent); padding-bottom: 0.25em; margin-top: 1.9em; font-size: 1.22em; }
p { color: var(--text); }
li { margin-bottom: 0.75em; }
strong { color: var(--navy); }
.popularity-box {
  background: #f7f9fc; border: 1px solid #dbe3f0; border-radius: 10px;
  padding: 1.1em 1.4em; margin: 1.2em 0 1.6em;
}
.popularity-box h3 {
  margin: 0 0 0.6em; color: var(--text-muted); text-transform: uppercase;
  letter-spacing: 0.06em; font-size: 0.78em;
}
.popularity-box p { margin: 0 0 0.6em; color: var(--text-muted); font-style: italic; font-size: 0.9em; }
.popularity-box ul { margin: 0; padding-left: 1.3em; }
.popularity-box li { margin-bottom: 0.35em; }
.badge {
  display: inline-block; padding: 0.15em 0.6em; border-radius: 3px;
  font-size: 0.78em; font-weight: 700; letter-spacing: 0.02em; margin-right: 0.4em;
}
.badge-critical { background: #fee2e2; color: #b91c1c; }
.badge-major { background: #ffedd5; color: #c2410c; }
.badge-minor { background: #fef9c3; color: #a16207; }
.badge-inferred { background: #f1f5f9; color: #475569; }
.badge-dim-arch { background: #dbeafe; color: #1d4ed8; }
.badge-dim-results { background: #ede9fe; color: #6d28d9; }
.badge-dim-docs { background: #ccfbf1; color: #0f766e; }
.qs-badge { display: inline-block; padding: 0.25em 0.85em; border-radius: 6px; font-weight: 700; font-size: 1.25em; }
.qs-good { background: #dcfce7; color: #15803d; }
.qs-mid { background: #fef9c3; color: #a16207; }
.qs-low { background: #fee2e2; color: #b91c1c; }
.qs-na { background: #e6ebf5; color: #33415c; }
.callout-fail { background: #fdecea; border-left: 4px solid #dc2626; padding: 0.7em 1em; margin: 0.8em 0; border-radius: 3px; }
.callout-fail p { margin: 0; }
</style>
"""


def render_html(markdown_text: str, title: str, quality_score: float | None, generated_at: str) -> str:
    body = markdown_lib.markdown(markdown_text)
    body = _strip_redundant_header_lines(body)
    body = _wrap_popularity_section(body)
    body = _apply_badges(body)
    logo_uri = _logo_data_uri()
    logo_img = f'<img class="cover-logo" src="{logo_uri}" alt="" />' if logo_uri else ""
    cover_page = (
        '<div class="cover-page">'
        '<div class="cover-header">'
        f"{logo_img}"
        '<div class="cover-eyebrow">GitHub Repo-Rater</div>'
        "</div>"
        '<div class="cover-hero">'
        f"<h1 class=\"cover-title\">{title}</h1>"
        '<div class="cover-title-rule"></div>'
        '<div class="cover-score-block">'
        '<div class="meta-label">Quality Score</div>'
        f"<div>{_quality_score_badge(quality_score)}</div>"
        "</div>"
        "</div>"
        '<div class="cover-footer-meta">'
        f'<div class="cover-generated">Generated {_format_generated_at(generated_at)}</div>'
        '<div class="cover-methodology">Rated across Architecture, Results/Functionality, and Design/Docs</div>'
        "</div>"
        "</div>"
    )
    return (
        "<!DOCTYPE html>\n"
        f"<html><head><meta charset=\"utf-8\"><title>{title}</title>{_STYLE}</head>\n"
        f"<body>\n{cover_page}\n<div class=\"report-body\">\n{body}\n</div>\n</body></html>\n"
    )


def render_pdf(html_text: str, output_path: Path) -> None:
    """Convert an HTML string to a PDF at output_path via headless Chrome.

    Chrome's --print-to-pdf needs a real file to read from (not stdin), so
    html_text is written to a throwaway temp file first. That temp file is
    never placed in REPORTS_DIR -- it's not a persisted deliverable.
    """
    if not CHROME_PATH.exists():
        raise FileNotFoundError(
            f"Chrome not found at {CHROME_PATH} -- required to render PDF reports."
        )

    # Chrome resolves a relative --print-to-pdf path against its own install
    # directory, not this process's cwd -- and reports exit code 0 even when
    # that write fails. Must pass an absolute path, and still verify the
    # file actually landed rather than trusting the exit code alone.
    output_path = output_path.resolve()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(html_text)
        tmp_html_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [
                str(CHROME_PATH),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={output_path}",
                str(tmp_html_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"Chrome PDF conversion failed (exit {result.returncode}): {result.stderr}"
            )
    finally:
        tmp_html_path.unlink(missing_ok=True)


def generate_report(rating: RepoRating, tag: str | None = None) -> tuple[Path, Path, Path | None]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    base = _base_filename(rating, tag=tag)

    json_path = REPORTS_DIR / f"{base}.json"
    json_path.write_text(rating.model_dump_json(indent=2), encoding="utf-8")

    md_text = render_markdown(rating)
    md_path = REPORTS_DIR / f"{base}.md"
    md_path.write_text(md_text, encoding="utf-8")

    # JSON/MD are already safely on disk by this point -- a PDF failure
    # (Chrome missing/crashes) shouldn't take those down with it. The
    # website in particular needs to still serve the HTML/Markdown view
    # even when PDF generation fails.
    html_text = render_html(
        md_text, title=rating.repo, quality_score=rating.quality_score, generated_at=rating.generated_at
    )
    pdf_path: Path | None = REPORTS_DIR / f"{base}.pdf"
    try:
        render_pdf(html_text, pdf_path)
    except Exception as e:
        print(f"warning: PDF generation failed, continuing without it: {e}")
        pdf_path = None

    return json_path, md_path, pdf_path
