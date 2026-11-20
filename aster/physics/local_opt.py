"""Constrained local modification using the sensitivity matrix (module C).

Solves the linearised problem from the task book:

    min || J_target * dx - dy_target ||^2
    s.t. |J_protected * dx| <= epsilon        (protect already-passing metrics)
         ||dx|| <= trust_region_radius

The solution is a *proposal*: it must still be validated by a real run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence
import math

import numpy as np

from .sensitivity import SensitivityMatrix


@dataclass
class LocalProposal:
    delta: dict[str, float]
    predicted_target_change: float
    predicted_protected_change: dict[str, float]
    accepted: bool
    reason: str = ""
    trust_region: float = 0.0
    trust_region_basis: str = "unavailable"


def propose_local_step(
    matrix: SensitivityMatrix,
    *,
    target_kind: str,
    desired_change: float,
    protected: dict[str, float] | None = None,
    trust_region: float = 0.3,
    parameter_names: Sequence[str] | None = None,
) -> LocalProposal:
    """Least-squares step that moves ``target_kind`` with limited side effects.

    ``protected`` maps metric kind -> maximum allowed absolute change.
    ``trust_region`` bounds ||dx / parameter_range|| for matrices carrying case
    ranges. Legacy matrices without ranges retain finite-difference-step units,
    explicitly reported in ``trust_region_basis`` and ``reason``. The returned
    ``delta`` is expressed in the parameters' own units.
    """
    protected = protected or {}
    if not math.isfinite(trust_region) or trust_region <= 0:
        raise ValueError("trust_region must be finite and positive")
    if not math.isfinite(desired_change):
        raise ValueError("desired_change must be finite")
    if any(not math.isfinite(limit) or limit < 0 for limit in protected.values()):
        raise ValueError("protected limits must be finite and non-negative")
    names = [n for n in (parameter_names or matrix.params) if n in matrix.steps]
    if not names:
        return LocalProposal({}, 0.0, {}, False, "no differentiable parameters")
    parameter_scales, basis = _parameter_scales(matrix, names)
    note = "; legacy finite-difference step units (parameter ranges unavailable)" \
        if basis == "finite_difference_steps_legacy" else ""
    target_row = np.array([matrix.raw.get(target_kind, {}).get(n, 0.0) for n in names])
    if not np.any(np.abs(target_row) > 1e-12):
        return LocalProposal({}, 0.0, {}, False, f"target {target_kind} has no sensitivity" + note,
                             trust_region=trust_region, trust_region_basis=basis)

    def normalised_to_units(vector: np.ndarray) -> np.ndarray:
        return vector * parameter_scales

    # step in normalised units that achieves the desired change (best fit)
    norm_target = target_row * parameter_scales
    base_step_norm = norm_target / max(float(np.dot(norm_target, norm_target)), 1e-12) \
        * float(desired_change)
    base_step_norm = _project(base_step_norm, protected, names, matrix, trust_region, parameter_scales)
    delta_units = normalised_to_units(base_step_norm)

    predicted_target = float(np.dot(target_row, delta_units))
    predicted_protected = {
        kind: float(np.dot(
            np.array([matrix.raw.get(kind, {}).get(n, 0.0) for n in names]), delta_units
        ))
        for kind in protected
    }
    violated = [
        kind for kind, limit in protected.items()
        if abs(predicted_protected.get(kind, 0.0)) > float(limit) + 1e-12 * max(1.0, abs(float(limit)))
    ]
    if violated:
        return LocalProposal(
            delta={n: float(delta_units[i]) for i, n in enumerate(names)},
            predicted_target_change=predicted_target,
            predicted_protected_change=predicted_protected,
            accepted=False,
            reason=f"protected metric(s) would move too much: {violated}" + note,
            trust_region=trust_region,
            trust_region_basis=basis,
        )
    if abs(predicted_target) < abs(float(desired_change)) * 0.25:
        return LocalProposal(
            delta={n: float(delta_units[i]) for i, n in enumerate(names)},
            predicted_target_change=predicted_target,
            predicted_protected_change=predicted_protected,
            accepted=False,
            reason="target change is too small under the current trust region" + note,
            trust_region=trust_region,
            trust_region_basis=basis,
        )
    return LocalProposal(
        delta={n: float(delta_units[i]) for i, n in enumerate(names)},
        predicted_target_change=predicted_target,
        predicted_protected_change=predicted_protected,
        accepted=True,
        reason="ok" + note,
        trust_region=trust_region,
        trust_region_basis=basis,
    )


def _parameter_scales(matrix: SensitivityMatrix, names: Sequence[str]) -> tuple[np.ndarray, str]:
    spans = getattr(matrix, "parameter_spans", {}) or {}
    use_ranges = all(name in spans for name in names)
    scales = np.array([spans[name] if use_ranges else matrix.steps[name] for name in names], dtype=float)
    if not np.all(np.isfinite(scales)) or np.any(scales <= 0):
        raise ValueError("parameter scales must be finite and positive")
    return scales, "case_parameter_ranges" if use_ranges else "finite_difference_steps_legacy"


def _project(step: np.ndarray, protected: dict[str, float], names: Sequence[str],
             matrix: SensitivityMatrix, trust_region: float, parameter_scales: np.ndarray) -> np.ndarray:
    """Shrink the step until the trust region and protected limits hold."""
    step = np.array(step, dtype=float)
    norm = float(np.linalg.norm(step))
    if norm > trust_region:
        step *= trust_region / norm
    delta_units = step * parameter_scales
    factor = 1.0
    for kind, limit in protected.items():
        row = np.array([matrix.raw.get(kind, {}).get(n, 0.0) for n in names])
        movement = abs(float(np.dot(row, delta_units)))
        if movement > float(limit):
            factor = min(factor, float(limit) / movement)
    # Every protected constraint is homogeneous in this radial step. Scaling
    # by the most restrictive ratio preserves the trust bound without a
    # finite-difference-dependent series of arbitrary halvings.
    return step * factor
