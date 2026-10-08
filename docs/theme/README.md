# Docs design tokens

`tokens.json` is the source for `docs/stylesheets/extra.css`. The stylesheet is generated. Edit the JSON, then regenerate:

```bash
python scripts/sync_tokens.py
python scripts/sync_tokens.py --check
```

`--check` exits non-zero when `extra.css` does not match `tokens.json`. CI runs that command.

`theme.font` stays `false`. Inter and JetBrains Mono stay the self-hosted files under `docs/assets/fonts/`. The generator does not add webfonts or remote URLs.

## Re-sync from the app

The app UI is the `frontend/` tree in the graph-ted repository. Clone it read-only. Do not commit changes into that repository.

- Repository: https://github.com/graph-ted/graph-ted
- Git ref: `7675086` (`main` when `tokens.json` was last synced; the script records the checkout's commit in `source.app_git_ref`)
- Path: `frontend/`

```bash
git clone https://github.com/graph-ted/graph-ted.git graph-ted-src
git -C graph-ted-src checkout 7675086
(cd graph-ted-src/frontend && npm ci)   # radii, spacing, shadows come from the installed Chakra package
python scripts/sync_tokens.py --from graph-ted-src
python scripts/sync_tokens.py --check
```

`--from` reads `frontend/package.json` and theme or token files under `frontend/` (CSS variables and object literals in `.css`, `.scss`, `.ts`, `.tsx`, `.js`, `.jsx`, and `.json`). Chakra v3 token objects (`name: { value: X }`, with `_light` and `_dark`) count as the named token. It copies a value only when the key path names a token this stylesheet understands: colors (light and dark), radii, spacing, font stacks, shadows, focus, and navbar or logo lengths. It records the icon package when `package.json` depends on one, and for `react-icons` the imported families. It does not invent missing groups, and it does not download icon SVGs.

When the app theme extends Chakra's `defaultConfig`, radii, spacing, and shadows the app does not override come from the Chakra package installed in `frontend/node_modules` (`dist/esm/theme/tokens` and `semantic-tokens`). Color references in shadows resolve the way Chakra emits them, as `color-mix(...)`. App values win over those defaults. Without `node_modules`, the groups stay as they are.

The checkout path is never written to `tokens.json` or `extra.css`.

Contrast overrides from `BRANDING.md` still apply after a sync: `#009999` is not used as light-mode link text (it is rewritten to `#007A7A`). A dark link of `#007A7A` is rewritten to `#009999`, which is 4.61:1 on slate and matches the app. An extracted header of `#009999` is still rewritten to `#007A7A`, because white text on that teal fails. The committed header is not that case: it is Chakra `bg.muted`.

If the checkout cannot be read, `--from` exits non-zero and leaves `tokens.json` unchanged.

Current state: synced from `graph-ted` `7675086` with Chakra UI 3.26.0. The app UI is the visual reference for the docs. The header is the app navbar: `bg.muted` (`#f4f4f5` light, `#18181b` dark), `fg` text (`#09090B` / `#fafafa`), and a 1px `border` (`#e4e4e7` / `#27272a`), with the full-color teal mark at 72px in an 81px row. Dark-scheme links are `#009999`. Light-scheme links stay `#007A7A`. The app extends `defaultConfig`, so `radius`, `spacing`, and `shadow` are Chakra's defaults. Buttons and the search field use the `l2` control radius (4px, as on app buttons and inputs); code blocks and admonitions use `md` (6px, as on app panels). `icon_set` is `react-icons` (mostly Feather, `fi`). Material's built-in icons stay, because no SVGs are vendored. To switch, self-host the SVGs under `overrides/.icons/` and point `theme.icon` at them. Do not load icons from a CDN.

`--from` accepts `--dry-run` to print the mapped values without writing. `--tokens` and `--css` override the output paths (used by tests).
