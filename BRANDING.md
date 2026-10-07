# Brand

The canonical domain is [graph-ted.com](https://graph-ted.com). The docs
live at `https://graph-ted.com/db/`.

## Color

| Role | Hex | Where it is used | Contrast |
| --- | --- | --- | --- |
| Brand teal | `#009999` | Wordmark, highlights, borders, large surfaces | 3.49:1 on white. Too low for small text. |
| Interactive | `#007A7A` | Header bar, light-scheme links, buttons, focus | 5.17:1 with white, in both directions. |
| Dark-scheme links | `#33CCCC` | Links and content focus on slate `#1e2129` | 8.16:1 on that background. `#007A7A` on slate is 3.12:1, so the dark scheme uses the lighter teal for text. |

The header is `#007A7A` because white type on `#009999` is 3.49:1. The
header logo is `docs/assets/logo-light-stroke.svg`: white circle strokes
and connectors on that bar. `#009999` strokes on `#007A7A` are 1.48:1.

## Wordmark

EB Garamond Regular 1.002, converted to outlined paths. The font binary
is not in the repo. SIL Open Font License 1.1 allows the outlines in the
SVG. Keep the EB Garamond name for the unmodified font, and do not sell
the font by itself.

Source: `https://github.com/octaviopardo/EBGaramond12` (`fonts/ttf/EBGaramond-Regular.ttf`).
Name-table version: `Version 1.002; ttfautohint (v1.8.4.16-eb64)`.

Regenerate the SVG, the light-stroke header file, the favicons, and the
README PNG:

```bash
curl -fsSL -o /tmp/EBGaramond-Regular.ttf \
  -L https://github.com/octaviopardo/EBGaramond12/raw/master/fonts/ttf/EBGaramond-Regular.ttf
python scripts/build_logo_svg.py --font /tmp/EBGaramond-Regular.ttf
```

Per-word `scale`, `letter_spacing`, `dx`, `dy`, and `stroke` live in
`scripts/logo_params.json`. Circles, connectors, and the viewBox stay
fixed. To try another font, edit those values and rerun:

```bash
python scripts/build_logo_svg.py --font /path/to/Other.ttf \
  --compare uploads/graph-ted-db_logo-color-v2.png
```

`--compare` renders at the PNG master's 340×116 and prints bbox deltas.
Pass `--out` to write somewhere other than `docs/assets`.

At a 48px-tall header the letter stems measure about 1.25px (graph, ted,
and db). The 32px favicon stem is about 1.1px, and the 16px favicon stem
is about 0.6px. The letters are thin at those sizes. The wordmark stays
EB Garamond.

## Docs text

Inter, self-hosted, so the site does not call Google Fonts.

- Release: [rsms/inter v4.1](https://github.com/rsms/inter/releases/tag/v4.1)
- Name-table version: `4.001;git-9221beed3`
- Files: `docs/assets/fonts/InterVariable.woff2` and `InterVariable-Italic.woff2` (weight axis 100–900, optical size 14–32)
- License: `docs/assets/fonts/Inter-OFL.txt` (SIL Open Font License 1.1)

`mkdocs.yml` sets `theme.font: false`. `docs/stylesheets/extra.css`
declares the `@font-face` rules and sets `--md-text-font: "Inter"`.

`theme.font: false` plus the committed woff2 files is the setup that
keeps `mkdocs build --strict` offline. Material's privacy plugin
downloads `fonts.googleapis.com` during the build and caches the result,
so the build still depends on Google.

## Code

JetBrains Mono 2.304, self-hosted the same way. `theme.font: false`
removes Roboto Mono along with Roboto, and one more OFL family is the
same `@font-face` pattern.

- Release: [JetBrainsMono v2.304](https://github.com/JetBrains/JetBrainsMono/releases/tag/v2.304)
- Files: `JetBrainsMono-Regular.woff2`, `JetBrainsMono-Italic.woff2`, `JetBrainsMono-Bold.woff2`, `JetBrainsMono-BoldItalic.woff2` (the 400 and 700 weights Material used to request, with italics)
- License: `docs/assets/fonts/JetBrainsMono-OFL.txt`

`--md-code-font` is `"JetBrains Mono"`.
