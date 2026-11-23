"""Compile a workflow brief into a bounded copy of its registered antenna case."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math

from .core import ParamSpace, ParamSpec


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return value


@dataclass(frozen=True)
class _PlanParam(ParamSpec):
    """An explicitly declared step uses an integer lattice anchored at the plan's lower bound."""
    plan_step: float | None = None

    def denormalize(self, normalized):
        if self.plan_step is None:
            return min(self.high, max(self.low, super().denormalize(normalized)))
        last = math.floor((self.high - self.low) / self.plan_step + 1e-10)
        i = round(min(1., max(0., normalized)) * (self.high - self.low) / self.plan_step)
        value = self.low + min(i, last) * self.plan_step
        return int(round(value)) if self.kind == "int" else min(self.high, round(value, 12))

    def validate(self, value):
        clean = super().validate(value)
        if clean != value:
            raise ValueError(f"{self.name}: integer required")
        if self.plan_step is not None:
            snapped = self.denormalize(self.normalize(clean))
            if not math.isclose(clean, snapped, rel_tol=0, abs_tol=self.plan_step * 1e-8):
                raise ValueError(f"{self.name}: value outside declared step grid")
            return snapped
        return clean


def _parameter(param, changes):
    if not isinstance(changes, dict):
        raise ValueError(f"{param.name}: parameter declaration must be an object")
    if not changes:
        return param
    if param.kind == "choice":
        if set(changes) - {"choices", "default"}:
            raise ValueError(f"{param.name}: choice parameters accept choices and default")
        options = changes.get("choices", param.choices)
        if (not isinstance(options, (tuple, list)) or not options or
                any(v not in param.choices or v in options[:i] for i, v in enumerate(options))):
            raise ValueError(f"{param.name}: invalid choice subset")
        default = changes.get("default", param.default if param.default in options else options[0])
        if default not in options:
            raise ValueError(f"{param.name}: default outside declared choices")
        return replace(param, choices=tuple(options), default=default)
    if set(changes) - {"low", "high", "step", "default"}:
        raise ValueError(f"{param.name}: unknown parameter settings")
    low = _number(changes.get("low", param.low), param.name)
    high = _number(changes.get("high", param.high), param.name)
    if not param.low <= low < high <= param.high:
        raise ValueError(f"{param.name}: plan must lie inside registered bounds")
    step = _number(changes["step"], param.name) if "step" in changes else None
    if step is not None and step <= 0:
        raise ValueError(f"{param.name}: step must be positive")
    if param.kind == "int" and any(int(n) != n for n in (low, high, step or 1)):
        raise ValueError(f"{param.name}: integer bounds and step required")
    default = min(high, max(low, param.default))
    effective = _PlanParam(**{**asdict(param), "low": low, "high": high, "default": default}, plan_step=step)
    default = changes.get("default", effective.denormalize(effective.normalize(default)))
    return replace(effective, default=effective.validate(default))


def _registered(items, declaration):
    if not isinstance(declaration, dict) or not declaration.get("kind"):
        raise ValueError("each objective/constraint must name its metric kind")
    matches = [item for item in items if item.kind == declaration["kind"]
               and ("id" not in declaration or item.id == declaration["id"])]
    if len(matches) != 1:
        raise ValueError(f"unknown or ambiguous metric {declaration}")
    return matches[0]


def effective_case(case, plan):
    known = {p.name for p in case.family.param_space}
    if set(plan.parameter_space) - known:
        raise ValueError("parameter_space contains unknown names")
    space = ParamSpace(_parameter(p, plan.parameter_space.get(p.name, {})) for p in case.family.param_space)
    objectives = []
    for declaration in plan.objectives:
        obj = _registered(case.objectives, declaration)
        if (set(declaration) - {"id", "kind", "direction"}
                or any(o.id == obj.id for o in objectives)):
            raise ValueError("invalid or duplicate plan objective")
        objectives.append(replace(obj, direction=declaration.get("direction", obj.direction)))
    constraints = {c.id: c for c in case.constraints}
    seen = set()
    for declaration in plan.constraints:
        c = _registered(case.constraints, declaration)
        if (c.id in seen or set(declaration) - {"id", "kind", "operator", "target", "band_ghz", "unit"}
                or declaration.get("operator", c.operator) != c.operator
                or declaration.get("unit", c.unit) != c.unit
                or ("band_ghz" in declaration and declaration["band_ghz"] != list(c.band_ghz or case.frequency_ghz))):
            raise ValueError("plan constraint must match a unique registered measurement, band, unit and operator")
        seen.add(c.id)
        target = _number(declaration.get("target", c.target), c.kind)
        if ((c.operator == "<=" and target > c.target) or (c.operator == ">=" and target < c.target)
                or (c.operator == "==" and target != c.target)):
            raise ValueError("plan cannot relax a registered hard constraint")
        constraints[c.id] = replace(c, target=target)
    return replace(case, family=replace(case.family, param_space=space),
                   objectives=objectives or list(case.objectives), constraints=list(constraints.values()))
