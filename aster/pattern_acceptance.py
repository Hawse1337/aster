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


@dataclass(frozen=True)
class AngularCoverageSpec:
    """A sampled coverage region in a named theta-phi coordinate system.

    Phi runs from the first endpoint towards increasing azimuth; (330, 30)
    crosses zero, (0, 360) is a full circle, and (0, 0) is one cut.
    """
    frequencies_ghz: tuple[float, ...]
    theta_deg: tuple[float, float]
    phi_deg: tuple[float, float]
    min_realized_gain_dbi: float
    max_ripple_db: float | None = None
    max_step_deg: float = 10.0
    coordinate_system: str = "Global"

    def __post_init__(self):
        frequencies = tuple(sorted(self.frequencies_ghz))
        if (not frequencies or any(not math.isfinite(f) or f <= 0 for f in frequencies)
                or any(b-a <= 2e-8 for a,b in zip(frequencies, frequencies[1:]))):
            raise ValueError("provide distinct positive working frequencies in GHz")
        lo, hi = self.theta_deg
        start, stop = self.phi_deg
        if not (0 <= lo <= hi <= 180 and all(math.isfinite(p) for p in (start, stop))
                and abs(stop-start) <= 360):
            raise ValueError("invalid theta or phi domain")
        if not math.isfinite(self.min_realized_gain_dbi) or not 0 < self.max_step_deg <= 180:
            raise ValueError("invalid realized gain floor or angular step")
        if self.max_ripple_db is not None and (not math.isfinite(self.max_ripple_db) or self.max_ripple_db < 0):
            raise ValueError("ripple limit must be finite and nonnegative")
        if not isinstance(self.coordinate_system, str) or not self.coordinate_system.strip():
            raise ValueError("coordinate system is required")
        object.__setattr__(self, "frequencies_ghz", frequencies)
        object.__setattr__(self, "theta_deg", (lo, hi))
        object.__setattr__(self, "phi_deg", (start, stop))


def _domain_axis(samples, start, stop, max_step, *, periodic=False):
    tol = 1e-7
    full = periodic and abs(stop-start) == 360
    span = stop - start
    if periodic:
        span = 360 if full else span % 360
    selected = []
    for index, coordinate in enumerate(samples):
        offset = (coordinate-start) % 360 if periodic else coordinate-start
        if periodic and abs(offset-360) <= tol:
            offset = 0.0
        if -tol <= offset <= span+tol:
            selected.append((offset, index))
    selected.sort()
    if not selected:
        raise ValueError("target angular domain is unsampled")
    positions = [offset for offset,_ in selected]
    if full:
        positions.append(positions[0]+360)
    elif abs(positions[0]) > tol or abs(positions[-1]-span) > tol:
        raise ValueError("target angular boundaries were not sampled")
    gap = max((b-a for a,b in zip(positions, positions[1:])), default=0.0)
    if gap > max_step+tol:
        raise ValueError("target angular grid is too sparse")
    return [index for _,index in selected], gap


def _region_metrics(sphere, spec):
    theta = [float(value) for value in sphere["theta_deg"]]
    phi = [float(value) for value in sphere["phi_deg"]]
    gain = [[float(value) for value in row] for row in sphere["gain_db"]]
    if (not theta or not phi or len(gain) != len(theta) or any(len(row) != len(phi) for row in gain)
            or not all(math.isfinite(value) for row in [theta, phi, *gain] for value in row)
            or any(not 0 <= value <= 180 for value in theta) or len(set(theta)) != len(theta)):
        raise ValueError("expected finite rectangular angular gain samples")
    # The common 0/360 seam may be repeated. A conflicting repeated report
    # value is ambiguous and is not averaged into an apparent pass.
    unique = {}
    for j, angle in enumerate(phi):
        key = round(angle % 360, 8) % 360
        if key in unique and any(abs(row[j]-row[unique[key]]) > 1e-6 for row in gain):
            raise ValueError("conflicting gains at the periodic phi seam")
        unique.setdefault(key, j)
    ts, tgap = _domain_axis(theta, *spec.theta_deg, spec.max_step_deg)
    angles = list(unique)
    js, pgap = _domain_axis(angles, *spec.phi_deg, spec.max_step_deg, periodic=True)
    selected = [(gain[i][unique[angles[j]]], theta[i], angles[j]) for i in ts for j in js]
    minimum, t, p = min(selected)
    maximum = max(selected)[0]
    return {"min_realized_gain_dbi": minimum, "max_realized_gain_dbi": maximum,
            "ripple_db": maximum-minimum, "min_theta_deg": t, "min_phi_deg": p,
            "sample_count": len(selected), "theta_gap_deg": tgap, "phi_gap_deg": pgap}


def assess_angular_coverage(snapshots, spec: AngularCoverageSpec) -> dict[str, Any]:
    """Measure a gain floor and within-region ripple at every required frequency.

    Complete evidence yields a worst-frequency dB margin usable for ranking.
    Scores refer only to sampled frequencies and directions.
    """
    snaps = list(snapshots)
    result = {"status": "INSUFFICIENT_EVIDENCE", "scope": "sampled_directions_and_frequencies",
              "quantity": "dB(RealizedGainTotal)", "coordinate_system": spec.coordinate_system,
              "frequencies_ghz": list(spec.frequencies_ghz), "theta_deg": list(spec.theta_deg),
              "phi_deg": list(spec.phi_deg), "min_realized_gain_dbi": spec.min_realized_gain_dbi,
              "max_ripple_db": spec.max_ripple_db, "max_step_deg": spec.max_step_deg,
              "missing_frequencies_ghz": [], "unverified_frequencies_ghz": [], "metrics": {}, "rows": []}
    identities = {key: set() for key in ("run_id", "solution", "sphere_name")}
    source_files = set()
    missing_run_id = False
    for f in spec.frequencies_ghz:
        matches = [s for s in snaps if isinstance(s.get("frequency_ghz"), (int,float))
                   and abs(s["frequency_ghz"]-f) <= 1e-8]
        row = {"frequency_ghz": f, "status": "INSUFFICIENT_EVIDENCE"}
        try:
            if not matches:
                result["missing_frequencies_ghz"].append(f)
                raise ValueError("missing requested frequency")
            if len(matches) != 1:
                raise ValueError("ambiguous snapshots for requested frequency")
            sample = matches[0]
            if (sample.get("quantity") != result["quantity"] or sample.get("source") != "MEASURED"
                    or sample.get("normalized") or sample.get("normalised")):
                raise ValueError("requires a solved absolute dB(RealizedGainTotal) grid")
            if (sample.get("coordinate_system") != spec.coordinate_system
                    or sample.get("coordinate_system_recorded", True) is not True):
                raise ValueError("coordinate system is missing or different")
            if not sample.get("sphere"):
                raise ValueError("no sampled angular grid")
            if sample.get("source_file"):
                source_files.add(sample["source_file"])
                missing_run_id |= not bool(sample.get("run_id"))
            for key, values in identities.items():
                if sample.get(key):
                    values.add(sample[key])
            metrics = _region_metrics(sample["sphere"], spec)
            gain_margin = metrics["min_realized_gain_dbi"] - spec.min_realized_gain_dbi
            ripple_margin = None if spec.max_ripple_db is None else spec.max_ripple_db - metrics["ripple_db"]
            margin = gain_margin if ripple_margin is None else min(gain_margin, ripple_margin)
            row.update(metrics, gain_margin_db=gain_margin, ripple_margin_db=ripple_margin,
                       coverage_margin_db=margin, status="SAMPLED_PASS" if margin >= 0 else "FAIL")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            result["unverified_frequencies_ghz"].append(f)
            row["reason"] = str(exc)
        result["rows"].append(row)
    if len(source_files) > 1 and missing_run_id:
        result["reason"] = "multi-file coverage requires a common recorded run_id"
        return result
    if any(len(values) > 1 for values in identities.values()):
        result["reason"] = "snapshots refer to conflicting runs, solutions or spheres"
        return result
    rows = result["rows"]
    if not result["unverified_frequencies_ghz"]:
        low = min(rows, key=lambda r: r["min_realized_gain_dbi"])
        high = max(rows, key=lambda r: r["ripple_db"])
        worst = min(rows, key=lambda r: r["coverage_margin_db"])
        result["metrics"] = {"min_realized_gain_dbi": low["min_realized_gain_dbi"],
                             "min_gain_frequency_ghz": low["frequency_ghz"],
                             "max_ripple_db": high["ripple_db"],
                             "max_ripple_frequency_ghz": high["frequency_ghz"],
                             "gain_margin_db": low["gain_margin_db"],
                             "ripple_margin_db": high["ripple_margin_db"],
                             "coverage_margin_db": worst["coverage_margin_db"],
                             "limiting_frequency_ghz": worst["frequency_ghz"]}
        result["status"] = "SAMPLED_PASS"
    if any(row["status"] == "FAIL" for row in rows):
        result["status"] = "FAIL"
    return result
