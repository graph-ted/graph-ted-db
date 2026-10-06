# Preview the docs site locally

From a clone of `graph-ted-db`:

```bash
python -m pip install -r requirements-docs.txt
mkdocs serve
```

Open **http://127.0.0.1:8000** in your browser. Edits under `docs/` reload automatically.

Build a static site (optional):

```bash
mkdocs build
# output in site/ — open site/index.html or serve that folder
```

This does **not** publish anything. GitHub Pages / graph-ted.com/db come later when you green-light public hosting.
