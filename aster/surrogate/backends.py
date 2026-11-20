"""Constrained batch acquisition with explicit preference, quality and fit diagnostics."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Sequence
import numpy as np
from .batch import diverse_batch
from .models import GaussianProcessModel, cross_validate, model_is_usable

METHODS = ("auto", "qlogehvi", "qlognehvi", "qlognparego", "diverse_ei", "believer")

@dataclass
class BatchSelection:
    points: np.ndarray
    method: str
    note: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    def to_dict(self):
        return {"method": self.method, "note": self.note, "diagnostics": self.diagnostics,
                "points": np.asarray(self.points, float).round(6).tolist()}

def available_methods():
    from .botorch_acq import available
    has_torch = available()
    return {"qlogehvi": has_torch, "qlognehvi": has_torch, "qlognparego": has_torch,
            "diverse_ei": True, "believer": True}

def _scalarise(y, weights=None):
    values = np.asarray(y, float)
    factor = np.ones(values.shape[1]) if weights is None else np.asarray(weights, float)
    if factor.shape != (values.shape[1],) or not np.all(np.isfinite(factor)) or np.any(factor < 0) or factor.sum() <= 0:
        raise ValueError("weights must be finite nonnegative values, one per objective")
    scale = np.maximum(np.ptp(values, axis=0), 1e-8)
    return np.sum((values - values.min(axis=0)) / scale * (factor / factor.sum()), axis=1)

def _quality(x, y, groups=None, times=None, minimum_coverage=0.6, *,
             role="objective", max_relative_rmse=0.5):
    reports = []
    for column in range(y.shape[1]):
        report = cross_validate(GaussianProcessModel(restarts=0), x, y[:, column],
                                folds=3, groups=groups, times=times)
        span = float(np.ptp(y[:, column]))
        # A constant observed constraint still has a held-out check in its
        # normalized physical units. A zero span is not a zero error tolerance.
        error_scale = span if span > 0 else 1.0
        reports.append({**report.to_dict(), "role": role, "output_index": column,
                        "observed_span": span, "error_scale": error_scale,
                        "constant_observed": span == 0,
                        "max_relative_rmse": float(max_relative_rmse),
                        "scope": "held-out observed design groups; not global feasibility calibration",
                        "usable": bool(model_is_usable(
            report, error_scale, max_relative_rmse=max_relative_rmse,
            minimum_coverage=minimum_coverage))})
    return reports

def _diverse_points(x, y, bounds, count, seed, *, weights=None, constraint_obs=None,
                    pending_X=None, diagnostics=None, explore=False):
    from scipy.special import ndtr
    from .selection import expected_improvement
    info = diagnostics if diagnostics is not None else {}
    low = np.array([pair[0] for pair in bounds])
    span = np.array([pair[1] - pair[0] for pair in bounds])
    unit_x = (x - low) / span
    unit_pending = np.empty((0, x.shape[1])) if pending_X is None else (pending_X - low) / span
    pool = np.random.RandomState(seed).rand(max(2000, 100 * count), x.shape[1])
    scores = _scalarise(y, weights)
    model = GaussianProcessModel(restarts=0).fit(unit_x, -scores)
    mean, std = model.predict(pool)
    g = np.empty((len(x), 0)) if constraint_obs is None else constraint_obs
    feasible = np.all(g <= 0, axis=1)
    pof, expected_violation = np.ones(len(pool)), np.zeros(len(pool))
    for column in range(g.shape[1]):
        scale = max(float(np.max(np.abs(g[:, column]))), 1e-8)
        constraint = GaussianProcessModel(restarts=0).fit(unit_x, g[:, column] / scale)
        g_mean, g_std = constraint.predict(pool)
        g_std = np.maximum(g_std, 1e-9)
        z = g_mean / g_std
        pof *= ndtr(-z)
        expected_violation += g_std * np.exp(-0.5 * z ** 2) / np.sqrt(2 * np.pi) + g_mean * ndtr(z)
    if not feasible.any():
        mean, std = -expected_violation, np.zeros(len(pool))
        utility = mean - mean.min() + 1e-12
        info["stage"] = "feasibility_first"
    else:
        incumbent = float(np.max(-scores[feasible]))
        utility = (std if explore else expected_improvement(mean, std, incumbent)) * pof
        info.update({"stage": "exploration" if explore else "objective", "observed_incumbent": incumbent})
    occupied = np.vstack([unit_x, unit_pending])
    picks = diverse_batch(mean, std, points=pool, count=count, best=float(np.max(-scores[feasible])) if feasible.any() else None,
                          pending=occupied, utility=np.maximum(utility, 0))
    info.update({"constraint_count": g.shape[1], "feasible_observations": int(feasible.sum()),
                 "pending_count": len(unit_pending), "preference": "weighted_scalar_ei",
                 "diversity": "unit_parameter_distance", "fit": {"status": "sklearn_mll_fitted"}})
    return low + pool[[pick.index for pick in picks]] * span

def _run_botorch(method, x, y, bounds, count, seed, noiseless, reference_point=None,
                 weights=None, constraint_obs=None, pending_X=None, diagnostics=None,
                 fit_maxiter=60, fit_timeout_s=15.0):
    from .botorch_acq import qlognehvi_batch, qlognparego_batch
    common = dict(q=count, seed=seed, constraint_obs=constraint_obs, pending_X=pending_X,
                  diagnostics=diagnostics, fit_maxiter=fit_maxiter, fit_timeout_s=fit_timeout_s)
    if method == "qlognparego":
        return qlognparego_batch(x, y, bounds, weights=weights, **common)
    return qlognehvi_batch(x, y, bounds, noiseless=(method == "qlogehvi") if noiseless is None else noiseless,
                           reference_point=reference_point, **common)

def select_batch(x_obs, y_obs, bounds, count=2, method="auto", seed=0, noiseless=None,
                 weights=None, reference_point=None, *, constraint_obs=None,
                 pending_X=None, groups=None, times=None, quality_check=True,
                 fit_maxiter=60, fit_timeout_s=15.0, minimum_coverage=0.6,
                 max_constraint_relative_rmse=0.35):
    """Select physical-unit points. Constraint observations satisfy g<=0.

    Nonuniform weights select user Chebyshev preferences through qLogNParEGO;
    hypervolume methods explicitly reject them rather than silently ignoring them.
    Legacy 'believer' requests return honestly named 'diverse_ei'.
    """
    from .botorch_acq import _validate_inputs
    if method not in METHODS:
        raise ValueError("unknown acquisition backend: " + method)
    if not np.isfinite(minimum_coverage) or not 0 <= minimum_coverage <= 1:
        raise ValueError("minimum_coverage must be finite and between zero and one")
    if not np.isfinite(max_constraint_relative_rmse) or max_constraint_relative_rmse < 0:
        raise ValueError("max_constraint_relative_rmse must be finite and nonnegative")
    x, y, low, high, g, pending = _validate_inputs(x_obs, y_obs, bounds, count, constraint_obs, pending_X)
    if weights is not None:
        _scalarise(y, weights)
    preference = weights is not None and not np.allclose(weights, np.asarray(weights)[0])
    if method in {"qlogehvi", "qlognehvi"} and preference:
        raise ValueError("hypervolume does not implement preference weights; use qlognparego or diverse_ei")
    info = {"constraints": "observed_g_le_zero", "pending_count": len(pending),
            "uncertainty": "GP intervals; held-out coverage is reported, calibration is not assumed",
            "minimum_coverage": float(minimum_coverage)}
    reports = _quality(x, y, groups, times, minimum_coverage) if quality_check else []
    constraint_reports = (_quality(x, g, groups, times, minimum_coverage, role="constraint",
                                   max_relative_rmse=max_constraint_relative_rmse)
                          if quality_check and g.shape[1] else [])
    info["quality"] = reports
    info["constraint_quality"] = constraint_reports
    info["constraint_quality_status"] = ("not_applicable" if not g.shape[1] else
                                          "checked" if quality_check else "not_checked")
    quality_ok = all(report["usable"] for report in reports + constraint_reports)
    info["quality_gate_basis"] = "objective_and_constraint_holdouts"
    info["quality_gate"] = ("not_checked" if not quality_check else
                            "validated" if quality_ok else "exploration_only")
    usable = available_methods()
    requested = method
    if method == "auto":
        method = "qlognparego" if preference else "qlognehvi"
        if not quality_ok or not usable.get(method, False):
            method = "diverse_ei"
    if method in {"believer", "diverse_ei"}:
        points = _diverse_points(x, y, bounds, count, seed, weights=weights, constraint_obs=g,
                                  pending_X=pending, diagnostics=info, explore=not quality_ok)
        note = ("model quality is not validated; acquisition explores" if not quality_ok else
                "parameter-space diverse EI; no GP fantasy conditioning")
        if requested == "auto" and not usable.get("qlognehvi", False):
            note += "; torch/botorch are not installed"
        return BatchSelection(points, "diverse_ei", note, info)
    if not usable.get(method, False):
        raise RuntimeError("the %s backend needs torch and botorch installed" % method)
    try:
        points = _run_botorch(method, x, y, bounds, count, seed, noiseless, reference_point,
                              weights=weights, constraint_obs=g, pending_X=pending,
                              diagnostics=info, fit_maxiter=fit_maxiter, fit_timeout_s=fit_timeout_s)
    except Exception as exc:
        if requested != "auto":
            raise
        info["backend_failure"] = {"method": method, "reason": str(exc)[:240]}
        points = _diverse_points(x, y, bounds, count, seed, weights=weights, constraint_obs=g,
                                  pending_X=pending, diagnostics=info, explore=not quality_ok)
        return BatchSelection(points, "diverse_ei", "%s failed (%s)" % (method, str(exc)[:180]), info)
    # Pending points and measured points are excluded even when an optimiser lands
    # on a baseline design. A diverse EI top-up replaces rejected locations.
    accepted = []
    occupied = list((np.vstack([x, pending]) - low) / (high - low))
    for point in np.asarray(points):
        unit = (point - low) / (high - low)
        if all(np.linalg.norm(unit - prior) > 1e-6 for prior in occupied):
            accepted.append(point)
            occupied.append(unit)
    if len(accepted) < count:
        extra = _diverse_points(x, y, bounds, count - len(accepted), seed + 1, weights=weights,
                                 constraint_obs=g, pending_X=np.vstack([pending, np.asarray(accepted).reshape(-1, x.shape[1])]))
        accepted.extend(extra)
        info["diversity_topup"] = len(extra)
    return BatchSelection(np.asarray(accepted).reshape(-1, x.shape[1]), method,
                           "" if quality_ok else "explicit backend requested despite unvalidated model quality", info)
