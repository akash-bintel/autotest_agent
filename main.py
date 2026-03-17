import sys
import os
from dotenv import load_dotenv
from graphs.graph import build_graphs

def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}

def _render_graph_png(graph, path: str):
    try:
        g = graph.get_graph()
        if hasattr(g, "draw_mermaid_png"):
            png_bytes = g.draw_mermaid_png()
        elif hasattr(g, "draw_png"):
            png_bytes = g.draw_png()
        else:
            print("⚠️ Graph render not supported by this langgraph version.")
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(png_bytes)
        print(f"🖼️ Graph image saved: {path}")
    except Exception as exc:
        print(f"⚠️ Failed to render graph image: {exc}")

# Load env (if using OpenAI/LangSmith tracing)
load_dotenv()

def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <path_to_project>")
        sys.exit(1)
        
    project_path = sys.argv[1]
    
    if not os.path.exists(project_path):
        print("Error: Project path does not exist.")
        sys.exit(1)
        
    print(f"Starting Test Automation for: {project_path}")
    
    # Initialize Graphs
    graph, subgraphs = build_graphs()

    # Optional graph rendering
    if _truthy_env("AUTOTEST_RENDER_GRAPHS"):
        out_dir = os.getenv("AUTOTEST_RENDER_GRAPHS_DIR", "graphs")
        _render_graph_png(graph, os.path.join(out_dir, "main_graph.png"))
        for name, sg in subgraphs.items():
            _render_graph_png(sg, os.path.join(out_dir, f"{name}.png"))
    
    # Initial State
    initial_state = {
        "project_path": project_path,
        "current_file_index": 0,
        "source_files": [],
        "is_base_setup_verified": False,
        "test_phase": "base",
        "unit_test_files": [],
        "unit_test_map": {},
        "unit_test_failures": [],
        "unit_missing_libs": [],
        "available_libraries": [],
        "unit_retry_count": 0,
        "is_unit_tests_verified": False,
        "unit_max_retries": int(os.getenv("AUTOTEST_UNIT_MAX_RETRIES", "3")),
        "unit_max_fixes": int(os.getenv("AUTOTEST_UNIT_MAX_FIXES", "50")),
        "integration_files": [],
        "integration_index": 0,
        "integration_test_files": [],
        "integration_test_map": {},
        "integration_test_failures": [],
        "integration_retry_count": 0,
        "is_integration_tests_verified": False,
        "integration_max_retries": int(os.getenv("AUTOTEST_INT_MAX_RETRIES", "3")),
        "integration_max_fixes": int(os.getenv("AUTOTEST_INT_MAX_FIXES", "50")),
    }
    
    # Run Graph (MemorySaver requires a thread_id)
    thread_id = os.getenv("AUTOTEST_THREAD_ID", project_path)
    config = {"configurable": {"thread_id": thread_id}}
    for event in graph.stream(initial_state, config):
        for key, value in event.items():
            print(f"Finished Step: {key}")

if __name__ == "__main__":
    main()
