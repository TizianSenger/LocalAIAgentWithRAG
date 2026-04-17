"""
indexer.py
----------
Scans the repository, analyses each source file with an LLM and writes/updates
Markdown notes in the Obsidian vault.  Only files whose content has changed
(detected via SHA-256 hash) are re-analysed.

Files are processed in parallel (INDEXER_WORKERS threads) to saturate the GPU.
"""

import os
import json
import time
import hashlib
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate

from config import (
    REPO_PATH, VAULT_CODE_PATH, STATE_FILE,
    LLM_MODEL, CODE_EXTENSIONS, SKIP_DIRS, INDEXER_WORKERS,
)

# Allow UI to override the model via environment variable
LLM_MODEL = os.environ.get('OVERRIDE_LLM_MODEL', LLM_MODEL)

# ---------------------------------------------------------------------------
# LLM setup  (one client instance per thread via threading.local)
# ---------------------------------------------------------------------------
_thread_local = threading.local()

def _get_chain():
    """Return a per-thread LLM chain (avoids shared-state issues)."""
    if not hasattr(_thread_local, "chain"):
        model  = OllamaLLM(model=LLM_MODEL)
        prompt = ChatPromptTemplate.from_template(_ANALYSIS_TEMPLATE)
        _thread_local.chain = prompt | model
    return _thread_local.chain


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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()


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
    relative  = os.path.relpath(repo_filepath, REPO_PATH)
    note_path = os.path.splitext(relative)[0] + ".md"
    return os.path.join(VAULT_CODE_PATH, note_path)


def _collect_source_files() -> list[str]:
    results = []
    for root, dirs, files in os.walk(REPO_PATH):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if os.path.splitext(fname)[1].lower() in CODE_EXTENSIONS:
                results.append(os.path.join(root, fname))
    return sorted(results)


# ---------------------------------------------------------------------------
# Per-file work (runs in worker thread)
# ---------------------------------------------------------------------------

def _process_file(fpath: str) -> tuple[str, str]:
    """Analyse a single file and write its vault note. Returns (fpath, hash)."""
    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()

    if len(code) > 14_000:
        code = code[:14_000] + "\n\n... (file truncated for analysis)"

    relative = os.path.relpath(fpath, REPO_PATH)
    analysis = _get_chain().invoke({"filepath": relative, "code": code})

    note_path  = _vault_note_path(fpath)
    note_title = os.path.splitext(os.path.basename(note_path))[0]
    current_hash = _file_hash(fpath)

    content = (
        f"---\n"
        f"source: {relative}\n"
        f"last_updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"hash: {current_hash}\n"
        f"---\n\n"
        f"# {note_title}\n\n"
        f"{analysis.strip()}\n"
    )

    os.makedirs(os.path.dirname(note_path), exist_ok=True)
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(content)

    return fpath, current_hash


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def index_repo(force: bool = False) -> dict:
    """
    Scan the repository and update vault notes in parallel.

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
    new_state     = dict(state)  # start with existing state
    stats         = {"analysed": 0, "skipped": 0, "removed": 0}

    # Remove notes for files that no longer exist in the repo
    for old_path in set(state.keys()) - current_files:
        note_path = _vault_note_path(old_path)
        if os.path.exists(note_path):
            os.remove(note_path)
        new_state.pop(old_path, None)
        rel = os.path.relpath(old_path, REPO_PATH)
        print(f"  [removed]  {rel}")
        stats["removed"] += 1

    # Determine which files need analysis
    to_analyse = []
    for fpath in sorted(current_files):
        current_hash = _file_hash(fpath)
        new_state[fpath] = current_hash
        if force or state.get(fpath) != current_hash:
            to_analyse.append(fpath)
        else:
            stats["skipped"] += 1

    total      = len(to_analyse)
    completed  = 0
    start_time = time.time()

    print(f"  Files to analyse : {total}")
    print(f"  Files skipped    : {stats['skipped']}")
    print(f"  Parallel workers : {INDEXER_WORKERS}\n", flush=True)

    # Emit initial progress so the UI shows 0% immediately
    print(f"PROGRESS:{json.dumps({'done': 0, 'total': total, 'file': '', 'elapsed': 0, 'eta': 0})}", flush=True)

    if total == 0:
        _save_state(new_state)
        return stats

    with ThreadPoolExecutor(max_workers=INDEXER_WORKERS) as executor:
        futures = {executor.submit(_process_file, fpath): fpath for fpath in to_analyse}

        for future in as_completed(futures):
            completed   += 1
            fpath        = futures[future]
            rel          = os.path.relpath(fpath, REPO_PATH)
            elapsed      = time.time() - start_time
            rate         = completed / elapsed if elapsed > 0 else 0
            remaining    = total - completed
            eta          = remaining / rate if rate > 0 else 0

            try:
                _, _ = future.result()
                stats["analysed"] += 1
                print(f"  [{completed}/{total}] ✓ {rel}", flush=True)
            except Exception as exc:
                print(f"  [{completed}/{total}] ✗ {rel}  ERROR: {exc}", flush=True)

            print(f"PROGRESS:{json.dumps({'done': completed, 'total': total, 'file': rel, 'elapsed': round(elapsed), 'eta': round(eta)})}", flush=True)

            # Save state incrementally every 50 files so progress
            # is not lost if the run is interrupted
            if completed % 50 == 0:
                with _state_lock:
                    _save_state(new_state)

    _save_state(new_state)
    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    force   = "--force" in sys.argv
    workers_arg = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == '--workers' and i+1 < len(sys.argv)), None)
    if workers_arg:
        import config
        config.INDEXER_WORKERS = int(workers_arg)
        from config import INDEXER_WORKERS  # re-import updated value

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
