"""
dep_graph.py
------------
Parses source files in REPO_PATH across multiple languages and builds a
dependency graph:

  {
    "LoginService": {
      "file":        "src/auth/LoginService.java",
      "fqn":         "com.example.auth.LoginService",
      "package":     "com.example.auth",
      "type":        "class",   # class | interface | enum | struct | ...
      "extends":     "BaseService",
      "implements":  ["UserDetailsService"],
      "imports":     ["UserRepository"],
      "annotations": ["Service"],
      "injected":    ["UserRepository"]
    },
    ...
    "_dependents": {          # reverse index: who uses symbol X
      "UserRepository": ["LoginService", "UserService"],
      ...
    }
  }

Supported languages: Java/Kotlin/Groovy/Scala, Python, TypeScript/JavaScript, C#, Go.
The graph is saved to VAULT_PATH/.dep_graph.json and loaded lazily by chat_api.py.
"""

import os
import re
import json
from pathlib import Path

from config import REPO_PATH, VAULT_PATH, SKIP_DIRS

GRAPH_FILE = os.path.join(VAULT_PATH, '.dep_graph.json')

# ── Language groups ──────────────────────────────────────────────────────────
_JVM_EXTS  = {'.java', '.kt', '.groovy', '.scala'}
_PY_EXTS   = {'.py'}
_TS_EXTS   = {'.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs'}
_CS_EXTS   = {'.cs'}
_GO_EXTS   = {'.go'}
_SWIFT_EXTS = {'.swift'}
_CODE_EXTS = _JVM_EXTS | _PY_EXTS | _TS_EXTS | _CS_EXTS | _GO_EXTS | _SWIFT_EXTS | {'.rb', '.php'}

# ── JVM regex patterns ────────────────────────────────────────────────────────
_RE_JVM_PACKAGE   = re.compile(r'^\s*package\s+([\w.]+)\s*;', re.MULTILINE)
_RE_JVM_IMPORT    = re.compile(r'^\s*import\s+(?:static\s+)?([\w.]+)\s*;', re.MULTILINE)
_RE_JVM_CLASS     = re.compile(
    r'\b(class|interface|enum|@interface)\s+(\w+)'
    r'(?:\s+extends\s+([\w<>,\s]+?))?'
    r'(?:\s+implements\s+([\w<>,\s]+?))?'
    r'\s*[{<]',
    re.MULTILINE,
)
_RE_JVM_AUTOWIRED = re.compile(
    r'@(?:Autowired|Inject|Resource)[^\n]*\n\s*(?:(?:private|protected|public)\s+)?'
    r'(?:final\s+)?(\w+)(?:<[^>]+>)?\s+\w+\s*[;=\n]',
    re.MULTILINE,
)
_RE_JVM_ANNOTATION = re.compile(r'@(\w+)', re.MULTILINE)
_SPRING_ANNOTATIONS = {
    'Service', 'Component', 'Repository', 'Controller', 'RestController',
    'Configuration', 'Bean', 'Transactional', 'Scheduled', 'EventListener',
    'MessageMapping', 'RequestMapping', 'GetMapping', 'PostMapping',
    'PutMapping', 'DeleteMapping', 'PatchMapping',
}

# ── Python regex patterns ─────────────────────────────────────────────────────
_RE_PY_CLASS   = re.compile(r'^class\s+(\w+)\s*(?:\(([^)]+)\))?\s*:', re.MULTILINE)
_RE_PY_IMPORT  = re.compile(r'^\s*(?:from\s+([\w.]+)\s+)?import\s+([\w,\s.]+)', re.MULTILINE)

# ── TypeScript/JavaScript regex patterns ──────────────────────────────────────
_RE_TS_CLASS   = re.compile(
    r'(?:export\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+(\w+)'
    r'(?:\s+extends\s+([\w<>,\s]+?))?'
    r'(?:\s+implements\s+([\w<>,\s]+?))?'
    r'\s*[{<]',
    re.MULTILINE,
)
_RE_TS_IMPORT  = re.compile(r"import\s+.*?from\s+['\"]([^'\"]+)['\"];", re.MULTILINE)
_RE_TS_DECORATOR = re.compile(r'@(\w+)', re.MULTILINE)

# ── C# regex patterns ─────────────────────────────────────────────────────────
_RE_CS_NS      = re.compile(r'^\s*namespace\s+([\w.]+)', re.MULTILINE)
_RE_CS_IMPORT  = re.compile(r'^\s*using\s+([\w.]+)\s*;', re.MULTILINE)
_RE_CS_CLASS   = re.compile(
    r'\b(class|interface|enum|struct|record)\s+(\w+)'
    r'(?:\s*:\s*([\w<>,\s]+?))?'
    r'\s*[{<]',
    re.MULTILINE,
)

# ── Go regex patterns ─────────────────────────────────────────────────────────
_RE_GO_PKG     = re.compile(r'^package\s+(\w+)', re.MULTILINE)
_RE_GO_STRUCT  = re.compile(r'^type\s+(\w+)\s+(struct|interface)', re.MULTILINE)
_RE_GO_IMPORT  = re.compile(r'"([\w./]+)"', re.MULTILINE)


def _simple_name(fqn: str) -> str:
    """com.example.Foo -> Foo"""
    return fqn.rsplit('.', 1)[-1]


def _collect_source_files() -> list[str]:
    results = []
    for root, dirs, files in os.walk(REPO_PATH):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for fname in files:
            if Path(fname).suffix.lower() in _CODE_EXTS:
                results.append(os.path.join(root, fname))
    return sorted(results)


def _parse_jvm(src: str, relative: str) -> dict | None:
    pkg_m   = _RE_JVM_PACKAGE.search(src)
    package = pkg_m.group(1) if pkg_m else ''

    imports = list(dict.fromkeys(
        _simple_name(m.group(1))
        for m in _RE_JVM_IMPORT.finditer(src)
        if not m.group(1).endswith('*')
    ))

    cls_m = _RE_JVM_CLASS.search(src)
    if not cls_m:
        return None

    kind           = cls_m.group(1)
    class_name     = cls_m.group(2)
    extends_raw    = cls_m.group(3) or ''
    implements_raw = cls_m.group(4) or ''
    extends    = _simple_name(extends_raw.strip().split('<')[0]) if extends_raw.strip() else None
    implements = [_simple_name(s.strip().split('<')[0]) for s in implements_raw.split(',') if s.strip()]
    injected   = list(dict.fromkeys(m.group(1) for m in _RE_JVM_AUTOWIRED.finditer(src)))
    annotations = sorted({m.group(1) for m in _RE_JVM_ANNOTATION.finditer(src)} & _SPRING_ANNOTATIONS)
    fqn = f'{package}.{class_name}' if package else class_name

    return {
        'file': relative, 'fqn': fqn, 'package': package,
        'type': 'interface' if kind == 'interface' else 'enum' if kind == 'enum' else 'class',
        'extends': extends, 'implements': implements,
        'imports': imports, 'annotations': annotations, 'injected': injected,
    }


def _parse_python(src: str, relative: str) -> dict | None:
    cls_m = _RE_PY_CLASS.search(src)
    if not cls_m:
        return None

    class_name = cls_m.group(1)
    bases_raw  = cls_m.group(2) or ''
    bases      = [b.strip().split('.')[-1] for b in bases_raw.split(',') if b.strip()]
    extends    = bases[0] if bases else None
    implements = bases[1:] if len(bases) > 1 else []

    # imports: collect module names
    imports = []
    for m in _RE_PY_IMPORT.finditer(src):
        if m.group(1):  # from X import Y
            imports.append(m.group(1).split('.')[-1])
        else:
            for sym in m.group(2).split(','):
                imports.append(sym.strip().split('.')[-1])
    imports = list(dict.fromkeys(i for i in imports if i and i != '*'))

    # Module = file path without extension, dots as separator
    module = relative.replace('/', '.').replace('\\', '.').removesuffix('.py')

    return {
        'file': relative, 'fqn': module + '.' + class_name, 'package': module,
        'type': 'class', 'extends': extends, 'implements': implements,
        'imports': imports, 'annotations': [], 'injected': [],
    }


def _parse_typescript(src: str, relative: str) -> dict | None:
    cls_m = _RE_TS_CLASS.search(src)
    if not cls_m:
        return None

    class_name     = cls_m.group(1)
    extends_raw    = cls_m.group(2) or ''
    implements_raw = cls_m.group(3) or ''
    extends    = extends_raw.strip().split('<')[0] if extends_raw.strip() else None
    implements = [s.strip().split('<')[0] for s in implements_raw.split(',') if s.strip()]

    imports = list(dict.fromkeys(
        Path(m.group(1)).name.split('.')[0]
        for m in _RE_TS_IMPORT.finditer(src)
        if not m.group(1).startswith('@')
    ))
    annotations = list(dict.fromkeys(m.group(1) for m in _RE_TS_DECORATOR.finditer(src)))[:8]

    return {
        'file': relative, 'fqn': class_name, 'package': str(Path(relative).parent),
        'type': 'class', 'extends': extends, 'implements': implements,
        'imports': imports, 'annotations': annotations, 'injected': [],
    }


def _parse_csharp(src: str, relative: str) -> dict | None:
    cls_m = _RE_CS_CLASS.search(src)
    if not cls_m:
        return None

    kind       = cls_m.group(1)
    class_name = cls_m.group(2)
    bases_raw  = cls_m.group(3) or ''
    bases      = [b.strip().split('<')[0] for b in bases_raw.split(',') if b.strip()]
    extends    = bases[0] if bases else None
    implements = bases[1:] if len(bases) > 1 else []

    ns_m    = _RE_CS_NS.search(src)
    package = ns_m.group(1) if ns_m else ''
    imports = list(dict.fromkeys(
        m.group(1).split('.')[-1] for m in _RE_CS_IMPORT.finditer(src)
    ))
    fqn = f'{package}.{class_name}' if package else class_name
    annotations = list(dict.fromkeys(m.group(1) for m in re.finditer(r'\[(\w+)', src)))[:8]

    return {
        'file': relative, 'fqn': fqn, 'package': package,
        'type': kind, 'extends': extends, 'implements': implements,
        'imports': imports, 'annotations': annotations, 'injected': [],
    }


def _parse_go(src: str, relative: str) -> dict | None:
    struct_m = _RE_GO_STRUCT.search(src)
    if not struct_m:
        return None

    class_name = struct_m.group(1)
    kind       = struct_m.group(2)
    pkg_m      = _RE_GO_PKG.search(src)
    package    = pkg_m.group(1) if pkg_m else ''

    imports = list(dict.fromkeys(
        m.group(1).split('/')[-1] for m in _RE_GO_IMPORT.finditer(src)
    ))

    return {
        'file': relative, 'fqn': f'{package}.{class_name}', 'package': package,
        'type': kind, 'extends': None, 'implements': [],
        'imports': imports, 'annotations': [], 'injected': [],
    }


def _parse_file(fpath: str) -> dict | None:
    try:
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            src = f.read()
    except OSError:
        return None

    relative = os.path.relpath(fpath, REPO_PATH).replace('\\', '/')
    ext      = Path(fpath).suffix.lower()

    if ext in _JVM_EXTS:   return _parse_jvm(src, relative)
    if ext in _PY_EXTS:    return _parse_python(src, relative)
    if ext in _TS_EXTS:    return _parse_typescript(src, relative)
    if ext in _CS_EXTS:    return _parse_csharp(src, relative)
    if ext in _GO_EXTS:    return _parse_go(src, relative)
    return None


def build_graph(progress_cb=None) -> dict:
    """
    Scan REPO_PATH, parse all supported source files and build the dependency graph.
    progress_cb(done, total) is called after each file if provided.
    Returns the graph dict.
    """
    files = _collect_source_files()
    total = len(files)
    graph: dict[str, dict] = {}

    for i, fpath in enumerate(files, 1):
        info = _parse_file(fpath)
        if info:
            graph[info['file']] = info   # keyed by relative file path
        if progress_cb:
            progress_cb(i, total)

    # Build reverse index: _dependents[ClassName] = [list of files that use it]
    dependents: dict[str, list[str]] = {}
    for rel_path, info in graph.items():
        used = set(info['imports'] + info['injected'])
        if info['extends']:
            used.add(info['extends'])
        used.update(info['implements'])
        for dep in used:
            dependents.setdefault(dep, [])
            if rel_path not in dependents[dep]:
                dependents[dep].append(rel_path)

    graph['_dependents'] = dependents

    os.makedirs(os.path.dirname(GRAPH_FILE), exist_ok=True)
    with open(GRAPH_FILE, 'w', encoding='utf-8') as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)

    print(f'[dep_graph] Graph built: {len(graph)-1} symbols, saved to {GRAPH_FILE}')
    return graph


# ── Query helpers (used by chat_api.py) ──────────────────────────────────────

_cached_graph: dict | None = None


def load_graph() -> dict:
    global _cached_graph
    if _cached_graph is not None:
        return _cached_graph
    if os.path.exists(GRAPH_FILE):
        with open(GRAPH_FILE, 'r', encoding='utf-8') as f:
            _cached_graph = json.load(f)
    else:
        _cached_graph = {}
    return _cached_graph


def get_class_info(class_name: str) -> dict | None:
    """Return graph entry for a class by simple name or file path."""
    graph = load_graph()
    # Try by file path suffix
    for key, info in graph.items():
        if key == '_dependents':
            continue
        if isinstance(info, dict) and (
            info.get('fqn', '').endswith(class_name) or
            os.path.splitext(os.path.basename(info.get('file', '')))[0] == class_name
        ):
            return info
    return None


def get_dependents(class_name: str) -> list[str]:
    """Return list of relative file paths that depend on class_name."""
    graph = load_graph()
    return graph.get('_dependents', {}).get(class_name, [])


def build_dependency_context(source_files: list[str]) -> str:
    """
    Given a list of relative source file paths (from RAG results),
    return a compact dependency summary to inject into the chat context.
    """
    graph = load_graph()
    if not graph:
        return ''

    lines = []
    seen  = set()

    for rel_path in source_files:
        # Normalize path separators
        rel_norm = rel_path.replace('\\', '/')
        info = graph.get(rel_norm)
        if not info or not isinstance(info, dict):
            continue
        cls = info.get('fqn') or os.path.basename(rel_norm)
        simple = _simple_name(cls)
        if simple in seen:
            continue
        seen.add(simple)

        parts = [f'**{simple}** (`{rel_norm}`)']
        if info.get('extends'):
            parts.append(f"  - Extends: `{info['extends']}`")
        if info.get('implements'):
            parts.append(f"  - Implements: {', '.join(f'`{i}`' for i in info['implements'])}")
        if info.get('annotations'):
            parts.append(f"  - Annotations: {', '.join(f'@{a}' for a in info['annotations'])}")
        if info.get('injected'):
            parts.append(f"  - Injects: {', '.join(f'`{i}`' for i in info['injected'])}")

        # Who uses this class?
        users = get_dependents(simple)
        if users:
            user_names = [os.path.splitext(os.path.basename(u))[0] for u in users[:8]]
            suffix = f' (+{len(users)-8} more)' if len(users) > 8 else ''
            parts.append(f"  - Used by: {', '.join(f'`{n}`' for n in user_names)}{suffix}")

        lines.append('\n'.join(parts))

    if not lines:
        return ''

    return '## Dependency Context\n' + '\n\n'.join(lines)


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == '__main__':
    def progress(done, total):
        if done % 100 == 0 or done == total:
            print(f'  {done}/{total} files parsed...', flush=True)
    build_graph(progress_cb=progress)
