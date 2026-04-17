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
import argparse
import textwrap
import urllib.request
from datetime import datetime
from pathlib import Path

from langchain_ollama.llms import OllamaLLM
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from config import REPO_PATH, VAULT_PATH, LLM_MODEL, SKIP_DIRS, CODE_EXTENSIONS
from dep_graph import load_graph, get_dependents, get_class_info

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


def tool_read_file(relative_path: str, max_lines: int = 120) -> str:
    """Read a file from the repository (relative path from repo root)."""
    fpath = os.path.join(REPO_PATH, relative_path.replace('/', os.sep))
    if not os.path.exists(fpath):
        return f'File not found: {relative_path}'
    try:
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            half = max_lines // 2
            snippet = lines[:half] + [f'\n... ({len(lines) - max_lines} lines omitted) ...\n'] + lines[-half:]
        else:
            snippet = lines
        return f'// {relative_path}\n' + ''.join(snippet)
    except OSError as e:
        return f'Cannot read file: {e}'


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

GREP(<pattern>, <extension>)
  Search all files with given extension for a regex pattern.
  Example: GREP(password.*=.*"[^"]+", .py)

READ_FILE(<relative_path>)
  Read a source file from the repository.
  Example: READ_FILE(src/auth/LoginService.java)

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

# ── LLM interaction ───────────────────────────────────────────────────────────

_AGENT_SYSTEM = """You are an expert software architect performing an autonomous code analysis.
You have access to tools to explore a large codebase (Java, Python, TypeScript, C#, Go, and others).
Your task is to find real issues in the code for the category: {category}.
{category_desc}

{tools}

WORKFLOW:
1. Use SEARCH_NOTES to find relevant classes for this category.
2. Use READ_FILE, GREP, GET_CLASS_INFO, GET_DEPENDENTS to investigate further.
3. For each real issue found, call WRITE_FINDING immediately.
4. Continue investigating until you say DONE.

RULES:
- Call exactly ONE tool per message. Wait for the result before continuing.
- Be concise. Only investigate files relevant to the category.
- Only call WRITE_FINDING for real, specific issues with a file path.
- When you have investigated enough (at least 5 tool calls), output: DONE

Start by searching for relevant code.
"""

_TOOL_CALL_RE = re.compile(
    r'(SEARCH_NOTES|GREP|READ_FILE|GET_DEPENDENTS|GET_CLASS_INFO|WRITE_FINDING)\((.+?)\)',
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
    if name == 'GREP':
        pattern = args[0] if args else ''
        ext     = args[1].strip() if len(args) > 1 else ''
        return tool_grep(pattern, ext)
    if name == 'READ_FILE':
        return tool_read_file(args[0] if args else '')
    if name == 'GET_DEPENDENTS':
        return tool_get_dependents(args[0] if args else '')
    if name == 'GET_CLASS_INFO':
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

def run_strategy(strategy: str, model, findings: list, budget_seconds: float, start_time: float) -> int:
    """Run one analysis strategy. Returns number of tool calls made."""
    desc       = STRATEGY_DESCRIPTIONS[strategy]
    system_msg = SystemMessage(content=_AGENT_SYSTEM.format(
        category=strategy,
        category_desc=desc,
        tools=TOOLS_DESCRIPTION,
    ))

    tool_calls        = 0
    min_calls         = 3   # must make at least this many tool calls before DONE is accepted
    max_calls         = _SETTINGS.get('max_calls', 25)   # cap per strategy
    conversation      = []   # list of HumanMessage / AIMessage
    findings_at_start = len(findings)
    consecutive_done  = 0
    last_tool_sig     = None   # (tool_name, first_arg) — repeat detection
    repeat_count      = 0

    print(f'\n[agent] === Strategy: {strategy.upper()} ===', flush=True)
    _emit_progress(strategy=strategy, tool_calls=0, findings=len(findings))

    while tool_calls < max_calls:
        # Check time budget
        if time.time() - start_time > budget_seconds:
            print(f'[agent] Time budget exhausted during {strategy}', flush=True)
            break

        # Build message list directly — avoids ChatPromptTemplate interpreting
        # { } in tool results as template variables
        prompt_messages = [system_msg] + conversation
        try:
            response = model.invoke(prompt_messages)
        except Exception as e:
            print(f'[agent] LLM error: {e}', flush=True)
            break

        response_text = str(response).strip()
        conversation.append(AIMessage(content=response_text))

        print(f'[agent] LLM ({tool_calls+1}/{max_calls}): {response_text[:300]}', flush=True)

        # Done?
        if 'DONE' in response_text and not _TOOL_CALL_RE.search(response_text):
            consecutive_done += 1
            new_findings = len(findings) - findings_at_start
            # Accept DONE if: enough tool calls, OR found something, OR stuck in DONE loop
            if tool_calls >= min_calls or new_findings > 0 or consecutive_done >= 2:
                print(f'[agent] Strategy {strategy} complete.', flush=True)
                break
            else:
                conversation.append(HumanMessage(content=f'You said DONE too early. You must make at least {min_calls} tool calls first. Please continue investigating.'))
                continue
        consecutive_done = 0

        # Parse tool call
        tool_name, tool_args = _parse_tool_call(response_text)
        if not tool_name:
            # No valid tool call — prompt again
            conversation.append(HumanMessage(content='Please call exactly one tool from the list, or say DONE if finished.'))
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
            f = Finding(severity, category, file_path, description)
            findings.append(f)
            print(f'AGENT_FINDING:{json.dumps(f.to_dict())}', flush=True)
            _emit_progress(strategy=strategy, tool_calls=tool_calls, findings=len(findings))
            conversation.append(HumanMessage(content=f'Finding recorded: [{f.severity}] {f.description}'))
        else:
            print(f'[agent] Tool call: {tool_name}({", ".join(tool_args[:2])})', flush=True)
            result = _run_tool(tool_name, tool_args)
            # Truncate very long results
            if len(result) > 3000:
                result = result[:3000] + '\n... (truncated)'
            conversation.append(HumanMessage(content=f'Tool result:\n{result}'))
            _emit_progress(strategy=strategy, tool_calls=tool_calls, findings=len(findings))

    return tool_calls


# ── Report generator ──────────────────────────────────────────────────────────

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
    parser.add_argument('--budget-minutes', type=float, default=60.0,
                        help='Time budget in minutes (default: 60)')
    parser.add_argument('--focus', type=str, default='all',
                        help=f'Comma-separated strategies or "all". Options: {", ".join(STRATEGIES)}')
    parser.add_argument('--report-path', type=str, default='',
                        help='Override output path for the report')
    parser.add_argument('--max-calls', type=int, default=25,
                        help='Max LLM tool-calls per strategy (default: 25)')
    parser.add_argument('--grep-limit', type=int, default=50,
                        help='Max grep results per search (default: 50)')
    parser.add_argument('--notes-k', type=int, default=8,
                        help='Number of vault notes retrieved per SEARCH_NOTES call (default: 8)')
    args = parser.parse_args()

    # Populate runtime settings
    _SETTINGS['max_calls']  = args.max_calls
    _SETTINGS['grep_limit'] = args.grep_limit
    _SETTINGS['notes_k']    = args.notes_k

    budget_seconds = args.budget_minutes * 60 if args.budget_minutes > 0 else float('inf')
    _no_budget = args.budget_minutes == 0
    focus = [s.strip() for s in args.focus.split(',')] if args.focus != 'all' else STRATEGIES
    focus = [s for s in focus if s in STRATEGIES]
    if not focus:
        focus = STRATEGIES

    print(f'[agent] Starting — budget: {"unlimited (full scan)" if args.budget_minutes == 0 else str(args.budget_minutes) + " min"}, strategies: {focus}', flush=True)
    print(f'[agent] Settings — max_calls: {args.max_calls}, grep_limit: {args.grep_limit}, notes_k: {args.notes_k}', flush=True)
    print(f'[agent] Model: {LLM_MODEL}', flush=True)
    _emit_progress(strategy='init', tool_calls=0, findings=0)

    model      = OllamaLLM(model=LLM_MODEL, temperature=0.1)
    findings   = []
    start_time = time.time()

    for strategy in focus:
        if time.time() - start_time > budget_seconds:
            print('[agent] Time budget reached — stopping early.', flush=True)
            break
        remaining = budget_seconds - (time.time() - start_time)
        run_strategy(strategy, model, findings, budget_seconds, start_time)

    elapsed = time.time() - start_time
    report  = generate_report(findings, focus, elapsed)

    # Save report
    os.makedirs(REPORT_DIR, exist_ok=True)
    ts_str      = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_name = f'agent_report_{ts_str}.md'
    report_path = args.report_path or os.path.join(REPORT_DIR, report_name)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f'[agent] Report saved to: {report_path}', flush=True)
    print(f'AGENT_DONE:{json.dumps({"findings": len(findings), "report_path": report_path, "elapsed_min": round(elapsed/60, 1)})}', flush=True)

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


if __name__ == '__main__':
    main()
