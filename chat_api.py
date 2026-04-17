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
import os

# Allow UI to override models via environment variables
LLM_MODEL   = os.environ.get('OVERRIDE_CHAT_MODEL', os.environ.get('OVERRIDE_LLM_MODEL', LLM_MODEL))
EMBED_MODEL = os.environ.get('OVERRIDE_EMBED_MODEL', EMBED_MODEL)

app   = Flask(__name__)
model = OllamaLLM(model=LLM_MODEL)

template = """
You are an expert software documentation assistant for the natMSS codebase.
Use the following notes from the Obsidian vault to answer the question.
If the notes do not contain enough information, say so clearly.
IMPORTANT: Always respond in the same language the user used to write their question.

Relevant notes:
{context}

Question: {question}
"""
prompt = ChatPromptTemplate.from_template(template)
chain  = prompt | model


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
    context = "\n\n---\n\n".join(
        f"[Source: {d.metadata.get('source', 'unknown')}]\n{d.page_content}"
        for d in docs
    )
    result  = chain.invoke({"context": context, "question": question})
    return jsonify({"answer": result})


@app.route('/stream', methods=['POST'])
def stream_chat():
    data     = request.get_json(force=True, silent=True) or {}
    question = data.get('question', '').strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400

    docs    = retriever.invoke(question)
    sources = list(dict.fromkeys(
        d.metadata.get('source', 'unknown') for d in docs
    ))
    context = "\n\n---\n\n".join(
        f"[Source: {d.metadata.get('source', 'unknown')}]\n{d.page_content}"
        for d in docs
    )

    def generate():
        # First event: sources used
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
        # Stream tokens
        for chunk in chain.stream({"context": context, "question": question}):
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
