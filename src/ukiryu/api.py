"""The public operations, each one call through the core's wire.

Nothing here implements Ukiryu behaviour: every function builds one wire
request from its arguments, sends it to the core and converts the reply to
the typed result. Requests carry every input explicitly; platform and shell
are never guessed (an unknown shell raises, as in the original).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

from . import _ffi
from .errors import ProtocolError, UkiryuError
from .models import BuiltCommand, CoreVersion, DefinitionReport, ExecutionResult, ToolInfo
from .values import Map, decode, encode

__all__ = [
    "core_version",
    "library_info",
    "detect_shell",
    "render_cli",
    "Register",
    "Tool",
    "Shell",
    "extract_version",
    "version_range_matches",
    "find_version_spec",
    "validate_type",
    "validate_definition",
    "validate_profile",
    "fully_validate",
]

PathLike = Union[str, "os.PathLike[str]"]


def _mapping(value: Any) -> Any:
    """A Ruby Hash argument: a ``Mapping`` or an ordered :class:`Map`."""
    if value is None:
        return {}
    if isinstance(value, Map):
        return encode(value)
    if isinstance(value, Mapping):
        return encode(dict(value))
    raise TypeError("expected a mapping or ukiryu.Map, got %s" % type(value).__name__)


def _call(request: Dict[str, Any]) -> Dict[str, Any]:
    reply = _ffi.library().call(request)
    if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
        raise ProtocolError("reply has no boolean ok: %r" % (reply,))
    if reply["ok"]:
        return reply
    error = reply.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        raise ProtocolError("failure reply has no error code: %r" % (reply,))
    result = error.get("result")
    raise UkiryuError(
        error["code"],
        error.get("message", ""),
        error.get("source_class"),
        ExecutionResult.from_wire(result) if result is not None else None,
        decode(error["timeout"]) if "timeout" in error else None,
        error.get("detail"),
    )


def _value(request: Dict[str, Any]) -> Any:
    reply = _call(request)
    if "value" not in reply:
        raise ProtocolError("reply has no value: %r" % (reply,))
    return reply["value"]


def core_version() -> CoreVersion:
    """The loaded core's crate version and ABI version."""
    reply = _call({"op": "version"})
    return CoreVersion(reply["version"], reply["abi_version"])


def library_info() -> _ffi.LibraryInfo:
    """Where the loaded ukiryu-ffi library came from."""
    return _ffi.library().info


def detect_shell(*, platform: str, env: Mapping[str, Optional[str]]) -> Dict[str, Any]:
    """Detect from the explicit platform/environment without reading host state.

    The result contains ``detect`` (a :class:`Symbol`) and ``shell_class``.
    Unknown shells retain the core's error and structured diagnostic.
    """
    return decode(_value({"op": "resolve", "method": "shell_detect",
                          "platform": platform, "env": dict(env)}))


def render_cli(argv: Iterable[str], *, platform: str, env: Mapping[str, str],
               snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    """Render CLI stdout/stderr/exit from an explicit read-only input snapshot.

    ``snapshot`` supplies ``files`` (virtual path to text), ``executables``,
    ``probes`` and ``definitions``. No host files, environment or processes
    are used; side-effecting CLI commands are refused by the core.
    """
    return _value({"op": "cli.run", "argv": list(argv), "platform": platform,
                   "env": dict(env), "snapshot": dict(snapshot)})


class Tool:
    """A tool definition plus the platform, shell and version to load it for.

    A ``Tool`` is a request description: each operation loads the tool in
    the core with the real loader and ``Tool.new``, then runs. Construct one
    with :meth:`from_definition`, :meth:`Register.tool` or
    :meth:`Register.version_file`.
    """

    def __init__(self, source: Dict[str, Any], *, platform: str, shell: str,
                 version: Optional[str] = None) -> None:
        self._request: Dict[str, Any] = {"source": source, "platform": platform, "shell": shell}
        if version is not None:
            self._request["version"] = version

    @classmethod
    def from_definition(cls, yaml: str, *, platform: str, shell: str,
                        version: Optional[str] = None) -> "Tool":
        """A tool from a ``ToolDefinition`` YAML document."""
        return cls({"kind": "definition", "yaml": yaml}, platform=platform, shell=shell,
                   version=version)

    def __repr__(self) -> str:
        return "Tool(%r)" % (self._request,)

    def resolve(self) -> ToolInfo:
        """Load the tool: identity, selected profile and executable discovery."""
        request = {"op": "resolve", "method": "tool"}
        request.update(self._request)
        return ToolInfo.from_wire(_value(request))

    def build(self, command: str, params: Any = None) -> BuiltCommand:
        """Build ``command`` (a name or routed action path such as
        ``remote:add``) without running it."""
        return BuiltCommand.from_wire(_value({
            "op": "build", "tool": self._request, "command": command,
            "params": _mapping(params),
        }))

    def execute(self, command: str, params: Any = None, *,
                env: Mapping[str, str], timeout: Union[int, float],
                stdin: Optional[Union[str, bytes]] = None) -> ExecutionResult:
        """Run ``command``. ``env`` and ``timeout`` are required, as in the
        original ``Tool#execute``; ``stdin`` is extra input bytes (the
        ``stdin`` parameter and this argument are exclusive).

        A finished run that fails raises :class:`UkiryuError` with its
        ``result``.
        """
        request: Dict[str, Any] = {
            "op": "execute", "tool": self._request, "command": command,
            "params": _mapping(params), "env": dict(env),
            "timeout": encode(timeout),
        }
        if stdin is not None:
            request["stdin"] = stdin if isinstance(stdin, str) else encode(bytes(stdin))
        return ExecutionResult.from_wire(_value(request))

    def exit_code_meaning(self, command: str, status: int) -> Optional[str]:
        """The documented meaning of ``status`` for ``command``, if any."""
        return decode(_value({
            "op": "results", "method": "exit_code_meaning", "tool": self._request,
            "command": command, "status": encode(status),
        }))


class Register:
    """A register directory (``ukiryu/register`` layout)."""

    def __init__(self, path: PathLike) -> None:
        self.path = os.fspath(path)

    def __repr__(self) -> str:
        return "Register(%r)" % self.path

    def tool(self, name: str, *, platform: str, shell: str,
             version: Optional[str] = None) -> Tool:
        """``Tool.get(name)``: implementation and version detection run the
        tool's detection commands when the tool is used."""
        return Tool({"kind": "register", "register": self.path, "tool": name},
                    platform=platform, shell=shell, version=version)

    def version_file(self, name: str, *, implementation: str, file: str, platform: str,
                     shell: str, version: Optional[str] = None) -> Tool:
        """One version file of a tool, chosen explicitly instead of detected."""
        return Tool({"kind": "version_file", "register": self.path, "tool": name,
                     "implementation": implementation, "file": file},
                    platform=platform, shell=shell, version=version)


class Shell:
    """Shell formatting for one named shell (``bash``, ``zsh``, ``fish``,
    ``sh``, ``dash``, ``tcsh``, ``powershell``, ``cmd`` or a path ending in
    one), independent of the host."""

    def __init__(self, name: str) -> None:
        self.name = name

    def _op(self, operation: Dict[str, Any]) -> Any:
        reply = _call({"op": "escape", "shell": self.name, "operation": operation})
        if "value" not in reply:
            raise ProtocolError("escape reply has no value: %r" % (reply,))
        return reply["value"]

    def escape(self, string: str) -> str:
        return self._op({"method": "escape", "string": string})

    def quote(self, string: str, for_exe: Optional[bool] = None) -> str:
        operation: Dict[str, Any] = {"method": "quote", "string": string}
        if for_exe is not None:
            operation["for_exe"] = for_exe
        return self._op(operation)

    def needs_quoting(self, string: str) -> bool:
        return self._op({"method": "needs_quoting", "string": string})

    def format_path(self, path: str) -> str:
        return self._op({"method": "format_path", "path": path})

    def env_var(self, name: str) -> str:
        return self._op({"method": "env_var", "name": name})

    def join(self, executable: str, args: Iterable[str]) -> str:
        return self._op({"method": "join", "executable": executable, "args": list(args)})

    def escape_for_double_quotes(self, string: str) -> str:
        return self._op({"method": "escape_for_double_quotes", "string": string})


def extract_version(output: Optional[Union[str, bytes]], pattern: Optional[str]) -> Any:
    """``Tool::Loader.extract_version_from_pattern``. Binary ``output`` is
    matched as ASCII-8BIT and its match returned as ``bytes``."""
    wire_output = output if output is None or isinstance(output, str) else encode(bytes(output))
    return decode(_value({"op": "resolve", "method": "extract_version",
                          "output": wire_output, "pattern": pattern}))


def version_range_matches(scheme: Any, range: str, bounds: Any, version: str) -> bool:
    """``Versionian::VersionRange.new(range, scheme, **bounds).matches?(version)``."""
    return _value({"op": "resolve", "method": "range", "scheme": encode(scheme),
                   "range": range, "bounds": _mapping(bounds), "version": version})


def find_version_spec(index_yaml: str, implementation: str, version: str) -> Any:
    """The ``file`` of the version spec an implementation index routes
    ``version`` to, or ``None``."""
    return decode(_value({"op": "resolve", "method": "version_spec", "index": index_yaml,
                          "implementation": implementation, "version": version}))


def validate_type(value: Any, type: Any, options: Any = None) -> Any:
    """``Type.validate(value, type, options)``: the converted value."""
    return decode(_value({"op": "validate", "method": "type", "value": encode(value),
                          "type": encode(type), "options": _mapping(options)}))


def validate_definition(yaml: str, schema_path: Optional[PathLike] = None) -> DefinitionReport:
    """``DefinitionValidator.validate_string``."""
    return DefinitionReport.from_wire(_value({
        "op": "validate", "method": "definition", "yaml": yaml,
        "schema_path": None if schema_path is None else os.fspath(schema_path),
    }))


def validate_profile(profile_yaml: str, schema_path: Optional[PathLike] = None,
                     strict: bool = False) -> List[str]:
    """``SchemaValidator.validate_profile``: the error messages."""
    return decode(_value({
        "op": "schema", "method": "validate_profile", "profile": profile_yaml,
        "schema_path": None if schema_path is None else os.fspath(schema_path),
        "strict": strict,
    }))


def fully_validate(profile_yaml: str, schema_yaml: str, strict: bool = False) -> List[str]:
    """``JSON::Validator.fully_validate`` of a profile against a schema document."""
    return decode(_value({"op": "schema", "method": "fully_validate", "profile": profile_yaml,
                          "schema": schema_yaml, "strict": strict}))


def lint_string(yaml: str) -> Map:
    """``DefinitionLinter.lint_string(yaml).to_h``: a Symbol-keyed ``Map``."""
    return decode(_value({"op": "lint", "method": "string", "yaml": yaml}))


def safe_load(yaml: str) -> Any:
    """``YAML.safe_load(yaml, permitted_classes: [Symbol, Date, Time])``.

    Ruby types survive: Symbols, Floats and non-String keys (as a ``Map``).
    """
    return decode(_value({"op": "yaml", "method": "safe_load", "yaml": yaml}))


def load_model(model: str, path: PathLike) -> Dict[str, Any]:
    """``Ukiryu::Models::<model>.from_yaml(path)`` for ``Interface``,
    ``ImplementationVersion`` or ``ImplementationIndex``: the object's
    ``class`` name and its ``ivars`` sorted by name."""
    return decode(_value({"op": "yaml", "method": "model_load", "model": model,
                          "path": os.fspath(path)}))


def model_to_yaml(model: str, yaml: str) -> str:
    """``Ukiryu::Models::<model>.from_yaml(yaml).to_yaml`` (``ToolDefinition``)."""
    return decode(_value({"op": "yaml", "method": "model_to_yaml", "model": model,
                          "yaml": yaml}))
