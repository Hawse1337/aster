"""The installed/CLI version must match the package metadata version."""

from __future__ import annotations

import re
from pathlib import Path

import aster
from aster.version import VERSION_INFO, __version__


def test_package_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None
    assert __version__ == match.group(1)


def test_version_tuple_matches_string() -> None:
    parts = tuple(int(part) for part in re.findall(r"\d+", __version__)[:3])
    assert VERSION_INFO == parts


def test_aster_exports_the_same_version() -> None:
    assert aster.__version__ == __version__
