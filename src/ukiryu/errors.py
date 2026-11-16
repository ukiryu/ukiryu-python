"""Exceptions raised by the binding.

Every failure the core reports becomes a :class:`UkiryuError` carrying the
core's stable ``code`` (the public error contract), the original Ruby
exception class in ``source_class`` when the failure has one, and the exact
message. The binding adds no codes of its own except for loading the
library (:class:`LibraryLoadError`) and malformed replies
(:class:`ProtocolError`).
"""

from __future__ import annotations

from typing import Any, Optional

__all__ = ["UkiryuError", "LibraryLoadError", "ProtocolError"]


class UkiryuError(Exception):
    """A failure reported by the core.

    ``result`` is the :class:`ukiryu.ExecutionResult` of a finished run that
    failed (``EXECUTION_FAILED`` and invalid output encodings), and
    ``timeout`` the configured timeout of a run that timed out. ``detail``
    holds structured failure facts the core adds for some operations (for a
    YAML reader failure, its ``category`` and ``stage``), else ``None``.
    """

    def __init__(self, code: str, message: str, source_class: Optional[str] = None,
                 result: Any = None, timeout: Any = None,
                 detail: Optional[dict] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.source_class = source_class
        self.result = result
        self.timeout = timeout
        self.detail = detail

    def __str__(self) -> str:
        origin = " (%s)" % self.source_class if self.source_class else ""
        return "%s%s: %s" % (self.code, origin, self.message)


class LibraryLoadError(UkiryuError):
    """The ukiryu-ffi library could not be loaded or has the wrong ABI.

    ``attempts`` lists each ``(source, candidate, reason)`` that was tried,
    in order.
    """

    def __init__(self, message: str, attempts=()) -> None:
        super().__init__("LIBRARY_LOAD_ERROR", message)
        self.attempts = tuple(attempts)


class ProtocolError(UkiryuError):
    """A reply from the library does not follow the wire contract."""

    def __init__(self, message: str) -> None:
        super().__init__("PROTOCOL_ERROR", message)
