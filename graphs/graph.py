from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from graphs.state import AgentState

# Import Agents
from agents.project_scanner import project_scanner_agent
from agents.project_classifier import project_classifier_agent
from agents.test_lib_detector import test_lib_detector_agent
from agents.installer import installer_agent
from agents.config_manager import config_agent
from agents.test_runner import test_runner_agent
from agents.test_generators import (
    list_source_files,
    list_integration_files,
    unit_test_generator_agent,
    integration_test_agent,
    e2e_test_agent,
)
from agents.unit_test_runner import unit_test_runner_agent
from agents.unit_test_fixer import unit_test_fixer_agent
from agents.unit_test_deps_installer import unit_test_deps_installer_agent
from agents.integration_test_runner import integration_test_runner_agent
from agents.integration_test_fixer import integration_test_fixer_agent

def _build_base_setup_subgraph():
    sub = StateGraph(AgentState)
    sub.add_node("config_manager", config_agent)
    sub.add_node("base_tester", test_runner_agent)

    def base_success(_: AgentState):
        return {"test_phase": "unit"}

    def base_failed(_: AgentState):
        return {"test_phase": "failed"}

    sub.add_node("base_success", base_success)
    sub.add_node("base_failed", base_failed)

    sub.add_edge(START, "config_manager")
    sub.add_edge("config_manager", "base_tester")

    def check_base_test(state: AgentState):
        if state.get("is_base_setup_verified"):
            return "base_success"
        if state.get("retry_count", 0) >= 3:
            print("❌ Max retries reached. Base setup failed.")
            return "base_failed"
        return "config_manager"

    sub.add_conditional_edges("base_tester", check_base_test)
    sub.add_edge("base_success", END)
    sub.add_edge("base_failed", END)
    return sub.compile()

def _build_unit_tests_subgraph():
    sub = StateGraph(AgentState)
    sub.add_node("file_lister", list_source_files)
    sub.add_node("unit_gen", unit_test_generator_agent)
    sub.add_node("unit_deps_installer", unit_test_deps_installer_agent)
    sub.add_node("unit_tester", unit_test_runner_agent)
    sub.add_node("unit_fixer", unit_test_fixer_agent)

    def unit_success(_: AgentState):
        return {"test_phase": "integration"}

    def unit_failed(_: AgentState):
        return {"test_phase": "integration"}

    sub.add_node("unit_success", unit_success)
    sub.add_node("unit_failed", unit_failed)

    sub.add_edge(START, "file_lister")
    sub.add_edge("file_lister", "unit_gen")

    def check_unit_done(state: AgentState):
        if state["current_file_index"] < len(state["source_files"]):
            return "unit_gen"
        return "unit_deps_installer"

    sub.add_conditional_edges("unit_gen", check_unit_done)
    sub.add_edge("unit_deps_installer", "unit_tester")

    def check_unit_tests(state: AgentState):
        if state.get("is_unit_tests_verified"):
            return "unit_success"
        max_retries = state.get("unit_max_retries", 3)
        if state.get("unit_retry_count", 0) >= max_retries:
            print("❌ Max unit retries reached. Proceeding to integration tests.")
            return "unit_failed"
        return "unit_fixer"

    sub.add_conditional_edges("unit_tester", check_unit_tests)
    sub.add_edge("unit_fixer", "unit_tester")
    sub.add_edge("unit_success", END)
    sub.add_edge("unit_failed", END)
    return sub.compile()

def _build_integration_subgraph():
    sub = StateGraph(AgentState)
    sub.add_node("integration_lister", list_integration_files)
    sub.add_node("integration_gen", integration_test_agent)
    sub.add_node("integration_tester", integration_test_runner_agent)
    sub.add_node("integration_fixer", integration_test_fixer_agent)

    def integration_success(_: AgentState):
        return {"test_phase": "e2e"}

    def integration_failed(_: AgentState):
        return {"test_phase": "e2e"}

    sub.add_node("integration_success", integration_success)
    sub.add_node("integration_failed", integration_failed)

    sub.add_edge(START, "integration_lister")
    sub.add_edge("integration_lister", "integration_gen")

    def check_integration_done(state: AgentState):
        if state["integration_index"] < len(state["integration_files"]):
            return "integration_gen"
        return "integration_tester"

    sub.add_conditional_edges("integration_gen", check_integration_done)

    def check_integration_tests(state: AgentState):
        if state.get("is_integration_tests_verified"):
            return "integration_success"
        max_retries = state.get("integration_max_retries", 3)
        if state.get("integration_retry_count", 0) >= max_retries:
            print("❌ Max integration retries reached. Proceeding to E2E tests.")
            return "integration_failed"
        return "integration_fixer"

    sub.add_conditional_edges("integration_tester", check_integration_tests)
    sub.add_edge("integration_fixer", "integration_tester")
    sub.add_edge("integration_success", END)
    sub.add_edge("integration_failed", END)
    return sub.compile()

def _build_e2e_subgraph():
    sub = StateGraph(AgentState)
    sub.add_node("e2e_gen", e2e_test_agent)

    def e2e_done(_: AgentState):
        return {"test_phase": "finished"}

    sub.add_node("e2e_done", e2e_done)
    sub.add_edge(START, "e2e_gen")
    sub.add_edge("e2e_gen", "e2e_done")
    sub.add_edge("e2e_done", END)
    return sub.compile()

def build_graphs():
    builder = StateGraph(AgentState)

    # Core pipeline
    builder.add_node("scanner", project_scanner_agent)
    builder.add_node("classifier", project_classifier_agent)
    builder.add_node("lib_detector", test_lib_detector_agent)
    builder.add_node("installer", installer_agent)

    # Subgraphs
    base_setup = _build_base_setup_subgraph()
    unit_tests = _build_unit_tests_subgraph()
    integration_tests = _build_integration_subgraph()
    e2e_tests = _build_e2e_subgraph()

    builder.add_node("base_setup", base_setup)
    builder.add_node("unit_tests", unit_tests)
    builder.add_node("integration_tests", integration_tests)
    builder.add_node("e2e_tests", e2e_tests)

    def phase_router(state: AgentState):
        phase = state.get("test_phase", "base")
        if phase == "base":
            return "base_setup"
        if phase == "unit":
            return "unit_tests"
        if phase == "integration":
            return "integration_tests"
        if phase == "e2e":
            return "e2e_tests"
        if phase in ["finished", "done", "end", "failed"]:
            return "__end__"
        return "base_setup"

    # Flow wiring
    builder.add_edge(START, "scanner")
    builder.add_edge("scanner", "classifier")
    builder.add_edge("classifier", "lib_detector")
    builder.add_edge("lib_detector", "installer")
    builder.add_conditional_edges("installer", phase_router)
    builder.add_conditional_edges("base_setup", phase_router)
    builder.add_conditional_edges("unit_tests", phase_router)
    builder.add_conditional_edges("integration_tests", phase_router)
    builder.add_conditional_edges("e2e_tests", phase_router)

    memory = MemorySaver()
    compiled = builder.compile(checkpointer=memory)
    subgraphs = {
        "base_setup": base_setup,
        "unit_tests": unit_tests,
        "integration_tests": integration_tests,
        "e2e_tests": e2e_tests,
    }
    return compiled, subgraphs
