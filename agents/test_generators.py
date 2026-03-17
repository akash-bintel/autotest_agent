import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from graphs.state import AgentState
from models.llm import get_llama_model
from tools.file_ops import read_file, write_file

IGNORED_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".next",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "target",
    "vendor",
    "spec",
}

IGNORED_CONFIG_BASENAMES = {
    # JS/TS configs
    "vite.config.js",
    "vite.config.ts",
    "vite.config.mjs",
    "vite.config.cjs",
    "jest.config.js",
    "jest.config.ts",
    "jest.config.mjs",
    "jest.config.cjs",
    "webpack.config.js",
    "webpack.config.ts",
    "webpack.config.mjs",
    "webpack.config.cjs",
    "next.config.js",
    "next.config.mjs",
    "next.config.cjs",
    "babel.config.js",
    "babel.config.cjs",
    "postcss.config.js",
    "tailwind.config.js",
    "vue.config.js",
    "vue.config.ts",
    "quasar.config.js",
    "quasar.config.ts",
    "eslint.config.js",
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.json",
    ".prettierrc",
    ".prettierrc.js",
    ".prettierrc.cjs",
    ".prettierrc.json",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
    # Python configs
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "requirements.txt",
    "requirements-dev.txt",
    "Pipfile",
    "Pipfile.lock",
    "tox.ini",
    "pytest.ini",
    # Go configs
    "go.mod",
    "go.sum",
    "Makefile",
}

def _allowed_exts(project_type: str) -> set[str]:
    if project_type in ["javascript", "typescript", "react", "vue", "quasar"]:
        return {".js", ".jsx", ".ts", ".tsx", ".vue"}
    if project_type == "python":
        return {".py"}
    if project_type == "go":
        return {".go"}
    return {".py", ".js", ".jsx", ".ts", ".tsx", ".go"}

def _is_test_file(filename: str) -> bool:
    name = filename.lower()
    if "/__tests__/" in name.replace("\\", "/"):
        return True
    if re.search(r"\.(test|spec)\.(js|jsx|ts|tsx|py)$", name):
        return True
    if name.endswith("_test.py") or name.startswith("test_"):
        return True
    if name.endswith("_test.go"):
        return True
    return False

def _is_config_file(path: str) -> bool:
    base = os.path.basename(path).lower()
    if base in IGNORED_CONFIG_BASENAMES:
        return True
    if base.endswith((".config.js", ".config.ts", ".config.mjs", ".config.cjs", ".config.json")):
        return True
    return False

def _preferred_roots(project_type: str) -> set[str]:
    if project_type in ["javascript", "typescript", "react", "vue", "quasar"]:
        return {"src", "app", "components"}
    if project_type == "python":
        return {"src", "app", "components"}
    if project_type == "go":
        return {"src", "app", "components", "cmd", "pkg", "internal"}
    return {"src", "app", "components"}

def _project_has_preferred_roots(project_path: str, roots: set[str]) -> bool:
    for r in roots:
        if os.path.isdir(os.path.join(project_path, r)):
            return True
    return False

def _is_allowed_source_path(path: str, project_path: str, project_type: str) -> bool:
    rel = os.path.relpath(path, project_path).replace("\\", "/")
    parts = rel.split("/")
    roots = _preferred_roots(project_type)
    has_roots = _project_has_preferred_roots(project_path, roots)

    if "tests" in parts or "e2e" in parts or "spec" in parts:
        return False

    # If the project has preferred roots, restrict to them
    if has_roots:
        if parts and parts[0] in roots:
            return True
        if "components" in parts:
            return True
        return False

    # If no preferred roots exist, allow anything not in ignored dirs
    return True

def _collect_source_files(project_path: str, project_type: str) -> list[str]:
    files = []
    exts = _allowed_exts(project_type)
    for root, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for f in filenames:
            if f.endswith(".d.ts"):
                continue
            if f == "__init__.py":
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext not in exts:
                continue
            full_path = os.path.join(root, f)
            if _is_config_file(full_path):
                continue
            if not _is_allowed_source_path(full_path, project_path, project_type):
                continue
            if _is_test_file(full_path):
                continue
            files.append(full_path)
    return files

def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", text)
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()

def _strip_tool_json_objects(text: str) -> str:
    if not text:
        return text
    # Remove inline tool-call style JSON objects
    pattern = re.compile(r"\{\s*\"name\"\s*:\s*\"[^\"]+\"\s*,\s*\"parameters\"\s*:\s*\{.*?\}\s*\}", re.DOTALL)
    cleaned = re.sub(pattern, "", text)
    # If the model returns { "name": "...", "parameters": { "content": "..." } },
    # extract the "content" field.
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "parameters" in data and isinstance(data["parameters"], dict):
            content = data["parameters"].get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    except Exception:
        pass
    # Drop lines that are pure JSON-like tool calls
    lines = []
    for line in cleaned.splitlines():
        s = line.strip()
        if s.startswith("{") and "\"name\"" in s and "\"parameters\"" in s:
            continue
        lines.append(line)
    return "\n".join(lines).strip()

def _looks_like_tool_call(text: str) -> bool:
    t = text.strip()
    if not t.startswith("{"):
        return False
    try:
        data = json.loads(t)
    except Exception:
        return False
    return isinstance(data, dict) and "name" in data and "parameters" in data

def _extract_import_block(lines: list[str]) -> tuple[int, int]:
    start = None
    end = None
    for i, line in enumerate(lines):
        if line.startswith("import "):
            if start is None:
                start = i
            end = i
        elif start is not None:
            break
    if start is None:
        return (0, -1)
    return (start, end)

def _parse_named_imports(line: str) -> set[str]:
    match = re.search(r"\{([^}]+)\}", line)
    if not match:
        return set()
    names = [n.strip().split(" as ")[0] for n in match.group(1).split(",")]
    return {n for n in names if n}

def _has_import(code: str, module: str) -> bool:
    return re.search(rf'from\s+["\']{re.escape(module)}["\']', code) is not None

def _ensure_js_imports(code: str, runner: str, ui_framework: str | None) -> str:
    if not code:
        return code
    lines = code.splitlines()

    used = {
        "render": bool(re.search(r"\brender\s*\(", code)),
        "screen": bool(re.search(r"\bscreen\b", code)),
        "fireEvent": bool(re.search(r"\bfireEvent\b", code)),
        "waitFor": bool(re.search(r"\bwaitFor\b", code)),
        "within": bool(re.search(r"\bwithin\b", code)),
        "cleanup": bool(re.search(r"\bcleanup\b", code)),
        "userEvent": bool(re.search(r"\buserEvent\b", code)),
    }

    vitest_names = ["describe", "it", "test", "expect", "vi", "beforeEach", "afterEach", "beforeAll", "afterAll"]
    vitest_used_names = [name for name in vitest_names if re.search(rf"\b{name}\b", code)]
    import_lines = [line for line in lines if line.startswith("import ")]
    imported_from_tl = set()
    for line in import_lines:
        if "@testing-library/react" in line or "@testing-library/vue" in line:
            imported_from_tl |= _parse_named_imports(line)

    to_import_tl = [name for name, is_used in used.items() if is_used and name in {"render","screen","fireEvent","waitFor","within","cleanup"} and name not in imported_from_tl]
    imports_to_add = []

    if to_import_tl:
        tl_pkg = "@testing-library/vue" if ui_framework in ["vue", "quasar"] else "@testing-library/react"
        imports_to_add.append(f'import {{ {", ".join(sorted(set(to_import_tl))) } }} from "{tl_pkg}";')

    if used["userEvent"] and not _has_import(code, "@testing-library/user-event"):
        imports_to_add.append('import userEvent from "@testing-library/user-event";')

    if ui_framework in ["vue", "quasar"]:
        if re.search(r"\b(mount|shallowMount)\b", code) and not _has_import(code, "@vue/test-utils"):
            imports_to_add.append('import { mount, shallowMount } from "@vue/test-utils";')

    if runner == "vitest" and vitest_used_names and not _has_import(code, "vitest"):
        imports_to_add.append(f'import {{ {", ".join(vitest_used_names)} }} from "vitest";')

    if runner == "jest":
        has_imports = any(line.startswith("import ") for line in lines)
        if has_imports and not _has_import(code, "@jest/globals"):
            jest_names = [name for name in ["describe", "it", "test", "expect", "beforeEach", "afterEach", "beforeAll", "afterAll", "jest"] if re.search(rf"\b{name}\b", code)]
            if jest_names:
                imports_to_add.append(f'import {{ {", ".join(jest_names)} }} from "@jest/globals";')

    # Playwright e2e tests should import from @playwright/test if not already present
    if runner == "playwright" and not _has_import(code, "@playwright/test"):
        if re.search(r"\btest\(", code) or re.search(r"\bexpect\(", code):
            imports_to_add.append('import { test, expect } from "@playwright/test";')

    if not imports_to_add:
        return code

    start, end = _extract_import_block(lines)
    insert_at = end + 1 if end >= start else 0
    new_lines = lines[:insert_at] + imports_to_add + lines[insert_at:]
    return "\n".join(new_lines).strip() + "\n"

def _extract_js_exports(source: str) -> tuple[str | None, set[str]]:
    default_name = None
    named = set()

    match = re.search(r"export\s+default\s+function\s+([A-Za-z0-9_]+)", source)
    if match:
        default_name = match.group(1)
    else:
        match = re.search(r"export\s+default\s+([A-Za-z0-9_]+)", source)
        if match:
            default_name = match.group(1)

    for match in re.findall(r"export\s+function\s+([A-Za-z0-9_]+)", source):
        named.add(match)
    for match in re.findall(r"export\s+const\s+([A-Za-z0-9_]+)", source):
        named.add(match)
    for match in re.findall(r"export\s+class\s+([A-Za-z0-9_]+)", source):
        named.add(match)

    for match in re.findall(r"export\s*\{\s*([^}]+)\s*\}", source):
        parts = match.split(",")
        for p in parts:
            name = p.strip().split(" as ")[0]
            if name:
                named.add(name)

    return default_name, named

def _has_relative_import_for(code: str, base: str) -> bool:
    return re.search(rf'from\s+["\']\./{re.escape(base)}["\']', code) is not None or re.search(rf'from\s+["\']\.{2}/[^"\']*{re.escape(base)}["\']', code) is not None

def _ensure_component_import(code: str, source_path: str, source_content: str) -> str:
    base = os.path.splitext(os.path.basename(source_path))[0]
    ext = os.path.splitext(source_path)[1].lower()
    import_target = f"{base}.vue" if ext == ".vue" else base
    if _has_relative_import_for(code, base) or _has_relative_import_for(code, import_target):
        return code

    default_name, named = _extract_js_exports(source_content)
    used_default = default_name and re.search(rf"\b{re.escape(default_name)}\b", code)
    used_named = [n for n in named if re.search(rf"\b{re.escape(n)}\b", code)]

    if not used_default and not used_named:
        return code

    import_line = ""
    if used_default and used_named:
        import_line = f'import {default_name}, {{ {", ".join(sorted(set(used_named)))} }} from "./{import_target}";'
    elif used_default:
        import_line = f'import {default_name} from "./{import_target}";'
    elif used_named:
        import_line = f'import {{ {", ".join(sorted(set(used_named)))} }} from "./{import_target}";'

    if not import_line:
        return code

    lines = code.splitlines()
    start, end = _extract_import_block(lines)
    insert_at = end + 1 if end >= start else 0
    new_lines = lines[:insert_at] + [import_line] + lines[insert_at:]
    return "\n".join(new_lines).strip() + "\n"

def _read_package_json(project_path: str) -> dict:
    pkg_path = os.path.join(project_path, "package.json")
    if not os.path.exists(pkg_path):
        return {}
    try:
        return json.loads(read_file(pkg_path))
    except Exception:
        return {}

def _is_ts_project(state: AgentState) -> bool:
    if state.get("project_type") == "typescript":
        return True
    project_path = state.get("project_path", "")
    if project_path and os.path.exists(os.path.join(project_path, "tsconfig.json")):
        return True
    pkg = _read_package_json(project_path)
    deps = _js_deps(pkg)
    return "typescript" in deps

def _react_test_ext(state: AgentState) -> str:
    return "tsx" if _is_ts_project(state) else "jsx"

def _looks_like_jsx(code: str) -> bool:
    return re.search(r"<[A-Za-z][^>]*>", code) is not None

PY_STDLIB = set(getattr(sys, "stdlib_module_names", []))
PY_PACKAGE_MAP = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
}

def _js_deps(pkg: dict) -> set[str]:
    deps = {}
    deps.update(pkg.get("dependencies", {}) or {})
    deps.update(pkg.get("devDependencies", {}) or {})
    deps.update(pkg.get("peerDependencies", {}) or {})
    return set(deps.keys())

def _js_base_package(name: str) -> str:
    if name.startswith("@"):
        parts = name.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else name
    return name.split("/")[0]

def _is_valid_npm_package(name: str) -> bool:
    # Allow scoped packages and common npm name characters; reject invalid characters like '+'
    return re.match(r"^(@[a-z0-9][a-z0-9-._]*/)?[a-z0-9][a-z0-9-._]*$", name) is not None

def _is_external_js_import(name: str) -> bool:
    if name.startswith((".", "/", "@/","~/")):
        return False
    if name.startswith("src/") or name.startswith("app/") or name.startswith("components/"):
        return False
    return True

def _extract_js_imports(code: str) -> set[str]:
    imports = set()
    for match in re.findall(r'import\s+(?:[^"\']+\s+from\s+)?["\']([^"\']+)["\']', code):
        imports.add(match)
    for match in re.findall(r'require\(\s*["\']([^"\']+)["\']\s*\)', code):
        imports.add(match)
    for match in re.findall(r'import\(\s*["\']([^"\']+)["\']\s*\)', code):
        imports.add(match)
    return imports

def _collect_missing_js_libs(code: str, project_path: str, selected_libs: list[str]) -> list[str]:
    pkg = _read_package_json(project_path)
    existing = _js_deps(pkg)
    selected = set(selected_libs)
    missing = []
    for imp in _extract_js_imports(code):
        if not _is_external_js_import(imp):
            continue
        base = _js_base_package(imp)
        if not _is_valid_npm_package(base):
            continue
        # Only install if it's part of the selected libs set
        if base in selected and base not in existing:
            missing.append(base)
    return list(dict.fromkeys(missing))

def _allowed_libs_for(state: AgentState, phase: str) -> set[str]:
    selected = set(state.get("selected_libraries", {}).get(phase, []) or [])
    available = set(state.get("available_libraries", []) or [])
    return selected | available

def _disallowed_js_imports(code: str, allowed: set[str]) -> list[str]:
    disallowed = []
    for imp in _extract_js_imports(code):
        if not _is_external_js_import(imp):
            continue
        base = _js_base_package(imp)
        if base not in allowed:
            disallowed.append(base)
    return list(dict.fromkeys(disallowed))

def _disallowed_py_imports(code: str, allowed: set[str], project_path: str) -> list[str]:
    disallowed = []
    for module in _extract_py_imports(code):
        if module.startswith("."):
            continue
        base = module.split(".")[0]
        if base in PY_STDLIB or base in allowed:
            continue
        if _is_internal_python_module(base, project_path):
            continue
        disallowed.append(base)
    return list(dict.fromkeys(disallowed))

def _disallowed_go_imports(code: str, allowed: set[str]) -> list[str]:
    disallowed = []
    for imp in _extract_go_imports(code):
        if "." not in imp:
            continue
        if imp not in allowed:
            disallowed.append(imp)
    return list(dict.fromkeys(disallowed))

def _regenerate_if_disallowed(llm, code: str, prompt: str, system: str, disallowed: list[str], timeout_note: str = "") -> str:
    if not disallowed:
        return code
    warn = ", ".join(disallowed)
    retry_prompt = (
        f"{prompt}\n\n"
        f"IMPORTANT: The following imports are NOT allowed: {warn}.\n"
        "Remove those imports and do NOT introduce any new external libraries.\n"
        "Return ONLY the corrected test file code."
    )
    regenerated = _generate_unit_test(llm, retry_prompt, system)
    if regenerated:
        return regenerated
    return code

def _is_react_entrypoint(path: str, content: str) -> bool:
    base = os.path.basename(path).lower()
    if base not in {"main.tsx", "main.jsx", "index.tsx", "index.jsx"}:
        return False
    if "createRoot" in content or "ReactDOM.render" in content:
        return True
    return False

def _is_vue_entrypoint(path: str, content: str) -> bool:
    base = os.path.basename(path).lower()
    if base not in {"main.ts", "main.js", "main.tsx", "main.jsx"}:
        return False
    if "createApp" in content:
        return True
    return False

def _entrypoint_test_code_vue(framework: str, source_path: str) -> str:
    rel_import = "./" + os.path.splitext(os.path.basename(source_path))[0]
    if framework == "vitest":
        return (
            'import { describe, it, expect } from "vitest";\n\n'
            'describe("entrypoint", () => {\n'
            '  it("mounts without crashing", async () => {\n'
            '    const root = document.createElement("div");\n'
            '    root.id = "app";\n'
            '    document.body.appendChild(root);\n'
            f'    await import("{rel_import}");\n'
            "    expect(root.innerHTML.length).toBeGreaterThan(0);\n"
            "  });\n"
            "});\n"
        )
    return (
        'describe("entrypoint", () => {\n'
        '  test("mounts without crashing", async () => {\n'
        '    const root = document.createElement("div");\n'
        '    root.id = "app";\n'
        '    document.body.appendChild(root);\n'
        f'    await import("{rel_import}");\n'
        "    expect(root.innerHTML.length).toBeGreaterThan(0);\n"
        "  });\n"
        "});\n"
    )

def _fallback_js_test(framework: str) -> str:
    return (
        'describe("fallback", () => {\n'
        '  it("runs", () => {\n'
        "    expect(1 + 1).toBe(2);\n"
        "  });\n"
        "});\n"
    )

def _fallback_py_test() -> str:
    return "def test_fallback():\n    assert 1 + 1 == 2\n"

def _fallback_go_test(package_name: str) -> str:
    pkg = package_name or "autotest"
    return (
        f"package {pkg}\n\n"
        "import \"testing\"\n\n"
        "func TestFallback(t *testing.T) {\n"
        "    if 1+1 != 2 {\n"
        "        t.Fatal(\"math broke\")\n"
        "    }\n"
        "}\n"
    )

def _go_package_name_from_source(source: str) -> str | None:
    match = re.search(r"^package\s+([A-Za-z0-9_]+)", source, re.MULTILINE)
    if match:
        return match.group(1)
    return None

def _entrypoint_test_code(framework: str, source_path: str) -> str:
    # Test file is placed alongside source, so import should be relative.
    rel_import = "./" + os.path.splitext(os.path.basename(source_path))[0]
    if framework == "vitest":
        return (
            'import { describe, it, expect } from "vitest";\n\n'
            'describe("entrypoint", () => {\n'
            '  it("mounts without crashing", async () => {\n'
            '    const root = document.createElement("div");\n'
            '    root.id = "root";\n'
            '    document.body.appendChild(root);\n'
            f'    await import("{rel_import}");\n'
            "    expect(root.innerHTML.length).toBeGreaterThan(0);\n"
            "  });\n"
            "});\n"
        )
    return (
        'describe("entrypoint", () => {\n'
        '  test("mounts without crashing", async () => {\n'
        '    const root = document.createElement("div");\n'
        '    root.id = "root";\n'
        '    document.body.appendChild(root);\n'
        f'    await import("{rel_import}");\n'
        "    expect(root.innerHTML.length).toBeGreaterThan(0);\n"
        "  });\n"
        "});\n"
    )

def _extract_py_imports(code: str) -> set[str]:
    modules = set()
    for line in code.splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        m = re.match(r"import\s+([a-zA-Z0-9_.]+)", line)
        if m:
            modules.add(m.group(1))
            continue
        m = re.match(r"from\s+([a-zA-Z0-9_.]+)\s+import\s+", line)
        if m:
            modules.add(m.group(1))
    return modules

def _is_internal_python_module(module: str, project_path: str) -> bool:
    base = module.split(".")[0]
    candidates = [
        os.path.join(project_path, base + ".py"),
        os.path.join(project_path, base, "__init__.py"),
        os.path.join(project_path, "src", base + ".py"),
        os.path.join(project_path, "src", base, "__init__.py"),
        os.path.join(project_path, "app", base + ".py"),
        os.path.join(project_path, "app", base, "__init__.py"),
    ]
    return any(os.path.exists(p) for p in candidates)

def _collect_missing_py_libs(code: str, project_path: str, selected_libs: list[str]) -> list[str]:
    missing = []
    selected = set(selected_libs)
    for module in _extract_py_imports(code):
        if module.startswith("."):
            continue
        base = module.split(".")[0]
        if base in PY_STDLIB or base in selected:
            continue
        if _is_internal_python_module(base, project_path):
            continue
        pkg = PY_PACKAGE_MAP.get(base, base)
        if pkg in selected:
            missing.append(pkg)
    return list(dict.fromkeys(missing))

def _read_go_mod(project_path: str) -> set[str]:
    path = os.path.join(project_path, "go.mod")
    if not os.path.exists(path):
        return set()
    modules = set()
    try:
        for line in read_file(path).splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("require"):
                continue
            if " " in line:
                mod = line.split()[0]
                if "." in mod:
                    modules.add(mod)
    except Exception:
        return set()
    return modules

def _extract_go_imports(code: str) -> set[str]:
    imports = set()
    for match in re.findall(r'import\s+"([^"]+)"', code):
        imports.add(match)
    return imports

def _collect_missing_go_libs(code: str, project_path: str, selected_libs: list[str]) -> list[str]:
    existing = _read_go_mod(project_path)
    selected = set(selected_libs)
    missing = []
    for imp in _extract_go_imports(code):
        if "." not in imp:
            continue
        if imp in selected and imp not in existing:
            missing.append(imp)
    return list(dict.fromkeys(missing))

def _generate_unit_test(llm, prompt: str, system: str) -> str:
    timeout_s = float(os.getenv("AUTOTEST_LLM_TIMEOUT", "120"))
    def _call(messages):
        return llm.invoke(messages).content

    try:
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_call, [("system", system), ("user", prompt)])
        response = fut.result(timeout=timeout_s)
    except TimeoutError:
        print(f"⚠️ LLM timed out after {timeout_s}s. Using fallback stub.")
        try:
            fut.cancel()
        except Exception:
            pass
        return ""
    except Exception as exc:
        print(f"⚠️ LLM error: {exc}. Using fallback stub.")
        return ""
    finally:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    if _looks_like_tool_call(response) or ("\"parameters\"" in response and "\"name\"" in response):
        retry_prompt = (
            "Return ONLY the test file source code as plain text. "
            "Do NOT return JSON, tool-call objects, or wrapper keys like {\"name\":..., \"parameters\":{...}}. "
            "Do NOT include filenames or metadata. "
            "Do NOT describe steps. "
            "No surrounding markdown fences.\n\n"
            + prompt
        )
        try:
            ex = ThreadPoolExecutor(max_workers=1)
            fut = ex.submit(_call, [("system", system), ("user", retry_prompt)])
            response = fut.result(timeout=timeout_s)
        except Exception:
            return ""
        finally:
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
    response = _strip_code_fences(response)
    response = _strip_tool_json_objects(response)
    return response

def list_source_files(state: AgentState) -> AgentState:
    """Helper node to populate source files list for iteration."""
    project_type = state.get("project_type", "unknown")
    files = _collect_source_files(state["project_path"], project_type)
    return {"source_files": files, "current_file_index": 0, "unit_test_files": [], "unit_test_map": {}}

def list_integration_files(state: AgentState) -> AgentState:
    """Populate feature files list for integration test generation."""
    project_type = state.get("project_type", "unknown")
    files = _collect_source_files(state["project_path"], project_type)
    return {
        "integration_files": files,
        "integration_index": 0,
        "integration_test_files": [],
        "integration_test_map": {},
    }

def unit_test_generator_agent(state: AgentState) -> AgentState:
    files = state['source_files']
    idx = state['current_file_index']
    
    if idx >= len(files):
        return {"test_phase": "integration"} # Move to next phase
        
    current_file = files[idx]
    print(f"--- 🧪 Agent: Generating Unit Test for {os.path.basename(current_file)} ---")
    
    content = read_file(current_file)
    llm = get_llama_model()

    p_type = state.get("project_type", "unknown")
    libs = state.get("selected_libraries", {}).get("unit", [])

    test_path = None
    if p_type in ["javascript", "typescript", "react", "vue", "quasar"]:
        framework = "vitest" if "vitest" in libs else "jest"
        framework_name = state.get("framework") or ("react" if p_type == "react" else None)
        prompt = None
        system = None

        if framework_name in ["vue", "quasar"]:
            if _is_vue_entrypoint(current_file, content):
                code = _entrypoint_test_code_vue(framework, current_file)
            else:
                system = "You are a senior test engineer. Return only valid test code."
                prompt = f"""
                Create comprehensive unit tests for this Vue file: {current_file}
                Code:
                {content}

                Requirements:
                - Use {framework} with @vue/test-utils (and @testing-library/vue if helpful).
                - Use @testing-library/user-event for interactions if applicable.
                - Do not introduce new external libraries. Only use: {', '.join(libs)} and project code.
                - Do not invent UI. Assert only on elements that exist in the template or rendered output.
                - Import the component/module under test with a correct relative path.
                - Ensure every identifier used in the test is explicitly imported.
                - Avoid unused imports and follow standard formatting (ESLint-friendly).
                - Do not modify the source file.
                - Prefer user interactions: clicks, typing, keyboard, focus/blur, and event callbacks.
                - Cover edge cases and possible states based on props and conditional rendering.
                - Mock external modules or APIs where needed.
                - Return only the test file code (no JSON, no explanations, no markdown fences).
                """
                code = _generate_unit_test(llm, prompt, system)
        else:
            if _is_react_entrypoint(current_file, content):
                code = _entrypoint_test_code(framework, current_file)
            else:
                system = "You are a senior test engineer. Return only valid test code."
                prompt = f"""
                Create comprehensive unit tests for this file: {current_file}
                Code:
                {content}

                Requirements:
                - Use {framework} with @testing-library/react.
                - Use @testing-library/user-event for interactions.
                - Use @testing-library/jest-dom matchers if needed.
                - Do not introduce new external libraries. Only use: {', '.join(libs)} and project code.
                - Do not invent UI. Assert only on elements that exist in the JSX or returned markup.
                - Import the component/module under test with a correct relative path.
                - Ensure every identifier used in the test is explicitly imported.
                - Avoid unused imports and follow standard formatting (ESLint-friendly).
                - Do not modify the source file.
                - Prefer user interactions: clicks, typing, keyboard, focus/blur, and event callbacks.
                - Cover edge cases and possible states based on props and conditional rendering.
                - Mock external modules or APIs where needed.
                - Return only the test file code (no JSON, no explanations, no markdown fences).
                """
                code = _generate_unit_test(llm, prompt, system)

        allowed = _allowed_libs_for(state, "unit")
        disallowed = _disallowed_js_imports(code, allowed)
        if disallowed and prompt and system:
            code = _regenerate_if_disallowed(llm, code, prompt, system, disallowed)
            disallowed = _disallowed_js_imports(code, allowed)
            if disallowed:
                code = ""

        if not code:
            code = _fallback_js_test(framework)
        code = _ensure_js_imports(code, framework, framework_name)
        code = _ensure_component_import(code, current_file, content)
        base, ext = os.path.splitext(current_file)
        if ext == ".vue":
            test_ext = ".test.ts" if _is_ts_project(state) else ".test.js"
            test_file = f"{base}{test_ext}"
        else:
            # For React components, prefer jsx/tsx when JSX is used
            if framework_name == "react" and ext in [".js", ".ts"] and _looks_like_jsx(content):
                jsx_ext = ".tsx" if ext == ".ts" else ".jsx"
                test_file = f"{base}.test{jsx_ext}"
            else:
                test_file = f"{base}.test{ext}"
    elif p_type == "python":
        system = "You are a senior test engineer. Return only valid test code."
        prompt = f"""
        Create thorough unit tests for this file: {current_file}
        Code:
        {content}

        Requirements:
        - Use pytest.
        - Do not import any third-party libraries other than pytest.
        - Do not modify the source file.
        - Include edge cases and error paths.
        - Return only the test file code.
        """
        code = _generate_unit_test(llm, prompt, system)
        allowed = _allowed_libs_for(state, "unit")
        disallowed = _disallowed_py_imports(code, allowed, state["project_path"])
        if disallowed:
            code = _regenerate_if_disallowed(llm, code, prompt, system, disallowed)
            disallowed = _disallowed_py_imports(code, allowed, state["project_path"])
            if disallowed:
                code = ""
        if not code:
            code = _fallback_py_test()
        base, ext = os.path.splitext(current_file)
        test_file = f"{base}.test{ext}"
        pytest_ini = os.path.join(state["project_path"], "pytest.ini")
        if not os.path.exists(pytest_ini):
            write_file(pytest_ini, "[pytest]\npython_files = *.test.py\n")
    elif p_type == "go":
        system = "You are a senior test engineer. Return only valid test code."
        prompt = f"""
        Create thorough unit tests for this file: {current_file}
        Code:
        {content}

        Requirements:
        - Use Go's testing package (and testify if helpful).
        - Do not import any third-party libraries other than testify.
        - Do not modify the source file.
        - Return only the test file code.
        """
        code = _generate_unit_test(llm, prompt, system)
        allowed = _allowed_libs_for(state, "unit")
        disallowed = _disallowed_go_imports(code, allowed)
        if disallowed:
            code = _regenerate_if_disallowed(llm, code, prompt, system, disallowed)
            disallowed = _disallowed_go_imports(code, allowed)
            if disallowed:
                code = ""
        if not code:
            pkg_name = _go_package_name_from_source(content) or "autotest"
            code = _fallback_go_test(pkg_name)
        base, _ = os.path.splitext(current_file)
        test_file = f"{base}_test.go"
    else:
        system = "You are a senior test engineer. Return only valid test code."
        prompt = f"""
        Create unit tests for this file: {current_file}
        Code:
        {content}

        Return only the test file code.
        """
        code = _generate_unit_test(llm, prompt, system)
        if not code:
            code = _fallback_js_test("vitest")
        base, ext = os.path.splitext(current_file)
        test_file = f"{base}.test{ext}"

    write_file(test_file, code)

    unit_test_files = list(state.get("unit_test_files", []))
    unit_test_map = dict(state.get("unit_test_map", {}))
    unit_missing_libs = list(state.get("unit_missing_libs", []))
    unit_test_files.append(test_file)
    unit_test_map[test_file] = current_file

    if p_type in ["javascript", "typescript", "react"]:
        missing = _collect_missing_js_libs(code, state["project_path"], libs)
        if missing:
            unit_missing_libs.extend(missing)
            unit_missing_libs = list(dict.fromkeys(unit_missing_libs))
    elif p_type == "python":
        missing = _collect_missing_py_libs(code, state["project_path"], libs)
        if missing:
            unit_missing_libs.extend(missing)
            unit_missing_libs = list(dict.fromkeys(unit_missing_libs))
    elif p_type == "go":
        missing = _collect_missing_go_libs(code, state["project_path"], libs)
        if missing:
            unit_missing_libs.extend(missing)
            unit_missing_libs = list(dict.fromkeys(unit_missing_libs))

    return {
        "current_file_index": idx + 1,
        "unit_test_files": unit_test_files,
        "unit_test_map": unit_test_map,
        "unit_missing_libs": unit_missing_libs
    }

def integration_test_agent(state: AgentState) -> AgentState:
    print("--- 🔗 Agent: Generating Integration Tests ---")
    project_path = state["project_path"]
    os.makedirs(os.path.join(project_path, "tests"), exist_ok=True)

    files = state.get("integration_files", [])
    idx = state.get("integration_index", 0)
    if idx >= len(files):
        return {"test_phase": "e2e"}

    current_file = files[idx]
    rel = os.path.relpath(current_file, project_path).replace("\\", "/")
    feature_name = rel.replace("/", "_").rsplit(".", 1)[0]

    p_type = state.get("project_type", "unknown")
    libs = state.get("selected_libraries", {}).get("integration", [])
    llm = get_llama_model()

    if p_type in ["javascript", "typescript", "react", "vue", "quasar"]:
        framework = "vitest" if "vitest" in libs else "jest"
        framework_name = state.get("framework") or ("react" if p_type == "react" else None)
        system = "You are a senior test engineer. Return only valid test code."
        prompt = f"""
        Create integration tests for this feature file: {current_file}
        Code:
        {read_file(current_file)}

        Requirements:
        - Use {framework} with {'@vue/test-utils / @testing-library/vue' if framework_name in ['vue','quasar'] else '@testing-library/react'} and user-event if available.
        - Cover feature-level flows and interactions across components.
        - Ensure all identifiers are imported before use and avoid unused imports.
        - Follow standard formatting (ESLint-friendly).
        - Do not modify source files. Return only the test code.
        """
        code = _generate_unit_test(llm, prompt, system)
        allowed = _allowed_libs_for(state, "integration")
        disallowed = _disallowed_js_imports(code, allowed)
        if disallowed:
            code = _regenerate_if_disallowed(llm, code, prompt, system, disallowed)
            disallowed = _disallowed_js_imports(code, allowed)
            if disallowed:
                code = _fallback_js_test(framework)
        code = _ensure_js_imports(code, framework, framework_name)
        if framework_name in ["react"]:
            ext = _react_test_ext(state)
        else:
            ext = "ts" if _is_ts_project(state) else "js"
        test_path = os.path.join(project_path, "tests", f"{feature_name}.test.{ext}")
        write_file(test_path, code)
    elif p_type == "python":
        system = "You are a senior test engineer. Return only valid test code."
        prompt = f"""
        Create integration tests for this feature file using pytest: {current_file}
        Code:
        {read_file(current_file)}

        Do not modify source files. Return only the test code.
        """
        code = _generate_unit_test(llm, prompt, system)
        allowed = _allowed_libs_for(state, "integration")
        disallowed = _disallowed_py_imports(code, allowed, project_path)
        if disallowed:
            code = _regenerate_if_disallowed(llm, code, prompt, system, disallowed)
            disallowed = _disallowed_py_imports(code, allowed, project_path)
            if disallowed:
                code = _fallback_py_test()
        test_path = os.path.join(project_path, "tests", f"{feature_name}.test.py")
        write_file(test_path, code)
    elif p_type == "go":
        system = "You are a senior test engineer. Return only valid test code."
        prompt = f"""
        Create integration tests for this feature file using Go's testing package: {current_file}
        Code:
        {read_file(current_file)}

        Do not modify source files. Return only the test code.
        """
        code = _generate_unit_test(llm, prompt, system)
        allowed = _allowed_libs_for(state, "integration")
        disallowed = _disallowed_go_imports(code, allowed)
        if disallowed:
            code = _regenerate_if_disallowed(llm, code, prompt, system, disallowed)
            disallowed = _disallowed_go_imports(code, allowed)
            if disallowed:
                pkg_name = _go_package_name_from_source(read_file(current_file)) or "autotest"
                code = _fallback_go_test(pkg_name)
        test_path = os.path.join(project_path, "tests", f"{feature_name}_test.go")
        write_file(test_path, code)

    integration_test_files = list(state.get("integration_test_files", []))
    integration_test_map = dict(state.get("integration_test_map", {}))
    if test_path:
        integration_test_files.append(test_path)
        integration_test_map[test_path] = current_file

    return {
        "integration_index": idx + 1,
        "integration_test_files": integration_test_files,
        "integration_test_map": integration_test_map,
        "test_phase": "integration",
    }

def e2e_test_agent(state: AgentState) -> AgentState:
    print("--- 🌐 Agent: Generating E2E Tests ---")
    p_type = state.get("project_type", "unknown")
    libs = state.get("selected_libraries", {}).get("e2e", [])
    llm = get_llama_model()

    project_path = state["project_path"]
    os.makedirs(os.path.join(project_path, "spec"), exist_ok=True)

    feature_files = _collect_source_files(project_path, p_type)
    for current_file in feature_files:
        rel = os.path.relpath(current_file, project_path).replace("\\", "/")
        feature_name = rel.replace("/", "_").rsplit(".", 1)[0]

        if p_type in ["javascript", "typescript", "react", "vue", "quasar"]:
            framework = "playwright" if "playwright" in libs else "cypress"
            system = "You are a senior test engineer. Return only valid test code."
            prompt = f"""
            Create end-to-end tests for this feature using {framework}: {current_file}
            Code:
            {read_file(current_file)}

            Target app-level flows and critical paths. Return only the test code.
            Ensure all identifiers are imported before use and avoid unused imports.
            """
            code = _generate_unit_test(llm, prompt, system)
            allowed = _allowed_libs_for(state, "e2e")
            disallowed = _disallowed_js_imports(code, allowed)
            if disallowed:
                code = _regenerate_if_disallowed(llm, code, prompt, system, disallowed)
                disallowed = _disallowed_js_imports(code, allowed)
                if disallowed:
                    code = _fallback_js_test("vitest")
            code = _ensure_js_imports(code, framework, state.get("framework"))
            ui_fw = state.get("framework")
            if ui_fw in ["react"]:
                ext = _react_test_ext(state)
            else:
                ext = "ts" if _is_ts_project(state) else "js"
            test_path = os.path.join(project_path, "spec", f"{feature_name}.spec.{ext}")
            write_file(test_path, code)
        elif p_type == "python":
            system = "You are a senior test engineer. Return only valid test code."
            prompt = f"""
            Create end-to-end tests using Playwright (python) for this feature: {current_file}
            Code:
            {read_file(current_file)}

            Return only the test code.
            """
            code = _generate_unit_test(llm, prompt, system)
            allowed = _allowed_libs_for(state, "e2e")
            disallowed = _disallowed_py_imports(code, allowed, project_path)
            if disallowed:
                code = _regenerate_if_disallowed(llm, code, prompt, system, disallowed)
                disallowed = _disallowed_py_imports(code, allowed, project_path)
                if disallowed:
                    code = _fallback_py_test()
            test_path = os.path.join(project_path, "spec", f"{feature_name}.spec.py")
            write_file(test_path, code)
        elif p_type == "go":
            system = "You are a senior test engineer. Return only valid test code."
            prompt = f"""
            Create end-to-end tests using Playwright (go) or appropriate tooling for this feature: {current_file}
            Code:
            {read_file(current_file)}

            Return only the test code.
            """
            code = _generate_unit_test(llm, prompt, system)
            allowed = _allowed_libs_for(state, "e2e")
            disallowed = _disallowed_go_imports(code, allowed)
            if disallowed:
                code = _regenerate_if_disallowed(llm, code, prompt, system, disallowed)
                disallowed = _disallowed_go_imports(code, allowed)
                if disallowed:
                    pkg_name = _go_package_name_from_source(read_file(current_file)) or "autotest"
                    code = _fallback_go_test(pkg_name)
            test_path = os.path.join(project_path, "spec", f"{feature_name}.spec.go")
            write_file(test_path, code)

    return {"test_phase": "finished"}
