from markdown_it import MarkdownIt
from markupsafe import Markup

# Raw HTML in a statement is shown as text; markdown-it also refuses javascript: and similar links.
_renderer = MarkdownIt("commonmark", {"html": False})


def render_markdown(source: str) -> Markup:
    return Markup(_renderer.render(source))  # noqa: S704  (html is disabled above)
