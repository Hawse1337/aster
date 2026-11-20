# -*- coding: utf-8 -*-
"""Mirror the canonical skill (skills/aster) into the package (aster/skills/aster)."""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "skills" / "aster"
DST = ROOT / "aster" / "skills" / "aster"


def main() -> int:
    if not SRC.is_dir():
        raise SystemExit("canonical skill not found: %s" % SRC)
    if DST.exists():
        shutil.rmtree(DST)
    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SRC, DST)
    (DST.parent / "__init__.py").write_text(
        '"""Bundled agent skill assets (mirrored from skills/aster at build time)."""\n',
        encoding="utf-8",
    )
    files = sorted(p.relative_to(DST).as_posix() for p in DST.rglob("*") if p.is_file())
    print("synced %d files into %s" % (len(files), DST))
    for name in files:
        print("  ", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
