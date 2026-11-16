# ukiryu-python

Ukiryu runtime migration: one Rust core, coordinated language runtimes (ukiryu/ukiryu#29).

This is the Python binding. It is pure `ctypes` over the C ABI of the Rust
core's `ukiryu-ffi` crate; there is no compiled extension module. Every
operation is one JSON request over the core's wire, and every behaviour
(tool loading, command building, validation, execution, shell formatting)
lives in the core. The package only converts Python values to wire values
and replies to typed dataclasses.

The core is pinned as the `ukiryu-rs` Git submodule.

## Finding the library

The binding loads the core library from the first of these that applies:

1. `UKIRYU_CDYLIB`, an explicit path. When set, nothing else is tried.
2. The library packaged inside the `ukiryu` package (`ukiryu/lib/`). If
   the file is there it must load.
3. The system loader (`ctypes.util.find_library("ukiryu_ffi")`, then the
   plain library file name).

If nothing loads, `ukiryu.LibraryLoadError` lists every candidate and why it
was rejected. A library whose ABI version is not 1 is rejected.

## Example

```python
import ukiryu

tool = ukiryu.Register("path/to/register").tool("jq", platform="linux", shell="bash")
built = tool.build("process", {"filter": ".foo"})
result = tool.execute("process", {"filter": ".foo"}, env={}, timeout=60, stdin='{"foo": 1}')
print(result.stdout)
```

Platform and shell are always given explicitly. `env` and `timeout` are
required for `execute`. A failed run raises `ukiryu.UkiryuError`, carrying
the original error class, message and, for a finished process, its result.

Ruby values that have no direct Python type cross as small typed values:
`ukiryu.Symbol`, `ukiryu.Time` (exact seconds and nanoseconds with the UTC
offset), `ukiryu.Regexp` and `ukiryu.Map` (an ordered hash whose keys need
not be strings). Integers of any size and Floats cross exactly.

## Development

Build the core and run the tests:

```sh
git submodule update --init
(cd ukiryu-rs && cargo build -p ukiryu-ffi --release --locked)
export UKIRYU_CDYLIB=$PWD/ukiryu-rs/target/release/libukiryu_ffi.so
export UKIRYU_CONFORMANCE_DIR=$PWD/ukiryu-rs/corpus/conformance
PYTHONPATH=src python -m unittest discover -s tests
```

The corpus test runs the shared conformance corpus through the public Python
API and pins how many rows each operation runs.
