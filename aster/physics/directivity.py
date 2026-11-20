"""Directivity from a full-sphere gain pattern, and efficiency from directivity vs gain.

The design in this repo reports only ``GainTotal`` over far-field cuts - no Directivity and no
radiated/accepted power (see ``docs/evidence/quantities_probe.json``). Directivity is still
recoverable from the pattern itself:

    D = 4*pi*U_max / P_rad,   P_rad = integral U dOmega = sum U * sin(theta) * dtheta * dphi

so a full theta/phi grid of gain is enough. With D and the gain at the same point, the radiation
efficiency follows: ``eta = 10 ** ((gain_db - directivity_db) / 10)``.

The integration is a plain trapezoid rule on a regular grid, which is what a far-field export
gives; tests pin the analytic values (isotropic -> 1.0, sin(theta) pattern -> 4/pi).
"""
from __future__ import annotations

import math
from typing import Sequence

__all__ = [
    "directivity_from_pattern",
    "directivity_db",
    "efficiency_from_pattern",
    "grid_shape",
]

MIN_DB = -60.0


def grid_shape(theta_deg: Sequence[float], phi_deg: Sequence[float]) -> tuple[int, int]:
    """Return (n_theta, n_phi) after checking the grid is regular enough to integrate."""
    thetas = [float(value) for value in theta_deg]
    phis = [float(value) for value in phi_deg]
    if len(thetas) < 3 or len(phis) < 3:
        raise ValueError("a directivity integral needs at least 3x3 samples")
    if max(thetas) - min(thetas) < 170.0:
        raise ValueError("theta must cover (almost) the full 0..180 degrees")
    if max(phis) - min(phis) < 350.0:
        raise ValueError("phi must cover the full 360 degrees")
    return len(thetas), len(phis)


def _to_linear(gain_db: float) -> float:
    value = float(gain_db)
    if value != value:  # NaN
        return 0.0
    if value < MIN_DB:
        return 0.0
    return 10.0 ** (value / 10.0)


def directivity_from_pattern(theta_deg: Sequence[float], phi_deg: Sequence[float],
                             gain_db: Sequence[Sequence[float]]) -> float:
    """Directivity (linear) from a gain grid indexed ``[theta][phi]``.

    ``gain_db`` must be the radiated gain (mismatch excluded); a realized gain would fold the
    mismatch into the result and understate the directivity.
    """
    n_theta, n_phi = grid_shape(theta_deg, phi_deg)
    if len(gain_db) != n_theta or any(len(row) != n_phi for row in gain_db):
        raise ValueError("gain grid does not match the theta/phi sample counts")

    thetas = [float(value) for value in theta_deg]
    phis = [float(value) for value in phi_deg]
    # regular spacing: use the mean step, which is what a generated grid gives
    d_theta = math.radians((max(thetas) - min(thetas)) / (n_theta - 1))
    # phi is periodic: a grid that runs 0..360 repeats its first column, so drop it and use the
    # wrapped step, otherwise the integral is 1/(1+1/n) too large and D comes out low
    wrapped = abs((max(phis) - min(phis)) - 360.0) < 1e-6
    columns = n_phi - 1 if wrapped else n_phi
    # with a wrapped grid the n_phi samples span n_phi-1 intervals of 360 degrees
    d_phi = math.radians(360.0 / (n_phi - 1)) if wrapped else math.radians(
        (max(phis) - min(phis)) / (n_phi - 1))

    total = 0.0
    peak = 0.0
    for i in range(n_theta):
        sin_theta = math.sin(math.radians(thetas[i]))
        row = gain_db[i]
        for j in range(columns):
            value = _to_linear(row[j])
            peak = max(peak, value)
            total += value * sin_theta * d_theta * d_phi
    if total <= 0.0 or peak <= 0.0:
        raise ValueError("degenerate pattern: no radiated power")
    return 4.0 * math.pi * peak / total


def directivity_db(linear: float) -> float:
    if linear <= 0:
        raise ValueError("directivity must be positive")
    return 10.0 * math.log10(linear)


def efficiency_from_pattern(theta_deg: Sequence[float], phi_deg: Sequence[float],
                            gain_db: Sequence[Sequence[float]]) -> float:
    """eta = gain / directivity, both taken at the pattern peak."""
    linear = directivity_from_pattern(theta_deg, phi_deg, gain_db)
    peak_gain_db = max(max(row) for row in gain_db)
    return max(0.0, min(1.0, 10.0 ** ((peak_gain_db - directivity_db(linear)) / 10.0)))
