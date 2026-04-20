import ast
import os
import re
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_community.document_loaders import ObsidianLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import VAULT_PATH, EMBED_MODEL, REPO_PATH, SKIP_DIRS

# Persistent DB lives next to this script so it survives restarts
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CHROMA_DIR = os.path.join(_SCRIPT_DIR, "chrome_langchain_db")

embeddings = OllamaEmbeddings(model=EMBED_MODEL)

vector_store = Chroma(
    collection_name="obsidian_notes",
    embedding_function=embeddings,
    persist_directory=_CHROMA_DIR,
)

# Only embed when the collection is empty (first run or after Clear Vault)
_existing = vector_store._collection.count()
if _existing == 0:
    print("Loading vault notes into memory ...")
    loader    = ObsidianLoader(VAULT_PATH)
    documents = loader.load()
    print(f"  {len(documents)} notes loaded.")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=600,
        chunk_overlap=80,
        separators=["\n## ", "\n### ", "\n\n", "\n", " "],
    )
    chunks = splitter.split_documents(documents)
    print(f"  {len(chunks)} chunks after splitting.")

    # Add in batches to avoid timeout with large vaults
    _BATCH = 50
    for i in range(0, len(chunks), _BATCH):
        vector_store.add_documents(documents=chunks[i:i + _BATCH])
        print(f"  Embedded {min(i + _BATCH, len(chunks))}/{len(chunks)} chunks …")

    print("  Embedding complete — stored to disk.")
else:
    print(f"  Vector store ready ({_existing} chunks cached — skipping embed).")

# k is configurable via CHAT_RAG_K env var (default: 20)
_rag_k = int(os.environ.get('CHAT_RAG_K', '20'))
print(f"  RAG k={_rag_k} (set CHAT_RAG_K env var to change)")

retriever = vector_store.as_retriever(
    search_kwargs={"k": _rag_k}
)

# ── Code index (method-level, from REPO_PATH) ─────────────────────────────────

# Only real programming languages — no XML/YAML/HTML/CSS/config
_CODE_INDEX_EXTS = {
    '.py', '.js', '.jsx', '.ts', '.tsx',
    '.cs', '.java', '.cpp', '.c', '.h', '.hpp',
    '.rs', '.go', '.kt', '.ps1',
}

_CODE_CHROMA_DIR = os.path.join(_SCRIPT_DIR, 'chrome_code_db')


class _PyVisitor(ast.NodeVisitor):
    """Extract top-level functions and class methods via Python AST."""

    def __init__(self, lines: list[str], rel_path: str):
        self._lines     = lines
        self._rel_path  = rel_path
        self._class_stk: list[str] = []
        self.docs: list[Document]  = []

    def _add(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        start = node.lineno - 1
        end   = getattr(node, 'end_lineno', min(start + 120, len(self._lines)))
        body  = '\n'.join(self._lines[start:end])
        cls   = self._class_stk[-1] if self._class_stk else ''
        label = f'{cls}.{node.name}' if cls else node.name
        self.docs.append(Document(
            page_content=f'# {self._rel_path} — {label} (lines {node.lineno}-{end})\n{body}',
            metadata={
                'source':     self._rel_path,
                'language':   'python',
                'class':      cls,
                'method':     node.name,
                'start_line': node.lineno,
                'end_line':   end,
            },
        ))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stk.append(node.name)
        self.generic_visit(node)   # recurse into class body to find methods
        self._class_stk.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add(node)
        # Do NOT recurse — skip nested functions inside methods

    visit_AsyncFunctionDef = visit_FunctionDef


def _extract_python(filepath: str, content: str) -> list[Document]:
    lines = content.splitlines()
    try:
        tree = ast.parse(content, filename=filepath)
    except SyntaxError:
        return []
    rel = os.path.relpath(filepath, REPO_PATH).replace('\\', '/')
    v   = _PyVisitor(lines, rel)
    v.visit(tree)
    return v.docs


# Patterns that mark the start of a named function/method in JS/TS/CS/Java etc.
_FUNC_START_RE = re.compile(
    r'^(?P<indent>[ \t]{0,8})'
    r'(?:(?:export|default|public|private|protected|static|abstract|override|'
    r'sealed|async|readonly|virtual|internal|extern)\s+)*'
    r'(?:async\s+)?'
    r'(?:'
    r'function\s+(?P<fn1>\w+)\s*[(<]'            # function declaration
    r'|(?:const|let|var)\s+(?P<fn2>\w+)\s*='    # const/let/var = arrow/function
    r'|(?:def|sub|end\s+sub)\s+(?P<fn3>\w+)\s*\('  # Python-like (PS1)
    r'|(?P<fn4>[a-zA-Z_]\w*)\s*\('              # method call / class method
    r')',
    re.MULTILINE,
)


def _extract_generic(filepath: str, content: str) -> list[Document]:
    """Regex-based method extraction for JS/TS/CS/Java/etc."""
    lines   = content.splitlines()
    rel     = os.path.relpath(filepath, REPO_PATH).replace('\\', '/')
    ext     = os.path.splitext(filepath)[1].lstrip('.')
    matches = list(_FUNC_START_RE.finditer(content))
    if not matches:
        return []

    docs: list[Document] = []
    for i, m in enumerate(matches):
        # Resolve function name from whichever capture group matched
        func_name = m.group('fn1') or m.group('fn2') or m.group('fn3') or m.group('fn4') or 'unknown'
        # Skip noise: very short names, keywords, common false-positives
        if func_name in {'if', 'for', 'while', 'switch', 'catch', 'return',
                         'import', 'from', 'class', 'new', 'throw', 'var',
                         'let', 'const', 'await', 'super', 'this', 'console'}:
            continue
        start_line = content[:m.start()].count('\n')
        # End = next match start (same or lower indent), capped at 100 lines
        end_line = start_line + 100
        if i + 1 < len(matches):
            next_start = content[:matches[i + 1].start()].count('\n')
            indent_here = len(m.group('indent'))
            next_indent = len(matches[i + 1].group('indent'))
            if next_indent <= indent_here:
                end_line = min(next_start, start_line + 100)
        end_line = min(end_line, len(lines))
        body = '\n'.join(lines[start_line:end_line])
        docs.append(Document(
            page_content=f'# {rel} — {func_name} (line {start_line + 1})\n{body}',
            metadata={
                'source':     rel,
                'language':   ext,
                'method':     func_name,
                'start_line': start_line + 1,
            },
        ))
    return docs


def _walk_code_files():
    """Yield (abs_path, content) for every indexable source file in REPO_PATH."""
    for root, dirs, files in os.walk(REPO_PATH):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _CODE_INDEX_EXTS:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding='utf-8', errors='replace') as fh:
                    yield fpath, fh.read()
            except OSError:
                pass


def _build_code_docs() -> list[Document]:
    all_docs: list[Document] = []
    for fpath, content in _walk_code_files():
        ext = os.path.splitext(fpath)[1].lower()
        if ext == '.py':
            units = _extract_python(fpath, content)
        else:
            units = _extract_generic(fpath, content)
        if units:
            all_docs.extend(units)
        else:
            # Fallback: file has no detectable functions → add as one chunk (capped)
            rel = os.path.relpath(fpath, REPO_PATH).replace('\\', '/')
            trimmed = content[:3000]
            all_docs.append(Document(
                page_content=f'# {rel}\n{trimmed}',
                metadata={'source': rel, 'language': ext.lstrip('.')},
            ))
    return all_docs


code_store = Chroma(
    collection_name='mss_code',
    embedding_function=embeddings,
    persist_directory=_CODE_CHROMA_DIR,
)

_code_existing = code_store._collection.count()
_force_reindex = os.environ.get('FORCE_CODE_REINDEX', '0') == '1'

if _code_existing == 0 or _force_reindex:
    if not os.path.isdir(REPO_PATH):
        print(f'  [code-index] REPO_PATH not found — skipping ({REPO_PATH})')
    else:
        print(f'  [code-index] Indexing source code from {REPO_PATH} …')
        _code_docs = _build_code_docs()
        print(f'  [code-index] {len(_code_docs)} method/function units extracted.')
        if _code_docs:
            _BATCH = 50
            for _i in range(0, len(_code_docs), _BATCH):
                code_store.add_documents(documents=_code_docs[_i:_i + _BATCH])
                print(f'  [code-index] Embedded {min(_i + _BATCH, len(_code_docs))}/{len(_code_docs)} …')
            print('  [code-index] Done.')
else:
    print(f'  [code-index] Code store ready ({_code_existing} units cached — set FORCE_CODE_REINDEX=1 to rebuild).')

_code_k = int(os.environ.get('CODE_RAG_K', '12'))
code_retriever = code_store.as_retriever(search_kwargs={'k': _code_k})
