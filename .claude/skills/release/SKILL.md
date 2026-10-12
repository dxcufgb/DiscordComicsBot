---
name: release
description: Publish a ComicsBot release by running the Release workflow with a version
disable-model-invocation: true
argument-hint: <version e.g. 2.4.0>
---

Releasing: **Actions → Release → Run workflow → type the version.** Fallback: publish a release `vX.Y.Z` on
GitHub, files are attached afterwards. No version bump in the code: the workflow stamps the version from the tag
into the built files only; never edit `__version__` or `pyproject.toml` for a release.

1. Version = `$ARGUMENTS` (a leading `v` is dropped). If empty, ask. It must match
   `^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$`; a hyphen suffix makes a pre-release.
2. Check it isn't already released or in flight:
   - `git ls-remote --tags origin "v<version>"` must be empty
   - `gh release view "v<version>"` must fail, and `gh run list --workflow release.yml --limit 5` must show no
     Release run in progress
   If any hits, stop and report.
3. Make sure `main` has what should ship and its CI is green (`gh run list --branch main --limit 5`).
4. Ask the user to confirm, then start it: `gh workflow run release.yml --ref main -f version=<version>`
   (or tell the user: Actions → Release → Run workflow → type `<version>`).
5. Report the run URL. The workflow runs the full CI, builds every file, creates the tag `v<version>` and publishes
   the release (draft first, published once all files are attached). Nothing is published if any job fails.
