import os
import json
import pathspec

def get_project_tree(root_path: str) -> str:
    """Generates a JSON tree structure ignoring gitignore files."""
    tree = {}
    gitignore_path = os.path.join(root_path, '.gitignore')
    spec = None
    
    if os.path.exists(gitignore_path):
        with open(gitignore_path, 'r') as f:
            spec = pathspec.PathSpec.from_lines('gitwildmatch', f)

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Filter directories and files based on gitignore
        if spec:
            dirnames[:] = [d for d in dirnames if not spec.match_file(os.path.relpath(os.path.join(dirpath, d), root_path))]
            filenames = [f for f in filenames if not spec.match_file(os.path.relpath(os.path.join(dirpath, f), root_path))]
            
        # Ignore .git folder explicitly
        if '.git' in dirnames:
            dirnames.remove('.git')

        rel_path = os.path.relpath(dirpath, root_path)
        if rel_path == ".":
            current_level = tree
        else:
            current_level = tree
            for part in rel_path.split(os.sep):
                current_level = current_level.setdefault(part, {})
        
        for f in filenames:
            current_level[f] = "__FILE__"
            
    return json.dumps(tree, indent=2)

def read_file(file_path: str) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"

def write_file(file_path: str, content: str):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)