import sys

REPO_PATH       = r"C:\natMSSProjects\mss"
VAULT_PATH      = r"C:\natMSSObsidian\natMSS"
VAULT_CODE_PATH = r"C:\natMSSObsidian\natMSS\Code"
STATE_FILE      = r"C:\natMSSObsidian\natMSS\.indexer_state.json"

LLM_MODEL   = "qwen2.5-coder:32b"
EMBED_MODEL = "mxbai-embed-large"

PYTHON_EXE = sys.executable

CODE_EXTENSIONS = {
    ".py", ".java", ".cs", ".ts", ".js", ".tsx", ".jsx",
    ".cpp", ".c", ".h", ".hpp", ".rs", ".go", ".kt", ".swift",
    ".rb", ".php", ".fs", ".vb",
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "bin", "obj",
    "dist", "build", ".vs", "packages", "vendor", ".idea",
}
