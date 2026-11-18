"""Python forms of the core's typed wire values.

The core speaks Ruby values. JSON carries nil, booleans, integers of any
size, strings, arrays and string-keyed hashes directly; everything else is a
tagged object (see the core crate's ``wire::value`` module). This module maps
each tag to one Python type and back, without converting through a lossy
form:

========================  =========================================
Wire                      Python
========================  =========================================
nil / true / false        ``None`` / ``True`` / ``False``
integer, ``$int``         ``int`` (arbitrary precision)
``$float``                ``float`` (encoded as Ruby ``Float#to_s``)
string                    ``str``
``$bytes``                ``bytes``
array                     ``list`` (a ``tuple`` also encodes as one)
object                    ``dict`` with ``str`` keys
``$map``                  :class:`Map` (ordered pairs, any key type)
``$sym``                  :class:`Symbol`
``$time``                 :class:`Time` (whole seconds + nanoseconds)
``$regexp``               :class:`Regexp`
========================  =========================================

A ``dict`` whose keys are not all strings encodes as ``$map`` in insertion
order. Decoding never produces a bare JSON float: a reply that carries one is
a protocol error, because the wire encodes every Float as ``$float``.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from typing import Any, Tuple

__all__ = ["Symbol", "Time", "Regexp", "Map", "encode", "decode", "float_to_s"]


@dataclass(frozen=True)
class Symbol:
    """A Ruby Symbol (``:name``)."""

    name: str

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class Regexp:
    """A Ruby Regexp: its source text and its integer option bits."""

    source: str
    options: int = 0


@dataclass(frozen=True)
class Time:
    """An exact Ruby Time.

    ``seconds`` are whole seconds since the Unix epoch (any size), ``nanos``
    the fraction in nanoseconds, ``utc_offset`` the offset in seconds and
    ``utc`` whether the value is a UTC time.
    """

    seconds: int
    nanos: int = 0
    utc_offset: int = 0
    utc: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.nanos < 1_000_000_000:
            raise ValueError("nanos must be in 0..999999999")

    @classmethod
    def from_datetime(cls, value: _dt.datetime) -> "Time":
        """The exact Time of an aware ``datetime`` (naive values are rejected)."""
        offset = value.utcoffset()
        if offset is None:
            raise ValueError("a naive datetime has no defined instant")
        epoch = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
        delta = value - epoch
        micros = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
        seconds, micro = divmod(micros, 1_000_000)
        whole_offset = offset.days * 86_400 + offset.seconds
        if offset.microseconds:
            raise ValueError("a Ruby utc_offset is whole seconds")
        return cls(seconds, micro * 1000, whole_offset, value.tzinfo is _dt.timezone.utc)


@dataclass(frozen=True)
class Map:
    """A Ruby Hash whose keys are not all strings, in insertion order."""

    pairs: Tuple[Tuple[Any, Any], ...]

    def __iter__(self):
        return iter(self.pairs)

    def __len__(self) -> int:
        return len(self.pairs)


class WireValueError(ValueError):
    """A value has no wire form, or a reply value is malformed."""


def float_to_s(value: float) -> str:
    """Ruby's ``Float#to_s`` text, which the core requires for ``$float``."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    sign = "-" if math.copysign(1.0, value) < 0 else ""
    if value == 0:
        return sign + "0.0"
    # repr is the shortest text that round-trips, as Ruby's to_s.
    text = repr(abs(value))
    mantissa, _, exponent = text.partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = (whole + fraction).lstrip("0")
    point = len(whole) + (int(exponent) if exponent else 0)
    point -= len(whole + fraction) - len((whole + fraction).lstrip("0"))
    digits = digits.rstrip("0") or "0"
    # flo_to_s: fixed notation for -4 < decpt <= DBL_DIG + 1, else e-notation.
    if 0 < point <= 16:
        if point >= len(digits):
            return sign + digits + "0" * (point - len(digits)) + ".0"
        return sign + digits[:point] + "." + digits[point:]
    if -4 < point <= 0:
        return sign + "0." + "0" * (-point) + digits
    exp = point - 1
    return "%s%s.%se%s%02d" % (sign, digits[0], digits[1:] or "0", "+" if exp >= 0 else "-", abs(exp))


def _days_from_civil(y: int, m: int, d: int) -> int:
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _utc_text(text: str) -> Time:
    # YYYY-MM-DDTHH:MM:SS.fffffffffZ, the core's UTC Time form.
    if len(text) != 30 or text[4] != "-" or text[7] != "-" or text[10] != "T" or text[-1] != "Z":
        raise WireValueError("malformed $time %r" % text)
    try:
        y, mo, d = int(text[0:4]), int(text[5:7]), int(text[8:10])
        h, mi, s = int(text[11:13]), int(text[14:16]), int(text[17:19])
        nanos = int(text[20:29])
    except ValueError:
        raise WireValueError("malformed $time %r" % text) from None
    seconds = _days_from_civil(y, mo, d) * 86_400 + h * 3600 + mi * 60 + s
    return Time(seconds, nanos, 0, True)


def encode(value: Any) -> Any:
    """The wire JSON form of a Python value."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return {"$float": float_to_s(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"$bytes": bytes(value).hex()}
    if isinstance(value, Symbol):
        return {"$sym": value.name}
    if isinstance(value, Regexp):
        if value.options == 0:
            return {"$regexp": value.source}
        return {"$regexp": {"source": value.source, "options": value.options}}
    if isinstance(value, Time):
        return {"$time": {"seconds": str(value.seconds), "nanos": value.nanos,
                          "utc_offset": value.utc_offset, "utc": value.utc}}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if isinstance(value, Map):
        return {"$map": [[encode(k), encode(v)] for k, v in value.pairs]}
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value):
            return {k: encode(v) for k, v in value.items()}
        return {"$map": [[encode(k), encode(v)] for k, v in value.items()]}
    raise WireValueError("no wire form for %s" % type(value).__name__)


_TAGS = ("$sym", "$float", "$int", "$time", "$regexp", "$bytes", "$map")


def decode(value: Any) -> Any:
    """The Python value of a wire JSON value."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise WireValueError("a bare JSON float is not a wire value")
    if isinstance(value, list):
        return [decode(item) for item in value]
    if not isinstance(value, dict):
        raise WireValueError("unexpected JSON value %r" % (value,))
    if len(value) == 1:
        (key, inner), = value.items()
        if key in _TAGS:
            return _tag(key, inner)
    return {k: decode(v) for k, v in value.items()}


def _tag(key: str, inner: Any) -> Any:
    if key == "$sym" and isinstance(inner, str):
        return Symbol(inner)
    if key == "$int" and isinstance(inner, str):
        return int(inner)
    if key == "$float" and isinstance(inner, str):
        return float(inner)
    if key == "$bytes" and isinstance(inner, str):
        return bytes.fromhex(inner)
    if key == "$regexp":
        if isinstance(inner, str):
            return Regexp(inner)
        if isinstance(inner, dict):
            return Regexp(inner["source"], inner.get("options", 0))
    if key == "$time":
        if isinstance(inner, str):
            return _utc_text(inner)
        if isinstance(inner, dict):
            return Time(int(inner["seconds"]), inner["nanos"], inner["utc_offset"], inner["utc"])
    if key == "$map" and isinstance(inner, list):
        return Map(tuple((decode(k), decode(v)) for k, v in inner))
    raise WireValueError("malformed %s value %r" % (key, inner))
