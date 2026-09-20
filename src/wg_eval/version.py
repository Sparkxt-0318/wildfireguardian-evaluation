"""Version and code-provenance helpers.

What ``code_version()`` asserts, precisely: *the package source in this
checkout matches the named commit*.  It reports ``+dirty`` when a tracked file
under ``src/`` or ``pyproject.toml`` differs from that commit, and it ignores
everything else -- regenerating a report, adding scratch files, or editing docs
does not make the scientific code look changed.

What it does **not** cover is tracked separately in
:class:`wg_eval.provenance.ReproducibilityManifest`: the analysis
configuration, the source data, and the preregistered protocol each get their
own hash, because each can change without the others and a single version
string would hide which one moved.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

__version__ = "0.1.0"

#: Bumped when the *rendering* of a report changes without the numbers changing.
#: Recorded separately from ``__version__`` so a reader can tell a reformatted
#: report from a re-analysed one.
REPORT_GENERATOR_VERSION = "report-1.0.0"


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
        # "+dirty" must mean "the code differs from this commit", so the check is
        # scoped to the package and its build metadata. Regenerating a tracked
        # report changes the worktree without changing what produced the number.
        dirty = subprocess.run(
            [
                "git", "-C", str(repo_root), "status", "--porcelain",
                "--untracked-files=no", "--", "src", "pyproject.toml",
            ],
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
