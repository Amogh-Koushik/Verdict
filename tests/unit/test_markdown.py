import pytest

from verdict.web.markdown import render_markdown


def test_markdown_is_rendered_to_html() -> None:
    html = render_markdown("# Sum\n\nPrint **a + b**.\n\n```\n1 2\n```\n")
    assert "<h1>Sum</h1>" in html
    assert "<strong>a + b</strong>" in html
    assert "<pre><code>1 2\n</code></pre>" in html


@pytest.mark.parametrize(
    "raw_html", ["<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "<b>bold</b>"]
)
def test_raw_html_in_a_statement_is_shown_as_text(raw_html: str) -> None:
    html = render_markdown(f"Before {raw_html} after")
    assert raw_html not in html
    assert "&lt;" in html


def test_javascript_links_are_not_turned_into_links() -> None:
    html = render_markdown("[click](javascript:alert(1))")
    assert "<a " not in html


def test_ordinary_links_still_work() -> None:
    assert '<a href="https://example.com">site</a>' in render_markdown(
        "[site](https://example.com)"
    )
