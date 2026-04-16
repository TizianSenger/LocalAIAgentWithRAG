"""
indexer.py
----------
Scans the repository, analyses each source file with an LLM and writes/updates
Markdown notes in the Obsidian vault.  Only files whose content has changed
(detected via SHA-256 hash) are re-analysed.
"""

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime

from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate

from config import (
    REPO_PATH, VAULT_CODE_PATH, STATE_FILE,
    LLM_MODEL, CODE_EXTENSIONS, SKIP_DIRS,
)

# ---------------------------------------------------------------------------
# LLM setup
# ---------------------------------------------------------------------------
_model = OllamaLLM(model=LLM_MODEL)

_ANALYSIS_TEMPLATE = """You are a senior software architect writing concise documentation.
Analyse the source-code file below and respond ONLY with structured Markdown.

File: {filepath}

```
{code}
```

Use exactly this structure (keep the headings, fill in the content):

## Purpose
(1–3 sentences: what this file / module does and why it exists)

## Classes & Functions
(bullet list – for each class or important function: name + one-line description)

## Relationships
(bullet list of other classes, modules or files this code depends on or calls,
 formatted as Obsidian wikilinks: [[ClassName]] or [[module_name]])

## Notes
(optional: design patterns, caveats, TODOs found in the code)
"""

_prompt = ChatPromptTemplate.from_template(_ANALYSIS_TEMPLATE)
_chain  = _prompt | _model

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _vault_note_path(repo_filepath: str) -> str:
    """Convert an absolute repo file path to its vault note path."""
    relative = os.path.relpath(repo_filepath, REPO_PATH)
    # Flatten directory separators into underscores for a flat vault layout
    note_name = relative.replace(os.sep, "_").replace("/", "_")
    note_name = os.path.splitext(note_name)[0] + ".md"
    return os.path.join(VAULT_CODE_PATH, note_name)


def _collect_source_files() -> list[str]:
    results = []
    for root, dirs, files in os.walk(REPO_PATH):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if os.path.splitext(fname)[1].lower() in CODE_EXTENSIONS:
                results.append(os.path.join(root, fname))
    return sorted(results)

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _analyse_and_write(filepath: str):
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()

    # Truncate very large files to stay within the LLM context window
    if len(code) > 14_000:
        code = code[:14_000] + "\n\n... (file truncated for analysis)"

    relative = os.path.relpath(filepath, REPO_PATH)
    analysis = _chain.invoke({"filepath": relative, "code": code})

    note_path  = _vault_note_path(filepath)
    note_title = os.path.splitext(os.path.basename(note_path))[0]

    content = (
        f"---\n"
        f"source: {relative}\n"
        f"last_updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"hash: {_file_hash(filepath)}\n"
        f"---\n\n"
        f"# {note_title}\n\n"
        f"{analysis.strip()}\n"
    )

    os.makedirs(os.path.dirname(note_path), exist_ok=True)
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(content)


def index_repo(force: bool = False) -> dict:
    """
    Scan the repository and update vault notes.

    Parameters
    ----------
    force : bool
        If True, re-analyse every file regardless of hash changes.

    Returns
    -------
    dict with keys: analysed, skipped, removed
    """
    os.makedirs(VAULT_CODE_PATH, exist_ok=True)

    state         = _load_state()
    current_files = set(_collect_source_files())
    new_state     = {}
    stats         = {"analysed": 0, "skipped": 0, "removed": 0}

    # Remove notes for files that no longer exist in the repo
    for old_path in set(state.keys()) - current_files:
        note_path = _vault_note_path(old_path)
        if os.path.exists(note_path):
            os.remove(note_path)
            rel = os.path.relpath(old_path, REPO_PATH)
            print(f"  [removed]  {rel}")
            stats["removed"] += 1

    total = len(current_files)
    for idx, fpath in enumerate(sorted(current_files), 1):
        current_hash       = _file_hash(fpath)
        new_state[fpath]   = current_hash
        rel                = os.path.relpath(fpath, REPO_PATH)

        if not force and state.get(fpath) == current_hash:
            stats["skipped"] += 1
            continue

        print(f"  [{idx}/{total}] Analysing: {rel}")
        _analyse_and_write(fpath)
        stats["analysed"] += 1

    _save_state(new_state)
    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv

    print(f"Repository : {REPO_PATH}")
    print(f"Vault      : {VAULT_CODE_PATH}")
    print(f"Mode       : {'force (all files)' if force else 'incremental (changed files only)'}\n")

    result = index_repo(force=force)

    print(
        f"\nFinished — "
        f"analysed: {result['analysed']}, "
        f"skipped: {result['skipped']}, "
        f"removed: {result['removed']}"
    )
