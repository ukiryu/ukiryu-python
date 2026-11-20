"""Ukiryu for Python: pure ctypes over the ukiryu-ffi C ABI.

All behaviour lives in the Rust core (``ukiryu-rs``); this package loads its
C library, sends typed requests over the core's JSON wire and returns typed
dataclasses. See :mod:`ukiryu._ffi` for how the library is found.
"""

from .api import (
    Register,
    Shell,
    Tool,
    core_version,
    detect_shell,
    extract_version,
    find_version_spec,
    fully_validate,
    library_info,
    lint_string,
    load_model,
    model_to_yaml,
    render_cli,
    safe_load,
    validate_definition,
    validate_profile,
    validate_type,
    version_range_matches,
)
from .errors import LibraryLoadError, ProtocolError, UkiryuError
from .models import (
    BuiltCommand,
    CommandInfo,
    CoreVersion,
    DefinitionReport,
    ExecutionResult,
    SchemaStage,
    ToolInfo,
)
from .values import Map, Regexp, Symbol, Time

__version__ = "0.1.0"

__all__ = [
    "Register", "Shell", "Tool", "core_version", "extract_version", "find_version_spec",
    "fully_validate", "library_info", "lint_string", "load_model", "model_to_yaml",
    "safe_load", "validate_definition", "validate_profile", "validate_type",
    "version_range_matches", "detect_shell", "render_cli",
    "LibraryLoadError", "ProtocolError", "UkiryuError",
    "BuiltCommand", "CommandInfo", "CoreVersion", "DefinitionReport", "ExecutionResult",
    "SchemaStage", "ToolInfo",
    "Map", "Regexp", "Symbol", "Time",
    "__version__",
]
