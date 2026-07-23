"""Shared test fixtures.

These are lightweight unit tests for forge-align's dispatch/forwarding logic —
they must run without a real forge-io (and its OIIO/OCIO stack) installed. When
forge-io is importable (e.g. inside the `forge` conda env) the real module is
used; otherwise a minimal stub is installed so `forge_cv.extractor` imports
cleanly. Either way the functions under test are monkeypatched, so no real
decode ever runs here — that path is covered by scripts/smoke_v0_1_1.py and the
in-Flame acceptance test.
"""

import sys
import types


def _install_forge_io_stub() -> None:
    if "forge_io" in sys.modules:
        return
    try:
        import forge_io  # noqa: F401  (real dep present — use it)
        return
    except Exception:
        pass

    stub = types.ModuleType("forge_io")

    def read(*args, **kwargs):  # placeholder — tests monkeypatch this
        raise RuntimeError("forge_io.read stub called; test should monkeypatch it")

    def resolve_pattern(pattern, frame_index):
        return pattern

    stub.read = read
    stub.resolve_pattern = resolve_pattern

    exc = types.ModuleType("forge_io.exceptions")

    class ForgeIOError(Exception):
        pass

    exc.ForgeIOError = ForgeIOError
    stub.exceptions = exc

    sys.modules["forge_io"] = stub
    sys.modules["forge_io.exceptions"] = exc


_install_forge_io_stub()
