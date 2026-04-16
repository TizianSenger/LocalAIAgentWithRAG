"""
update.py
---------
Manual trigger: pull latest git changes and re-index the repository.
Run this whenever you want to update the vault immediately.

Usage:
    python update.py           # incremental (only changed files)
    python update.py --force   # re-analyse every file
"""

import subprocess
import sys
import os

from config import REPO_PATH
from indexer import index_repo


def git_pull():
    git_dir = os.path.join(REPO_PATH, ".git")
    if not os.path.exists(git_dir):
        print("No .git directory found – skipping git pull.")
        return

    print(f"Pulling latest changes in {REPO_PATH} ...")
    result = subprocess.run(
        ["git", "-C", REPO_PATH, "pull"],
        capture_output=True,
        text=True,
    )
    print(result.stdout.strip() or "(no output)")
    if result.returncode != 0:
        print(f"git pull warning: {result.stderr.strip()}")


if __name__ == "__main__":
    force = "--force" in sys.argv

    git_pull()
    print("\nIndexing repository ...\n")
    stats = index_repo(force=force)

    print(
        f"\nUpdate complete — "
        f"analysed: {stats['analysed']}, "
        f"skipped: {stats['skipped']}, "
        f"removed: {stats['removed']}"
    )
