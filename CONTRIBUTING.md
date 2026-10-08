# Contributing to graph-ted-db

Thanks for your interest in improving graph-ted-db.

## Where things go

- **Bugs, enhancement requests, docs problems:** open an issue with one of the [issue forms](https://github.com/graph-ted/graph-ted-db/issues/new/choose).
- **Questions and ideas:** use [Discussions](https://github.com/graph-ted/graph-ted-db/discussions).
- **Security vulnerabilities:** report them privately as described in [SECURITY.md](SECURITY.md). Never in a public issue.

## Development setup

```bash
pip install -e ".[dev]"
pytest
```

Docs (MkDocs) are previewed as described in [PREVIEW.md](PREVIEW.md). Before opening a pull request, run what CI runs:

```bash
pytest
python scripts/sync_tokens.py --check
pip install -r requirements-docs.txt
mkdocs build --strict
```

## Pull requests

- Branch from `main` and open the pull request against `main`.
- Keep each pull request focused on one change, with tests for behavior changes.
- Update the docs and README when user-facing behavior changes.
- Link the issue the pull request addresses.

Contributions are accepted under the project's [MIT license](LICENSE).
