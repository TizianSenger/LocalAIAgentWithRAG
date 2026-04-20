"""
code_units.py
-------------
Shared logic for splitting source files into method/function-level units.

Used by both agent.py (deep scan chunking) and vector.py (code indexing).

Each unit is a CodeUnit namedtuple:
  - start:   0-based start line index (inclusive)
  - end:     0-based end line index (exclusive)
  - name:    function/method name ('' for file header or fallback chunks)
  - cls:     class name if inside a class ('' otherwise)
  - label:   human-readable 'ClassName.method_name' or 'function_name'
"""

import ast
import re
from typing import NamedTuple


class CodeUnit(NamedTuple):
    start: int   # 0-based, inclusive
    end:   int   # 0-based, exclusive
    name:  str   # function / method name
    cls:   str   # enclosing class name ('' if top-level)
    label: str   # display label, e.g. 'MyClass.my_method'


# ── Python: AST-based extraction ──────────────────────────────────────────────

class _PyVisitor(ast.NodeVisitor):
    """Extract every function / method at any nesting level via AST."""

    def __init__(self, lines: list[str]):
        self._lines    = lines
        self._cls_stk: list[str] = []
        self.units: list[CodeUnit] = []

    def _add(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        start = node.lineno - 1                        # 0-based
        end   = getattr(node, 'end_lineno', min(start + 200, len(self._lines)))
        cls   = self._cls_stk[-1] if self._cls_stk else ''
        label = f'{cls}.{node.name}' if cls else node.name
        self.units.append(CodeUnit(start=start, end=end,
                                   name=node.name, cls=cls, label=label))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._cls_stk.append(node.name)
        self.generic_visit(node)
        self._cls_stk.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add(node)
        # Do NOT recurse — skip nested functions inside methods

    visit_AsyncFunctionDef = visit_FunctionDef


def _split_python(lines: list[str], max_unit: int) -> list[CodeUnit]:
    """AST-based split for Python files. Falls back to heuristic on SyntaxError."""
    source = ''.join(lines)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _split_heuristic(lines, max_unit)

    v = _PyVisitor(lines)
    v.visit(tree)

    if not v.units:
        # File has no functions (e.g. pure-config module) → one chunk
        return [CodeUnit(0, len(lines), '', '', '')]

    # Hard-split any unit that exceeds max_unit
    result: list[CodeUnit] = []
    for u in v.units:
        if u.end - u.start <= max_unit:
            result.append(u)
        else:
            for chunk_start in range(u.start, u.end, max_unit):
                chunk_end = min(chunk_start + max_unit, u.end)
                result.append(CodeUnit(chunk_start, chunk_end, u.name, u.cls, u.label))
    return result


# ── Generic: indentation-heuristic split ─────────────────────────────────────

_TOP_LEVEL_RE = re.compile(
    r'^(?:(?:public|private|protected|static|abstract|async|override|'
    r'sealed|internal|extern|virtual|readonly)\s+)*'
    r'(?:async\s+)?'
    r'(?:function\s+\w|def\s+\w|sub\s+\w|'
    r'(?:[a-zA-Z_]\w*(?:<[^>]+>)?)\s+[a-zA-Z_]\w*\s*\()',
)

_STARTERS = ('def ', 'async def ', 'class ', 'function ', 'public ', 'private ',
             'protected ', 'static ', '# %%')


def _split_heuristic(lines: list[str], max_unit: int) -> list[CodeUnit]:
    """Indentation-based split for non-Python files (JS/TS/CS/Java/etc.)."""
    units: list[CodeUnit] = []
    unit_start = 0
    current_name = ''

    for i in range(len(lines)):
        stripped = lines[i].lstrip()
        indent   = len(lines[i]) - len(stripped)
        is_boundary = (
            indent == 0
            and any(stripped.startswith(s) for s in _STARTERS)
            and i > unit_start + 3
        )
        if is_boundary or (i - unit_start) >= max_unit:
            if i > unit_start:
                units.append(CodeUnit(unit_start, i, current_name, '', current_name))
            unit_start   = i
            current_name = stripped.split('(')[0].split()[-1] if stripped else ''

    if unit_start < len(lines):
        units.append(CodeUnit(unit_start, len(lines), current_name, '', current_name))

    # Hard-split oversized units
    result: list[CodeUnit] = []
    for u in units:
        if u.end - u.start <= max_unit:
            result.append(u)
        else:
            for chunk_start in range(u.start, u.end, max_unit):
                chunk_end = min(chunk_start + max_unit, u.end)
                result.append(CodeUnit(chunk_start, chunk_end, u.name, u.cls, u.label))
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def split_file_into_units(lines: list[str], filepath: str,
                          max_unit: int = 150) -> list[CodeUnit]:
    """
    Split a source file into logical code units (functions/methods).

    Args:
        lines:     File lines as a list of strings (with newlines).
        filepath:  Used only to detect file extension (.py → AST, else heuristic).
        max_unit:  Maximum number of lines per unit (hard cap).

    Returns:
        List of CodeUnit namedtuples sorted by start line.
    """
    ext = filepath.rsplit('.', 1)[-1].lower() if '.' in filepath else ''
    if ext == 'py':
        units = _split_python(lines, max_unit)
    else:
        units = _split_heuristic(lines, max_unit)

    return sorted(units, key=lambda u: u.start)
