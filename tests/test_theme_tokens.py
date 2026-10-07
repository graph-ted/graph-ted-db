"""Design-token sync: the stylesheet matches tokens.json, and --from does not invent values."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_tokens.py"
TOKENS = ROOT / "docs" / "theme" / "tokens.json"
CSS = ROOT / "docs" / "stylesheets" / "extra.css"


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_stylesheet_matches_tokens():
    result = _run("--check")
    assert result.returncode == 0, result.stderr


def test_committed_tokens_come_from_the_app():
    tokens = json.loads(TOKENS.read_text(encoding="utf-8"))
    assert tokens["source"]["status"] == "extracted"
    assert tokens["source"]["app_repo"] == "https://github.com/graph-ted/graph-ted"
    assert tokens["source"]["app_path"] == "frontend/"
    assert tokens["source"]["chakra_defaults"] is True
    assert tokens["radius"]["l2"] == "0.25rem"
    assert tokens["radius"]["md"] == "0.375rem"
    assert tokens["spacing"]["4"] == "1rem"
    assert set(tokens["shadow"]["md"]) == {"light", "dark"}
    assert tokens["icon_set"] == "react-icons"
    assert tokens["font"]["text"].startswith('"Inter Variable", Inter')
    assert tokens["color"]["light"]["link"].lower() == "#007a7a"
    assert tokens["color"]["dark"]["link"].lower() == "#33cccc"
    assert tokens["color"]["light"]["header"].lower() == "#007a7a"
    assert tokens["layout"]["navbar_height"] == "81px"
    assert tokens["layout"]["logo_height"] == "72px"
    assert tokens["layout"]["root_font_size"] == "16px"
    assert tokens["button"]["height"] == "2.5rem"


def test_app_rem_lengths_render_in_px_at_the_app_root_size():
    css = CSS.read_text(encoding="utf-8")
    # Material's root is 125%; 0.25rem there would be 5px instead of the app's 4px.
    assert "--gt-radius-l2: 4px;" in css
    assert "--gt-radius-md: 6px;" in css
    assert "min-height: 40px;" in css
    assert "padding: 0 16px;" in css


def test_committed_tokens_carry_no_local_paths():
    text = TOKENS.read_text(encoding="utf-8") + CSS.read_text(encoding="utf-8")
    assert str(Path.home()) not in text
    tokens = json.loads(TOKENS.read_text(encoding="utf-8"))
    assert "checkout" not in tokens["source"]

    def strings(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from strings(value)
        elif isinstance(node, list):
            for value in node:
                yield from strings(value)
        elif isinstance(node, str):
            yield node

    assert not [value for value in strings(tokens) if value.startswith("/")]


def test_stylesheet_uses_aa_colors_and_navbar_size():
    css = CSS.read_text(encoding="utf-8")
    assert "--md-typeset-a-color: #007A7A;" in css
    assert "--md-typeset-a-color: #33CCCC;" in css
    assert "--md-typeset-a-color: #009999;" not in css
    assert "height: 81px;" in css
    assert "height: 72px;" in css
    assert "http://" not in css
    assert "https://" not in css
    assert "fonts.googleapis.com" not in css


def test_stale_stylesheet_fails_check(tmp_path: Path):
    tokens = tmp_path / "tokens.json"
    css = tmp_path / "extra.css"
    tokens.write_text(TOKENS.read_text(encoding="utf-8"), encoding="utf-8")
    css.write_text("stale\n", encoding="utf-8")
    result = _run("--check", "--tokens", str(tokens), "--css", str(css))
    assert result.returncode == 1
    assert "stale" in result.stderr


def test_from_missing_checkout_does_not_invent(tmp_path: Path):
    before = TOKENS.read_text(encoding="utf-8")
    result = _run("--from", str(tmp_path))
    assert result.returncode == 2
    assert "invent" in result.stderr.lower() or "frontend" in result.stderr.lower()
    assert TOKENS.read_text(encoding="utf-8") == before


def test_from_extracts_named_tokens_and_keeps_contrast(tmp_path: Path):
    frontend = tmp_path / "frontend"
    theme = frontend / "src" / "theme"
    theme.mkdir(parents=True)
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@chakra-ui/react": "2.8.2",
                    "lucide-react": "0.460.0",
                    "@fontsource-variable/inter": "5.1.0",
                }
            }
        ),
        encoding="utf-8",
    )
    (theme / "tokens.ts").write_text(
        """
        export const tokens = {
          colors: {
            light: {
              header: "#009999",
              headerText: "#FFFFFF",
              link: "#009999",
              brand: "#009999",
              accent: "#009999",
            },
            dark: {
              header: "#007A7A",
              headerText: "#FFFFFF",
              link: "#007A7A",
              brand: "#009999",
              accent: "#007A7A",
              focus: "#007A7A",
            },
          },
          radii: { md: "10px" },
          space: { navbarHeight: "90px", logoHeight: "80px", gap: "12px" },
          shadows: { card: "0 1px 2px #00000014" },
          fonts: { body: "Inter Variable, sans-serif" },
        };
        """,
        encoding="utf-8",
    )
    out_tokens = tmp_path / "out-tokens.json"
    out_css = tmp_path / "out.css"
    out_tokens.write_text(TOKENS.read_text(encoding="utf-8"), encoding="utf-8")
    result = _run(
        "--from",
        str(tmp_path),
        "--tokens",
        str(out_tokens),
        "--css",
        str(out_css),
    )
    assert result.returncode == 0, result.stderr
    extracted = json.loads(out_tokens.read_text(encoding="utf-8"))
    assert extracted["source"]["status"] == "extracted"
    assert extracted["icon_set"] == "lucide"
    assert extracted["color"]["light"]["link"] == "#007A7A"
    assert extracted["color"]["light"]["header"] == "#007A7A"
    assert extracted["color"]["dark"]["link"] == "#33CCCC"
    assert extracted["layout"]["navbar_height"] == "90px"
    assert extracted["layout"]["logo_height"] == "80px"
    assert extracted["radius"]["md"] == "10px"
    assert extracted["spacing"]["gap"] == "12px"
    assert "card" in extracted["shadow"]
    assert "Inter Variable" in extracted["font"]["text"]
    css = out_css.read_text(encoding="utf-8")
    assert "--md-typeset-a-color: #009999;" not in css
    assert "height: 90px;" in css
    assert str(tmp_path) not in out_tokens.read_text(encoding="utf-8")
    assert str(tmp_path) not in css


def test_from_reads_chakra_token_shape_and_defaults(tmp_path: Path):
    frontend = tmp_path / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "package.json").write_text(
        json.dumps({"dependencies": {"@chakra-ui/react": "3.26.0", "react-icons": "5.5.0"}}),
        encoding="utf-8",
    )
    (frontend / "src" / "theme.tsx").write_text(
        """
        import { createSystem, defaultConfig } from "@chakra-ui/react"
        import { FiMail } from "react-icons/fi"
        export const system = createSystem(defaultConfig, {
          theme: {
            tokens: {
              fonts: { body: { value: '"Inter Variable", Inter, sans-serif' } },
              colors: { ui: { brand: { value: "#009999" } } },
              radii: { md: { value: "8px" } },
            },
          },
        })
        """,
        encoding="utf-8",
    )
    chakra = frontend / "node_modules" / "@chakra-ui" / "react"
    theme = chakra / "dist" / "esm" / "theme"
    (theme / "tokens").mkdir(parents=True)
    (theme / "semantic-tokens").mkdir(parents=True)
    (chakra / "package.json").write_text(json.dumps({"version": "3.26.0"}), encoding="utf-8")
    (theme / "tokens" / "colors.js").write_text(
        'const colors = defineTokens.colors({ gray: { 900: { value: "#18181b" } } });',
        encoding="utf-8",
    )
    (theme / "tokens" / "radius.js").write_text(
        'const radii = defineTokens.radii({ sm: { value: "0.25rem" }, md: { value: "0.375rem" } });',
        encoding="utf-8",
    )
    (theme / "semantic-tokens" / "radii.js").write_text(
        'const r = defineSemanticTokens.radii({ l2: { value: "{radii.sm}" } });',
        encoding="utf-8",
    )
    (theme / "tokens" / "spacing.js").write_text(
        'const spacing = defineTokens.spacing({ 0.5: { value: "0.125rem" }, 4: { value: "1rem" } });',
        encoding="utf-8",
    )
    (theme / "semantic-tokens" / "shadows.js").write_text(
        """const s = defineSemanticTokens.shadows({
          md: { value: { _light: "0px 4px 8px {colors.gray.900/10}", _dark: "0px 4px 8px {black/64}" } }
        });""",
        encoding="utf-8",
    )
    out_tokens = tmp_path / "out-tokens.json"
    out_css = tmp_path / "out.css"
    out_tokens.write_text(TOKENS.read_text(encoding="utf-8"), encoding="utf-8")
    result = _run("--from", str(tmp_path), "--tokens", str(out_tokens), "--css", str(out_css))
    assert result.returncode == 0, result.stderr
    extracted = json.loads(out_tokens.read_text(encoding="utf-8"))
    assert extracted["font"]["text"] == '"Inter Variable", Inter, sans-serif'
    assert extracted["radius"]["md"] == "8px"  # app override wins
    assert extracted["radius"]["l2"] == "0.25rem"
    assert extracted["spacing"] == {"0.5": "0.125rem", "4": "1rem"}
    assert extracted["shadow"]["md"] == {
        "light": "0px 4px 8px color-mix(in srgb, #18181b 10%, transparent)",
        "dark": "0px 4px 8px color-mix(in srgb, black 64%, transparent)",
    }
    assert extracted["icon_set"] == "react-icons"
    assert extracted["source"]["icon_families"] == ["fi"]
    css = out_css.read_text(encoding="utf-8")
    assert '--md-text-font: "Inter Variable", Inter, sans-serif;' in css
    assert "--gt-space-0-5: 2px;" in css  # root_font_size 16px from the committed tokens
    assert "--gt-shadow-md: 0px 4px 8px color-mix(in srgb, black 64%, transparent);" in css
    assert str(tmp_path) not in out_tokens.read_text(encoding="utf-8")
