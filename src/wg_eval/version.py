"""Version and code-provenance helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

__version__ = "0.1.0"


def _git_describe() -> str | None:
    """Return a short git description of the working tree, or None."""
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / ".git").exists():
        return None
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if sha.returncode != 0:
            return None
        rev = sha.stdout.strip()
        # Untracked files (generated reports, scratch data) are not code changes;
        # only tracked modifications make the recorded version untrustworthy.
        dirty = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            rev += "+dirty"
        return rev
    except (OSError, subprocess.SubprocessError):
        return None


def code_version() -> str:
    """Identify the code that produced a result.

    Returns ``"wg-eval 0.1.0 (git abc1234)"`` when a git checkout is available
    and ``"wg-eval 0.1.0"`` otherwise.  Recorded in every provenance block.
    """
    rev = _git_describe()
    return f"wg-eval {__version__} (git {rev})" if rev else f"wg-eval {__version__}"
