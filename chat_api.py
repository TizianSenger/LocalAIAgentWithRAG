"""
chat_api.py
-----------
Minimal Flask server that answers questions using the Obsidian vault.
Started by the Electron UI via "Start Chat API".

Usage: python chat_api.py [port]   (default: 5001)
"""

import sys
import json
from flask import Flask, request, jsonify, Response, stream_with_context

from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate
from vector import retriever
from config import LLM_MODEL, EMBED_MODEL
from dep_graph import build_dependency_context
import os

# Allow UI to override models via environment variables
LLM_MODEL   = os.environ.get('OVERRIDE_CHAT_MODEL', os.environ.get('OVERRIDE_LLM_MODEL', LLM_MODEL))
EMBED_MODEL = os.environ.get('OVERRIDE_EMBED_MODEL', EMBED_MODEL)

app   = Flask(__name__)
model = OllamaLLM(model=LLM_MODEL)

template = """
You are an expert software development assistant for a large codebase.
Use the following notes from the documentation vault to answer the question.
If the notes do not contain enough information, say so clearly.

RULES:
- Always respond in the same language the user used to write their question.
- Always mention the exact file path (from the [Source: ...] tags) where relevant code is located.
- If multiple files are relevant, list all of them.
- Format file paths as code: `path/to/File.py`
- Use the Dependency Context section to explain class relationships and impact of changes.

Relevant notes:
{context}

{dep_context}

Question: {question}
"""
prompt = ChatPromptTemplate.from_template(template)
chain  = prompt | model


def _build_context(docs):
    """Build RAG context + dependency graph context from retrieved docs."""
    sources = list(dict.fromkeys(
        d.metadata.get('source', 'unknown') for d in docs
    ))
    rag_context = "\n\n---\n\n".join(
        f"[Source: {d.metadata.get('source', 'unknown')}]\n{d.page_content}"
        for d in docs
    )
    dep_context = build_dependency_context(sources)
    return rag_context, dep_context, sources


@app.route('/health')
def health():
    return jsonify({"status": "ok"})


@app.route('/chat', methods=['POST'])
def chat():
    data     = request.get_json(force=True, silent=True) or {}
    question = data.get('question', '').strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400

    docs    = retriever.invoke(question)
    context, dep_context, _ = _build_context(docs)
    result  = chain.invoke({"context": context, "dep_context": dep_context, "question": question})
    return jsonify({"answer": result})


@app.route('/stream', methods=['POST'])
def stream_chat():
    data     = request.get_json(force=True, silent=True) or {}
    question = data.get('question', '').strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400

    docs    = retriever.invoke(question)
    context, dep_context, sources = _build_context(docs)

    def generate():
        # First event: sources used
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
        # Stream tokens
        for chunk in chain.stream({"context": context, "dep_context": dep_context, "question": question}):
            if chunk:
                yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
    print(f"Chat API ready on http://127.0.0.1:{port}", flush=True)
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
