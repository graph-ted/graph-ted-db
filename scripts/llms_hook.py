"""MkDocs hook: write llms.txt and llms-full.txt into the built site.

Pages follow nav order. Body text is taken from the rendered HTML so
mkdocstrings output is included, not the ``:::`` directives. Nothing is
fetched at build time.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

_order: list[str] = []
_pages: list[dict[str, str]] = []
_rendered: dict[str, str] = {}


def on_pre_build(**_kwargs) -> None:
    _order.clear()
    _pages.clear()
    _rendered.clear()


def on_nav(nav, **_kwargs):
    _order.clear()
    for page in nav.pages:
        _order.append(page.file.src_path)
    return nav


def on_page_content(html: str, page, **_kwargs) -> str:
    _rendered[page.file.src_path] = html_to_markdown(html)
    return html


def on_post_page(output: str, page, config) -> str:
    site_url = (config.get("site_url") or "").rstrip("/")
    if getattr(page, "canonical_url", None):
        url = page.canonical_url
    elif site_url:
        url = site_url + "/" + page.url.lstrip("/")
    else:
        url = page.url
    _pages.append(
        {
            "src": page.file.src_path,
            "title": page.title or page.file.src_path,
            "url": url,
            "markdown": _rendered.get(page.file.src_path, ""),
        }
    )
    return output


class _PageText(HTMLParser):
    """Turn a rendered MkDocs page into headings, paragraphs, and code fences."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._skip_anchor = 0
        self._in_pre = False
        self._inline_code = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        classes = set(attr.get("class", "").split())
        if tag in {"script", "style", "svg"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "a" and "headerlink" in classes:
            self._skip_anchor += 1
            return
        if self._skip_anchor:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n" + ("#" * int(tag[1])) + " ")
        elif tag in {"p", "blockquote"}:
            self.parts.append("\n\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "pre":
            self._in_pre = True
            self.parts.append("\n\n```\n")
        elif tag == "code" and not self._in_pre:
            self._inline_code = True
            self.parts.append("`")
        elif tag == "tr":
            self.parts.append("\n")
        elif tag in {"th", "td"}:
            self.parts.append("| ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg"} and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "a" and self._skip_anchor:
            self._skip_anchor -= 1
            return
        if self._skip_anchor:
            return
        if tag == "pre" and self._in_pre:
            self.parts.append("\n```\n")
            self._in_pre = False
        elif tag == "code" and self._inline_code:
            self.parts.append("`")
            self._inline_code = False
        elif tag in {"th", "td"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip or self._skip_anchor:
            return
        self.parts.append(data)


def html_to_markdown(html: str) -> str:
    parser = _PageText()
    parser.feed(html)
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _ordered_pages() -> list[dict[str, str]]:
    by_src = {page["src"]: page for page in _pages}
    ordered: list[dict[str, str]] = []
    seen: set[str] = set()
    for src in _order:
        page = by_src.get(src)
        if page is None:
            continue
        ordered.append(page)
        seen.add(src)
    for page in _pages:
        if page["src"] not in seen:
            ordered.append(page)
    return ordered


def on_post_build(config) -> None:
    site_url = (config.get("site_url") or "").rstrip("/")
    index, full = render_llms(
        _ordered_pages(),
        site_name=config.get("site_name") or "graph-ted-db",
        site_description=config.get("site_description") or "",
        site_url=site_url,
    )
    site = Path(config["site_dir"])
    (site / "llms.txt").write_text(index, encoding="utf-8")
    (site / "llms-full.txt").write_text(full, encoding="utf-8")


def summary_of(markdown: str, limit: int = 180) -> str:
    """First prose paragraph, with markup reduced to plain text."""
    collected: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            if collected:
                break
            continue
        if in_fence:
            continue
        if not stripped:
            if collected:
                break
            continue
        if stripped.startswith(("#", ":::", "!!!", "<", "|")):
            if collected:
                break
            continue
        if ".md-button" in stripped:
            continue
        collected.append(stripped)
    text = " ".join(collected)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        trimmed = text[: limit - 3].rsplit(" ", 1)[0]
        text = trimmed + "..."
    return text


def render_llms(
    pages: list[dict[str, str]],
    *,
    site_name: str,
    site_description: str,
    site_url: str,
) -> tuple[str, str]:
    full_url = f"{site_url}/llms-full.txt" if site_url else "llms-full.txt"
    lines = [f"# {site_name}", ""]
    if site_description:
        lines.append(f"> {site_description}")
        lines.append("")
    lines.append("## Docs")
    lines.append("")
    for page in pages:
        blurb = summary_of(page.get("markdown") or "")
        title = page.get("title") or "Page"
        url = page.get("url") or ""
        if blurb:
            lines.append(f"- [{title}]({url}): {blurb}")
        else:
            lines.append(f"- [{title}]({url})")
    lines.extend(["", "## Full text", "", f"- [Complete documentation]({full_url})", ""])
    index = "\n".join(lines)

    parts = [f"# {site_name}", ""]
    if site_description:
        parts.extend([f"> {site_description}", ""])
    for page in pages:
        parts.append(f"# {page.get('title') or 'Page'}")
        parts.append("")
        parts.append(f"Source: {page.get('url') or ''}")
        parts.append("")
        parts.append((page.get("markdown") or "").strip())
        parts.append("")
    full = "\n".join(parts).rstrip() + "\n"
    return index, full
