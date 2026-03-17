import os
import re
import shutil
import sys
import subprocess
from graphs.state import AgentState

def _extract_npm_dest(stderr: str) -> str | None:
    match = re.search(r"npm error dest (.+)", stderr)
    if not match:
        return None
    return match.group(1).strip()

def _safe_cleanup_npm_temp(dest: str, project_path: str) -> bool:
    if not dest:
        return False
    try:
        dest_abs = os.path.abspath(dest)
        node_modules = os.path.abspath(os.path.join(project_path, "node_modules"))
        if not dest_abs.startswith(node_modules + os.sep):
            return False
        if not os.path.basename(dest_abs).startswith("."):
            return False
        if os.path.exists(dest_abs):
            shutil.rmtree(dest_abs)
            return True
    except Exception:
        return False
    return False

def _read_env_name(env_path: str) -> str | None:
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("name:"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        return None
    return None

def _detect_python_installer(project_path: str) -> tuple[str, str | None]:
    """
    Returns (tool, env_name). tool in {"conda","uv","pip"}.
    """
    conda_files = [
        "environment.yml",
        "environment.yaml",
        "conda.yml",
        "conda.yaml",
    ]
    for fname in conda_files:
        path = os.path.join(project_path, fname)
        if os.path.exists(path):
            return "conda", _read_env_name(path)

    if os.path.exists(os.path.join(project_path, "uv.lock")):
        return "uv", None

    # Fallback to pip for common Python structures
    if any(os.path.exists(os.path.join(project_path, f)) for f in ["pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile"]):
        return "pip", None

    return "pip", None

def _safe_remove_path(path: str, project_path: str) -> bool:
    try:
        path_abs = os.path.abspath(path)
        project_abs = os.path.abspath(project_path)
        if not path_abs.startswith(project_abs + os.sep):
            return False
        if os.path.isdir(path_abs):
            shutil.rmtree(path_abs)
        elif os.path.isfile(path_abs):
            os.remove(path_abs)
        else:
            return False
        return True
    except Exception:
        return False

def installer_agent(state: AgentState) -> AgentState:
    print("--- 📦 Agent: Runtime Installation ---")
    
    libs = state.get('selected_libraries', {})
    all_libs = libs.get('unit', []) + libs.get('integration', []) + libs.get('e2e', [])
    # De-duplicate while preserving order
    seen = set()
    all_libs = [lib for lib in all_libs if not (lib in seen or seen.add(lib))]
    
    if not all_libs:
        return {"latest_error_log": None}

    project_path = state['project_path']
    p_type = state['project_type'].lower()
    
    if not project_path or not os.path.isdir(project_path):
        return {"latest_error_log": f"Invalid project path: {project_path}"}

    # 1. Determine the Command
    if p_type == "python":
        tool, env_name = _detect_python_installer(project_path)
        if tool == "conda":
            if env_name:
                cmd = ["conda", "install", "-y", "-n", env_name, *all_libs]
            else:
                cmd = ["conda", "install", "-y", *all_libs]
        elif tool == "uv":
            cmd = ["uv", "add", *all_libs]
        else:
            cmd = [sys.executable, "-m", "pip", "install", *all_libs]
    elif p_type in ["javascript", "typescript", "react"]:
        pkg_path = os.path.join(project_path, "package.json")
        if not os.path.exists(pkg_path):
            return {"latest_error_log": f"package.json not found in {project_path}"}
        # npm install for JS projects
        cmd = ["npm", "--prefix", project_path, "install", "--save-dev", *all_libs]
    elif p_type == "go":
        cmd = ["go", "get", *all_libs]
    else:
        return {"latest_error_log": f"Unsupported environment: {p_type}"}

    print(f"🚀 Running: {' '.join(cmd)}")
    print(f"📁 Working directory: {project_path}")

    # 2. Execute via Subprocess
    try:
        process = subprocess.run(
            cmd,
            shell=False,
            cwd=project_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300 # 5 minute limit for slow installs
        )
        
        if process.returncode == 0:
            print("✅ Installation Successful")
            return {"latest_error_log": None}
        else:
            stderr = process.stderr or ""
            # Handle common npm ENOTEMPTY rename error by cleaning temp dir and retrying once
            if p_type in ["javascript", "typescript", "react"] and "ENOTEMPTY" in stderr:
                dest = _extract_npm_dest(stderr)
                if _safe_cleanup_npm_temp(dest, project_path):
                    print(f"🧹 Cleaned npm temp dir: {dest}. Retrying install once...")
                    retry = subprocess.run(
                        cmd,
                        shell=False,
                        cwd=project_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=300
                    )
                    if retry.returncode == 0:
                        print("✅ Installation Successful (after cleanup)")
                        return {"latest_error_log": None}
                    stderr = retry.stderr or stderr

                # Clean install fallback: remove node_modules (and lockfile) then retry once
                node_modules = os.path.join(project_path, "node_modules")
                pkg_lock = os.path.join(project_path, "package-lock.json")
                removed_any = False
                if os.path.exists(node_modules):
                    removed_any = _safe_remove_path(node_modules, project_path) or removed_any
                if os.path.exists(pkg_lock):
                    removed_any = _safe_remove_path(pkg_lock, project_path) or removed_any

                if removed_any:
                    print("🧹 Clean install fallback: removed node_modules and/or package-lock.json. Retrying install once...")
                    retry = subprocess.run(
                        cmd,
                        shell=False,
                        cwd=project_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=300
                    )
                    if retry.returncode == 0:
                        print("✅ Installation Successful (after clean install)")
                        return {"latest_error_log": None}
                    stderr = retry.stderr or stderr

            print(f"❌ Installation Failed: {stderr}")
            return {"latest_error_log": stderr}
            
    except Exception as e:
        return {"latest_error_log": str(e)}
