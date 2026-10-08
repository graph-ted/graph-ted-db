# Brand

The canonical domain is [graph-ted.com](https://graph-ted.com). The docs
live at `https://graph-ted.com/db/`.

## Color

The app UI is the visual reference for the docs. Header, type, and link
colors below match the app navbar and its Chakra tokens.

| Role | Hex | Where it is used | Contrast |
| --- | --- | --- | --- |
| Brand teal | `#009999` | Wordmark, highlights, borders, large surfaces, dark-scheme links | 3.49:1 on white, so it is not small text on white. 4.61:1 on slate `#1e2129`. |
| Interactive | `#007A7A` | Light-scheme links, buttons, content focus, footer | 5.17:1 with white, in both directions. |
| Header, light | `#f4f4f5` | Header and tabs. Chakra `bg.muted` (`gray.100`) | Text `#09090B` is 18.10:1. |
| Header, dark | `#18181b` | Header and tabs. Chakra `bg.muted` (`gray.900`) | Text `#fafafa` is 16.97:1. |
| Header border, light | `#e4e4e7` | 1px bottom border. Chakra `border` (`gray.200`) | Separates the bar from the page. |
| Header border, dark | `#27272a` | 1px bottom border. Chakra `border` (`gray.800`) | Separates the bar from the page. |

The header is the app top bar: light gray `#f4f4f5` or zinc `#18181b`, with
that 1px border. Text, tabs, search, and icons use Chakra `fg`
(`#09090B` / `#fafafa`). The header logo is the full-color mark
`docs/assets/logo.svg` (teal strokes, white discs), at 72px in an 81px row.
`docs/assets/logo-light-stroke.svg` is still produced by the logo build for
other callers; the docs header does not use it.

## Wordmark

EB Garamond Regular 1.002, converted to outlined paths. The font binary
is not in the repo. SIL Open Font License 1.1 allows the outlines in the
SVG. Keep the EB Garamond name for the unmodified font, and do not sell
the font by itself.

Source: `https://github.com/octaviopardo/EBGaramond12` (`fonts/ttf/EBGaramond-Regular.ttf`).
Name-table version: `Version 1.002; ttfautohint (v1.8.4.16-eb64)`.

Regenerate the SVG, the light-stroke file, the favicons, and the README PNG:

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

## Docs theme

The app UI is the visual reference. The MkDocs stylesheet is generated from `docs/theme/tokens.json`:

```bash
python scripts/sync_tokens.py
python scripts/sync_tokens.py --check
```

Re-sync from the app repository (`frontend/` in `graph-ted/graph-ted`) is documented in `docs/theme/README.md`. The tokens were last synced from ref `7675086`: the font stack, Chakra's radii, spacing, and light/dark shadows (the app extends Chakra's `defaultConfig`), and the `react-icons` package. The header uses Chakra `bg.muted`, `fg`, and `border` from that same 3.26.0 theme. Light-scheme links stay `#007A7A`. Dark-scheme links are `#009999`, matching the app.

The header row is 81px tall, including the 1px bottom border, and the logo image is 72px tall, matching the app navbar (`py={1}` around the 72px mark). Logo SVG files and the favicon are unchanged. The header points at `docs/assets/logo.svg`. Spacing around that mark is set in CSS.

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
