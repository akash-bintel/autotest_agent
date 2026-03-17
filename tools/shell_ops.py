import subprocess
import os

def run_command(command: str, cwd: str) -> tuple[int, str]:
    """Runs a shell command and returns (return_code, output/error)."""
    if not cwd or not os.path.isdir(cwd):
        return 1, f"Invalid working directory: {cwd}"
    try:
        result = subprocess.run(
            command, 
            shell=True, 
            cwd=cwd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True
        )
        return result.returncode, result.stdout + "\n" + result.stderr
    except Exception as e:
        return 1, str(e)
