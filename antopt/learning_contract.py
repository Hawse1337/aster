"""Evidence identity shared by recommendation, queueing and real execution.

Old measurements remain readable, but are never silently assigned today's geometry,
missing dimensions or measurement recipe. Objective targets are deliberately outside
the measurement identity: complete raw measurements can be re-scored explicitly.
"""
from __future__ import annotations

import hashlib
import inspect
import importlib.metadata
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def _source_hash(value: Any) -> str:
    try:
        source = inspect.getsourcefile(value)
        return hashlib.sha256(Path(source).read_bytes()).hexdigest() if source else ""
    except (OSError, TypeError):
        return ""


def case_contract(case: Any, model: Any = None,
                  measurement_recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    schema = [asdict(param) for param in case.family.param_space]
    sources = sorted(set(filter(None, [_source_hash(type(model))] + [
        _source_hash(getattr(model, name, None))
        for name in ("_operations", "_geometry_checks", "_em_checks")
    ]))) if model is not None else []
    structure = {"family_id": case.family.family_id, "builder": case.family.builder,
                 "parameter_schema": schema, "sources": sources}
    # Renderer/profile helpers can change the physical geometry without changing
    # the case callback itself. Keep them in the geometry identity as well.
    structure["renderers"] = {
        name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
        for name in ("parametric/aedt.py", "parametric/profile.py")
        if (Path(__file__).parent / name).is_file()}
    spec = case.to_spec()
    # Metric extraction code is part of the recipe; a changed bandwidth/FF definition
    # must not quietly share training evidence with its previous implementation.
    extraction = {}
    for name in ("metrics.py", "touchstone.py", "farfield.py", "parametric/aedt.py"):
        path = Path(__file__).parent / name
        if path.is_file():
            extraction[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    from . import config
    environment = config.load()
    guard_root = getattr(environment, "guard_repo", None)
    guard_sources = {}
    for name in ("evaluator.py", "pattern.py"):
        path = Path(guard_root) / "aedt_guard" / name if guard_root else None
        try:
            guard_sources[name] = (hashlib.sha256(path.read_bytes()).hexdigest()
                                   if path is not None and path.is_file() else "unknown")
        except OSError:
            guard_sources[name] = "unknown"
    packages = {}
    for name in ("ansys-aedt-core", "aedt-guard"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "unknown"
    recipe = {"frequency_ghz": list(case.frequency_ghz), "setup_name": case.setup_name,
              "evaluation": spec["evaluation"], "extraction": extraction,
              "execution_policy": spec["execution_policy"],
              "guard_sources": guard_sources, "packages": packages,
              "actual_aedt_version": "unknown",
              "metric_definitions": sorted({json.dumps({key: metric.get(key)
                  for key in ("kind", "unit", "band_ghz")}, sort_keys=True)
                  for metric in spec["metrics"]})}
    recipe.update(dict(measurement_recipe or {}))
    result = {"version": 1, "case_id": case.case_id,
              "family_id": case.family.family_id, "parameter_schema": schema,
              "structure_hash": _hash(structure), "recipe_hash": _hash(recipe),
              "measurement_recipe": recipe,
              "objectives": [asdict(item) for item in case.objectives],
              "constraints": [asdict(item) for item in case.constraints]}
    result["identity"] = _hash({key: result[key] for key in
                               ("case_id", "family_id", "structure_hash", "recipe_hash")})
    # Contracts are persisted as JSON; keep in-memory and round-trip forms equal.
    return json.loads(json.dumps(result, ensure_ascii=False, default=str))


def stored_contract(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(row.get("learning_contract") or
                (row.get("telemetry") or {}).get("learning_contract") or {})


def compatibility(row: Mapping[str, Any], expected: Mapping[str, Any]) -> tuple[bool, str]:
    actual = stored_contract(row)
    row_case = row.get("case") or row.get("case_name") or actual.get("case_id")
    if row_case and row_case != expected.get("case_id"):
        return False, "different_case"
    if not actual:
        return False, "legacy_missing_contract"
    for key in ("version", "case_id", "family_id", "structure_hash", "recipe_hash", "identity"):
        if actual.get(key) != expected.get(key):
            return False, "different_" + key
    return True, "compatible"


def finite_metrics(metrics: Mapping[str, Any]) -> dict[str, float | None]:
    result = {}
    for key, value in metrics.items():
        try:
            number = float(value) if value is not None else math.nan
        except (TypeError, ValueError):
            number = math.nan
        result[key] = number if math.isfinite(number) else None
    return result


def constraint_values(metrics: Mapping[str, Any], case: Any) -> list[float] | None:
    """Signed, scaled hard constraints, with feasibility defined by g <= 0."""
    values = finite_metrics(metrics)
    result = []
    for constraint in case.constraints:
        value = values.get(constraint.kind)
        if value is None:
            return None
        if constraint.operator == "<=":
            residual = value - constraint.target
        elif constraint.operator == ">=":
            residual = constraint.target - value
        else:
            residual = abs(value - constraint.target)
        result.append(float(residual) / constraint.violation_scale)
    return result
