from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate
from vector import retriever
from config import LLM_MODEL

model = OllamaLLM(model=LLM_MODEL)

template = """
You are an expert software documentation assistant for the natMSS codebase.
Use the following notes from the Obsidian vault to answer the question.
If the notes do not contain enough information, say so clearly.

Relevant notes:
{context}

Question: {question}
"""
prompt = ChatPromptTemplate.from_template(template)
chain  = prompt | model

while True:
    print("\n\n-------------------------------")
    question = input("Ask your question (q to quit): ")
    print("\n\n")
    if question == "q":
        break

    docs   = retriever.invoke(question)
    result = chain.invoke({"context": docs, "question": question})
    print(result)