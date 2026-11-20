"""Typed results of the core operations.

Each dataclass is the reply shape of one wire operation of ABI version 1
(see the core crate's ``wire`` module); field values are decoded with
:mod:`ukiryu.values`. Unknown or missing reply fields are a
:class:`ukiryu.errors.ProtocolError`, so a core change cannot silently drop
or invent data here.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Dict, Optional, Tuple, Type, TypeVar, Union

from .errors import ProtocolError
from .values import Time, decode

__all__ = [
    "CoreVersion",
    "ToolInfo",
    "BuiltCommand",
    "CommandInfo",
    "ExecutionResult",
    "SchemaStage",
    "DefinitionReport",
]

T = TypeVar("T")
Stream = Union[str, bytes]


def _build(cls: Type[T], data: Any, convert: Optional[Dict[str, Any]] = None) -> T:
    if not isinstance(data, dict):
        raise ProtocolError("%s reply is not an object: %r" % (cls.__name__, data))
    names = [f.name for f in fields(cls)]
    if set(data) != set(names):
        raise ProtocolError("%s reply fields %s, expected %s"
                            % (cls.__name__, sorted(data), sorted(names)))
    convert = convert or {}
    return cls(**{name: convert.get(name, decode)(data[name]) for name in names})


@dataclass(frozen=True)
class CoreVersion:
    """The core crate version and the C ABI version of the loaded library."""

    version: str
    abi_version: int


@dataclass(frozen=True)
class ToolInfo:
    """A loaded tool (``Tool.get`` / ``Tool.new``): identity, selected profile
    and discovered executable."""

    name: str
    version: Optional[str]
    platform: str
    shell: str
    profile_index: int
    profile_name: Optional[str]
    executable: Optional[str]
    available: bool
    unavailability_reason: Optional[str]
    discovery: Optional[str]
    routing: bool
    commands: Tuple[str, ...]

    @classmethod
    def from_wire(cls, data: Any) -> "ToolInfo":
        return _build(cls, data, {"commands": lambda v: tuple(decode(v))})


@dataclass(frozen=True)
class BuiltCommand:
    """A command built up to the executor.

    ``executable`` is ``None`` when the tool's executable was not discovered;
    the arguments do not depend on it. ``env`` keeps the command's variables
    in order. ``stdin`` is the ``stdin`` parameter's bytes (``str`` when they
    are UTF-8).
    """

    executable: Optional[str]
    args: Tuple[str, ...]
    env: Tuple[Tuple[str, str], ...]
    stdin: Optional[Stream]
    command_name: str

    @classmethod
    def from_wire(cls, data: Any) -> "BuiltCommand":
        return _build(cls, data, {
            "args": lambda v: tuple(decode(v)),
            "env": lambda v: tuple((k, val) for k, val in decode(v)),
        })


@dataclass(frozen=True)
class CommandInfo:
    """``Execution::CommandInfo`` of a run."""

    executable: str
    executable_name: str
    arguments: Tuple[str, ...]
    full_command: str
    shell: Optional[str]
    tool_name: Optional[str]
    command_name: Optional[str]

    @classmethod
    def from_wire(cls, data: Any) -> "CommandInfo":
        return _build(cls, data, {"arguments": lambda v: tuple(decode(v))})


@dataclass(frozen=True)
class ExecutionResult:
    """``Execution::Result`` of a finished run.

    Streams are the exact bytes the process wrote: ``str`` when they are
    UTF-8, ``bytes`` otherwise. Times are exact :class:`ukiryu.Time` values;
    ``duration`` is the Float the core computes.
    """

    command: CommandInfo
    stdout: Stream
    stderr: Stream
    status: Optional[int]
    success: bool
    started_at: Optional[Time]
    finished_at: Optional[Time]
    duration: Optional[float]
    timeout: Optional[Union[int, float]]
    timed_out: bool

    @classmethod
    def from_wire(cls, data: Any) -> "ExecutionResult":
        return _build(cls, data, {"command": CommandInfo.from_wire})


@dataclass(frozen=True)
class SchemaStage:
    """How ``DefinitionValidator`` chose its schema: ``given``,
    ``given_not_loaded``, ``env`` or ``remote_fallback``.

    ``loaded`` records whether a remote fallback returned a schema. Older
    cores may report ``remote_fallback_not_performed`` without that field.
    """

    kind: str
    path: Optional[str] = None
    loaded: Optional[bool] = None

    @classmethod
    def from_wire(cls, data: Any) -> Optional["SchemaStage"]:
        if data is None:
            return None
        if (not isinstance(data, dict) or "kind" not in data
                or set(data) - {"kind", "path", "loaded"}
                or ("loaded" in data and type(data["loaded"]) is not bool)):
            raise ProtocolError("malformed schema_stage %r" % (data,))
        return cls(data["kind"], data.get("path"), data.get("loaded"))


@dataclass(frozen=True)
class DefinitionReport:
    """``DefinitionValidator.validate_string`` result."""

    valid: bool
    errors: Tuple[str, ...]
    warnings: Tuple[str, ...]
    schema_path: Optional[str]
    error_count: int
    warning_count: int
    schema_stage: Optional[SchemaStage]

    @classmethod
    def from_wire(cls, data: Any) -> "DefinitionReport":
        return _build(cls, data, {
            "errors": lambda v: tuple(decode(v)),
            "warnings": lambda v: tuple(decode(v)),
            "schema_stage": SchemaStage.from_wire,
        })
