"""Make the renderers importable by name.

The tools are scripts, not a package - they are run as `python3 tools/...`
inside the container, and the entry points depend on that. The tests still
want at the constants a renderer declares (the palette, the font stack, the
page width) rather than copying them, so the two directories go on sys.path
here instead of in every test file.

They go on the **end** of the path, not the front. `tools/render/pptx.py` is
called `pptx.py` because SPEC 4 names it that, and so is python-pptx; putting
the renderer directory first would mean `from pptx import Presentation` in a
test silently imported the renderer instead of the library. Appending keeps
the installed packages winning, and `import charts` still resolves because
nothing else is called that. Use `renderer("pptx")` below to get at the
renderer itself.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent
for directory in (REPO / "tools", REPO / "tools" / "render", REPO / "tools" / "editor"):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))


def renderer(name: str) -> ModuleType:
    """Load a renderer from its file, whatever else claims the same name.

    By path rather than by `import`, so a renderer whose filename collides
    with an installed package (pptx) is reachable from a test without
    shadowing the package for every other test in the session.
    """
    path = REPO / "tools" / "render" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"continuum_render_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
