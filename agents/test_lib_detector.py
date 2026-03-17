import json
import os
import re
import tomllib
from graphs.state import AgentState
from models.llm import get_llama_model
from tools.file_ops import read_file
from tools.json_ops import parse_json_from_llm 

def _load_package_json(state: AgentState) -> dict:
    if state.get("package_file") != "package.json":
        return {}
    pkg_path = os.path.join(state["project_path"], "package.json")
    if not os.path.exists(pkg_path):
        return {}
    try:
        return json.loads(read_file(pkg_path))
    except Exception:
        return {}

def _collect_deps(pkg: dict) -> dict:
    deps = {}
    deps.update(pkg.get("dependencies", {}) or {})
    deps.update(pkg.get("devDependencies", {}) or {})
    deps.update(pkg.get("peerDependencies", {}) or {})
    return deps

def _parse_requirements(text: str) -> list[str]:
    libs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[]", line, 1)[0].strip()
        if name:
            libs.append(name)
    return libs

def _available_python_libs(state: AgentState) -> list[str]:
    pkg_file = state.get("package_file", "")
    project_path = state.get("project_path", "")
    if not pkg_file or not project_path:
        return []
    path = os.path.join(project_path, pkg_file)
    if not os.path.exists(path):
        return []
    try:
        content = read_file(path)
        if pkg_file.endswith(".txt"):
            return _parse_requirements(content)
        if pkg_file == "pyproject.toml":
            data = tomllib.loads(content)
            deps = data.get("project", {}).get("dependencies", [])
            return [re.split(r"[<>=!~\[]", d, 1)[0].strip() for d in deps if isinstance(d, str)]
    except Exception:
        return []
    return []

def _available_go_libs(state: AgentState) -> list[str]:
    project_path = state.get("project_path", "")
    if not project_path:
        return []
    path = os.path.join(project_path, "go.mod")
    if not os.path.exists(path):
        return []
    libs = []
    for line in read_file(path).splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("module") or line.startswith("require"):
            continue
        parts = line.split()
        if parts and "." in parts[0]:
            libs.append(parts[0])
    return libs

def _top_level_files(state: AgentState) -> list[str]:
    try:
        tree = json.loads(state.get("project_tree", "{}"))
        return list(tree.keys())
    except Exception:
        return []

def _detect_bundler(deps: dict, scripts: dict, top_files: list[str]) -> str:
    def has_dep(name: str) -> bool:
        return name in deps

    script_values = " ".join(str(v) for v in (scripts or {}).values()).lower()

    vite_files = {"vite.config.js", "vite.config.ts", "vite.config.mjs", "vite.config.cjs"}
    webpack_files = {"webpack.config.js", "webpack.config.ts", "webpack.config.mjs", "webpack.config.cjs"}

    if (
        has_dep("vite")
        or any(name.startswith("@vitejs/") for name in deps)
        or "vite" in script_values
        or any(f in top_files for f in vite_files)
        or has_dep("quasar")
        or has_dep("@quasar/app-vite")
    ):
        return "vite"

    if (
        has_dep("webpack")
        or has_dep("webpack-cli")
        or has_dep("webpack-dev-server")
        or has_dep("react-scripts")
        or "webpack" in script_values
        or any(f in top_files for f in webpack_files)
    ):
        return "webpack"

    if has_dep("vitest"):
        return "vite"
    if has_dep("jest"):
        return "webpack"

    return "unknown"

def test_lib_detector_agent(state: AgentState) -> AgentState:
    print("--- 🕵️ Agent: Detecting Test Libraries ---")
    llm = get_llama_model()
    
    if not state.get('package_file'):
        pkg_content = "No package file found."
    else:
        pkg_path = os.path.join(state['project_path'], state['package_file'])
        if os.path.exists(pkg_path):
            pkg_content = read_file(pkg_path)
        else:
            pkg_content = "Package file missing."

    # Deterministic JS/TS selection based on bundler
    if state.get("project_type") in ["javascript", "typescript", "react", "vue", "quasar"]:
        pkg = _load_package_json(state)
        deps = _collect_deps(pkg)
        scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
        top_files = _top_level_files(state)
        bundler = _detect_bundler(deps, scripts, top_files)

        is_ts = state.get("project_type") == "typescript" or "typescript" in deps
        framework = state.get("framework")
        uses_react = framework == "react" or "react" in deps or "react-dom" in deps
        uses_vue = framework in ["vue", "quasar"] or "vue" in deps or "quasar" in deps

        if bundler == "vite":
            unit_libs = ["vitest", "jsdom", "@testing-library/user-event"]
            if uses_react:
                unit_libs += ["@testing-library/react", "@testing-library/jest-dom"]
            if uses_vue:
                unit_libs += ["@vue/test-utils", "@testing-library/vue"]
            e2e_libs = ["playwright"]
        else:
            unit_libs = ["jest", "@testing-library/user-event"]
            if is_ts:
                unit_libs += ["ts-jest", "@types/jest"]
            if uses_react:
                unit_libs += ["@testing-library/react", "@testing-library/jest-dom"]
            if uses_vue:
                unit_libs += ["@vue/test-utils", "@testing-library/vue"]
            e2e_libs = ["cypress"]

        libs = {"unit": unit_libs, "integration": unit_libs.copy(), "e2e": e2e_libs}
        print(f"Selected Libs: {libs} (bundler={bundler})")
        return {"selected_libraries": libs, "bundler": bundler, "available_libraries": list(deps.keys())}

    if state.get("project_type") == "python":
        libs = {"unit": ["pytest"], "integration": ["pytest"], "e2e": ["playwright"]}
        print(f"Selected Libs: {libs}")
        available = _available_python_libs(state)
        return {"selected_libraries": libs, "available_libraries": available}

    if state.get("project_type") == "go":
        libs = {"unit": ["testify"], "integration": ["testify"], "e2e": []}
        print(f"Selected Libs: {libs}")
        available = _available_go_libs(state)
        return {"selected_libraries": libs, "available_libraries": available}
    
    prompt = f"""
    Project Type: {state['project_type']}
    Dependency File Content:
    {pkg_content}
    
    Return a valid JSON object with 3 keys: "unit", "integration", "e2e".
    Values must be lists of library names (strings) compatible with {state['project_type']}.
    
    Example Output:
    {{
      "unit": ["pytest"],
      "integration": ["pytest-cov"],
      "e2e": ["playwright"]
    }}
    """
    
    response = llm.invoke(prompt)
    
    try:
        raw_data = parse_json_from_llm(response.content)
    except Exception:
        raw_data = {}

    # --- NORMALIZATION STEP ---
    # This guarantees the keys exist for the next agent
    # We look for common variations the LLM might have output
    libs = {
        "unit": raw_data.get("unit") or raw_data.get("Unit") or [],
        "integration": raw_data.get("integration") or raw_data.get("Integration") or [],
        "e2e": raw_data.get("e2e") or raw_data.get("E2E") or []
    }
    
    # If completely empty, apply defaults (Safe Fallback)
    if not any(libs.values()):
        print("⚠️ LLM failed to select libraries. Using defaults.")
        if state['project_type'] == "python":
            libs = {"unit": ["pytest"], "integration": ["pytest"], "e2e": ["playwright"]}
        else:
            libs = {"unit": ["jest"], "integration": ["jest"], "e2e": ["cypress"]}

    print(f"Selected Libs: {libs}")
    return {"selected_libraries": libs}
