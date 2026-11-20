"""Parameter definitions: the design system's "engineering language" (S1).

A parameter file is plain JSON so it can be reviewed, diffed and versioned:

    {
      "case": "lens28",
      "params": [
        {"name": "step_L", "unit": "mm", "low": 0.5, "high": 4.0, "default": 2.0,
         "kind": "float", "step": 0.1,
         "physics": "capacitive step height ...",
         "manufacturing": {"min_gap_mm": 0.5}}
      ]
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..contract import validate_spec  # re-exported for convenience
from ..core import ParamSpace, ParamSpec, SpecError

PARAM_FILE_VERSION = 1
_ALIASES = {"step": "integer_step", "integer_step": "integer_step"}


@dataclass
class ParamDefinition:
    name: str
    unit: str
    low: float
    high: float
    default: Any
    kind: str = "float"
    choices: tuple[Any, ...] = ()
    step: float = 0.1
    physics: str = ""
    manufacturing: dict[str, Any] = field(default_factory=dict)
    group: str = "general"

    def to_param_spec(self) -> ParamSpec:
        return ParamSpec(
            name=self.name,
            unit=self.unit,
            low=float(self.low),
            high=float(self.high),
            default=self.default,
            kind=self.kind,
            choices=tuple(self.choices),
            physics=self.physics,
            manufacturing=dict(self.manufacturing),
            integer_step=float(self.step),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "unit": self.unit,
            "low": self.low,
            "high": self.high,
            "default": self.default,
            "kind": self.kind,
            "step": self.step,
            "physics": self.physics,
            "manufacturing": dict(self.manufacturing),
            "group": self.group,
        }
        if self.choices:
            payload["choices"] = list(self.choices)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ParamDefinition":
        unknown = set(payload) - {
            "name", "unit", "low", "high", "default", "kind", "choices", "step",
            "integer_step", "physics", "manufacturing", "group",
        }
        if unknown:
            raise SpecError(f"param {payload.get('name')}: unknown keys {sorted(unknown)}")
        step = payload.get("step", payload.get("integer_step", 0.1))
        definition = cls(
            name=str(payload["name"]),
            unit=str(payload.get("unit", "")),
            low=float(payload["low"]),
            high=float(payload["high"]),
            default=payload.get("default", payload["low"]),
            kind=str(payload.get("kind", "float")),
            choices=tuple(payload.get("choices", ())),
            step=float(step),
            physics=str(payload.get("physics", "")),
            manufacturing=dict(payload.get("manufacturing", {})),
            group=str(payload.get("group", "general")),
        )
        definition.to_param_spec()  # validates bounds/kind/choices
        return definition


@dataclass
class ParamFile:
    case: str
    params: list[ParamDefinition]
    version: int = PARAM_FILE_VERSION
    notes: str = ""

    def space(self) -> ParamSpace:
        return ParamSpace([definition.to_param_spec() for definition in self.params])

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "case": self.case,
            "notes": self.notes,
            "params": [definition.to_dict() for definition in self.params],
        }

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                          encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "ParamFile":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ParamFile":
        if int(payload.get("version", PARAM_FILE_VERSION)) != PARAM_FILE_VERSION:
            raise SpecError(f"unsupported param file version: {payload.get('version')}")
        if not payload.get("params"):
            raise SpecError("param file has no params")
        params = [ParamDefinition.from_dict(item) for item in payload["params"]]
        names = [p.name for p in params]
        if len(set(names)) != len(names):
            raise SpecError("duplicate parameter names in param file")
        param_file = cls(
            case=str(payload.get("case", "")),
            params=params,
            notes=str(payload.get("notes", "")),
        )
        param_file.space()  # validates the whole space
        return param_file
