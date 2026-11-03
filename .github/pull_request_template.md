Requirement: REQ-xxx / BUG-xxx
<!-- Required: the REQ ID from REQUIREMENTS.md (graph-ted-orchestrator) this PR builds, or BUG-<n> for a fix. The req-id check fails without one, or if the REQ ID is not in .github/req-ids.txt. -->

## Summary

<!-- What does this change and why? -->

Closes #

## Checklist

- [ ] `pytest` passes
- [ ] `python scripts/sync_tokens.py --check` passes
- [ ] `mkdocs build --strict` passes (if docs changed)
- [ ] Tests added or updated for behavior changes
- [ ] Docs updated for this change (user docs, CHANGELOG, upgrade notes if needed) before the version bump.
- [ ] No secrets, personal data, or local file paths in code, tests, or fixtures
