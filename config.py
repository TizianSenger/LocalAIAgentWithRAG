import sys

REPO_PATH       = r"C:\natMSSProjects\mss"
VAULT_PATH      = r"C:\natMSSObsidian\natMSS"
VAULT_CODE_PATH = r"C:\natMSSObsidian\natMSS\Code"
STATE_FILE      = r"C:\natMSSObsidian\natMSS\.indexer_state.json"

LLM_MODEL   = "qwen2.5-coder:32b"
EMBED_MODEL = "bge-m3"
# variants for embedings:
#mxbai-embed-large
#nomic-embed-text
#bge-m3

# Number of files analysed in parallel.
# With a single GPU, 4–6 is a good starting point.
# Increase if GPU utilisation stays below 80%, decrease if Ollama errors out.
INDEXER_WORKERS = 1

PYTHON_EXE = sys.executable

CODE_EXTENSIONS = {
    # General languages
    ".py", ".java", ".cs", ".ts", ".js", ".tsx", ".jsx",
    ".cpp", ".c", ".h", ".hpp", ".rs", ".go", ".kt", ".swift",
    ".rb", ".php", ".fs", ".vb", ".scala", ".groovy", ".clj",
    # Eclipse / EMF / Xtext
    ".ecore", ".genmodel", ".xcore", ".xtext", ".xtend",
    ".mwe2", ".workflow", ".qvto", ".ocl", ".uml",
    # Eclipse build / config
    ".target", ".product", ".feature", ".exsd",
    ".bnd", ".bndrun",
    # Build / project descriptors
    ".xml", ".gradle", ".kts", ".pom", ".project", ".classpath",
    ".properties", ".yaml", ".yml", ".toml", ".ini", ".conf",
    # Web / templating
    ".html", ".htm", ".css", ".scss", ".less",
    # SQL / data / schema
    ".sql", ".graphql", ".proto", ".xsd",
    # Shell / scripts
    ".sh", ".bat", ".ps1",
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "bin", "obj",
    "dist", "build", ".vs", "packages", "vendor", ".idea",
}
