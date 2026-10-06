"""PostToolUse hook: run ruff fix + format on the .py file Claude just edited (no-op if ruff isn't installed)."""
import json
import shutil
import subprocess
import sys

path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
if path.endswith(".py") and shutil.which("ruff"):
    subprocess.run(["ruff", "check", "--fix", "--quiet", path])
    subprocess.run(["ruff", "format", "--quiet", path])
