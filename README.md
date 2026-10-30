# DiscordComicsBot

A Discord bot that watches RSS/Atom feeds of web comics and posts new strips to a channel.

## Installation

You need a Discord application with a bot token first: see [Discord Setup](https://github.com/dxcufgb/DiscordComicsBot/wiki/Discord-Setup).

Linux (any distribution with systemd):

```sh
curl -fsSL https://raw.githubusercontent.com/dxcufgb/install/main/install.sh | sudo bash -s comicsbot
```

Windows: run `comicsbot-setup.exe` from the [Releases](../../releases) page.

Installer options, upgrades and running from source: see [Installation](https://github.com/dxcufgb/DiscordComicsBot/wiki/Installation) in the wiki.

## Documentation

The full guide is in the [wiki](https://github.com/dxcufgb/DiscordComicsBot/wiki):

- [Discord Setup](https://github.com/dxcufgb/DiscordComicsBot/wiki/Discord-Setup): the application, the bot token and the invite URL.
- [Installation](https://github.com/dxcufgb/DiscordComicsBot/wiki/Installation): Linux and Windows installers and their options.
- [Commands](https://github.com/dxcufgb/DiscordComicsBot/wiki/Commands): every slash command and who may use it.
- [Automatic Updates](https://github.com/dxcufgb/DiscordComicsBot/wiki/Automatic-Updates): the opt-in daily updater, rollback and the optional GitHub token.
- [Update Notifications](https://github.com/dxcufgb/DiscordComicsBot/wiki/Update-Notifications): the DM server admins get after an update.
- [Files and Configuration](https://github.com/dxcufgb/DiscordComicsBot/wiki/Files-and-Configuration): the Linux layout, `.env`, the data folder and running from source.
- [Upgrading](https://github.com/dxcufgb/DiscordComicsBot/wiki/Upgrading): from 1.x, from before 2.3 and from 2.2.0 or older.
- [Troubleshooting](https://github.com/dxcufgb/DiscordComicsBot/wiki/Troubleshooting): missing commands, update problems.

## Development

```sh
pip install -r requirements-dev.txt
ruff check . && ruff format --check . && pytest
```

The **Security** workflow (`.github/workflows/security.yml`; GitHub's own secret scanning and CodeQL run on this
public repository as well) runs on every pull request, on pushes to `main` and weekly: `Secrets (gitleaks)` scans the
whole git history, `Code (Semgrep)` runs the Semgrep registry rules, `Dependencies` runs pip-audit on
`requirements.txt` and `requirements-dev.txt`. The same checks locally (gitleaks 8.30.1, semgrep 1.180.0, pip-audit
2.10.1):

```sh
gitleaks git --redact --log-opts="--full-history --diff-filter=tuxdb --diff-merges=first-parent HEAD" .
semgrep scan --metrics=off --error --severity WARNING --severity ERROR \
  --config p/python --config p/javascript --config p/security-audit \
  --config p/secrets --config p/github-actions --config r/bash
pip-audit --strict -r requirements.txt
pip-audit --strict -r requirements-dev.txt
```

Mark a Semgrep false positive with `# nosemgrep: <rule-id>` (Python, shell, YAML, PowerShell) on the matched line or
the line immediately before it, with a reason. Mark a gitleaks false positive with a narrow, commented allowlist entry
in `.gitleaks.toml`; the file must have `[extend]` with `useDefault = true` (the workflow checks this), or it would
replace the default rules. Before scanning, the workflow checks that the pinned gitleaks, with that configuration,
detects a token generated at run time; it scans only the checked-out commit's history (`HEAD`, merge commits against
their first parent), not every branch.

### Releasing

**Actions → Release → Run workflow → type the version** (`1.2.3`; a leading `v` is dropped, `1.2.3-rc.1` makes a
pre-release). Fallback: publish a release `vX.Y.Z` on GitHub; the files are attached afterwards. No version bump in
the code: the version comes from the tag and is stamped into the built files only (`comicsbot/__init__.py` and
`VERSION` in the archives); `__version__` in the repository is just the last release's version.

The Release workflow refuses a version whose tag or release already exists, runs the tests and the real-install CI
jobs (`ci.yml`), builds the archives with `scripts/build_release.py --version X.Y.Z` and the Windows setup.exe with
Inno Setup (`scripts/build_setup.ps1`, `packaging/windows/ComicsBot.iss`), and publishes the release with all files
at once and install instructions above the generated changelog (for a release published by hand it attaches the
files and adds the instructions). The files:

| File | What |
|---|---|
| `ComicsBot-X.Y.Z-linux.tar.gz` | Linux archive; one top folder `ComicsBot-X.Y.Z/` with `install.sh` at its top |
| `ComicsBot-X.Y.Z-windows.zip` | Windows archive (`install.bat`) |
| `ComicsBot-X.Y.Z-setup.exe` | Windows setup wizard |
| `comicsbot-linux.tar.gz`, `comicsbot-windows.zip`, `comicsbot-setup.exe` | the same under fixed names (top folder `comicsbot-X.Y.Z/`), so `releases/latest/download/<name>` links never change |
| `SHA256SUMS` | checksums of the three versioned files, as up to 2.3.2 |
| `SHA256SUMS.txt` | `sha256sum` checksums of every other file |

The versioned names keep the case of 2.3.2 (`ComicsBot-`) while installations on 2.3.2 or older may still update:
their updater needs exactly those names, and GitHub refuses two file names that differ only in case, so
`comicsbot-X.Y.Z-*` cannot be attached as well (the build fails if two names would clash). Once no installation runs
2.3.2 or older, `LEGACY_ASSETS` goes: the versioned names and their top folder become lowercase `comicsbot-X.Y.Z`.

To inspect the files locally, run `python scripts/build_release.py --version 9.9.9`; they're written to `dist/`, and
the same commit and version always build byte-identical archives.

### Dependencies

Renovate (`renovate.json`) opens pull requests when dependencies have new releases. Enable it at <https://github.com/apps/renovate>.

## License

MIT, see [LICENSE](LICENSE).
