import json
import os
import re
from graphs.state import AgentState
from tools.shell_ops import run_command

def _read_package_json(project_path: str) -> dict:
    path = os.path.join(project_path, "package.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _js_int_cmd(project_path: str) -> str:
    pkg = _read_package_json(project_path)
    scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
    if "test:int" in scripts:
        return "npm run test:int"
    return "npm test"

def _determine_int_cmd(p_type: str, project_path: str) -> str:
    if p_type in ["javascript", "typescript", "react", "vue", "quasar"]:
        return _js_int_cmd(project_path)
    if p_type == "python":
        tests_dir = os.path.join(project_path, "tests")
        return "pytest tests" if os.path.isdir(tests_dir) else "pytest"
    if p_type == "go":
        tests_dir = os.path.join(project_path, "tests")
        return "go test ./tests/..." if os.path.isdir(tests_dir) else "go test ./..."
    return "pytest"

def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")

def _match_test_files(candidates: list[str], test_files: list[str]) -> list[str]:
    if not candidates or not test_files:
        return []
    normalized = {_normalize_path(p): p for p in test_files}
    by_base = {os.path.basename(p): p for p in test_files}
    resolved = []
    for cand in candidates:
        c = _normalize_path(cand)
        if c in normalized:
            resolved.append(normalized[c])
            continue
        base = os.path.basename(c)
        if base in by_base:
            resolved.append(by_base[base])
            continue
        for full in test_files:
            if _normalize_path(full).endswith(c):
                resolved.append(full)
                break
    return list(dict.fromkeys(resolved))

def _extract_failed_tests(log: str, test_files: list[str], p_type: str) -> list[str]:
    if not log or not test_files:
        return []

    candidates = []
    if p_type in ["javascript", "typescript", "react", "vue", "quasar"]:
        for match in re.findall(r"^\s*FAIL\s+(.+)$", log, re.MULTILINE):
            candidates.append(match.strip())
        candidates += [m[0] for m in re.findall(r"([A-Za-z0-9_./\\-]+?\.(test|spec)\.(js|jsx|ts|tsx))", log)]
    elif p_type == "python":
        for match in re.findall(r"FAILED\s+([^\s:]+)", log):
            candidates.append(match.strip())
        candidates += [m[0] for m in re.findall(r"([A-Za-z0-9_./\\-]+?\.test\.py)", log)]
    elif p_type == "go":
        candidates += [m[0] for m in re.findall(r"([A-Za-z0-9_./\\-]+?_test\.go)", log)]

    if not candidates:
        for path in test_files:
            if os.path.basename(path) in log:
                candidates.append(path)

    return _match_test_files(candidates, test_files)

def integration_test_runner_agent(state: AgentState) -> AgentState:
    p_type = state.get("project_type", "unknown")
    cmd = _determine_int_cmd(p_type, state["project_path"])
    code, log = run_command(cmd, state["project_path"])

    if code == 0:
        return {
            "is_integration_tests_verified": True,
            "latest_error_log": None,
            "integration_test_failures": [],
            "integration_retry_count": 0,
        }

    test_files = state.get("integration_test_files", [])
    failures = _extract_failed_tests(log, test_files, p_type)
    return {
        "is_integration_tests_verified": False,
        "latest_error_log": log,
        "integration_test_failures": failures,
    }
