import json
import os
import re
from graphs.state import AgentState
from tools.shell_ops import run_command

def _determine_unit_cmd(p_type: str) -> str:
    if p_type in ["javascript", "typescript", "react"]:
        return "npm test"
    if p_type == "python":
        return "pytest"
    if p_type == "go":
        return "go test ./..."
    return "pytest"

def _read_package_json(project_path: str) -> dict:
    path = os.path.join(project_path, "package.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _js_unit_cmd(project_path: str) -> str:
    pkg = _read_package_json(project_path)
    scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
    if "test:unit" in scripts:
        return "npm run test:unit"
    return "npm test"

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
        # Try suffix match for absolute paths
        for full in test_files:
            if _normalize_path(full).endswith(c):
                resolved.append(full)
                break
    return list(dict.fromkeys(resolved))

def _extract_failed_tests(log: str, test_files: list[str], p_type: str) -> list[str]:
    if not log or not test_files:
        return []

    candidates = []

    if p_type in ["javascript", "typescript", "react"]:
        # Jest/Vitest lines like: FAIL  path/to/file.test.tsx
        for match in re.findall(r"^\s*FAIL\s+(.+)$", log, re.MULTILINE):
            candidates.append(match.strip())
        # File paths in stack traces
        candidates += [m[0] for m in re.findall(r"([A-Za-z0-9_./\\-]+?\.(test|spec)\.(js|jsx|ts|tsx))", log)]

    if p_type == "python":
        # Pytest failure lines: FAILED path::test_name
        for match in re.findall(r"FAILED\s+([^\s:]+)", log):
            candidates.append(match.strip())
        candidates += [m[0] for m in re.findall(r"([A-Za-z0-9_./\\-]+?\.test\.py)", log)]

    if p_type == "go":
        # Go test failures usually include file_test.go:line
        candidates += [m[0] for m in re.findall(r"([A-Za-z0-9_./\\-]+?_test\.go)", log)]

    if not candidates:
        # Generic fallback: any known test filename in log
        for path in test_files:
            if os.path.basename(path) in log:
                candidates.append(path)

    return _match_test_files(candidates, test_files)

def unit_test_runner_agent(state: AgentState) -> AgentState:
    p_type = state.get("project_type", "unknown")
    if p_type in ["javascript", "typescript", "react"]:
        cmd = _js_unit_cmd(state["project_path"])
    else:
        cmd = _determine_unit_cmd(p_type)
    code, log = run_command(cmd, state["project_path"])

    if code == 0:
        return {
            "is_unit_tests_verified": True,
            "latest_error_log": None,
            "unit_test_failures": [],
            "unit_retry_count": 0
        }

    test_files = state.get("unit_test_files", [])
    failures = _extract_failed_tests(log, test_files, p_type)
    return {
        "is_unit_tests_verified": False,
        "latest_error_log": log,
        "unit_test_failures": failures
    }
