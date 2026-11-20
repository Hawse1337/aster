"""Quantitative equatorial pattern checks on a saved spherical gain grid."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RingPatternSpec:
    belt_deg: tuple[float, float] = (60.0, 120.0)
    near_axis_deg: float = 30.0
    axial_deg: float = 45.0
    max_step_deg: float = 10.0

    def __post_init__(self) -> None:
        lo, hi = self.belt_deg
        if not (0 < self.near_axis_deg <= self.axial_deg < lo < 90 < hi < 180 - self.axial_deg
                and 0 < self.max_step_deg <= self.near_axis_deg):
            raise ValueError("invalid ring belt, axial cap or angular spacing")


# The study target is opt-in. It is kept separate from the optimizer's case constraints.
ASTRABB_RING_TARGET = {
    "horizon_peak_dbi": (">=", 7.0),
    "horizon_ripple_db": ("<=", 2.0),
    "horizon_to_axial_db": (">=", 15.0),
    "near_axis_peak_dbi": ("<=", -5.0),
    "belt_power_fraction": (">=", 0.85),
}


def _grid(sphere: dict[str, Any]) -> tuple[list[float], list[float], list[list[float]]]:
    theta = [float(x) for x in sphere["theta_deg"]]
    phi = [float(x) for x in sphere["phi_deg"]]
    gain = [[float(x) for x in row] for row in sphere["gain_db"]]
    if (len(theta) < 3 or len(phi) < 3 or len(gain) != len(theta)
            or any(len(row) != len(phi) for row in gain)):
        raise ValueError("expected a rectangular theta × phi grid")
    if not all(math.isfinite(x) for row in [theta, phi, *gain] for x in row):
        raise ValueError("nonfinite angular samples")
    if theta[0] != 0.0 or theta[-1] != 180.0 or not 0 <= phi[0] < 360:
        raise ValueError("full polar coverage required")
    if any(b <= a for axis in (theta, phi) for a, b in zip(axis, axis[1:])):
        raise ValueError("angular axes must increase without duplicate coordinates")
    if abs(phi[-1] - phi[0] - 360) <= 1e-8:
        if any(abs(row[-1] - row[0]) > 1e-6 for row in gain):
            raise ValueError("azimuth seam values disagree")
        phi.pop()
        gain = [row[:-1] for row in gain]
    if len(phi) < 3 or phi[-1] >= 360:
        raise ValueError("expected one azimuth revolution")
    return theta, phi, gain


def _angular_integral(mu: list[float], power: list[float], low: float, high: float) -> float:
    """Integrate a linear interpolant in cos(theta), the solid-angle variable."""
    terms = []
    for left, right, p0, p1 in zip(mu, mu[1:], power, power[1:]):
        a, b = max(low, left), min(high, right)
        if b > a:
            slope = (p1 - p0) / (right - left)
            terms.append((p0 + slope * ((a + b) * 0.5 - left)) * (b - a))
    return math.fsum(terms)


def assess_ring_pattern(sphere: dict[str, Any] | None, spec: RingPatternSpec = RingPatternSpec(), *,
                        quantity: str = "dB(GainTotal)", source: str = "MEASURED",
                        target: dict[str, tuple[str, float]] | None = None) -> dict[str, Any]:
    """Return sampled extrema and a solid-angle quadrature, never reconstructed from cuts.

    A fraction is the belt integral of linear gain divided by its sphere
    integral at the same frequency. It is not an absolute radiation efficiency.
    ``SAMPLED_PASS`` only refers to this angular grid at this solved frequency.
    """
    result: dict[str, Any] = {"status": "INSUFFICIENT_EVIDENCE", "source": source,
                              "quantity": quantity, "belt_deg": list(spec.belt_deg),
                              "near_axis_deg": spec.near_axis_deg, "axial_deg": spec.axial_deg,
                              "metrics": {}, "checks": {},
                              "scope": "one_solved_frequency_sampled_grid"}
    if source != "MEASURED" or not sphere:
        return {**result, "reason": "requires a solved full sphere"}
    if quantity != "dB(GainTotal)":
        return {**result, "reason": "ring checks require dB(GainTotal)"}
    try:
        theta, phi, gain = _grid(sphere)
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        return {**result, "reason": str(exc)}
    dphi = [b - a for a, b in zip(phi, phi[1:] + [phi[0] + 360])]
    peak_theta_step = max(b-a for a,b in zip(theta, theta[1:]))
    result["grid"] = {"theta_count": len(theta), "phi_count": len(phi),
                      "max_theta_step_deg": peak_theta_step, "max_phi_step_deg": max(dphi)}
    if max(peak_theta_step, max(dphi)) > spec.max_step_deg + 1e-8:
        return {**result, "reason": "angular grid is too sparse for the configured belt and caps"}
    if 90.0 not in theta:
        return {**result, "reason": "theta=90° was not sampled"}

    peak, i_peak, j_peak = max((value, i, j) for i,row in enumerate(gain) for j,value in enumerate(row))
    # Relative power avoids exponent overflow and cancels in the fraction.
    power = [[10.0 ** ((value-peak)/10.0) for value in row] for row in gain]
    phi_avg = [math.fsum((a+b) * step/720 for a,b,step in zip(row, row[1:]+row[:1], dphi))
               for row in power]
    mu = [math.cos(math.radians(t)) for t in reversed(theta)]
    polar_power = list(reversed(phi_avg))
    lo, hi = spec.belt_deg
    belt_lo, belt_hi = math.cos(math.radians(hi)), math.cos(math.radians(lo))
    belt = _angular_integral(mu, polar_power, belt_lo, belt_hi)
    total = _angular_integral(mu, polar_power, -1.0, 1.0)
    horizon_index = theta.index(90.0)
    horizon = gain[horizon_index]
    pole_max = max(max(gain[0]), max(gain[-1]))
    cap_max = max(max(row) for t,row in zip(theta, gain) if min(t,180-t) <= spec.near_axis_deg)
    axial_max = max(max(row) for t,row in zip(theta, gain) if min(t,180-t) <= spec.axial_deg)
    peak_thetas = [t for t,row in zip(theta,gain) if max(row) >= peak - 1e-8]
    metrics = {"peak_dbi": peak, "peak_theta_deg": theta[i_peak], "peak_phi_deg": phi[j_peak],
               "peak_theta_range_deg": [peak_thetas[0], peak_thetas[-1]],
               "horizon_peak_dbi": max(horizon), "horizon_min_dbi": min(horizon),
               "horizon_mean_dbi": 10 * math.log10(phi_avg[horizon_index]) + peak if phi_avg[horizon_index] > 0 else None,
               "horizon_ripple_db": max(horizon) - min(horizon),
               "pole_peak_dbi": pole_max, "axial_peak_dbi": axial_max, "near_axis_peak_dbi": cap_max,
               "horizon_to_axial_db": max(horizon) - axial_max,
               "belt_peak_dbi": max(max(row) for t,row in zip(theta,gain) if lo <= t <= hi),
               "belt_min_dbi": min(min(row) for t,row in zip(theta,gain) if lo <= t <= hi),
               "belt_mean_dbi": 10 * math.log10(belt / (belt_hi-belt_lo)) + peak if belt > 0 else None,
               "belt_power_fraction": max(0.0, min(1.0, belt/total))}
    result.update(status="SAMPLED_METRICS", metrics=metrics)
    if target is not None:
        for key, (op, limit) in target.items():
            value = metrics.get(key)
            if value is None or op not in (">=", "<=") or not math.isfinite(limit):
                raise ValueError(f"unsupported pattern criterion: {key}")
            passed = value >= limit if op == ">=" else value <= limit
            result["checks"][key] = {"value": value, "operator": op, "target": limit, "passed": passed}
        result["status"] = "SAMPLED_PASS" if all(c["passed"] for c in result["checks"].values()) else "FAIL"
    return result
