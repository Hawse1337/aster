"""Version of the application, in one place, for the window title and the installer."""

from __future__ import annotations

import re
import sys
from pathlib import Path

__all__ = ["__version__", "describe", "VERSION_INFO"]

__version__ = "1.53.0"

VERSION_INFO = tuple(int(part) for part in re.findall(r"\d+", __version__)[:3])


def describe() -> str:
    """A one-line description used in the console banner and the /api/about route."""
    return "aster %s (python %s)" % (__version__, ".".join(map(str, sys.version_info[:2])))


def frozen() -> bool:
    """True when running from a PyInstaller build, which changes how data files are found."""
    return bool(getattr(sys, "frozen", False))


def resource_path(relative: str) -> Path:
    """Locate a bundled file both in a checkout and inside a one-folder build."""
    if frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base / relative
