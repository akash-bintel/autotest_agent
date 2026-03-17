from agents.installer import installer_agent
from graphs.state import AgentState

def unit_test_deps_installer_agent(state: AgentState) -> AgentState:
    missing = list(dict.fromkeys(state.get("unit_missing_libs", []) or []))
    if not missing:
        return {}

    temp_state = dict(state)
    temp_state["selected_libraries"] = {"unit": missing, "integration": [], "e2e": []}
    result = installer_agent(temp_state)
    result["unit_missing_libs"] = []
    return result
