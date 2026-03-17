import os
from langchain_openai import ChatOpenAI

def get_llama_model():
    """
    Returns a ChatOpenAI instance configured for local Llama 3.1 via Ollama.
    Ensure 'ollama serve' is running and 'ollama pull llama3.1' is done.
    """
    streaming = os.getenv("AUTOTEST_LLM_STREAMING", "0").strip().lower() in {"1", "true", "yes", "on"}
    timeout = float(os.getenv("AUTOTEST_LLM_TIMEOUT", "120"))
    return ChatOpenAI(
        base_url="http://bnyn-accelerator-1:18080/v1",
        api_key="ollama",
        model="llama3.1",
        temperature=0.0,
        streaming=streaming,
        timeout=timeout,
        request_timeout=timeout,
    )
