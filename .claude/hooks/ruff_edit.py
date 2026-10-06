"""PostToolUse hook: run ruff fix + format on the .py file Claude just edited (no-op if ruff isn't installed)."""

import json
import shutil
import subprocess
import sys

path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
ruff = shutil.which("ruff")
if path.endswith(".py") and ruff:
    subprocess.run([ruff, "check", "--fix", "--quiet", path])  # noqa: S603
    subprocess.run([ruff, "format", "--quiet", path])  # noqa: S603
