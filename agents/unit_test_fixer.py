import re
from graphs.state import AgentState
from models.llm import get_llama_model
from tools.file_ops import read_file, write_file

def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", text)
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()

def _extract_relevant_log(error_log: str, test_file: str, max_lines: int = 200) -> str:
    if not error_log:
        return ""
    lines = error_log.splitlines()
    base = test_file.replace("\\", "/").split("/")[-1]
    hits = [i for i, line in enumerate(lines) if base in line]
    if not hits:
        return "\n".join(lines[-max_lines:])
    start = max(min(hits) - 10, 0)
    end = min(max(hits) + 20, len(lines))
    excerpt = lines[start:end]
    if len(excerpt) > max_lines:
        excerpt = excerpt[-max_lines:]
    return "\n".join(excerpt)

def unit_test_fixer_agent(state: AgentState) -> AgentState:
    failures = state.get("unit_test_failures", []) or []
    unit_test_map = state.get("unit_test_map", {}) or {}
    error_log = state.get("latest_error_log", "") or ""

    if not failures:
        return {"unit_retry_count": state.get("unit_retry_count", 0) + 1}

    llm = get_llama_model()
    max_fixes = int(state.get("unit_max_fixes", 50))
    for test_file in failures[:max_fixes]:
        test_content = read_file(test_file)
        source_file = unit_test_map.get(test_file, "")
        source_content = read_file(source_file) if source_file else ""
        relevant_log = _extract_relevant_log(error_log, test_file)

        prompt = f"""
        The following unit test is failing. Fix the test file ONLY (do not change source files).

        [ERROR LOG]
        {relevant_log}

        [SOURCE FILE] {source_file}
        {source_content}

        [TEST FILE] {test_file}
        {test_content}

        Return ONLY the corrected test file content.
        """

        response = llm.invoke(prompt).content
        updated = _strip_code_fences(response)
        if updated:
            write_file(test_file, updated)

    return {"unit_retry_count": state.get("unit_retry_count", 0) + 1}
