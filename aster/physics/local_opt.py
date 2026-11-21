"""Constrained local modification using the sensitivity matrix (module C).

Solves the linearised problem from the task book:

    min || J_target * dx - dy_target ||^2
    s.t. |J_protected * dx| <= epsilon        (protect already-passing metrics)
         ||dx|| <= trust_region_radius

The solution is a *proposal*: it must still be validated by a real run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
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
    names = list(dict.fromkeys(n for n in (parameter_names or matrix.params) if n in matrix.steps))
    if not names:
        return LocalProposal({}, 0.0, {}, False, "no differentiable parameters")
    parameter_scales, basis = _parameter_scales(matrix, names)
    note = "; legacy finite-difference step units (parameter ranges unavailable)" \
        if basis == "finite_difference_steps_legacy" else ""
    target_row = np.array([matrix.raw.get(target_kind, {}).get(n, 0.0) for n in names])
    norm_target = target_row * parameter_scales
    rows = np.array([[matrix.raw.get(kind, {}).get(n, 0.0) for n in names]
                     for kind in protected], dtype=float).reshape(-1, len(names))
    norm_rows = rows * parameter_scales
    if not np.all(np.isfinite(norm_target)) or not np.all(np.isfinite(norm_rows)):
        raise ValueError("sensitivities must be finite in the trust-region basis")
    if not np.any(norm_target):
        return LocalProposal({}, 0.0, {}, False, f"target {target_kind} has no sensitivity" + note,
                             trust_region=trust_region, trust_region_basis=basis)
    normalised = _constrained_step(norm_target, desired_change, norm_rows,
                                   np.array(list(protected.values())), trust_region)
    if normalised is None:
        return LocalProposal({}, 0.0, {}, False, "local constraint search budget exhausted" + note,
                             trust_region=trust_region, trust_region_basis=basis)
    delta_units = normalised * parameter_scales
    if not np.all(np.isfinite(delta_units)) or math.hypot(*normalised) > trust_region * (1 + 1e-12):
        return LocalProposal({}, 0.0, {}, False, "invalid physical step or trust-region violation" + note,
                             trust_region=trust_region, trust_region_basis=basis)

    predicted_target = float(np.dot(target_row, delta_units))
    predicted_protected = dict(zip(protected, map(float, rows @ delta_units)))
    if not math.isfinite(predicted_target):
        return LocalProposal({}, 0.0, {}, False, "non-finite target prediction" + note,
                             trust_region=trust_region, trust_region_basis=basis)
    violated = [
        kind for kind, limit in protected.items()
        if not math.isfinite(predicted_protected[kind])
        or abs(predicted_protected[kind]) > limit + 1e-12 * max(1.0, limit)
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


def _constrained_step(target, desired, rows, limits, radius):
    """Solve a scalar least-squares problem on a ball intersected by slabs.

    The feasible set contains the origin and is symmetric. Maximise the signed
    target projection, then scale back if the requested change is attainable.
    On an affine face the support point of a ball has a closed form. If that
    point violates a slab, some violated boundary blocks the segment from a
    constrained optimum to it. Searching those faces therefore suffices.
    """
    if desired == 0:
        return np.zeros_like(target)
    magnitude = math.hypot(*target)
    direction = math.copysign(1.0, desired) * target / magnitude
    wanted = abs(desired) / magnitude / radius
    # Every vector below is in a unit-radius ball, with unit-length slab rows.
    lengths = np.array([math.hypot(*row) for row in rows])
    keep = lengths > 0
    normals = rows[keep] / lengths[keep, None]
    bounds = limits[keep] / lengths[keep] / radius
    equalities = normals[bounds == 0]
    limited = (bounds > 0) & (bounds < 1)
    walls = np.concatenate((normals[limited], -normals[limited]))
    offsets = np.tile(bounds[limited], 2)
    best = np.zeros_like(target)
    best_value = 0.0
    faces = [()]
    visited = set()
    tol = 1e-13
    while faces:
        face = faces.pop()
        if face in visited:
            continue
        visited.add(face)
        # Many nearly redundant protected metrics can generate exponentially
        # many faces. Fail explicitly instead of returning an uncertified step.
        if len(visited) > 4096:
            return None
        active = np.concatenate((equalities, walls[list(face)]))
        centre = np.zeros_like(target)
        tangent = direction
        if len(active):
            rhs = np.concatenate((np.zeros(len(equalities)), offsets[list(face)]))
            left, singular, right = np.linalg.svd(active, full_matrices=False)
            rank = singular > singular[0] * np.finfo(float).eps * max(active.shape)
            basis = right[rank]
            centre = basis.T @ ((left[:, rank].T @ rhs) / singular[rank])
            if np.max(np.abs(active @ centre - rhs)) > tol:
                continue
            tangent = direction - basis.T @ (basis @ direction)
        squared = float(centre @ centre)
        if squared > 1 + tol:
            continue
        point = centre
        length = math.hypot(*tangent)
        if length > tol:
            point = centre + (tangent / length) * math.sqrt(max(0.0, 1 - squared))
        upper = float(direction @ point)
        if upper <= best_value:
            continue
        # Scaling towards zero preserves all original equalities and slabs.
        step = point * min(1.0, wanted / upper)
        violations = walls @ step - offsets
        if not np.any(violations > tol):
            best, best_value = step, min(upper, wanted)
            if upper >= wanted:
                break
            continue
        # Branch on the unscaled support point, whose value bounds the face.
        for index in np.flatnonzero(walls @ point - offsets > tol):
            if index not in face:
                faces.append(tuple(sorted((*face, int(index)))))
    return best * radius
