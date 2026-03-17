import json
import os
from graphs.state import AgentState
from models.llm import get_llama_model
from tools.file_ops import read_file
from tools.json_ops import parse_json_from_llm 

def _load_package_json(project_path: str) -> dict:
    pkg_path = os.path.join(project_path, "package.json")
    if not os.path.exists(pkg_path):
        return {}
    try:
        return json.loads(read_file(pkg_path))
    except Exception:
        return {}

def _detect_js_type(top_level_files: list[str], project_path: str) -> str:
    pkg = _load_package_json(project_path)
    deps = {}
    deps.update(pkg.get("dependencies", {}) or {})
    deps.update(pkg.get("devDependencies", {}) or {})
    deps.update(pkg.get("peerDependencies", {}) or {})

    is_ts = (
        "tsconfig.json" in top_level_files
        or "typescript" in deps
        or any(name.startswith("@types/") for name in deps)
    )
    return "typescript" if is_ts else "javascript"

def _detect_js_framework(project_path: str) -> str | None:
    pkg = _load_package_json(project_path)
    deps = {}
    deps.update(pkg.get("dependencies", {}) or {})
    deps.update(pkg.get("devDependencies", {}) or {})
    deps.update(pkg.get("peerDependencies", {}) or {})

    if "quasar" in deps:
        return "quasar"
    if "vue" in deps:
        return "vue"
    if "react" in deps or "react-dom" in deps:
        return "react"
    return None

def project_classifier_agent(state: AgentState) -> AgentState:
    print("--- 🏷️ Agent: Multi-Language Classification ---")
    llm = get_llama_model()
    
    # 1. Get the flat list of files
    tree = json.loads(state['project_tree'])
    top_level_files = list(tree.keys())
    
    # 2. Manual Check (Heuristics)
    if "go.mod" in top_level_files:
        return {"project_type": "go", "package_file": "go.mod"}

    if any(name in top_level_files for name in ["pyproject.toml", "requirements.txt", "setup.py", "Pipfile"]):
        pkg = "pyproject.toml" if "pyproject.toml" in top_level_files else (
            "requirements.txt" if "requirements.txt" in top_level_files else (
                "setup.py" if "setup.py" in top_level_files else "Pipfile"
            )
        )
        return {"project_type": "python", "package_file": pkg}

    if "package.json" in top_level_files:
        p_type = _detect_js_type(top_level_files, state["project_path"])
        framework = _detect_js_framework(state["project_path"])
        if framework:
            print(f"DEBUG: Heuristic detected {p_type} ({framework})")
        else:
            print(f"DEBUG: Heuristic detected {p_type}")
        return {"project_type": p_type, "package_file": "package.json", "framework": framework}
    
    if "pom.xml" in top_level_files or "build.gradle" in top_level_files:
        return {"project_type": "java", "package_file": "pom.xml"}

    # 3. LLM Fallback (only if manual check fails)
    prompt = f"Analyze these files: {top_level_files}. Return JSON: {{\"type\": \"...\", \"package_file\": \"...\"}}"
    try:
        response = llm.invoke(prompt)
        data = parse_json_from_llm(response.content)
        return {"project_type": data.get('type', 'unknown'), "package_file": data.get('package_file', '')}
    except:
        return {"project_type": "unknown"}
