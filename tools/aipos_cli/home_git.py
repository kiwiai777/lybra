"""AIPOS-226 (governance home, Slice 2 / Phase 2a) — one-shot, transparent home git setup.

The governance home (the survivable container of per-project truth subtrees) is best run as a
user-managed, git-versioned, remote-backed repository. `lybra home git-init` is the Owner's
one-shot path to version it: `git init` -> write a home `.gitignore` -> `git add` truth ->
initial `git commit`. It is **welded gate-not-engine**:

  - It is an Owner CLI/TUI action only — the read-only copilot (role="copilot", scopes []) can
    never invoke it.
  - It is a SINGLE on-demand action. There is NO background, scheduler, on-change hook,
    auto-commit, or auto-push.
  - It NEVER configures a remote and NEVER pushes. It only PRINTS the exact `git remote add` +
    `git push` commands for the Owner to run with their own URL (M2).

Implemented with the stdlib only: it shells **system git** via `subprocess` (no Python
third-party import, so the zero-dep gate core / acceptance third-party-import probe stay green).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

# The home .gitignore IGNORES ephemeral/local-only artifacts; it deliberately does NOT ignore
# truth — `*/5_tasks/`, `*/governance/`, `*/stage_archive/`, and `project.json` are TRACKED so
# the governance home is fully versioned/remote-backed.
HOME_GITIGNORE = """\
# Lybra governance home .gitignore (AIPOS-226).
# Ignores local-only / ephemeral artifacts. It TRACKS truth — 5_tasks/, governance/,
# stage_archive/, and project.json are versioned (do NOT add them here).
.lybra/local/
*.tgz
__pycache__/
*.pyc
.DS_Store
"""

_COMMIT_MESSAGE = "chore(home): initialize Lybra governance home"


def is_git_repo(home_root: str | Path) -> bool:
    return (Path(home_root).expanduser().resolve() / ".git").exists()


def git_repo_ancestor(path: str | Path) -> Path | None:
    """Walk up from `path` looking for a `.git` dir; return the repo root, or None.

    AIPOS-226 §3: this is how `lybra home git-init` detects it is already INSIDE an existing
    git repo (e.g. the Owner's `ai-project-os` repo, topology C) and refuses to nest a new one.
    """
    current = Path(path).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def plan_home_git_init(target: str | Path, actor: str = "owner") -> dict[str, Any]:
    """Pure plan (no subprocess) of the one-shot init: gitignore + exact commands + push hint.

    `target` is the resolved directory to version — the home root (topology A) or a project
    root `<home>/<project>` (topology B). The commit carries an explicit, non-anonymous
    identity (`-c user.name=<actor> -c user.email=<actor>@lybra.local`) without mutating any
    global git config. The push hint is informational only — `git-init` never runs it.
    """
    home = Path(target).expanduser().resolve()
    return {
        "home": str(home),
        "gitignore": HOME_GITIGNORE,
        "commands": [
            ["git", "init"],
            ["git", "add", "."],
            [
                "git",
                "-c",
                f"user.name={actor}",
                "-c",
                f"user.email={actor}@lybra.local",
                "commit",
                "-m",
                _COMMIT_MESSAGE,
            ],
        ],
        "push_hint": [
            "git remote add origin <YOUR_REMOTE_URL>",
            "git push -u origin HEAD",
        ],
    }


def execute_home_git_init(target: str | Path, *, actor: str = "owner") -> dict[str, Any]:
    """Run the one-shot local init in the resolved target. NEVER configures a remote or pushes.

    `target` is the home root (topology A) or `<home>/<project>` (topology B, via --project).

    Raises FileNotFoundError if the target does not exist, and FileExistsError if:
      - the target is itself already a git repo (refuse rather than re-init), OR
      - the target is already INSIDE an existing git repo (AIPOS-226 §3 — Lybra will not nest a
        repo; this is what makes the Owner dogfood topology C safe: inside `ai-project-os` it
        detects the ancestor repo and declines).
    """
    home = Path(target).expanduser().resolve()
    if not home.exists():
        raise FileNotFoundError(f"HOME_NOT_FOUND: home root does not exist: {home}")
    if is_git_repo(home):
        raise FileExistsError(f"HOME_ALREADY_GIT: home is already a git repo: {home}")
    ancestor = git_repo_ancestor(home)
    if ancestor is not None:
        raise FileExistsError(
            f"ALREADY_IN_GIT_REPO: {ancestor} — Lybra will not nest a git repo; "
            "commit/push via the existing repo (topology C)"
        )

    plan = plan_home_git_init(home, actor)
    (home / ".gitignore").write_text(plan["gitignore"], encoding="utf-8")

    ran: list[str] = []
    for cmd in plan["commands"]:
        subprocess.run(cmd, cwd=str(home), check=True, capture_output=True, text=True)
        ran.append(" ".join(cmd))

    return {"home": str(home), "ran": ran, "push_hint": list(plan["push_hint"])}
