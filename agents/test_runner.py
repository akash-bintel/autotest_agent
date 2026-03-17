from graphs.state import AgentState
from tools.shell_ops import run_command

def test_runner_agent(state: AgentState) -> AgentState:
    p_type = state.get('project_type', 'unknown').lower()
    print(f"DEBUG: Running tests for PROJECT TYPE: {p_type}")

    if p_type == "unknown":
        return {
            "is_base_setup_verified": False, 
            "latest_error_log": "Critical Error: Project type is unknown. Cannot determine test runner."
        }

    # Map runners to types
    runner_map = {
        "typescript": "npm test",
        "javascript": "npm test",
        "react": "npm test",
        "vue": "npm test",
        "quasar": "npm test",
        "python": "pytest",
        "java": "mvn test",
        "go": "go test ./..."
    }

    cmd = runner_map.get(p_type, "pytest")

    code, log = run_command(cmd, state['project_path'])
    if code != 0:
        print("❌ Base test run failed. Passing error log to config manager.")
        if log:
            lines = log.splitlines()
            snippet = "\n".join(lines[-200:])
            print("----- BASE TEST ERROR (last 200 lines) -----")
            print(snippet)
            print("----- END ERROR -----")
        return {"is_base_setup_verified": False, "latest_error_log": log}

    return {"is_base_setup_verified": True, "latest_error_log": None}
