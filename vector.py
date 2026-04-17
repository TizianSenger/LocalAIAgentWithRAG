import os
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_community.document_loaders import ObsidianLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import VAULT_PATH, EMBED_MODEL

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