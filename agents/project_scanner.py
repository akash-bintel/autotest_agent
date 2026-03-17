from graphs.state import AgentState
from tools.file_ops import get_project_tree

def project_scanner_agent(state: AgentState) -> AgentState:
    print("--- 🔍 Agent: Scanning Project Structure ---")
    tree = get_project_tree(state['project_path'])
    return {"project_tree": tree}