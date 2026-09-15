"""Make the renderers importable by name.

The tools are scripts, not a package - they are run as `python3 tools/...`
inside the container, and the entry points depend on that. The tests still
want at the constants a renderer declares (the palette, the font stack, the
page width) rather than copying them, so the two directories go on sys.path
here instead of in every test file.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for directory in (REPO / "tools", REPO / "tools" / "render"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
