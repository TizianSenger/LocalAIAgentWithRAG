import os
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_community.document_loaders import ObsidianLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import VAULT_PATH, EMBED_MODEL, REPO_PATH, SKIP_DIRS
from code_units import split_file_into_units

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
        rel = os.path.relpath(fpath, REPO_PATH).replace('\\', '/')
        lines = content.splitlines(keepends=True)
        units = split_file_into_units(lines, fpath, max_unit=120)

        # nomic-embed-text / snowflake-arctic-embed2 / bge-m3: 8192-token context; ~4 chars/token → ~30000 chars safe limit
        _MAX_CHARS = 30000
        if units:
            for u in units:
                body = ''.join(lines[u.start:u.end])
                if len(body) > _MAX_CHARS:
                    body = body[:_MAX_CHARS]
                label = u.label or f'lines {u.start+1}-{u.end}'
                all_docs.append(Document(
                    page_content=f'# {rel} — {label} (lines {u.start+1}-{u.end})\n{body}',
                    metadata={
                        'source':     rel,
                        'language':   ext.lstrip('.'),
                        'class':      u.cls,
                        'method':     u.name,
                        'start_line': u.start + 1,
                        'end_line':   u.end,
                    },
                ))
        else:
            # Fallback: no functions detected → one chunk capped at 30000 chars
            trimmed = content[:30000]
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
