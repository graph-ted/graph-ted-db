"""llms.txt rendering stays tied to the page list passed in."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _hook():
    path = ROOT / "scripts" / "llms_hook.py"
    spec = importlib.util.spec_from_file_location("llms_hook", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_render_llms_lists_pages_and_full_text():
    hook = _hook()
    pages = [
        {
            "title": "Home",
            "url": "https://graph-ted.com/db/",
            "markdown": "# graph-ted-db\n\n**Local property-graph storage for Python.**\n",
        },
        {
            "title": "API reference",
            "url": "https://graph-ted.com/db/api/",
            "markdown": "Public names exported by `graph_ted_db`.\n\n## GraphStore\n",
        },
    ]
    index, full = hook.render_llms(
        pages,
        site_name="graph-ted-db",
        site_description="Local property-graph storage for Python",
        site_url="https://graph-ted.com/db",
    )
    assert "https://graph-ted.com/db/llms-full.txt" in index
    assert "[Home](https://graph-ted.com/db/)" in index
    assert "[API reference](https://graph-ted.com/db/api/)" in index
    assert "graphted.com" not in index
    assert "graphted.com" not in full
    assert "# API reference" in full
    assert "graph_ted_db" in full
    assert "sqlite" not in index.lower()
    assert "sqlite" not in full.lower()
    fenced = hook.summary_of("# Title\n\n```\npip install example\n```\n\nInstall from a checkout.\n")
    assert fenced == "Install from a checkout."


def test_rendered_html_becomes_markdown_without_permalink_glyph():
    hook = _hook()
    html = """
    <h1 id="api-reference">API reference<a class="headerlink" href="#api-reference" title="Anchor">&para;</a></h1>
    <p>Public names exported by <code>graph_ted_db</code>.</p>
    <div class="highlight"><pre><span></span><code>from graph_ted_db import GraphStore
</code></pre></div>
    """
    text = hook.html_to_markdown(html)
    assert "# API reference" in text
    assert "¶" not in text
    assert "`graph_ted_db`" in text
    assert "from graph_ted_db import GraphStore" in text
    assert "```" in text
