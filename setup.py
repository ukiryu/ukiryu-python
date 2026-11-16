"""Build a platform wheel that carries the core's C library.

The package is pure ctypes, so the wheel is tagged ``py3-none-<platform>``:
one wheel per OS and architecture, usable by every CPython 3 that can load
the library. The platform tag is given by UKIRYU_WHEEL_PLAT (for example
``manylinux_2_28_x86_64``); it is never guessed from the build host. Only a
wheel is built; there is no sdist.
"""

import os

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel


class PlatformWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self):
        plat = os.environ.get("UKIRYU_WHEEL_PLAT")
        if not plat:
            raise SystemExit("UKIRYU_WHEEL_PLAT must name the wheel platform tag")
        return "py3", "none", plat.replace("-", "_").replace(".", "_")


setup(cmdclass={"bdist_wheel": PlatformWheel})
