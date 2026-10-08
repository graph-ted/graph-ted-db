# Preview the docs site locally

From a clone of `graph-ted-db`:

```bash
python -m pip install -r requirements-docs.txt
mkdocs serve
```

Open **http://127.0.0.1:8000** in your browser. Edits under `docs/` reload automatically.

Build a static site (optional):

```bash
python scripts/sync_tokens.py --check
mkdocs build --strict
# output in site/ — open site/index.html or serve that folder
```

`site/llms.txt` and `site/llms-full.txt` are written by the build. Design tokens live in `docs/theme/tokens.json`; see `docs/theme/README.md` to regenerate the stylesheet or re-sync from the app checkout.

To serve the preview under a different path than the published one, override `site_url` for that run only:

```bash
DOCS_SITE_URL=http://127.0.0.1:8002/db/ mkdocs serve -a 127.0.0.1:8002
```

## Where the docs are published

The docs are published at `https://graph-ted.com/graph-ted-db/docs/` by the build in the `graph-ted/graph-ted.github.io` repository, which pulls a pinned commit of this repo and builds it into that path. GitHub Pages stays **off** in this repo: if it were turned on, GitHub would serve this repo at `/graph-ted-db/` and collide with the org site's graph-ted-db landing page.

This does **not** publish anything. Public hosting comes later, when it is green-lit.
