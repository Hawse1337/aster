"""Family baselines: a structure change produces a new project to optimise on.

A structure operator changes the geometry itself, so optimising parameters
afterwards means optimising *on a new baseline*, not on the old one. This module
materialises that baseline as a project copy, records lineage and a content
hash, and refuses to silently reuse a baseline whose file changed underneath.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core import SpecError


@dataclass
class FamilyBase:
    family_id: str
    parent: str | None
    operator: str | None
    values: dict[str, Any]
    project_path: str
    sha256: str = ""
    created_utc: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "parent": self.parent,
            "operator": self.operator,
            "values": dict(self.values),
            "project_path": self.project_path,
            "sha256": self.sha256,
            "created_utc": self.created_utc,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FamilyBase":
        return cls(
            family_id=str(payload["family_id"]),
            parent=payload.get("parent"),
            operator=payload.get("operator"),
            values=dict(payload.get("values") or {}),
            project_path=str(payload["project_path"]),
            sha256=str(payload.get("sha256") or ""),
            created_utc=str(payload.get("created_utc") or ""),
            notes=str(payload.get("notes") or ""),
        )


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FamilyRegistry:
    """JSON-backed registry of family baselines inside one campaign."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, FamilyBase]:
        if not self.path.is_file():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return {entry["family_id"]: FamilyBase.from_dict(entry)
                for entry in payload.get("families") or []}

    def save(self, families: dict[str, FamilyBase]) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"families": [family.to_dict() for family in families.values()]}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        return self.path

    def register(self, family: FamilyBase) -> FamilyBase:
        families = self.load()
        if family.family_id in families:
            raise SpecError(f"family already registered: {family.family_id}")
        families[family.family_id] = family
        self.save(families)
        return family

    def verify(self, family_id: str) -> tuple[bool, str]:
        families = self.load()
        family = families.get(family_id)
        if family is None:
            return False, f"unknown family: {family_id}"
        path = Path(family.project_path)
        if not path.is_file():
            return False, f"project missing: {path}"
        actual = file_sha256(path)
        if family.sha256 and actual != family.sha256:
            return False, "project content changed since the family was registered"
        return True, "ok"
