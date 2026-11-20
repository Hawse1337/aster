"""Structure operators and design families (module D).

An operator is a *declarative* structural change: it declares which antenna
types it applies to, what it changes, how to check legality, which metrics it
may damage and how to roll back. The LLM never edits geometry directly; it
picks an operator and the code performs a validated, reversible change.

The operator library is data, not code-per-antenna: a case plugin registers its
own operators, so the core stays antenna-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..core import FamilySpec, ParamSpace, ParamSpec, SpecError


@dataclass
class StructureOperator:
    operator_id: str
    name: str
    antenna_types: tuple[str, ...]
    changes: str
    mechanism: str = "hypothesis to be validated"
    risked_metrics: tuple[str, ...] = ()
    manufacturing_rules: dict[str, Any] = field(default_factory=dict)
    rollback: str = "keep the previous family"
    extra_params: tuple[ParamSpec, ...] = ()
    legality: Callable[[dict[str, Any]], tuple[bool, str]] | None = None

    def applies_to(self, antenna_type: str) -> bool:
        return antenna_type in self.antenna_types

    def validate_inputs(self, values: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for spec in self.extra_params:
            if spec.name not in values:
                raise SpecError(f"operator {self.operator_id}: missing {spec.name}")
            clean[spec.name] = spec.validate(values[spec.name])
        if self.legality is not None:
            ok, reason = self.legality(clean)
            if not ok:
                raise SpecError(f"operator {self.operator_id} illegal: {reason}")
        return clean


@dataclass
class FamilyVersion:
    family_id: str
    antenna_type: str
    parent: str | None
    operator: str | None
    param_space: ParamSpace
    description: str = ""
    notes: str = ""

    def to_family_spec(self) -> FamilySpec:
        return FamilySpec(
            family_id=self.family_id,
            antenna_type=self.antenna_type,
            param_space=self.param_space,
            parent=self.parent,
            notes=self.notes,
        )


class FamilyManager:
    """Tracks families, their lineage and the operator that created them."""

    def __init__(self) -> None:
        self.versions: dict[str, FamilyVersion] = {}
        self.children: dict[str, list[str]] = {}

    def add_root(self, version: FamilyVersion) -> FamilyVersion:
        if version.parent is not None:
            raise SpecError("a root family must not have a parent")
        if version.family_id in self.versions:
            raise SpecError(f"family already exists: {version.family_id}")
        self.versions[version.family_id] = version
        self.children.setdefault(version.family_id, [])
        return version

    def fork(self, parent_id: str, operator: StructureOperator,
             inputs: dict[str, Any], *, new_id: str, description: str = "") -> FamilyVersion:
        if parent_id not in self.versions:
            raise SpecError(f"unknown parent family: {parent_id}")
        parent = self.versions[parent_id]
        if not operator.applies_to(parent.antenna_type):
            raise SpecError(
                f"operator {operator.operator_id} does not apply to {parent.antenna_type}"
            )
        if new_id in self.versions:
            raise SpecError(f"family already exists: {new_id}")
        clean = operator.validate_inputs(inputs)
        params = list(parent.param_space) + [
            spec for spec in operator.extra_params if spec.name not in parent.param_space
        ]
        version = FamilyVersion(
            family_id=new_id,
            antenna_type=parent.antenna_type,
            parent=parent_id,
            operator=operator.operator_id,
            param_space=ParamSpace(params),
            description=description or operator.changes,
            notes="; ".join(f"{key}={value}" for key, value in clean.items()),
        )
        self.versions[new_id] = version
        self.children.setdefault(new_id, [])
        self.children[parent_id].append(new_id)
        return version

    def lineage(self, family_id: str) -> list[str]:
        chain: list[str] = []
        current: str | None = family_id
        while current is not None:
            chain.append(current)
            current = self.versions[current].parent
        return list(reversed(chain))

    def rollback_target(self, family_id: str) -> str | None:
        return self.versions[family_id].parent

    def summary(self) -> dict[str, Any]:
        return {
            "families": sorted(self.versions),
            "lineage": {fid: self.lineage(fid) for fid in sorted(self.versions)},
        }


def build_operator_registry(operators: list[StructureOperator]) -> dict[str, StructureOperator]:
    registry: dict[str, StructureOperator] = {}
    for operator in operators:
        if operator.operator_id in registry:
            raise SpecError(f"duplicate operator id: {operator.operator_id}")
        registry[operator.operator_id] = operator
    return registry
