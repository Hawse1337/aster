"""Parametric model protocol and its script-based implementation (S1).

A model turns a parameter dictionary into a concrete AEDT design and reports
whether the resulting geometry and EM set-up are still valid. The design system
never edits geometry ad hoc: every change goes through ``update()`` so it can be
snapshotted, validated and rolled back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..core import ParamSpace, SpecError


@dataclass
class GeometryReport:
    valid: bool
    checks: dict[str, bool] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, message: str = "") -> "GeometryReport":
        self.checks[name] = bool(ok)
        if not ok:
            self.valid = False
            if message:
                self.messages.append(f"{name}: {message}")
        return self


@dataclass
class ModelSnapshot:
    tag: str
    params: dict[str, Any]
    operations: list[dict[str, Any]]
    notes: str = ""


class ParametricModel(Protocol):
    """Contract every antenna case must implement."""

    def build(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def update(self, params: dict[str, Any]) -> list[dict[str, Any]]: ...

    def validate_geometry(self, params: dict[str, Any] | None = None) -> GeometryReport: ...

    def validate_em_setup(self, params: dict[str, Any] | None = None) -> GeometryReport: ...

    def snapshot(self, tag: str) -> ModelSnapshot: ...

    def restore(self, tag: str) -> dict[str, Any]: ...


class OperationModel:
    """Script-based model: a parameter set maps to an ordered operation list.

    ``operations(params)`` describes *what* to change; the actual AEDT execution
    is injected as ``apply`` (a callable that receives the operation list). This
    keeps the model testable without AEDT and lets the case decide whether the
    operations become PyAEDT calls or HFSS design-variable assignments.
    """

    def __init__(
        self,
        space: ParamSpace,
        operations: Callable[[dict[str, Any]], list[dict[str, Any]]],
        *,
        apply: Callable[[list[dict[str, Any]]], Any] | None = None,
        geometry_checks: Callable[[dict[str, Any]], GeometryReport] | None = None,
        em_checks: Callable[[dict[str, Any]], GeometryReport] | None = None,
        incremental_operations: Callable[[dict[str, Any], dict[str, Any]],
                                         list[dict[str, Any]]] | None = None,
    ):
        self.space = space
        self._operations = operations
        self._apply = apply
        self._geometry_checks = geometry_checks
        self._em_checks = em_checks
        # update() may drop destructive operations whose inputs did not change; a
        # case that never supplies one keeps the previous full-list behaviour
        self._incremental = incremental_operations
        self.current: dict[str, Any] = dict(space.default())
        self.history: list[ModelSnapshot] = []
        self.snapshots: dict[str, ModelSnapshot] = {}
        self.apply_count = 0

    # ------------------------------------------------------------------ build
    def build(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        clean = self.space.validate(params or self.space.default())
        operations = self._operations(clean)
        self._execute(operations)
        self.current = clean
        return operations

    # ----------------------------------------------------------------- update
    def update(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        clean = self.space.validate(params)
        if clean == self.current:
            return []
        previous = dict(self.current)
        operations = (self._incremental(clean, previous) if self._incremental
                      else self._operations(clean))
        self._execute(operations)
        self.current = clean
        return operations

    def _execute(self, operations: list[dict[str, Any]]) -> None:
        if self._apply is not None and operations:
            self._apply(operations)
            self.apply_count += 1

    # --------------------------------------------------------------- validate
    def validate_geometry(self, params: dict[str, Any] | None = None) -> GeometryReport:
        target = dict(params) if params is not None else dict(self.current)
        if self._geometry_checks is None:
            return GeometryReport(valid=True, checks={"validator": True})
        return self._geometry_checks(target)

    def validate_em_setup(self, params: dict[str, Any] | None = None) -> GeometryReport:
        target = dict(params) if params is not None else dict(self.current)
        if self._em_checks is None:
            return GeometryReport(valid=True, checks={"em_setup": True})
        return self._em_checks(target)

    # --------------------------------------------------------------- snapshot
    def snapshot(self, tag: str) -> ModelSnapshot:
        snapshot = ModelSnapshot(tag=tag, params=dict(self.current),
                                 operations=self._operations(dict(self.current)))
        self.snapshots[tag] = snapshot
        self.history.append(snapshot)
        return snapshot

    def restore(self, tag: str) -> dict[str, Any]:
        if tag not in self.snapshots:
            raise SpecError(f"unknown snapshot: {tag}")
        snapshot = self.snapshots[tag]
        self.update(dict(snapshot.params))
        return dict(snapshot.params)


def check_manufacturing(params: dict[str, Any], rules: dict[str, dict[str, Any]]) -> GeometryReport:
    """Evaluate simple per-parameter manufacturing limits declared on ParamSpec."""
    report = GeometryReport(valid=True)
    for name, rule in (rules or {}).items():
        if name not in params:
            continue
        minimum = rule.get("min")
        maximum = rule.get("max")
        value = params[name]
        if minimum is not None and value < minimum:
            report.add(f"manufacturing.{name}", False, f"{value} < min {minimum}")
        if maximum is not None and value > maximum:
            report.add(f"manufacturing.{name}", False, f"{value} > max {maximum}")
    return report


