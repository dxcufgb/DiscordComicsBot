"""Static checks of the installers' automatic-update support (CI runs them for real)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = (ROOT / "install.sh").read_text(encoding="utf-8")
INSTALL_PS1 = (ROOT / "install.ps1").read_text(encoding="utf-8")
ISS = (ROOT / "packaging" / "windows" / "ComicsBot.iss").read_bytes()


@pytest.mark.parametrize(
    "option", ["--auto-update", "--enable-auto-update", "--disable-auto-update", "--check-update", "--update-now"]
)
def test_install_sh_has_update_options(option):
    assert f"        {option})" in INSTALL_SH


def test_install_sh_never_takes_the_github_token_as_an_option():
    assert "COMICSBOT_GITHUB_TOKEN" in INSTALL_SH
    assert not re.search(r"--github-token|--update-token", INSTALL_SH)
    # The token file is written by root with umask 077 and ends up 600.
    assert 'chmod 600 "$tmp"' in INSTALL_SH


def test_install_sh_keeps_program_files_root_only():
    body = INSTALL_SH[INSTALL_SH.index("do_install() {") :]
    assert body.index("migrate_layout") < body.index('info "Copying program files')
    assert 'chown -R root:root "$INSTALL_DIR/$item"' in body
    assert 'chown "$SERVICE_USER:$SERVICE_USER" "$env_file"' in body and 'chmod 600 "$env_file"' in body
    # Root never runs what the bot's account could have changed: .venv is rebuilt unless root's alone.
    assert body.index("venv_untrusted") < body.index('"$INSTALL_DIR/start-bot.sh" setup')
    assert "run_as_service_user env PYTHON" not in INSTALL_SH
    updater = INSTALL_SH[INSTALL_SH.index("install_updater() {") :]
    assert updater.splitlines()[1].strip() == "require_root_only"


def test_install_sh_refuses_a_folder_that_is_not_a_comicsbot_install():
    body = INSTALL_SH[INSTALL_SH.index("do_install() {") :]
    assert body.index("is not a ComicsBot install") < body.index("stop_service")


def test_install_sh_removes_the_updater_on_uninstall():
    body = INSTALL_SH[INSTALL_SH.index("do_uninstall() {") :]
    assert body.splitlines()[1].strip() == "remove_updater"


def test_auto_update_is_off_by_default():
    assert "AUTO_UPDATE=0\n" in INSTALL_SH
    assert "ask_no " in INSTALL_SH  # the interactive question defaults to no
    assert re.search(r'Name: "autoupdate";[^\r\n]*\\\r?\n\s+Flags: unchecked\r?\n', ISS.decode("ascii"))


@pytest.mark.parametrize("switch", ["EnableAutoUpdate", "DisableAutoUpdate", "CheckUpdate", "UpdateNow", "Unattended"])
def test_install_ps1_has_update_switches(switch):
    assert f"[switch]${switch}" in INSTALL_PS1


def test_install_ps1_takes_the_github_token_only_from_the_environment():
    assert "$env:COMICSBOT_GITHUB_TOKEN" in INSTALL_PS1
    params = INSTALL_PS1[INSTALL_PS1.index("param(") : INSTALL_PS1.index(")\n\n$ErrorActionPreference")]
    assert "GitHub" not in params


def test_iss_is_ascii_and_comments_do_not_start_with_hash():
    text = ISS.decode("ascii")
    for line in text.splitlines():
        if line.startswith("#"):
            assert re.match(r"#(ifndef|define|endif|error)\b", line), line


def test_updater_is_shipped_in_both_archives():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import build_release

    for platform in ("linux", "windows"):
        assert "comicsbot/updater.py" in {name for name, _ in build_release.collect(ROOT, platform)}


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck is not installed")
def test_shellcheck():
    subprocess.run(["shellcheck", "install.sh", "start-bot.sh", ".github/check-linux-layout.sh"], cwd=ROOT, check=True)
