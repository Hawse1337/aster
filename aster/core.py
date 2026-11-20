"""Generic design-case model for the antenna optimisation layer.

The layer is deliberately case-agnostic: an antenna design case is described by
a *family* (parameter space + builder), a list of *constraints* and
*objectives*, and the evaluation settings used by ``aedt_guard``. The lens /
leaky-wave specifics live in ``aster.cases`` and must not leak into this file.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .contract import (
    DEFAULT_EVALUATION,
    DEFAULT_EXECUTION_POLICY,
    SCHEMA_VERSION,
    validate_spec,
)


class SpecError(ValueError):
    """Raised when a parameter, family or case description is unusable."""

PARAM_KINDS = {"float", "int", "choice"}
OPERATORS = {"<=", ">=", "=="}
METRIC_KINDS = {
    "s11",
    "realized_gain_h",
    "peak_gain",
    "ripple",
    "sll",
    "beam_theta",
    "effective_bandwidth",
    "s11_bandwidth",
    "radiation_efficiency",
    "directivity_db",
    "directivity_horiz_db",  # dBi: peak directivity in the theta=90 deg plane
}


@dataclass(frozen=True)
class ParamSpec:
    """One independently optimisable design parameter."""

    name: str
    unit: str
    low: float
    high: float
    default: float
    kind: str = "float"
    choices: tuple[Any, ...] = ()
    physics: str = ""
    manufacturing: dict[str, Any] = field(default_factory=dict)
    integer_step: float = 0.1

    def __post_init__(self) -> None:
        if self.kind not in PARAM_KINDS:
            raise SpecError(f"param {self.name}: kind must be one of {sorted(PARAM_KINDS)}")
        if self.kind == "choice":
            if not self.choices:
                raise SpecError(f"param {self.name}: choice params need choices")
        elif self.low >= self.high:
            raise SpecError(f"param {self.name}: low must be < high")
        if self.kind != "choice" and not (self.low <= self.default <= self.high):
            raise SpecError(f"param {self.name}: default outside [low, high]")

    def validate(self, value: Any) -> Any:
        if self.kind == "choice":
            if value not in self.choices:
                raise SpecError(f"param {self.name}: {value!r} not in {self.choices}")
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SpecError(f"param {self.name}: numeric value required")
        if not (self.low <= float(value) <= self.high):
            raise SpecError(f"param {self.name}: {value} outside [{self.low}, {self.high}]")
        return int(round(float(value))) if self.kind == "int" else float(value)

    def normalize(self, value: Any) -> float:
        if self.kind == "choice":
            return float(self.choices.index(value)) / max(1, len(self.choices) - 1)
        return (float(value) - self.low) / (self.high - self.low)

    def denormalize(self, normalized: float) -> Any:
        normalized = min(max(float(normalized), 0.0), 1.0)
        if self.kind == "choice":
            index = int(round(normalized * (len(self.choices) - 1)))
            return self.choices[index]
        value = self.low + normalized * (self.high - self.low)
        if self.kind == "int":
            return int(round(value))
        step = float(self.integer_step)
        if step > 0:
            value = round(value / step) * step
        return round(float(value), 10)


class ParamSpace:
    """An ordered collection of independent parameters."""

    def __init__(self, params: Iterable[ParamSpec]):
        self.params = list(params)
        if not self.params:
            raise SpecError("parameter space must not be empty")
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise SpecError("duplicate parameter names")
        self._index = {p.name: p for p in self.params}

    def __iter__(self):
        return iter(self.params)

    def __len__(self) -> int:
        return len(self.params)

    def __getitem__(self, name: str) -> ParamSpec:
        return self._index[name]

    def complete(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fill dimensions that did not exist when the data was measured; reject unknown keys.

        Only for ingesting stored measurements and resuming old queues: a design measured before a
        dimension was added is identical to the same design with the new dimension at its default when
        that default reproduces the old geometry (documented by the case). Candidate generation keeps
        using the strict ``validate`` so nothing is silently invented for new work.
        """
        unknown = [name for name in params if name not in self._index]
        if unknown:
            raise SpecError(f"unknown parameters: {unknown}")
        merged = {p.name: params.get(p.name, p.default) for p in self.params}
        return self.validate(merged)

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        missing = [p.name for p in self.params if p.name not in params]
        extra = [name for name in params if name not in self._index]
        if missing:
            raise SpecError(f"missing parameters: {missing}")
        if extra:
            raise SpecError(f"unknown parameters: {extra}")
        return {p.name: p.validate(params[p.name]) for p in self.params}

    def to_vector(self, params: dict[str, Any]) -> list[float]:
        return [p.normalize(params[p.name]) for p in self.params]

    def from_vector(self, vector: Sequence[float]) -> dict[str, Any]:
        if len(vector) != len(self.params):
            raise SpecError("vector length does not match the parameter space")
        return {p.name: p.denormalize(value) for p, value in zip(self.params, vector)}

    def default(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def sample(self, rng: random.Random | None = None) -> dict[str, Any]:
        rng = rng or random.Random()
        return {p.name: p.denormalize(rng.random()) for p in self.params}


@dataclass(frozen=True)
class Constraint:
    """A hard requirement on one metric kind."""

    id: str
    kind: str
    operator: str
    target: float
    unit: str = ""
    band_ghz: tuple[float, float] | None = None
    violation_scale: float = 1.0

    def __post_init__(self) -> None:
        if self.kind not in METRIC_KINDS:
            raise SpecError(f"constraint {self.id}: unknown kind {self.kind}")
        if self.operator not in OPERATORS:
            raise SpecError(f"constraint {self.id}: unknown operator {self.operator}")
        if self.violation_scale <= 0:
            raise SpecError(f"constraint {self.id}: violation_scale must be positive")

    def to_spec_metric(self) -> dict[str, Any]:
        metric = {
            "id": self.id,
            "kind": self.kind,
            "unit": self.unit,
            "operator": self.operator,
            "target": float(self.target),
            "hard": True,
        }
        if self.band_ghz:
            metric["band_ghz"] = [float(self.band_ghz[0]), float(self.band_ghz[1])]
        return metric


@dataclass(frozen=True)
class Objective:
    id: str
    kind: str
    direction: str  # maximize | minimize

    def __post_init__(self) -> None:
        if self.kind not in METRIC_KINDS:
            raise SpecError(f"objective {self.id}: unknown kind {self.kind}")
        if self.direction not in {"maximize", "minimize"}:
            raise SpecError(f"objective {self.id}: direction must be maximize or minimize")


@dataclass
class FamilySpec:
    """A structure family: one parameter space plus the builder that realises it."""

    family_id: str
    antenna_type: str
    param_space: ParamSpace
    builder: str = ""
    parent: str | None = None
    notes: str = ""


@dataclass
class Candidate:
    family_id: str
    params: dict[str, Any]
    origin: dict[str, Any] = field(default_factory=dict)

    @property
    def candidate_id(self) -> str:
        payload = json.dumps(
            {"family": self.family_id, "params": self.params},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "family_id": self.family_id,
            "params": dict(self.params),
            "origin": dict(self.origin),
        }


@dataclass
class CaseSpec:
    """Everything the optimisation layer needs to know about one design case."""

    case_id: str
    project: str
    design_type: str
    frequency_ghz: tuple[float, float]
    family: FamilySpec
    constraints: list[Constraint]
    objectives: list[Objective]
    evaluation: dict[str, Any] = field(default_factory=dict)
    execution_policy: dict[str, Any] = field(default_factory=dict)
    project_path: str | None = None
    setup_name: str = "Setup1"
    notes: str = ""

    def validate(self) -> None:
        self.family.param_space.validate(self.family.param_space.default())
        ids = [c.id for c in self.constraints]
        if len(set(ids)) != len(ids):
            raise SpecError("duplicate constraint ids")
        if not self.constraints:
            raise SpecError("a case needs at least one hard constraint")
        metric_ids = {c.id for c in self.constraints}
        for objective in self.objectives:
            if objective.kind not in METRIC_KINDS:
                raise SpecError(f"objective {objective.id}: unknown kind")
            # objectives may target a metric without a matching hard constraint
            _ = metric_ids

    def to_spec(self) -> dict[str, Any]:
        """Build the aedt_guard design specification for this case."""
        self.validate()
        metrics = [c.to_spec_metric() for c in self.constraints]
        ids = {m["id"] for m in metrics}
        for objective in self.objectives:
            if objective.id in ids:
                continue
            metrics.append(
                {
                    "id": objective.id,
                    "kind": objective.kind,
                    "unit": "",
                    "operator": ">=" if objective.direction == "maximize" else "<=",
                    "target": 0.0,
                    "hard": False,
                }
            )
            ids.add(objective.id)
        spec = {
            "schema_version": SCHEMA_VERSION,
            "project": self.project,
            "design_type": self.design_type,
            "version": 1,
            "frequency_ghz": [float(self.frequency_ghz[0]), float(self.frequency_ghz[1])],
            "metrics": metrics,
            "objectives": [
                {"metric": o.id, "direction": o.direction} for o in self.objectives
            ],
            "evaluation": {**DEFAULT_EVALUATION, **(self.evaluation or {})},
            "execution_policy": {
                **DEFAULT_EXECUTION_POLICY,
                **(self.execution_policy or {}),
            },
            "history": [],
        }
        problems = validate_spec(spec)
        if problems:
            raise SpecError("; ".join(problems))
        return spec

    def make_candidate(self, params: dict[str, Any], **origin: Any) -> Candidate:
        clean = self.family.param_space.validate(params)
        return Candidate(family_id=self.family.family_id, params=clean, origin=dict(origin))


def sample_population(space: ParamSpace, count: int, seed: int = 0) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    return [space.sample(rng) for _ in range(int(count))]


def latin_hypercube(space: ParamSpace, count: int, seed: int = 0) -> list[dict[str, Any]]:
    """Stratified sampling: each parameter is sampled once per equal bin."""
    count = int(count)
    if count <= 0:
        return []
    rng = random.Random(seed)
    vectors: list[list[float]] = [[] for _ in range(count)]
    for _ in space:
        bins = list(range(count))
        rng.shuffle(bins)
        for row, bin_index in enumerate(bins):
            offset = rng.random()
            vectors[row].append((bin_index + offset) / count)
    return [space.from_vector(vector) for vector in vectors]


def case_from_json(path: str) -> CaseSpec:  # pragma: no cover - convenience loader
    payload = json.loads(open(path, encoding="utf-8").read())
    params = [
        ParamSpec(
            name=p["name"],
            unit=p.get("unit", ""),
            low=float(p.get("low", 0.0)),
            high=float(p.get("high", 1.0)),
            default=float(p.get("default", p.get("low", 0.0))),
            kind=p.get("kind", "float"),
            choices=tuple(p.get("choices", ())),
            physics=p.get("physics", ""),
            manufacturing=p.get("manufacturing", {}),
            integer_step=float(p.get("integer_step", 0.1)),
        )
        for p in payload["params"]
    ]
    family = FamilySpec(
        family_id=payload["family_id"],
        antenna_type=payload.get("antenna_type", "generic"),
        param_space=ParamSpace(params),
        builder=payload.get("builder", ""),
        parent=payload.get("parent"),
    )
    constraints = [
        Constraint(
            id=c["id"],
            kind=c["kind"],
            operator=c["operator"],
            target=float(c["target"]),
            unit=c.get("unit", ""),
            band_ghz=tuple(c["band_ghz"]) if c.get("band_ghz") else None,
            violation_scale=float(c.get("violation_scale", 1.0)),
        )
        for c in payload["constraints"]
    ]
    objectives = [Objective(**o) for o in payload["objectives"]]
    return CaseSpec(
        case_id=payload["case_id"],
        project=payload.get("project", payload["case_id"]),
        design_type=payload.get("design_type", family.antenna_type),
        frequency_ghz=tuple(payload["frequency_ghz"]),
        family=family,
        constraints=constraints,
        objectives=objectives,
        evaluation=payload.get("evaluation", {}),
        execution_policy=payload.get("execution_policy", {}),
        project_path=payload.get("project_path"),
        setup_name=payload.get("setup_name", "Setup1"),
        notes=payload.get("notes", ""),
    )


