# Releasing graph-ted-db

Maintainer runbook. Not part of the published docs site.

Publishing uses PyPI trusted publishing from `.github/workflows/release.yml`
(environments `pypi` and `testpypi`). No API tokens exist anywhere. Only the
release workflow can upload.

## 0. Launch step: restore full CI on pull requests

While the repository is private, pull requests run a trimmed CI to save Actions
minutes: Linux on Python 3.10 and 3.13 (the 3.13 job is the full gate: tests,
PII scan, docs, package build) plus one wheel install. Push to `main`, `v*`
tags and manual runs (Actions → CI → Run workflow) always run the full matrix:
tests on Linux 3.10–3.14, macOS and Windows, and the 30 wheel/sdist install jobs.

Right after the repository is made public:

1. In `.github/workflows/ci.yml`, set `FULL_MATRIX_ON_PULL_REQUESTS: "true"`
   (the `env:` block at the top). That is the only switch.
2. Merge that one-line PR and check that its own run shows the full matrix.

Until then, check the full-matrix run on `main` after each merge, and run it
manually on a branch before merging anything platform-specific (paths, locks,
file attributes).

## 1. Cut a release

1. On `main`, with the full CI green (tests, install matrix, PII scan, docs build;
   the push-to-`main` run, not the trimmed PR run):
   - Set `version` in `pyproject.toml`.
   - Move the `Unreleased` notes in `CHANGELOG.md` under the new version and date.
     Call out breaking changes under **Changed**, and any on-disk format change
     with its migration step.
2. Merge that PR, then note the merge SHA.
3. Dry run on TestPyPI: Actions → `release` → Run workflow → target `testpypi`.
   Then in a clean venv:
   ```sh
   python -m venv /tmp/rc && /tmp/rc/bin/pip install \
     --index-url https://test.pypi.org/simple/ graph-ted-db==X.Y.Z
   /tmp/rc/bin/graph-ted-db --version
   ```
   Run the README quick start. A version number can only be uploaded once per
   index; if the TestPyPI build is wrong, fix it and bump to `X.Y.Z` with a
   new pre-release suffix (for example `0.1.0rc2`) for TestPyPI only.
4. Create a GitHub release with tag `vX.Y.Z` on the merge SHA and paste the changelog
   section as notes. Publishing the release runs the `pypi` job; the workflow
   refuses to run if the tag and `pyproject.toml` disagree.
5. Verify: `pip install graph-ted-db==X.Y.Z` in a clean venv, check the PyPI page renders,
   and check the docs site shows the new version.

## 2. Yank a bad release

Yank when a release loses or corrupts data, fails to install, or ships something
that must not be public. Yanking hides the version from `pip install graph-ted-db`
but keeps it installable when pinned exactly, so existing lockfiles do not break.

1. PyPI → Manage project → Releases → `X.Y.Z` → Options → Yank. Give a short reason
   (it is shown to users), for example "data loss on Windows when …; use X.Y.Z+1".
2. Edit the GitHub release: prefix the title with `[YANKED]` and put the reason
   and the fixed version at the top.
3. Add a `Yanked` note to that version in `CHANGELOG.md`.
4. If the release exposed secrets or personal data: yanking does not remove the files.
   Delete the release files on PyPI (Manage → Options → Delete; that version
   number can never be reused), rotate whatever leaked, and follow `SECURITY.md`.

Never delete a release just to re-upload the same version. PyPI does not allow it.

## 3. Hotfix (for example 0.1.1)

1. Branch from the release tag: `git switch -c hotfix/0.1.1 v0.1.0`.
2. Fix with a regression test. Keep the change minimal: no features, no format changes.
3. Bump to `0.1.1`, add a `CHANGELOG.md` entry, open a PR to `main` (or cherry-pick
   onto `main` if it has moved on), and wait for CI to pass.
4. Release as in section 1 (the TestPyPI dry run may be skipped for a one-line fix,
   but the install matrix must pass).
5. If 0.1.0 is unsafe, yank it after 0.1.1 is live (section 2).

## 4. Roll back the docs and the site

The docs site is built from `docs/` on `main` by the site repo's Pages workflow.

- **Bad docs content:** revert the commit on `main` (`git revert <sha>`), merge it, and
  let Pages redeploy. Do not force-push `main`.
- **Bad deploy, good source:** in the site repo, Actions → the Pages workflow → re-run the
  last good run, or revert the site commit and push.
- **Site down or DNS wrong:** check GitHub Pages settings (custom domain
  `graph-ted.com`, HTTPS enforced) and the DNS records at the registrar. While it is
  fixed, the PyPI page and README still work, because they use absolute URLs and
  the repo itself carries the docs.
- **Docs ahead of the released package:** label the section "Unreleased" or revert it
  until the release ships.

## 5. First-week Issues triage

For the first 7 days after a release, check Issues and Discussions at least once a day.

| Kind | Label | Target response | Action |
|------|-------|-----------------|--------|
| Data loss, corruption, store will not open | `bug`, `data-loss` | same day | Reproduce, ask for `graph-ted-db doctor <path>` output (no `--fix`), and consider a hotfix and a yank |
| Install failure (OS or Python version) | `bug`, `packaging` | 1 day | Reproduce in the install-matrix job and add the missing case to CI |
| Security report in a public issue | — | immediately | Hide or lock the issue, move it to a private advisory per `SECURITY.md`, and thank the reporter |
| Wrong or confusing docs | `docs` | 2 days | Fix in a PR; link the issue |
| Unsupported Cypher | `cypher`, `enhancement` | 3 days | Confirm it is outside the documented subset; label it and record it for the roadmap |
| Feature request | `enhancement` | 3 days | Thank the reporter, label it, and don't promise a version |
| Question | — | 2 days | Answer it, or convert it to a Discussion |

At the end of the week, write a short summary (counts, top themes, anything for
0.1.1) and close or label every issue.
