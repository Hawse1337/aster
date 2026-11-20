"""The bundled skill mirror and the `aster skill` CLI must stay in sync."""

from __future__ import annotations

import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "skills" / "aster"
BUNDLED = ROOT / "aster" / "skills" / "aster"


def _relative_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


class TestSkillBundle:
    def test_canonical_skill_exists(self) -> None:
        assert (CANONICAL / "SKILL.md").is_file()
        assert (CANONICAL / "references" / "api-map.md").is_file()
        assert (CANONICAL / "scripts" / "dry_demo.py").is_file()

    def test_bundle_mirrors_canonical(self) -> None:
        canonical = _relative_files(CANONICAL)
        bundled = _relative_files(BUNDLED)
        assert bundled == canonical, "re-run tools/sync_skill_bundle.py"
        for name in canonical:
            assert (CANONICAL / name).read_bytes() == (BUNDLED / name).read_bytes(), name

    def test_skill_install_copies_the_bundle(self, tmp_path: Path) -> None:
        cli = importlib.import_module("aster.cli")
        dest = tmp_path / "skills" / "aster"
        assert cli.main(["skill", "install", "--dest", str(dest)]) == 0
        assert (dest / "SKILL.md").is_file()
        assert _relative_files(dest) == _relative_files(BUNDLED)

    def test_skill_command_paths(self, tmp_path: Path, monkeypatch) -> None:
        cli = importlib.import_module("aster.cli")
        monkeypatch.setenv("ASTER_SKILL_DIR", str(tmp_path / "custom"))
        assert str(cli._skill_dest()) == str(tmp_path / "custom")
        assert cli.main(["skill", "path"]) == 0
        assert cli._skill_source().is_dir()
