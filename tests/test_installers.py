"""Static checks of the installers' automatic-update support (CI runs them for real)."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = (ROOT / "install.sh").read_text(encoding="utf-8")
INSTALL_PS1 = (ROOT / "install.ps1").read_text(encoding="utf-8")
ISS = (ROOT / "packaging" / "windows" / "ComicsBot.iss").read_bytes()


@pytest.mark.parametrize(
    "option",
    [
        "--auto-update",
        "--no-auto-update",
        "--enable-auto-update",
        "--disable-auto-update",
        "--check-update",
        "--update-now",
    ],
)
def test_install_sh_has_update_options(option):
    assert f"        {option})" in INSTALL_SH


def test_install_sh_help_lists_the_common_options_first():
    result = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True, check=True)
    options = [line.split()[0] for line in result.stdout.splitlines() if line.startswith("  -") or "(none)" in line]
    assert options[:10] == [
        "(none)",
        "--yes,",
        "--auto-update",
        "--no-auto-update",
        "--enable-auto-update",
        "--disable-auto-update",
        "--check-update",
        "--update-now",
        "--uninstall",
        "--help",
    ]
    assert "--dir" in options[10:]


def test_install_sh_takes_the_one_liners_token_from_the_environment_only():
    assert 'INSTALL_TOKEN="${INSTALL_GITHUB_TOKEN:-}"' in INSTALL_SH
    assert "--preserve-env=DISCORD_TOKEN,COMICSBOT_GITHUB_TOKEN,INSTALL_GITHUB_TOKEN,PYTHON" in INSTALL_SH
    # An interactive install asks before reusing it for automatic updates.
    body = INSTALL_SH[INSTALL_SH.index("need_update_token() {") :]
    assert body.index("! interactive") < body.index('UPDATE_TOKEN="$INSTALL_TOKEN"')


def _install_sh_function(name):
    start = INSTALL_SH.index(f"{name}() {{")
    end = INSTALL_SH.index("\n", start) if INSTALL_SH[start:].split("\n", 1)[0].endswith("}") else None
    return INSTALL_SH[start : end + 1] if end else INSTALL_SH[start : INSTALL_SH.index("\n}\n", start) + 3]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ("", ""),  # no token anywhere: fine, the repository is public
        ("INSTALL_TOKEN=from-one-liner", "from-one-liner"),  # --yes: the one-liner's token is used without asking
        ("UPDATE_TOKEN=given", "given"),
    ],
)
def test_install_sh_never_needs_a_github_token(tmp_path, env, expected):
    script = (
        f"ASSUME_YES=1\nUPDATE_TOKEN=\nINSTALL_TOKEN=\nUPDATE_TOKEN_FILE={tmp_path}/missing\nGITHUB_REPO=x/y\n{env}\n"
    )
    script += "".join(_install_sh_function(f) for f in ("die", "confirm", "interactive", "need_update_token"))
    script += 'need_update_token < /dev/null && echo "OK token=$UPDATE_TOKEN"\n'
    result = subprocess.run(["bash", "--norc", "--noprofile", "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == f"OK token={expected}"


def test_installers_never_ask_for_or_demand_a_github_token():
    body = _install_sh_function("need_update_token")
    assert "die " not in body and "read " not in body
    enable = _install_sh_function("enable_auto_update")
    assert "die " not in enable
    assert "need a GitHub token" not in INSTALL_SH and "needs a GitHub token" not in INSTALL_SH
    assert "Read-Secret 'GitHub token'" not in INSTALL_PS1
    assert "need a GitHub token" not in INSTALL_PS1
    iss = ISS.decode("ascii")
    assert "Please paste the GitHub token" not in iss and "need a GitHub token" not in iss
    assert "GitHub token (optional)" in iss


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


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
@pytest.mark.parametrize("function", ["confirm", "ask_no"])
def test_end_of_input_at_a_prompt_is_no(function):
    """Ctrl-D (or a closed terminal) must never count as yes, e.g. to storing INSTALL_GITHUB_TOKEN."""
    import os
    import pty

    script = "ASSUME_YES=0\n" + "".join(_install_sh_function(f) for f in ("confirm", "interactive", "ask_no"))
    script += f'if {function} "Store it?"; then echo ANSWER=yes; else echo ANSWER=no; fi\n'
    pid, fd = pty.fork()
    if pid == 0:  # child: bash at a terminal
        os.execvp("bash", ["bash", "--norc", "--noprofile", "-c", script])
    output = b""
    os.write(fd, b"\x04")  # end of input, as at an empty line
    while True:
        try:
            chunk = os.read(fd, 1024)
        except OSError:
            break
        if not chunk:
            break
        output += chunk
    os.waitpid(pid, 0)
    assert b"ANSWER=no" in output, output
