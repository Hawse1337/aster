"""Parameter sensitivity analysis (module C).

Central differences on the *measured* metrics, normalised so quantities with
different units can be compared. Every perturbation is a real guarded run, so
the caller passes a budget; the evaluator cache prevents paying twice for the
same candidate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence
import math

from ..core import CaseSpec


class EvaluationBudgetExceeded(RuntimeError):
    """No new measurement may start after this evaluator's budget is spent."""


class BudgetedEvaluator:
    """One shared, cached hard gate for every measurement in a search action.

    Calls are charged before entering the expensive operation, including failed
    attempts. Existing successful evidence may be seeded without being charged.
    """

    def __init__(self, case: CaseSpec, evaluator: Callable, budget: int | None = None,
                 cache: dict[str, Any] | None = None):
        self.case, self.evaluator, self.budget = case, evaluator, budget
        self.cache = dict(cache or {})
        self.calls = 0

    @property
    def remaining(self) -> int | None:
        return None if self.budget is None else max(0, int(self.budget) - self.calls)

    def __call__(self, params: dict[str, Any]) -> Any:
        candidate = self.case.make_candidate(params)
        key = candidate.candidate_id
        if key in self.cache:
            return self.cache[key]
        if self.remaining is not None and self.remaining <= 0:
            raise EvaluationBudgetExceeded("measurement budget exhausted before starting a new run")
        self.calls += 1
        # The identity and expensive evaluation must use the same canonical
        # parameter values, particularly after integer rounding.
        result = self.evaluator(dict(candidate.params))
        if str(getattr(result, "status", "COMPLETED")).upper() == "COMPLETED":
            self.cache[key] = result
        return result


@dataclass
class SensitivityMatrix:
    base_params: dict[str, float]
    raw: dict[str, dict[str, float]] = field(default_factory=dict)
    normalized: dict[str, dict[str, float]] = field(default_factory=dict)
    steps: dict[str, float] = field(default_factory=dict)
    evaluations: int = 0
    metric_kinds: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    complete: bool = True
    skipped: dict[str, str] = field(default_factory=dict)
    parameter_spans: dict[str, float] = field(default_factory=dict)

    def top_params(self, metric_kind: str, count: int = 3) -> list[tuple[str, float]]:
        row = self.normalized.get(metric_kind, {})
        ranked = sorted(row.items(), key=lambda item: abs(item[1]), reverse=True)
        return ranked[: max(1, int(count))]

    def strongest_metric(self, param: str) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        for metric_kind, row in self.normalized.items():
            value = row.get(param)
            if value is None:
                continue
            if best is None or abs(value) > abs(best[1]):
                best = (metric_kind, value)
        return best

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_params": dict(self.base_params),
            "raw": self.raw,
            "normalized": self.normalized,
            "steps": self.steps,
            "evaluations": self.evaluations,
            "metric_kinds": self.metric_kinds,
            "params": self.params,
            "complete": self.complete, "skipped": self.skipped,
            "parameter_spans": dict(self.parameter_spans),
        }


def _perturbed(params: dict[str, float], name: str, delta: float) -> dict[str, float]:
    return {**params, name: float(params[name]) + float(delta)}


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def compute_sensitivity(
    case: CaseSpec,
    evaluator: Callable[[dict[str, Any]], Any],
    base_params: dict[str, float],
    *,
    params: Sequence[str] | None = None,
    metric_kinds: Sequence[str] | None = None,
    step_fraction: float = 0.1,
    budget: int | None = None,
) -> SensitivityMatrix:
    """Central-difference Jacobian over the case's parameters and metrics.

    ``evaluator`` is a callable ``params -> Evaluation`` (usually a cached
    executor); ``evaluations`` counts how many *new* runs were charged.
    """
    space = case.family.param_space
    base_params = space.validate(dict(base_params))
    if not math.isfinite(step_fraction) or step_fraction <= 0:
        raise ValueError("step_fraction must be finite and positive")
    if budget is not None:
        evaluator = BudgetedEvaluator(case, evaluator, max(0, int(budget)))
    names = list(params) if params else [p.name for p in space]
    kinds = list(metric_kinds) if metric_kinds else sorted(
        {metric.kind for metric in case.constraints}
    )
    before = getattr(evaluator, "calls", None)
    observed = 0
    def measure(values):
        nonlocal observed
        entry = evaluator(values)
        observed += 1
        return entry
    base_entry = measure(dict(base_params))
    matrix = SensitivityMatrix(base_params=dict(base_params), metric_kinds=kinds,
                               params=names, parameter_spans={
                                   name: float(space[name].high) - float(space[name].low)
                                   for name in names if space[name].kind != "choice"})
    if str(getattr(base_entry, "status", "COMPLETED")).upper() != "COMPLETED":
        matrix.complete = False
        matrix.skipped["baseline"] = "baseline is not completed measurement evidence"
        matrix.evaluations = (getattr(evaluator, "calls", 0) - before
                              if before is not None else observed)
        return matrix
    for name in names:
        spec = space[name]
        if spec.kind == "choice":
            continue
        width = float(spec.high) - float(spec.low)
        step = max(width * float(step_fraction), 1.0 if spec.kind == "int" else 1e-6)
        plus_params = _perturbed(base_params, name, step)
        minus_params = _perturbed(base_params, name, -step)
        plus_params[name] = spec.validate(_clamp(plus_params[name], spec.low, spec.high))
        minus_params[name] = spec.validate(_clamp(minus_params[name], spec.low, spec.high))
        actual = plus_params[name] - minus_params[name]
        if abs(actual) < 1e-12:
            continue
        try:
            plus = measure(plus_params)
            minus = measure(minus_params)
        except EvaluationBudgetExceeded:
            matrix.complete = False
            matrix.skipped[name] = "measurement budget exhausted"
            break
        if any(str(getattr(entry, "status", "COMPLETED")).upper() != "COMPLETED"
               for entry in (plus, minus)):
            matrix.skipped[name] = "failed perturbation is not derivative evidence"
            matrix.complete = False
            continue
        matrix.steps[name] = float(actual)
        for kind in kinds:
            y_plus = plus.metrics.get(kind)
            y_minus = minus.metrics.get(kind)
            if y_plus is None or y_minus is None or not all(
                    math.isfinite(float(value)) for value in (y_plus, y_minus)):
                continue
            raw = float(y_plus - y_minus) / float(actual)
            metric_scale = _metric_scale(base_entry, kind, kinds)
            matrix.raw.setdefault(kind, {})[name] = raw
            matrix.normalized.setdefault(kind, {})[name] = raw * width / metric_scale
    after = getattr(evaluator, "calls", None)
    if before is not None and after is not None:
        matrix.evaluations = int(after) - int(before)
    else:
        # Plain callables cannot report cache misses: report actual invocations,
        # rather than silently claiming the sensitivity scan cost zero runs.
        matrix.evaluations = observed
    return matrix


def _metric_scale(base_entry: Any, kind: str, kinds: Sequence[str]) -> float:
    value = base_entry.metrics.get(kind)
    scale = abs(float(value)) if isinstance(value, (int, float)) else 1.0
    return scale if scale > 1e-9 else 1.0


def screen_parameters(
    case: CaseSpec,
    evaluator: Callable[[dict[str, Any]], Any],
    base_params: dict[str, float],
    *,
    metric_kind: str,
    top_k: int = 3,
    step_fraction: float = 0.1,
    budget: int | None = None,
) -> list[tuple[str, float]]:
    """Cheap one-sided screen: rank parameters by influence on one metric."""
    space = case.family.param_space
    if not math.isfinite(step_fraction) or step_fraction <= 0:
        raise ValueError("step_fraction must be finite and positive")
    base_params = space.validate(dict(base_params))
    if budget is not None:
        evaluator = BudgetedEvaluator(case, evaluator, max(0, int(budget)))
    base_entry = evaluator(dict(base_params))
    if str(getattr(base_entry, "status", "COMPLETED")).upper() != "COMPLETED":
        return []
    base_value = base_entry.metrics.get(metric_kind)
    scored: list[tuple[str, float]] = []
    for spec in space:
        if spec.kind == "choice":
            continue
        width = float(spec.high) - float(spec.low)
        step = max(width * float(step_fraction), 1.0 if spec.kind == "int" else 1e-6)
        candidate = _perturbed(base_params, spec.name, step)
        candidate[spec.name] = spec.validate(_clamp(candidate[spec.name], spec.low, spec.high))
        if abs(candidate[spec.name] - base_params[spec.name]) < 1e-12:
            continue
        try:
            entry = evaluator(candidate)
        except EvaluationBudgetExceeded:
            break
        if str(getattr(entry, "status", "COMPLETED")).upper() != "COMPLETED":
            continue
        value = entry.metrics.get(metric_kind)
        if value is None or base_value is None or not all(
                math.isfinite(float(number)) for number in (value, base_value)):
            continue
        delta = float(value) - float(base_value)
        scored.append((spec.name, delta / width))
    scored.sort(key=lambda item: abs(item[1]), reverse=True)
    return scored[: max(1, int(top_k))]
