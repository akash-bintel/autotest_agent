import json
import os
import re
from graphs.state import AgentState
from models.llm import get_llama_model
from tools.file_ops import read_file, write_file

def _strip_code_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        code = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", code)
        if code.endswith("```"):
            code = code[: -3]
    return code.strip()

def _parse_file_blocks(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"FILENAME:\s*(.+?)\s*\nCODE:\s*\n([\s\S]*?)(?=\nFILENAME:|\Z)",
        re.IGNORECASE,
    )
    blocks = []
    for match in pattern.finditer(text):
        filename = match.group(1).strip().strip("`")
        code = _strip_code_fences(match.group(2))
        if filename and code:
            blocks.append((filename, code))
    return blocks

def _safe_join(base_dir: str, rel_path: str) -> str | None:
    base_abs = os.path.abspath(base_dir)
    if os.path.isabs(rel_path):
        candidate = os.path.abspath(rel_path)
    else:
        candidate = os.path.abspath(os.path.join(base_abs, rel_path))
    if candidate == base_abs or candidate.startswith(base_abs + os.sep):
        return candidate
    return None

def _read_json(path: str) -> dict:
    try:
        return json.loads(read_file(path))
    except Exception:
        return {}

def _write_json(path: str, data: dict):
    write_file(path, json.dumps(data, indent=2) + "\n")

def _has_dep(pkg: dict, name: str) -> bool:
    deps = {}
    deps.update(pkg.get("dependencies", {}) or {})
    deps.update(pkg.get("devDependencies", {}) or {})
    deps.update(pkg.get("peerDependencies", {}) or {})
    return name in deps

def _determine_js_runner(state: AgentState, pkg: dict) -> tuple[str, str]:
    libs = state.get("selected_libraries", {})
    unit_libs = libs.get("unit", []) or []
    bundler = state.get("bundler", "unknown")
    if "vitest" in unit_libs or bundler == "vite" or _has_dep(pkg, "vitest"):
        return "vitest", "vitest run"
    return "jest", "jest"

def _vitest_config(ts: bool, use_jest_dom: bool) -> str:
    setup_line = '    setupFiles: ["./vitest.setup.' + ("ts" if ts else "js") + '"],\n' if use_jest_dom else ""
    return (
        'import { defineConfig } from "vitest/config";\n\n'
        "export default defineConfig({\n"
        "  test: {\n"
        '    environment: "jsdom",\n'
        "    globals: true,\n"
        '    include: ["**/*.{test,spec}.{ts,tsx,js,jsx}"],\n'
        f"{setup_line}"
        "  },\n"
        "});\n"
    )

def _jest_config(ts: bool, use_jest_dom: bool) -> str:
    lines = [
        "module.exports = {",
        '  testEnvironment: "jsdom",',
        '  testMatch: ["<rootDir>/**/?(*.)+(spec|test).(ts|tsx|js|jsx)"],',
    ]
    if ts:
        lines.append('  transform: { "^.+\\\\.(ts|tsx)$": ["ts-jest", { tsconfig: "tsconfig.json" }] },')
    if use_jest_dom:
        lines.append('  setupFilesAfterEnv: ["<rootDir>/jest.setup.js"],')
    lines.append("};")
    return "\n".join(lines) + "\n"

def _base_test_content(runner: str, ts: bool) -> str:
    if runner == "vitest":
        return (
            'import { describe, it, expect } from "vitest";\n\n'
            'describe("base", () => {\n'
            '  it("runs", () => {\n'
            "    expect(1 + 1).toBe(2);\n"
            "  });\n"
            "});\n"
        )
    return (
        'describe("base", () => {\n'
        '  test("runs", () => {\n'
        "    expect(1 + 1).toBe(2);\n"
        "  });\n"
        "});\n"
    )

def _build_js_scripts(runner: str, e2e_runner: str, is_ts: bool) -> dict:
    vitest_unit_cfg = "vitest.unit.config.ts" if is_ts else "vitest.unit.config.js"
    vitest_int_cfg = "vitest.int.config.ts" if is_ts else "vitest.int.config.js"
    unit_cmd = (
        f"vitest run --config {vitest_unit_cfg}"
        if runner == "vitest"
        else 'jest --testMatch "<rootDir>/src/**/?(*.)+(spec|test).(ts|tsx|js|jsx)" '
             '--testPathIgnorePatterns "/src/tests/" "/tests/" "/spec/"'
    )
    int_cmd = (
        f"vitest run --config {vitest_int_cfg}"
        if runner == "vitest"
        else 'jest --testMatch "<rootDir>/{tests,src/tests}/**/?(*.)+(spec|test).(ts|tsx|js|jsx)"'
    )
    if e2e_runner == "playwright":
        e2e_cmd = "playwright test spec"
    else:
        e2e_cmd = 'cypress run --spec "spec/**/*.spec.*"'

    return {
        "test:unit": unit_cmd,
        "test:int": int_cmd,
        "test:e2e": e2e_cmd,
        "test:all": "npm run test:unit && npm run test:int && npm run test:e2e",
        "test": "npm run test:unit",
    }

def _vitest_unit_config(ts: bool, use_jest_dom: bool) -> str:
    setup_line = '    setupFiles: ["./vitest.setup.' + ("ts" if ts else "js") + '"],\n' if use_jest_dom else ""
    return (
        'import { defineConfig } from "vitest/config";\n\n'
        "export default defineConfig({\n"
        "  test: {\n"
        '    environment: "jsdom",\n'
        "    globals: true,\n"
        '    include: ["src/**/*.{test,spec}.{ts,tsx,js,jsx}"],\n'
        '    exclude: ["src/tests/**", "tests/**", "spec/**"],\n'
        f"{setup_line}"
        "  },\n"
        "});\n"
    )

def _vitest_int_config(ts: bool, use_jest_dom: bool) -> str:
    setup_line = '    setupFiles: ["./vitest.setup.' + ("ts" if ts else "js") + '"],\n' if use_jest_dom else ""
    return (
        'import { defineConfig } from "vitest/config";\n\n'
        "export default defineConfig({\n"
        "  test: {\n"
        '    environment: "jsdom",\n'
        "    globals: true,\n"
        '    include: ["tests/**/*.{test,spec}.{ts,tsx,js,jsx}", "src/tests/**/*.{test,spec}.{ts,tsx,js,jsx}"],\n'
        f"{setup_line}"
        "  },\n"
        "});\n"
    )

def _fallback_config(state: AgentState, error_log: str) -> bool:
    project_path = state.get("project_path")
    p_type = state.get("project_type", "unknown")
    if not project_path or not os.path.isdir(project_path):
        return False

    if p_type in ["javascript", "typescript", "react"]:
        pkg_path = os.path.join(project_path, "package.json")
        pkg = _read_json(pkg_path)
        if not pkg:
            return False

        libs = state.get("selected_libraries", {})
        is_ts = p_type == "typescript"
        runner, _ = _determine_js_runner(state, pkg)
        e2e_runner = "playwright" if "playwright" in libs.get("e2e", []) else "cypress"
        use_jest_dom = "@testing-library/jest-dom" in libs.get("unit", [])
        scripts = pkg.get("scripts", {}) or {}
        desired_scripts = _build_js_scripts(runner, e2e_runner, is_ts)
        updated = False
        for key, value in desired_scripts.items():
            if scripts.get(key) != value:
                scripts[key] = value
                updated = True
        if updated:
            pkg["scripts"] = scripts
            _write_json(pkg_path, pkg)
            print("✅ Updated package.json test scripts for unit/int/e2e/all")

        use_cjs = pkg.get("type") == "module"
        if runner == "vitest":
            cfg_name = "vitest.config.ts" if is_ts else "vitest.config.js"
            cfg_path = os.path.join(project_path, cfg_name)
            if not os.path.exists(cfg_path):
                write_file(cfg_path, _vitest_config(is_ts, use_jest_dom))
                print(f"✅ Created {cfg_name}")

            unit_cfg = "vitest.unit.config.ts" if is_ts else "vitest.unit.config.js"
            unit_cfg_path = os.path.join(project_path, unit_cfg)
            if not os.path.exists(unit_cfg_path):
                write_file(unit_cfg_path, _vitest_unit_config(is_ts, use_jest_dom))
                print(f"✅ Created {unit_cfg}")

            int_cfg = "vitest.int.config.ts" if is_ts else "vitest.int.config.js"
            int_cfg_path = os.path.join(project_path, int_cfg)
            if not os.path.exists(int_cfg_path):
                write_file(int_cfg_path, _vitest_int_config(is_ts, use_jest_dom))
                print(f"✅ Created {int_cfg}")
        else:
            cfg_name = "jest.config.cjs" if use_cjs else "jest.config.js"
            cfg_path = os.path.join(project_path, cfg_name)
            if not os.path.exists(cfg_path):
                write_file(cfg_path, _jest_config(is_ts, use_jest_dom))
                print(f"✅ Created {cfg_name}")

        if use_jest_dom:
            if runner == "vitest":
                setup_name = "vitest.setup.ts" if is_ts else "vitest.setup.js"
                setup_path = os.path.join(project_path, setup_name)
                if not os.path.exists(setup_path):
                    write_file(setup_path, 'import "@testing-library/jest-dom";\n')
                    print(f"✅ Created {setup_name}")
            else:
                setup_name = "jest.setup.js"
                setup_path = os.path.join(project_path, setup_name)
                if not os.path.exists(setup_path):
                    write_file(setup_path, 'import "@testing-library/jest-dom";\n')
                    print(f"✅ Created {setup_name}")

        ext = "ts" if is_ts else "js"
        base_dir = os.path.join(project_path, "src", "autotest_base") if os.path.isdir(os.path.join(project_path, "src")) else os.path.join(project_path, "autotest_base")
        base_test_path = os.path.join(base_dir, f"base.test.{ext}")
        if not os.path.exists(base_test_path):
            write_file(base_test_path, _base_test_content(runner, is_ts))
            print(f"✅ Created {base_test_path}")
        return True

    if p_type == "python":
        base_test_path = os.path.join(project_path, "autotest_base", "test_base.py")
        if not os.path.exists(base_test_path):
            write_file(base_test_path, "def test_base():\n    assert 1 + 1 == 2\n")
            print(f"✅ Created {base_test_path}")
        return True

    if p_type == "go":
        base_test_path = os.path.join(project_path, "autotest_base", "base_test.go")
        if not os.path.exists(base_test_path):
            write_file(
                base_test_path,
                "package autotest_base\n\nimport \"testing\"\n\nfunc TestBase(t *testing.T) {\n    if 1+1 != 2 {\n        t.Fatal(\"math broke\")\n    }\n}\n",
            )
            print(f"✅ Created {base_test_path}")
        return True

    return False

def config_agent(state: AgentState) -> AgentState:
    error_log = state.get('latest_error_log', "No error log found.")
    retry_count = state.get('retry_count', 0)
    
    print(f"--- ⚙️ Agent: Fixing Config (Attempt {retry_count + 1}) ---")
    
    current_files_context = ""
    if retry_count > 0:
        # Example: Read the generated config file
        # current_files_context = read_file(os.path.join(state['project_path'], 'jest.config.js'))
        pass
    llm = get_llama_model()
    
    # The Prompt is the "Brain" of the retry
    system_msg = "You are a specialized DevOps agent that fixes broken test configurations."
    bundler = state.get("bundler", "unknown")
    user_msg = f"""
    DEBUGGING SESSION (Attempt {retry_count + 1})
    Project Type: {state['project_type']}
    Bundler: {bundler}
    Selected Libraries: {state['selected_libraries']}
    
    [PREVIOUS ERROR]
    {error_log}

    [INSTRUCTION]
    The previous configuration failed with the error above. 
    Common fixes for {state['project_type']}:
    - If 'command not found', check if the test script exists in package.json.
    - If 'module not found', ensure the imports match the file structure.
    - If 'ReferenceError', check if the testing environment (jsdom/node) is set correctly.
    
    TASK:
    1. Analyze the error log above.
    2. Update the test configuration files (e.g., jest.config.js, package.json scripts) to fix the error.
    3. Ensure the base test case is valid for the framework.
    
    Generate the CORRECTED configuration files and a simple base test.
    Return files in this format:
    FILENAME: <name>
    CODE:
    <code>
    """
    
    try:
        response = llm.invoke([("system", system_msg), ("user", user_msg)]).content
    except Exception as exc:
        response = ""
        print(f"⚠️ Config LLM error: {exc}. Falling back to deterministic config.")

    file_blocks = _parse_file_blocks(response) if response else []
    if file_blocks:
        for filename, code in file_blocks:
            target_path = _safe_join(state['project_path'], filename)
            if not target_path:
                print(f"⚠️ Skipping unsafe path from LLM: {filename}")
                continue
            write_file(target_path, code)
            print(f"✅ Updated: {target_path}")
    wrote = _fallback_config(state, error_log)
    if not wrote and not file_blocks:
        print("⚠️ No config files parsed and fallback could not be applied.")

    return {
        "retry_count": retry_count + 1,
        "test_phase": "base",
        "latest_error_log": None
    }
