"""
agent.py
--------
Autonomous code-analysis agent.

The agent is given a time budget and autonomously:
  1. Picks analysis strategies (performance, bugs, security, code-smell, architecture)
  2. Uses tools to explore the codebase (RAG search, grep, file read, dep graph)
  3. Writes structured findings
  4. At the end produces a Markdown report saved to the Obsidian vault

Usage:
  python -u agent.py [--budget-minutes N] [--focus FOCUS] [--report-path PATH]

Output protocol (stdout):
  AGENT_PROGRESS:{json}   - progress update for the UI
  AGENT_FINDING:{json}    - a finding was recorded
  AGENT_DONE:{json}       - agent finished, report path included
  Everything else is a log line.
"""

import os
import sys
import json
import re
import time
import csv
import argparse
import textwrap
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

from langchain_ollama.llms import OllamaLLM
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from config import REPO_PATH, VAULT_PATH, LLM_MODEL, SKIP_DIRS, CODE_EXTENSIONS
from dep_graph import load_graph, get_dependents, get_class_info
from code_units import split_file_into_units

# ── Persistent finding store (temp CSV) ─────────────────────────────────────

class FindingStore:
    """Writes findings immediately to a temp CSV; never keeps them all in RAM.
    Provides a row-count and a load() method for end-of-run report generation.
    """
    _FIELDS = ('severity', 'category', 'file', 'description', 'ts')

    def __init__(self, path: str):
        self.path  = path
        self._count = 0
        with open(path, 'w', newline='', encoding='utf-8') as fh:
            csv.DictWriter(fh, fieldnames=self._FIELDS).writeheader()

    def append(self, finding: 'Finding') -> None:
        with open(self.path, 'a', newline='', encoding='utf-8') as fh:
            csv.DictWriter(fh, fieldnames=self._FIELDS).writerow(finding.to_dict())
        self._count += 1

    def __len__(self) -> int:
        return self._count

    def load(self) -> list['Finding']:
        result = []
        with open(self.path, newline='', encoding='utf-8') as fh:
            for row in csv.DictReader(fh):
                f = Finding(row['severity'], row['category'], row['file'], row['description'])
                f.ts = row['ts']
                result.append(f)
        return result

    def delete(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


# ── Allow UI to override model ────────────────────────────────────────────────
LLM_MODEL = os.environ.get('OVERRIDE_AGENT_MODEL', os.environ.get('OVERRIDE_LLM_MODEL', LLM_MODEL))

# ── Report output path ────────────────────────────────────────────────────────
REPORT_DIR = os.path.join(VAULT_PATH, 'AgentReports')

# ── Runtime settings (overridden by CLI args) ────────────────────────────────
_SETTINGS: dict = {}

# ── Analysis strategies ───────────────────────────────────────────────────────
STRATEGIES = [
    'security',
    'performance',
    'potential_bugs',
    'architecture',
    'code_smell',
]

STRATEGY_DESCRIPTIONS = {
    'security':       'Security vulnerabilities: SQL injection, XSS, improper authentication, hardcoded credentials, insecure deserialization, missing input validation.',
    'performance':    'Performance issues: N+1 queries, missing indexes, excessive memory allocation, blocking I/O in hot paths, unnecessary object creation in loops.',
    'potential_bugs': 'Potential bugs: null pointer risks, unchecked casts, resource leaks (streams/connections not closed), race conditions, incorrect exception handling.',
    'architecture':   'Architecture concerns: circular dependencies, God classes (too many responsibilities), missing abstraction layers, tight coupling, violation of SOLID principles.',
    'code_smell':     'Code smells: duplicated logic, dead code, overly long methods, excessive nesting, magic numbers/strings, unclear naming.',
}


# ── Tools ─────────────────────────────────────────────────────────────────────

def tool_search_notes(query: str, k: int = 8) -> str:
    """Search the Obsidian vault notes using ChromaDB."""
    k = _SETTINGS.get('notes_k', k)
    try:
        from vector import retriever
        docs = retriever.invoke(query)[:k]
        if not docs:
            return 'No relevant notes found.'
        parts = []
        for d in docs:
            src = d.metadata.get('source', 'unknown')
            parts.append(f'[{src}]\n{d.page_content[:600]}')
        return '\n\n---\n\n'.join(parts)
    except Exception as e:
        return f'search_notes error: {e}'


def tool_search_code(query: str, k: int = 12) -> str:
    """Search the method-level source code index using ChromaDB."""
    k = _SETTINGS.get('code_k', k)
    try:
        from vector import code_retriever
        docs = code_retriever.invoke(query)[:k]
        if not docs:
            return 'No matching code units found.'
        parts = []
        for d in docs:
            src    = d.metadata.get('source', 'unknown')
            method = d.metadata.get('method', '')
            cls    = d.metadata.get('class', '')
            line   = d.metadata.get('start_line', '')
            label  = f'{cls}.{method}' if cls else method
            header = f'[{src}:{line}] {label}' if label else f'[{src}:{line}]'
            parts.append(f'{header}\n{d.page_content[:800]}')
        return '\n\n---\n\n'.join(parts)
    except Exception as e:
        return f'search_code error: {e}'


def tool_grep(pattern: str, file_ext: str = '', max_results: int = 50) -> str:
    """Grep the repository for a regex pattern in files with the given extension."""
    # Allow runtime override via module-level setting
    max_results = _SETTINGS.get('grep_limit', max_results)
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f'Invalid regex: {e}'

    results = []
    for root, dirs, files in os.walk(REPO_PATH):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for fname in files:
            if not fname.endswith(file_ext):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    for lineno, line in enumerate(f, 1):
                        if rx.search(line):
                            rel = os.path.relpath(fpath, REPO_PATH).replace('\\', '/')
                            results.append(f'{rel}:{lineno}: {line.rstrip()}')
                            if len(results) >= max_results:
                                return '\n'.join(results) + f'\n... (limited to {max_results} results)'
            except OSError:
                continue
    return '\n'.join(results) if results else 'No matches found.'


def tool_read_file(relative_path: str, start_line: int = 0, max_lines: int = 200) -> str:
    """Read a file from the repository (relative path from repo root).
    start_line: 0-based line offset to start reading from (useful for large files).
    """
    fpath = os.path.join(REPO_PATH, relative_path.replace('/', os.sep))
    if not os.path.exists(fpath):
        return f'File not found: {relative_path}'
    try:
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()
        total = len(all_lines)
        start_line = max(0, min(start_line, total - 1))
        window = all_lines[start_line: start_line + max_lines]
        header = f'// {relative_path}  [lines {start_line+1}–{start_line+len(window)}/{total}]\n'
        if start_line + max_lines < total:
            header += f'// (file continues — use READ_FILE({relative_path}, {start_line + max_lines}) for next section)\n'
        return header + ''.join(window)
    except OSError as e:
        return f'Cannot read file: {e}'


def tool_list_files(directory: str = '', ext: str = '') -> str:
    """List source files in a directory of the repository (recursively, filtered by extension)."""
    base = os.path.join(REPO_PATH, directory.replace('/', os.sep)) if directory else REPO_PATH
    if not os.path.exists(base):
        return f'Directory not found: {directory}'
    results = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for fname in sorted(files):
            if ext and not fname.endswith(ext):
                continue
            if not any(fname.endswith(e) for e in CODE_EXTENSIONS):
                continue
            rel = os.path.relpath(os.path.join(root, fname), REPO_PATH).replace('\\', '/')
            results.append(rel)
            if len(results) >= 200:
                results.append('... (limited to 200 results)')
                return '\n'.join(results)
    return '\n'.join(results) if results else f'No files found in {directory or "repo root"}.'


def tool_get_dependents(class_name: str) -> str:
    """Return all files that depend on (import/extend/inject) the given class."""
    deps = get_dependents(class_name)
    if not deps:
        return f'No dependents found for {class_name}.'
    return f'Files that depend on {class_name}:\n' + '\n'.join(f'  - {d}' for d in deps[:30])


def tool_get_class_info(class_name: str) -> str:
    """Return structural info about a class/module/type from the dependency graph."""
    info = get_class_info(class_name)
    if not info:
        return f'No info found for class {class_name}.'
    lines = [
        f"Class: {info.get('fqn', class_name)}",
        f"File:  {info.get('file', '?')}",
        f"Type:  {info.get('type', '?')}",
    ]
    if info.get('extends'):
        lines.append(f"Extends: {info['extends']}")
    if info.get('implements'):
        lines.append(f"Implements: {', '.join(info['implements'])}")
    if info.get('annotations'):
        lines.append(f"Annotations: {', '.join('@' + a for a in info['annotations'])}")
    if info.get('injected'):
        lines.append(f"Injects: {', '.join(info['injected'])}")
    return '\n'.join(lines)


TOOLS_DESCRIPTION = """
Available tools (call them exactly as shown):

SEARCH_NOTES(<query>)
  Search the documentation vault for notes matching the query.
  Example: SEARCH_NOTES(authentication login user session)

SEARCH_CODE(<query>)
  Search the method-level source code index. Returns actual function/method bodies
  with file path, class name, and line numbers. Use this to find and inspect specific
  implementations directly — much faster than GREP + READ_FILE for known concepts.
  Example: SEARCH_CODE(infer_memory_room classification)
  Example: SEARCH_CODE(load_settings API key encryption)
  Example: SEARCH_CODE(race condition browser close web_agent)

GREP(<pattern>, <extension>)
  Search all files with given extension for a regex pattern.
  Example: GREP(password.*=.*"[^"]+", .py)

LIST_FILES(<directory>, <extension>)
  List source files in a directory (recursive). Use to navigate large projects.
  Example: LIST_FILES(backend/services, .py)
  Example: LIST_FILES(src, .ts)

READ_FILE(<relative_path>)
  Read up to 120 lines of a source file. For large files, use an offset:
  Example: READ_FILE(src/auth/LoginService.java)
  Example: READ_FILE(src/auth/LoginService.java, 120)   ← reads lines 121–240

GET_DEPENDENTS(<ClassName>)
  Find all files that depend on a given class.
  Example: GET_DEPENDENTS(LoginService)

GET_CLASS_INFO(<ClassName>)
  Get structural info about a class (package, extends, injects etc).
  Example: GET_CLASS_INFO(UserRepository)

WRITE_FINDING(<severity>, <category>, <file>, <description>)
  Record a finding. severity = CRITICAL | HIGH | MEDIUM | LOW
  Example: WRITE_FINDING(HIGH, security, src/auth/LoginService.py, SQL query built via string concatenation — potential SQL injection)
"""

_AGENT_SYSTEM = """You are an expert software architect performing an autonomous code analysis of a large, potentially legacy codebase.
Your task is to find real issues in the code for the category: {category}.
{category_desc}

{tools}

WORKFLOW:
1. Start with SEARCH_CODE or SEARCH_NOTES to find relevant implementations quickly.
2. Use GREP for additional pattern-based discovery across the full codebase.
3. For each suspicious GREP hit: call READ_FILE on that file to inspect the actual code.
4. ONLY AFTER reading the file (or after SEARCH_CODE returned the actual body), call WRITE_FINDING if a real issue is confirmed.
5. Keep investigating different files and patterns until you have exhausted the category.
6. When done (minimum 6 tool calls), output exactly: DONE

RULES:
- Call exactly ONE tool per message. Wait for the result before the next call.
- CRITICAL: You MUST call READ_FILE on a file (or have seen the code via SEARCH_CODE) BEFORE you may call WRITE_FINDING for it. GREP alone is never sufficient evidence.
- Never repeat the same tool call with identical arguments more than once.
- Only call WRITE_FINDING for real, specific issues confirmed by reading actual code — include the exact file path and the problematic line/function.
- Do NOT say DONE before making at least 6 tool calls.
- The codebase may be large with many legacy files — keep exploring systematically.

Start now.
"""

_TOOL_CALL_RE = re.compile(
    r'(SEARCH_NOTES|SEARCH_CODE|GREP|READ_FILE|LIST_FILES|GET_DEPENDENTS|GET_CLASS_INFO|WRITE_FINDING)\((.+?)\)',
    re.DOTALL
)


def _parse_tool_call(text: str):
    """Extract the first tool call from LLM output."""
    m = _TOOL_CALL_RE.search(text)
    if not m:
        return None, None
    name = m.group(1)
    raw  = m.group(2).strip()
    # WRITE_FINDING has 4 args; split on first 3 commas only, rest is description
    if name == 'WRITE_FINDING':
        parts = raw.split(',', 3)
    else:
        parts = raw.split(',', 1)  # GREP has 2 args, others have 1
    args = [a.strip().strip('"\'') for a in parts]
    return name, args


def _run_tool(name: str, args: list) -> str:
    if name == 'SEARCH_NOTES':
        return tool_search_notes(args[0] if args else '')
    if name == 'SEARCH_CODE':
        return tool_search_code(args[0] if args else '')
    if name == 'GREP':
        pattern = args[0] if args else ''
        ext     = args[1].strip() if len(args) > 1 else ''
        return tool_grep(pattern, ext)
    if name == 'READ_FILE':
        parts = args[0].split(',', 1) if args else ['']
        path  = parts[0].strip()
        try:
            offset = int(parts[1].strip()) if len(parts) > 1 else 0
        except ValueError:
            offset = 0
        return tool_read_file(path, start_line=offset)
    if name == 'LIST_FILES':
        directory = args[0] if args else ''
        ext       = args[1] if len(args) > 1 else ''
        return tool_list_files(directory, ext)
    if name == 'GET_DEPENDENTS':
        return tool_get_class_info(args[0] if args else '')
    if name == 'WRITE_FINDING':
        # handled by caller
        return ''
    return f'Unknown tool: {name}'


# ── Finding recorder ──────────────────────────────────────────────────────────

class Finding:
    def __init__(self, severity: str, category: str, file: str, description: str):
        self.severity    = severity.upper()
        self.category    = category
        self.file        = file
        self.description = description
        self.ts          = datetime.now().strftime('%H:%M:%S')

    def to_dict(self):
        return {
            'severity': self.severity,
            'category': self.category,
            'file':     self.file,
            'description': self.description,
            'ts': self.ts,
        }


# ── Strategy runner ───────────────────────────────────────────────────────────

def run_strategy(strategy: str, model, findings: 'FindingStore', seen_keys: set,
                 budget_seconds: float, start_time: float) -> int:
    """Run one analysis strategy. Returns number of tool calls made.
    seen_keys: shared set of (file, description_prefix) to avoid duplicate findings.
    """
    desc       = STRATEGY_DESCRIPTIONS[strategy]
    system_msg = SystemMessage(content=_AGENT_SYSTEM.format(
        category=strategy,
        category_desc=desc,
        tools=TOOLS_DESCRIPTION,
    ))

    tool_calls        = 0
    min_calls         = 6   # must make at least this many tool calls before DONE is accepted
    max_calls         = _SETTINGS.get('max_calls', 25)   # cap per strategy
    conversation      = []   # list of HumanMessage / AIMessage
    findings_at_start = len(findings)
    consecutive_done  = 0
    consecutive_invalid = 0  # times model output neither tool call nor DONE
    last_tool_sig     = None   # (tool_name, first_arg) — repeat detection
    repeat_count      = 0
    files_read        = set()  # tracks files read this strategy — required before WRITE_FINDING

    print(f'\n[agent] === Strategy: {strategy.upper()} ===', flush=True)
    _emit_progress(strategy=strategy, tool_calls=0, findings=len(findings))

    while tool_calls < max_calls:
        # Check time budget
        if time.time() - start_time > budget_seconds:
            print(f'[agent] Time budget exhausted during {strategy}', flush=True)
            break

        # Build message list — trim old tool results to keep within context window.
        # Keep the last 8 messages verbatim; condense older HumanMessages with long content.
        _MAX_RECENT = 8
        if len(conversation) > _MAX_RECENT:
            condensed = []
            for msg in conversation[:-_MAX_RECENT]:
                if isinstance(msg, HumanMessage) and len(msg.content) > 300:
                    first_line = msg.content.splitlines()[0][:120]
                    condensed.append(HumanMessage(content=f'[prior result condensed] {first_line}'))
                else:
                    condensed.append(msg)
            prompt_messages = [system_msg] + condensed + conversation[-_MAX_RECENT:]
        else:
            prompt_messages = [system_msg] + conversation
        # LLM call with retry/backoff
        response_text = None
        for attempt in range(3):
            try:
                print(f'[agent] Querying LLM…', flush=True)
                response = model.invoke(prompt_messages)
                response_text = str(response).strip()
                break
            except Exception as e:
                wait = 2 ** attempt
                print(f'[agent] LLM error (attempt {attempt+1}/3): {e} — retrying in {wait}s', flush=True)
                time.sleep(wait)
        if response_text is None:
            print(f'[agent] LLM failed after 3 attempts — aborting strategy {strategy}', flush=True)
            break
        conversation.append(AIMessage(content=response_text))

        print(f'[agent] LLM ({tool_calls+1}/{max_calls}): {response_text[:300]}', flush=True)

        # Done?
        if 'DONE' in response_text and not _TOOL_CALL_RE.search(response_text):
            consecutive_done += 1
            # Accept DONE only after min_calls, or if stuck in a DONE loop
            if tool_calls >= min_calls or consecutive_done >= 2:
                print(f'[agent] Strategy {strategy} complete.', flush=True)
                break
            else:
                conversation.append(HumanMessage(content=f'You said DONE after only {tool_calls} tool calls. You must make at least {min_calls} before finishing. Keep investigating — try GREP, READ_FILE on different files, or LIST_FILES to find more issues.'))
                continue
        consecutive_done = 0

        # Parse tool call
        tool_name, tool_args = _parse_tool_call(response_text)
        if not tool_name:
            consecutive_invalid += 1
            if consecutive_invalid >= 4:
                print(f'[agent] Strategy {strategy} aborted — model not producing valid output.', flush=True)
                break
            conversation.append(HumanMessage(content='Please call exactly one tool from the list, or say DONE if finished.'))
            continue
        consecutive_invalid = 0

        # Reject batch: multiple WRITE_FINDINGs in one response
        all_calls_in_response = _TOOL_CALL_RE.findall(response_text)
        finding_count_in_response = sum(1 for n, _ in all_calls_in_response if n == 'WRITE_FINDING')
        if finding_count_in_response > 1:
            print(f'[agent] Rejected batch of {finding_count_in_response} findings — model must submit ONE at a time.', flush=True)
            conversation.append(HumanMessage(content=f'You submitted {finding_count_in_response} WRITE_FINDING calls at once. You MUST submit exactly ONE tool call per message. Also, you MUST call READ_FILE on a file before reporting findings for it. Start over: call READ_FILE on one file, then WRITE_FINDING for that one finding only.'))
            continue

        tool_calls += 1

        # Detect repeated identical tool calls
        tool_sig = (tool_name, tool_args[0] if tool_args else '')
        if tool_sig == last_tool_sig:
            repeat_count += 1
        else:
            repeat_count = 0
        last_tool_sig = tool_sig

        if repeat_count >= 2:
            print(f'[agent] Repeat loop: {tool_name} x{repeat_count+1} — redirecting', flush=True)
            conversation.append(HumanMessage(content=f'You already called {tool_name} with the same arguments {repeat_count+1} times and got the same result. Do NOT repeat it. Call a DIFFERENT tool (GREP, READ_FILE, LIST_FILES) or say DONE.'))
            tool_calls -= 1   # don\'t waste a call slot on repeats
            if repeat_count >= 4:
                print(f'[agent] Strategy {strategy} aborted — stuck in repeat loop.', flush=True)
                break
            continue

        if tool_name == 'WRITE_FINDING':
            # Parse finding args
            severity    = tool_args[0] if len(tool_args) > 0 else 'LOW'
            category    = tool_args[1] if len(tool_args) > 1 else strategy
            file_path   = tool_args[2] if len(tool_args) > 2 else 'unknown'
            description = tool_args[3] if len(tool_args) > 3 else 'No description'
            # Require READ_FILE before WRITE_FINDING for this file
            fp_norm = file_path.strip().replace('\\', '/').lower()
            read_match = any(fp_norm in r or r in fp_norm for r in files_read)
            if not read_match:
                print(f'[agent] WRITE_FINDING rejected — {file_path} not yet read this strategy.', flush=True)
                tool_calls -= 1  # don't count this as a valid call
                conversation.append(HumanMessage(content=f'REJECTED: You must call READ_FILE({file_path}) before reporting a finding for it. GREP results are not enough — read the actual code first, confirm the issue exists, then call WRITE_FINDING.'))
                continue
            # Deduplicate: skip if same file + first 60 chars of description already seen
            dedup_key = (file_path.strip(), description.strip()[:60].lower())
            if dedup_key in seen_keys:
                print(f'[agent] Duplicate finding skipped: {file_path}', flush=True)
                conversation.append(HumanMessage(content=f'That finding was already recorded. Please investigate a DIFFERENT file or issue.'))
            else:
                seen_keys.add(dedup_key)
                f = Finding(severity, category, file_path, description)
                findings.append(f)  # writes to CSV immediately
                print(f'AGENT_FINDING:{json.dumps(f.to_dict())}', flush=True)
                _emit_progress(strategy=strategy, tool_calls=tool_calls, findings=len(findings))
                conversation.append(HumanMessage(content=f'Finding recorded: [{f.severity}] {f.description}. Good work! Now keep investigating — there may be more issues. Use GREP, READ_FILE, or LIST_FILES to explore further. Do NOT say DONE yet.'))
        else:
            print(f'[agent] Tool call: {tool_name}({", ".join(tool_args[:2])})', flush=True)
            # Track which files have been read this strategy
            if tool_name == 'READ_FILE' and tool_args:
                read_path = tool_args[0].split(',')[0].strip().replace('\\', '/').lower()
                files_read.add(read_path)
            result = _run_tool(tool_name, tool_args)
            # SEARCH_CODE results embed the file path in the page_content header — extract them
            if tool_name == 'SEARCH_CODE':
                for hit in re.findall(r'#\s+([\w./\-]+)\s+—', result):
                    files_read.add(hit.lower())
            # Truncate very long results
            if len(result) > 3000:
                result = result[:3000] + '\n... (truncated)'
            conversation.append(HumanMessage(content=f'Tool result:\n{result}'))
            _emit_progress(strategy=strategy, tool_calls=tool_calls, findings=len(findings))

    return tool_calls


# ── Report generator ──────────────────────────────────────────────────────────

def cluster_findings(findings: list) -> list:
    """Merge similar findings within the same severity+category into clusters.

    Two findings are "similar" when their description keyword sets overlap
    by Jaccard ≥ 0.25 (about 1-in-4 meaningful words in common).
    Groups of MIN_CLUSTER (3) or more are merged into a single cluster Finding;
    smaller groups are kept as-is.
    """
    from collections import defaultdict as _dd

    _STOP = {
        'a', 'an', 'the', 'in', 'on', 'at', 'to', 'of', 'and', 'or',
        'is', 'are', 'was', 'be', 'by', 'for', 'with', 'this', 'that',
        'it', 'as', 'from', 'via', 'not', 'no', 'may', 'can', 'could',
        'should', 'will', 'has', 'have', 'its', 'but', 'also', 'into',
        'when', 'if', 'than', 'line', 'file', 'code', 'class', 'method',
        'function', 'variable', 'value', 'string', 'type', 'object',
        'return', 'used', 'using', 'call', 'calls', 'called',
    }

    def _kw(text: str) -> frozenset:
        words = re.findall(r'[a-zA-Z_]\w*', text.lower())
        return frozenset(w for w in words if len(w) > 3 and w not in _STOP)

    def _jaccard(a: frozenset, b: frozenset) -> float:
        union = len(a | b)
        return len(a & b) / union if union else 0.0

    THRESHOLD  = 0.25
    MIN_CLUSTER = 3

    buckets: dict = _dd(list)
    for f in findings:
        buckets[(f.severity, f.category)].append(f)

    result = []
    for (sev, cat), group in buckets.items():
        if len(group) < MIN_CLUSTER:
            result.extend(group)
            continue

        kw_list = [_kw(f.description) for f in group]
        clusters: list[list[int]] = []

        for i, kw in enumerate(kw_list):
            placed = False
            for cl in clusters:
                if _jaccard(kw, kw_list[cl[0]]) >= THRESHOLD:
                    cl.append(i)
                    placed = True
                    break
            if not placed:
                clusters.append([i])

        for cl in clusters:
            members = [group[i] for i in cl]
            if len(members) < MIN_CLUSTER:
                result.extend(members)
                continue

            files = list(dict.fromkeys(m.file for m in members))
            rep   = members[0]
            file_list = ', '.join(f'`{f}`' for f in files)
            merged_desc = (
                f'[CLUSTER · {len(members)} occurrences in {len(files)} file(s)] '
                f'{rep.description} — also in: {file_list}'
            )
            merged    = Finding(rep.severity, rep.category, files[0], merged_desc)
            merged.ts = rep.ts
            result.append(merged)
            print(f'[cluster] {sev}/{cat}: merged {len(members)} findings → 1 cluster', flush=True)

    return result


def generate_report(findings: list[Finding], strategies_run: list[str], elapsed: float) -> str:
    """Generate a Markdown report from findings."""
    now  = datetime.now().strftime('%Y-%m-%d %H:%M')
    repo = os.path.basename(REPO_PATH)

    # Group by severity
    by_severity = {'CRITICAL': [], 'HIGH': [], 'MEDIUM': [], 'LOW': []}
    for f in findings:
        by_severity.setdefault(f.severity, []).append(f)

    lines = [
        f'---',
        f'generated: {now}',
        f'repo: {repo}',
        f'findings: {len(findings)}',
        f'duration_min: {round(elapsed / 60, 1)}',
        f'strategies: {", ".join(strategies_run)}',
        f'---',
        f'',
        f'# Agent Analysis Report — {repo}',
        f'',
        f'**Generated:** {now}  ',
        f'**Repository:** `{REPO_PATH}`  ',
        f'**Analysis duration:** {round(elapsed/60, 1)} minutes  ',
        f'**Strategies:** {", ".join(strategies_run)}  ',
        f'**Total findings:** {len(findings)}',
        f'',
        f'## Summary',
        f'',
        f'| Severity | Count |',
        f'|----------|-------|',
    ]

    for sev in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
        count = len(by_severity.get(sev, []))
        if count:
            lines.append(f'| {sev} | {count} |')

    lines += ['', '---', '']

    for sev in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
        group = by_severity.get(sev, [])
        if not group:
            continue
        lines.append(f'## {sev} Findings')
        lines.append('')
        for i, f in enumerate(group, 1):
            lines.append(f'### {i}. [{f.category.upper()}] {f.description[:80]}')
            lines.append(f'')
            lines.append(f'- **File:** `{f.file}`')
            lines.append(f'- **Category:** {f.category}')
            lines.append(f'- **Detected at:** {f.ts}')
            lines.append(f'')
            lines.append(f'> {f.description}')
            lines.append(f'')

    if not findings:
        lines.append('*No findings recorded during this analysis run.*')

    return '\n'.join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _emit_progress(strategy: str, tool_calls: int, findings: int):
    print(f'AGENT_PROGRESS:{json.dumps({"strategy": strategy, "tool_calls": tool_calls, "findings": findings})}', flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report-path', type=str, default='',
                        help='Override output path for the report')
    args = parser.parse_args()

    focus = STRATEGIES  # always all strategies

    print(f'[agent] Starting — mode: deep scan, budget: unlimited, strategies: {focus}', flush=True)
    print(f'[agent] Model: {LLM_MODEL}', flush=True)
    _emit_progress(strategy='init', tool_calls=0, findings=0)

    llm_timeout = _get_model_timeout(LLM_MODEL)
    print(f'[agent] LLM timeout: {llm_timeout}s (based on model size)', flush=True)
    model      = OllamaLLM(model=LLM_MODEL, temperature=0.1, timeout=llm_timeout)
    ts_str     = datetime.now().strftime('%Y%m%d_%H%M%S')
    tmp_csv    = os.path.join(REPORT_DIR, f'.findings_tmp_{ts_str}.csv')
    os.makedirs(REPORT_DIR, exist_ok=True)
    store      = FindingStore(tmp_csv)
    seen_keys  = set()
    start_time = time.time()

    run_deep_scan(model, store, seen_keys, focus, float('inf'), start_time)

    elapsed  = time.time() - start_time
    findings = cluster_findings(store.load())
    store.delete()
    report   = generate_report(findings, focus, elapsed)

    # Save report
    os.makedirs(REPORT_DIR, exist_ok=True)
    ts_str      = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_name = f'agent_report_{ts_str}.md'
    report_path = args.report_path or os.path.join(REPORT_DIR, report_name)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f'[agent] Report saved to: {report_path}', flush=True)
    print(f'AGENT_DONE:{json.dumps({"findings": len(findings), "report_path": report_path, "elapsed_min": round(elapsed/60, 1)})}', flush=True)  # findings list is small here (clustered)

    # Unload model from VRAM immediately (Ollama keep_alive=0)
    try:
        payload = json.dumps({'model': LLM_MODEL, 'keep_alive': 0}).encode()
        req = urllib.request.Request(
            'http://localhost:11434/api/generate',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        urllib.request.urlopen(req, timeout=5)
        print(f'[agent] Model unloaded from VRAM.', flush=True)
    except Exception as e:
        print(f'[agent] VRAM unload skipped: {e}', flush=True)


def run_deep_scan(model, findings: 'FindingStore', seen_keys: set, strategies: list,
                  budget_seconds: float, start_time: float):
    """Deep Scan mode: function-level code review with optional verification pass.

    Design:
    - Only scans actual source code files (not XML/YAML/HTML/CSS/config noise).
    - Split each file at top-level function/class boundaries → LLM always
      sees complete, self-contained units of code, never torn fragments.
    - File header (imports, globals) is prepended to every unit so the LLM
      knows the full module context.
    - Conservative prompt: requires specific line numbers, function names, and
      a concrete impact statement. Vague single-word findings are discarded.
    - After scanning all files, a lightweight verification pass re-shows each
      candidate finding alongside its source code and asks the LLM to confirm
      it is a genuine issue — eliminating false positives.
    """
    # Only scan actual programming language files — skip config/markup/build noise
    DEEP_SCAN_EXTENSIONS = {
        '.py', '.java', '.cs', '.ts', '.js', '.tsx', '.jsx',
        '.cpp', '.c', '.h', '.hpp', '.rs', '.go', '.kt', '.swift',
        '.rb', '.php', '.fs', '.vb', '.scala', '.groovy', '.clj',
        '.xtend', '.xtext',
        '.sh', '.bat', '.ps1',
        '.sql', '.graphql',
    }
    HEADER_ROWS  = 80    # lines prepended as context to every method unit
    MAX_UNIT     = 150   # max lines per function/class unit (smaller = faster LLM)
    MIN_DESC_LEN = 40    # discard one-liner non-specific descriptions shorter than this

    strat_text = '\n'.join(f'- {STRATEGY_DESCRIPTIONS[s]}' for s in strategies)

    system_prompt = (
        'You are a senior software engineer doing a professional code review.\n'
        'You will be shown one function, class, or module section at a time.\n\n'
        'Review for the following issue types:\n' + strat_text + '\n\n'
        'STRICT OUTPUT RULES:\n'
        '1. Only report issues you are highly confident are GENUINE bugs, security flaws, '
        'performance problems, or dangerous logic errors. Do NOT report style preferences, '
        'naming conventions, missing docstrings, or hypothetical edge-cases with no evidence.\n'
        '2. For each confirmed issue output EXACTLY one line:\n'
        '     WRITE_FINDING(<SEVERITY>, <category>, <file_path>, "<description>")\n'
        '   SEVERITY: CRITICAL | HIGH | MEDIUM | LOW\n'
        '3. Every description MUST contain:\n'
        '   • The line number (e.g. "Line 47:")\n'
        '   • The exact function or variable name\n'
        '   • What can go wrong (concrete impact, not a vague label)\n'
        '   Example: "Line 47: token = request.args[\'token\'] in validate() — '
        'KeyError if token missing, no default or try/except"\n'
        '4. Do NOT invent issues that are not visible in the provided code.\n'
        '5. If there are no genuine issues output exactly: NONE\n'
        '6. Output nothing else — no markdown, no explanation, no headers.'
    )

    _FIND_RE = re.compile(
        r'WRITE_FINDING\(\s*(\w+)\s*,\s*(\w+)\s*,\s*([^,]+?)\s*,\s*"([^"]+)"\s*\)',
        re.IGNORECASE,
    )

    # ── helpers ───────────────────────────────────────────────────────────────
    def _is_vague(desc: str) -> bool:
        """Reject single-word or very short findings the LLM emits as filler."""
        if len(desc) < MIN_DESC_LEN:
            return True
        vague_phrases = (
            'potential issue', 'possible bug', 'may cause', 'could be improved',
            'needs review', 'consider', 'TODO', 'fixme',
        )
        dl = desc.lower()
        return any(p in dl for p in vague_phrases) and len(desc) < 80

    def _parse_candidates(text: str, default_rel: str) -> list:
        candidates = []
        for m in _FIND_RE.finditer(text):
            severity = m.group(1).strip().upper()
            category = m.group(2).strip()
            ffile    = m.group(3).strip().strip('"\'') or default_rel
            desc     = m.group(4).strip()
            if _is_vague(desc):
                print(f'[deep] discarded vague: {desc[:60]}', flush=True)
                continue
            if severity not in ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW'):
                continue
            candidates.append((severity, category, ffile, desc))
        return candidates

    def _record(severity: str, category: str, ffile: str, desc: str) -> None:
        key = (ffile, desc[:60].lower())
        if key in seen_keys:
            return
        seen_keys.add(key)
        f_obj = Finding(severity, category, ffile, desc)
        findings.append(f_obj)  # writes to CSV immediately
        print(f'AGENT_FINDING:{json.dumps(f_obj.to_dict())}', flush=True)
        print(f'[deep] [{severity}] {ffile}: {desc[:100]}', flush=True)

    def _call_llm(user_msg: str, label: str) -> str | None:
        # Per-call timeout so a stuck LLM doesn't freeze the whole scan
        llm_timeout = _get_model_timeout(LLM_MODEL)
        for attempt in range(3):
            result_box: list = []
            exc_box:    list = []

            def _invoke():
                try:
                    result_box.append(str(model.invoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_msg),
                    ])).strip())
                except Exception as exc:
                    exc_box.append(exc)

            t = threading.Thread(target=_invoke, daemon=True)
            print(f'[agent] Querying LLM… ({label})', flush=True)
            t.start()
            t.join(timeout=llm_timeout)
            if t.is_alive():
                print(f'[deep] LLM timeout after {llm_timeout}s on {label} (attempt {attempt+1}/3) — skipping', flush=True)
                # Don't retry a timed-out call — move on
                return None
            if exc_box:
                wait = 2 ** attempt
                print(f'[deep] LLM error (attempt {attempt+1}/3): {exc_box[0]} — retry in {wait}s', flush=True)
                time.sleep(wait)
                continue
            return result_box[0] if result_box else None
        return None

    def _verify_finding(severity: str, category: str, ffile: str, desc: str,
                        code_snippet: str) -> bool:
        """Ask the LLM to confirm a candidate finding is genuine."""
        verify_prompt = (
            f'File: {ffile}\n\nCode:\n```\n{code_snippet}\n```\n\n'
            f'Candidate finding: [{severity}] {desc}\n\n'
            'Is this a GENUINE issue clearly visible in the code above?\n'
            'Answer with YES or NO on the first line, then optionally one sentence of reasoning.'
        )
        llm_timeout = _get_model_timeout(LLM_MODEL)
        result_box: list = []

        def _invoke():
            try:
                result_box.append(str(model.invoke([
                    SystemMessage(content='You are a code review validator. Be strict — only confirm real issues.'),
                    HumanMessage(content=verify_prompt),
                ])).strip())
            except Exception:
                pass

        print(f'[agent] Querying LLM… (verify)', flush=True)
        t = threading.Thread(target=_invoke, daemon=True)
        t.start()
        t.join(timeout=llm_timeout)
        if t.is_alive() or not result_box:
            return True  # timeout or error → keep the finding
        return result_box[0].upper().startswith('YES')

    # ── Collect all code files ────────────────────────────────────────────────
    all_files: list = []
    for root, dirs, files in os.walk(REPO_PATH):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for fname in sorted(files):
            if any(fname.endswith(e) for e in DEEP_SCAN_EXTENSIONS):
                all_files.append(os.path.join(root, fname))

    total_files = len(all_files)
    print(f'[deep] {total_files} source files to analyse (config/markup skipped)', flush=True)

    # ── Phase 1: scan ─────────────────────────────────────────────────────────
    call_no    = 0
    candidates: list = []   # (severity, category, ffile, desc, code_snippet)

    for file_idx, fpath in enumerate(all_files):
        if time.time() - start_time > budget_seconds:
            print('[deep] Time budget reached during scan — stopping.', flush=True)
            break

        rel = os.path.relpath(fpath, REPO_PATH).replace('\\', '/')
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                all_lines = fh.readlines()
        except OSError:
            continue

        total_lines = len(all_lines)
        if total_lines == 0:
            continue

        _emit_progress(strategy=f'deep:{rel}', tool_calls=call_no, findings=len(findings))
        print(f'[deep] [{file_idx+1}/{total_files}] {rel} ({total_lines} lines)', flush=True)

        header_end   = min(HEADER_ROWS, total_lines)
        header_block = ''.join(all_lines[:header_end])
        units        = split_file_into_units(all_lines, fpath, MAX_UNIT)

        for unit in units:
            if time.time() - start_time > budget_seconds:
                break

            u_start, u_end = unit.start, unit.end
            unit_lines = all_lines[u_start:u_end]
            call_no   += 1

            # Prepend the file header as context so LLM sees imports/globals
            unit_label = f' [{unit.label}]' if unit.label else ''
            if u_start >= header_end:
                preamble = (
                    f'=== {rel} — module header (lines 1–{header_end}, for context) ===\n'
                    + header_block
                    + f'=== {unit_label.strip() or "section"} to review: lines {u_start+1}–{u_end} ===\n'
                )
            else:
                preamble = f'=== {rel}{unit_label} — lines {u_start+1}–{u_end} ===\n'

            user_msg = preamble + ''.join(unit_lines)
            label    = f'{rel}:{u_start+1}-{u_end}'
            _emit_progress(strategy=f'deep:{rel}', tool_calls=call_no, findings=len(findings))

            resp = _call_llm(user_msg, label)
            if not resp or resp.upper() == 'NONE':
                continue

            for (sev, cat, ff, desc) in _parse_candidates(resp, rel):
                # store snippet for verification (surrounding ±5 lines in file)
                snip_start = max(0, u_start - 5)
                snip_end   = min(total_lines, u_end + 5)
                snippet    = ''.join(all_lines[snip_start:snip_end])
                candidates.append((sev, cat, ff, desc, snippet))

    # ── Phase 1.5: cross-file context ────────────────────────────────────────
    # For each CRITICAL/HIGH candidate, look up callers via dep_graph and scan
    # the units in those files that actually call the flagged function.
    _cross_scanned: set = set()   # (abs_path, unit_start) already scanned

    def _extract_symbol(desc: str) -> str:
        """Pull the most likely function/class name out of a finding description."""
        # Look for word() pattern first — most specific
        m = re.search(r'\b([A-Za-z_]\w+)\s*\(', desc)
        if m:
            return m.group(1)
        # Fall back to CamelCase word
        m = re.search(r'\b([A-Z][a-z]+[A-Za-z0-9]+)\b', desc)
        if m:
            return m.group(1)
        return ''

    high_critical = [(sev, cat, ff, desc, snip)
                     for (sev, cat, ff, desc, snip) in candidates
                     if sev in ('CRITICAL', 'HIGH')]

    if high_critical:
        print(f'[deep] Cross-file scan: checking callers for {len(high_critical)} CRITICAL/HIGH candidates…', flush=True)

    for (sev, cat, ff, desc, _snip) in high_critical:
        if time.time() - start_time > budget_seconds:
            break

        symbol = _extract_symbol(desc)
        if not symbol:
            continue

        caller_files = get_dependents(symbol)
        if not caller_files:
            continue

        print(f'[deep] [{symbol}] {len(caller_files)} caller file(s) to check', flush=True)

        for caller_rel in caller_files[:5]:   # cap at 5 callers per finding
            if time.time() - start_time > budget_seconds:
                break

            caller_abs = os.path.join(REPO_PATH, caller_rel.replace('/', os.sep))
            if not os.path.isfile(caller_abs):
                continue

            try:
                with open(caller_abs, encoding='utf-8', errors='ignore') as fh:
                    caller_lines = fh.readlines()
            except OSError:
                continue

            caller_units = split_file_into_units(caller_lines, caller_abs, MAX_UNIT)
            caller_total = len(caller_lines)
            caller_header_end = min(HEADER_ROWS, caller_total)
            caller_header = ''.join(caller_lines[:caller_header_end])

            for cu in caller_units:
                if (caller_abs, cu.start) in _cross_scanned:
                    continue

                # Only scan units that actually reference the flagged symbol
                unit_text = ''.join(caller_lines[cu.start:cu.end])
                if symbol not in unit_text:
                    continue

                _cross_scanned.add((caller_abs, cu.start))
                call_no += 1

                cx_label = f'{caller_rel}:{cu.start+1}-{cu.end}'
                unit_label = f' [{cu.label}]' if cu.label else ''
                if cu.start >= caller_header_end:
                    preamble = (
                        f'=== {caller_rel} — module header (lines 1–{caller_header_end}, for context) ===\n'
                        + caller_header
                        + f'=== CALLER of {symbol}{unit_label}: lines {cu.start+1}–{cu.end} ===\n'
                        + f'[Context: this code calls {symbol} which has a known {sev} issue: {desc[:120]}]\n'
                    )
                else:
                    preamble = (
                        f'=== {caller_rel}{unit_label} — lines {cu.start+1}–{cu.end} ===\n'
                        + f'[Context: this code calls {symbol} which has a known {sev} issue: {desc[:120]}]\n'
                    )

                _emit_progress(strategy=f'deep:xref:{caller_rel}', tool_calls=call_no, findings=len(findings))
                resp = _call_llm(preamble + unit_text, cx_label)
                if not resp or resp.upper() == 'NONE':
                    continue

                for (xsev, xcat, xff, xdesc) in _parse_candidates(resp, caller_rel):
                    snip_start = max(0, cu.start - 5)
                    snip_end   = min(caller_total, cu.end + 5)
                    snippet    = ''.join(caller_lines[snip_start:snip_end])
                    candidates.append((xsev, xcat, xff, xdesc, snippet))

    # ── Phase 2: verify ───────────────────────────────────────────────────────
    # Only verify MEDIUM/LOW — CRITICAL and HIGH are recorded directly
    print(f'[deep] Scan done — {len(candidates)} candidates, starting verification…', flush=True)
    for (sev, cat, ff, desc, snippet) in candidates:
        if time.time() - start_time > budget_seconds:
            print('[deep] Budget reached during verification — recording remaining as-is.', flush=True)
            _record(sev, cat, ff, desc)
            continue

        if sev in ('CRITICAL', 'HIGH'):
            _record(sev, cat, ff, desc)
        else:
            confirmed = _verify_finding(sev, cat, ff, desc, snippet)
            if confirmed:
                _record(sev, cat, ff, desc)
            else:
                print(f'[deep] rejected by verification: {desc[:60]}', flush=True)

    print(f'[deep] Done — {call_no} LLM calls, {len(findings)} confirmed findings.', flush=True)



def _get_model_timeout(model_name: str) -> int:

    """Query Ollama for the model size and return an appropriate timeout in seconds.

    Thresholds (unquantized parameter count estimate via file size):
      < 4 GB  → small  (e.g. 3b / 7b Q4) → 60 s
      < 10 GB → medium (e.g. 14b Q4)     → 120 s
      < 20 GB → large  (e.g. 32b Q4)     → 240 s
      >= 20 GB → xlarge (e.g. 70b+)       → 360 s
    Falls back to 180 s if Ollama is unreachable.
    """
    try:
        req = urllib.request.Request('http://localhost:11434/api/tags')
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        for m in data.get('models', []):
            if m.get('name') == model_name:
                size_gb = m.get('size', 0) / 1_073_741_824
                if size_gb < 4:
                    return 60
                elif size_gb < 10:
                    return 120
                elif size_gb < 20:
                    return 240
                else:
                    return 360
    except Exception:
        pass
    return 180  # fallback


if __name__ == '__main__':
    main()
