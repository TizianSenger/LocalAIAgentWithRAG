from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_community.document_loaders import ObsidianLoader

from config import VAULT_PATH, EMBED_MODEL

embeddings = OllamaEmbeddings(model=EMBED_MODEL)

print("Loading vault notes into memory ...")
loader    = ObsidianLoader(VAULT_PATH)
documents = loader.load()
print(f"  {len(documents)} notes loaded.")

vector_store = Chroma(
    collection_name="obsidian_notes",
    embedding_function=embeddings,
)
vector_store.add_documents(documents=documents)

retriever = vector_store.as_retriever(
    search_kwargs={"k": 5}
)