"""The scraper used to hand the model markup and call it content.

Measured on 2026-08-06 against `pitch.digitalaf.xyz`: 6000 characters delivered
to the agent carried 306 characters of readable text, and the page's own dates
sat at raw offset ~18,300 — past the caller's cut. These tests pin the
properties that failure violated, not the exact wording of any output.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import (  # noqa: E402
    BROWSER_HEADERS,
    MAX_BYTES,
    MAX_RAW_BYTES,
    MIN_MAIN_CHARS,
    extract_text,
)


def test_script_and_style_bodies_never_reach_the_output():
    """The defect in one assertion: 95% of the payload was this."""
    html = """
    <html><head><style>body{background:#eee;font-family:sans-serif}</style>
    <script>var t=localStorage.getItem('theme');console.log(t);</script></head>
    <body><p>The event is in Seoul.</p></body></html>
    """
    out = extract_text(html, "text/html")
    assert "Seoul" in out
    assert "localStorage" not in out
    assert "background" not in out
    assert "sans-serif" not in out


def test_text_far_down_a_long_document_survives():
    """The regression that made the agent unable to see the dates.

    The fact sits behind ~40KB of markup — beyond any caller-side character cut
    applied to the raw body, but near the front once the markup is gone.
    """
    filler = "<div><script>" + ("x" * 40_000) + "</script></div>"
    html = f"<html><body><h1>Pitch</h1>{filler}<p>September 29 to October 02</p></body></html>"

    out = extract_text(html, "text/html")

    assert "September 29 to October 02" in out
    # The point is not merely that it survives — it must be reachable inside a
    # caller budget of a few thousand characters.
    assert out.index("September") < 2_000


def test_entities_are_unescaped():
    out = extract_text("<html><body><p>Art &amp; Gaming &mdash; 2026</p></body></html>", "text/html")
    assert "Art & Gaming" in out
    assert "&amp;" not in out


def test_block_tags_separate_words_instead_of_gluing_them():
    html = "<html><body><li>Register here</li><li>Venue</li></body></html>"
    out = extract_text(html, "text/html")
    assert "hereVenue" not in out
    assert "Register here" in out
    assert "Venue" in out


def test_self_closing_break_separates_lines():
    out = extract_text("<html><body>Seoul<br/>2026</body></html>", "text/html")
    assert "Seoul2026" not in out


def test_title_is_kept():
    """<title> lives inside <head>, which is dropped wholesale."""
    html = "<html><head><title>DIGITAL AF Seoul 2026</title></head><body><p>Body</p></body></html>"
    out = extract_text(html, "text/html")
    assert "DIGITAL AF Seoul 2026" in out


def test_title_is_not_prepended_when_the_body_already_opens_with_it():
    """Otherwise every page whose H1 matches its title opens with a stutter."""
    html = "<html><head><title>Seoul</title></head><body><p>Seoul is the venue</p></body></html>"
    out = extract_text(html, "text/html")
    assert out == "Seoul is the venue"


def test_whitespace_is_collapsed_but_paragraphs_are_kept():
    html = "<html><body><p>One</p>\n\n\n<p>Two</p></body></html>"
    out = extract_text(html, "text/html")
    assert "\n\n\n" not in out
    assert "One" in out and "Two" in out


def test_non_html_bodies_are_returned_untouched():
    """A JSON API response must survive verbatim — parsing it would mangle it."""
    payload = '{"event": "DIGITAL AF", "dates": ["2026-09-29", "2026-10-02"]}'
    assert extract_text(payload, "application/json") == payload

    plain = "line one\nline two"
    assert extract_text(plain, "text/plain") == plain


def test_html_is_detected_without_a_content_type_header():
    html = "<!doctype html><html><body><p>Seoul</p></body></html>"
    out = extract_text(html, "")
    assert out.strip() == "Seoul"


def test_malformed_html_does_not_raise():
    for broken in (
        "<html><body><p>Unclosed",
        "</div></div><p>Stray closes</p>",
        "<html><body><script>if (a < b) { }</script><p>After</p></body></html>",
        "<<<>>><html><body>Weird</body>",
    ):
        assert isinstance(extract_text(broken, "text/html"), str)


def test_stray_close_tag_does_not_swallow_the_document():
    """A negative skip counter would have hidden everything after it."""
    html = "<html><body></script><p>Still visible</p></body></html>"
    assert "Still visible" in extract_text(html, "text/html")


def test_client_rendered_page_falls_back_to_the_raw_body():
    """Empty text is worth less to the model than imperfect markup."""
    html = '<html><head><script src="/app.js"></script></head><body><div id="root"></div></body></html>'
    out = extract_text(html, "text/html")
    assert out == html


def test_a_browser_user_agent_is_sent():
    """httpx's default announces python-httpx, which Wikipedia answers with 403."""
    ua = BROWSER_HEADERS["User-Agent"]
    assert "python-httpx" not in ua.lower()
    assert "Mozilla/5.0" in ua


def test_byte_cap_is_unchanged():
    assert MAX_BYTES == 50_000


@pytest.mark.parametrize("tag", ["noscript", "svg", "template", "iframe", "canvas"])
def test_other_non_content_elements_are_dropped(tag):
    html = f"<html><body><{tag}>NOISE</{tag}><p>Signal</p></body></html>"
    out = extract_text(html, "text/html")
    assert "NOISE" not in out
    assert "Signal" in out


# --- content selection -------------------------------------------------------
#
# Truncating from the front hands the caller whatever the site put first. On a
# large site that is navigation: Wikipedia's prose begins ~50,000 characters
# into its own text. These pin the two rules that move content forward.


def test_navigation_is_dropped_so_content_comes_first():
    html = """
    <html><body>
      <nav><a>Home</a><a>Products</a><a>Careers</a><a>268 languages</a></nav>
      <aside><a>Related links</a></aside>
      <p>The venue is in Seoul.</p>
    </body></html>
    """
    out = extract_text(html, "text/html")
    assert "Careers" not in out
    assert "Related links" not in out
    assert out.strip().startswith("The venue is in Seoul.")


def test_footer_is_kept_because_it_carries_facts():
    """Event pages routinely put the dates and the address in the footer."""
    html = (
        "<html><body><p>Pitch</p>"
        "<footer>September 29 to October 02 - Seoul</footer></body></html>"
    )
    out = extract_text(html, "text/html")
    assert "September 29 to October 02" in out


def test_main_subtree_wins_over_surrounding_chrome():
    chrome = "<div>" + ("Sponsor listing. " * 40) + "</div>"
    html = (
        f"<html><body>{chrome}"
        f"<main><p>{'The article body. ' * 20}</p></main>"
        f"{chrome}</body></html>"
    )
    out = extract_text(html, "text/html")
    assert "The article body." in out
    assert "Sponsor listing." not in out


def test_article_subtree_is_selected_too():
    html = (
        "<html><body><div>Chrome</div>"
        f"<article><p>{'Real content. ' * 30}</p></article></body></html>"
    )
    out = extract_text(html, "text/html")
    assert "Chrome" not in out
    assert "Real content." in out


def test_a_decorative_main_does_not_hide_the_page():
    """A tiny <main> is a layout wrapper, not the content — keep everything."""
    body_text = "The whole page body that actually carries the information. " * 5
    html = f"<html><body><p>{body_text}</p><main>Skip</main></body></html>"
    out = extract_text(html, "text/html")
    assert "actually carries the information" in out
    assert len(out) > MIN_MAIN_CHARS


def test_nested_main_does_not_lose_the_tail():
    html = (
        "<html><body><main><div>"
        f"<article><p>{'Inner. ' * 40}</p></article>"
        f"</div><p>{'Tail of main. ' * 10}</p></main></body></html>"
    )
    out = extract_text(html, "text/html")
    assert "Inner." in out
    assert "Tail of main." in out


def test_the_two_caps_are_distinct():
    """Capping the input at the output size truncates big pages before content."""
    assert MAX_RAW_BYTES > MAX_BYTES
    assert MAX_BYTES == 50_000
