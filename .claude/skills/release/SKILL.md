---
name: release
description: Bump the ComicsBot version consistently, verify, and open the bump PR
disable-model-invocation: true
argument-hint: <new-version e.g. 2.3.0>
---

Release flow: version lives in `comicsbot/__init__.py` (`__version__`) and must match
`pyproject.toml`. Merging to main does NOT publish; the user clicks Actions -> Release -> "Run workflow".

1. Version = `$ARGUMENTS`. If empty, ask. It must be valid semver (hyphen suffix = pre-release).
2. Check it isn't already released or in flight:
   - `git ls-remote --tags origin "v<version>"` must be empty
   - `gh pr list --state all --search "Bump version to <version>"` must show nothing open/merged
   If either hits, stop and report (duplicate bump PRs #14/#16 happened before).
3. Branch `release/<version>` from up-to-date main.
4. Set the version in BOTH `comicsbot/__init__.py` and `pyproject.toml`.
5. Run `ruff check .` and `pytest` (tests/test_release.py enforces the two versions match). Stop on failure.
6. Commit "Bump version to <version>", push, `gh pr create`. Never enable auto-merge.
7. Tell the user: after merge, run the Release workflow on main to tag `v<version>` and publish.
