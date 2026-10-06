# Build local wheels for graph-ted-db (repo root) and the graphted meta package.
# Does not upload to PyPI or TestPyPI.
#
# Prerequisite: python -m pip install build

.PHONY: wheels
wheels:
	python -m build --outdir dist
	python -m build --outdir dist packages/graphted
