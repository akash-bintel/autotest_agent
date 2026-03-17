from typing import TypedDict, List, Optional, Dict, Any

class AgentState(TypedDict):
    project_path: str
    project_tree: str          # JSON string representation of the file tree
    project_type: str          # e.g., "python", "javascript", "typescript"
    package_file: str          # e.g., "requirements.txt", "package.json"
    
    selected_libraries: Dict[str, List[str]] # {'unit': [], 'integration': [], 'e2e': []}
    available_libraries: List[str]
    
    # Unit Testing Iteration State
    source_files: List[str]    # List of files identified for unit testing
    current_file_index: int    # Pointer to the file currently being processed
    unit_test_files: List[str] # Generated unit test files
    unit_test_map: Dict[str, str] # Map of test file -> source file
    unit_test_failures: List[str]
    unit_missing_libs: List[str]
    unit_retry_count: int
    is_unit_tests_verified: bool
    unit_max_retries: int
    unit_max_fixes: int

    # Integration Testing State
    integration_files: List[str]
    integration_index: int
    integration_test_files: List[str]
    integration_test_map: Dict[str, str]
    integration_test_failures: List[str]
    integration_retry_count: int
    integration_max_retries: int
    integration_max_fixes: int
    is_integration_tests_verified: bool
    
    # Error Handling & Logs
    latest_error_log: Optional[str]
    test_phase: str            # 'base', 'unit', 'integration', 'e2e'
    bundler: Optional[str]     # 'vite', 'webpack', etc.
    framework: Optional[str]   # 'react', 'vue', 'quasar', etc.
    
    # Final Status
    is_base_setup_verified: bool
    final_report: str
    retry_count: int
