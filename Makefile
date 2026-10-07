# Build local wheels for graph-ted-db (repo root) and the graphted meta package.
# Does not upload to PyPI or TestPyPI.
#
# Prerequisite: python -m pip install build

.PHONY: wheels docs docs-serve
wheels:
	python -m build --outdir dist
	python -m build --outdir dist packages/graphted

# Local MkDocs preview. Does not publish.
docs:
	python scripts/sync_tokens.py --check
	mkdocs build --strict

docs-serve:
	mkdocs serve
