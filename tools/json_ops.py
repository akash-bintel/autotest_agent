import json
import re

def parse_json_from_llm(llm_response: str) -> dict:
    """
    Robustly parses JSON from an LLM response, handling markdown fences 
    and extra conversational text.
    """
    # 1. Try to find content within ```json ... ``` or just ``` ... ```
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", llm_response, re.DOTALL)
    
    if match:
        json_str = match.group(1).strip()
    else:
        # 2. If no code blocks, try to find the first { and last }
        # This handles cases where the LLM just dumps the JSON with some text around it.
        match = re.search(r"(\{.*\})", llm_response, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
        else:
            json_str = llm_response.strip()

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON. Raw content:\n{json_str}")
        raise e