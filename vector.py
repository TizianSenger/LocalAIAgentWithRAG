import os
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_community.document_loaders import ObsidianLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import VAULT_PATH, EMBED_MODEL

embeddings = OllamaEmbeddings(model=EMBED_MODEL)

print("Loading vault notes into memory ...")
loader    = ObsidianLoader(VAULT_PATH)
documents = loader.load()
print(f"  {len(documents)} notes loaded.")

# Split long notes into chunks so embeddings are more precise.
# Each chunk inherits the original document's metadata (incl. source path).
splitter = RecursiveCharacterTextSplitter(
    chunk_size=600,
    chunk_overlap=80,
    separators=["\n## ", "\n### ", "\n\n", "\n", " "],
)
chunks = splitter.split_documents(documents)
print(f"  {len(chunks)} chunks after splitting.")

vector_store = Chroma(
    collection_name="obsidian_notes",
    embedding_function=embeddings,
)
vector_store.add_documents(documents=chunks)

# k is configurable via CHAT_RAG_K env var (default: 20)
_rag_k = int(os.environ.get('CHAT_RAG_K', '20'))
print(f"  RAG k={_rag_k} (set CHAT_RAG_K env var to change)")

retriever = vector_store.as_retriever(
    search_kwargs={"k": _rag_k}
)