"""Measured S11 response surrogate, with explicit axes, provenance and holdout checks.

Predictions and derived scores are never published as measured campaign evidence.
This optional preview does not invoke AEDT or expand the measured frequency range.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .touchstone import contiguous_bandwidth


class S11ResponseModel:
    def __init__(self, param_names, bounds, contract, *, minimum_samples=8,
                 max_mae_db=2.0, minimum_coverage=0.6):
        self.names = tuple(param_names)
        box = np.asarray(bounds, dtype=float)
        if box.shape != (len(self.names), 2) or not np.all(np.isfinite(box)) or np.any(box[:, 1] <= box[:, 0]):
            raise ValueError("response bounds must be finite, ordered pairs")
        self.low, self.span = box[:, 0], box[:, 1] - box[:, 0]
        self.contract = dict(contract)
        if not self.contract.get("identity"):
            raise ValueError("a versioned learning contract is required")
        self.minimum_samples = max(8, int(minimum_samples))
        self.max_mae_db = float(max_mae_db)
        self.minimum_coverage = float(minimum_coverage)
        self.model = None

    @staticmethod
    def _gp():
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
        return GaussianProcessRegressor(
            kernel=ConstantKernel(1.0, (1e-3, 1e3)) * Matern(0.5, (0.02, 5.0), nu=2.5)
            + WhiteKernel(1e-3, (1e-6, 0.5)), normalize_y=True, random_state=0,
            n_restarts_optimizer=0, alpha=1e-8)

    def fit(self, rows):
        self.model = None
        accepted = []
        rejected = []
        seen = set()
        axis = None
        # Keep newest repeated physical designs together, preventing train/holdout leakage.
        ordered = sorted(rows, key=lambda r: str(r.get("started_utc") or r.get("created_utc") or ""), reverse=True)
        for row in ordered:
            reason = ""
            saved = row.get("learning_contract") or (row.get("telemetry") or {}).get("learning_contract") or {}
            try:
                if str(row.get("status", "")).upper() != "COMPLETED" or row.get("source") in {"PREDICTED", "predicted"}:
                    raise ValueError("not measured completed evidence")
                if saved.get("identity") != self.contract["identity"]:
                    raise ValueError("incompatible learning contract")
                params = row.get("params") or {}
                x = np.array([float(params[name]) for name in self.names])
                unit = (x - self.low) / self.span
                if not np.all(np.isfinite(unit)) or np.any(unit < 0) or np.any(unit > 1):
                    raise ValueError("invalid parameters")
                raw = row.get("s11_points") or []
                points = [(float(p["freq_ghz"]), float(p["s11_db"])) if isinstance(p, dict)
                          else (float(p[0]), float(p[1])) for p in raw]
                points.sort()
                frequencies = np.array([p[0] for p in points])
                values = np.array([p[1] for p in points])
                if len(points) < 3 or not np.all(np.isfinite(points)) or np.any(frequencies <= 0) or np.any(np.diff(frequencies) <= 0):
                    raise ValueError("invalid or duplicate frequency axis")
                if axis is not None and (axis.shape != frequencies.shape or not np.allclose(axis, frequencies, rtol=0, atol=1e-9)):
                    raise ValueError("frequency axes differ; no inferred frequencies")
                key = tuple(x)
                if key in seen:
                    raise ValueError("repeated physical design")
                seen.add(key)
                if axis is None:
                    axis = frequencies
                accepted.append((unit, values, row.get("run_id")))
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                reason = str(exc)
            if reason:
                rejected.append({"run_id": row.get("run_id"), "reason": reason})
        accepted.reverse()
        self.diagnostics = {"samples": len(accepted), "excluded": rejected,
                            "usable_for_selection": False, "validation": "forward_completed_block",
                            "source_runs": [r[2] for r in accepted], "quantity": "S11_dB",
                            "thresholds": {"max_mae_db": self.max_mae_db,
                                           "minimum_coverage": self.minimum_coverage}}
        if len(accepted) < self.minimum_samples:
            self.diagnostics["reason"] = "insufficient distinct compatible measured curves"
            return self
        x = np.array([row[0] for row in accepted])
        y = np.array([row[1] for row in accepted])
        holdout = max(2, math.ceil(len(x) * 0.25))
        validation = self._gp().fit(x[:-holdout], y[:-holdout])
        mean, std = validation.predict(x[-holdout:], return_std=True)
        mae = float(np.mean(np.abs(mean - y[-holdout:])))
        coverage = float(np.mean(np.abs(mean - y[-holdout:]) <= 1.96 * np.maximum(std, 1e-8)))
        self.diagnostics.update({"holdout_samples": holdout, "holdout_mae_db": mae,
                                 "holdout_95pct_coverage": coverage,
                                 "usable_for_selection": mae <= self.max_mae_db and coverage >= self.minimum_coverage})
        self.model = self._gp().fit(x, y)
        self.axis, self.x = axis, x
        self.center = x.mean(axis=0)
        _, singular, basis = np.linalg.svd(x - self.center, full_matrices=False)
        self.support_basis = basis[singular > max(1e-10, singular[0] * 1e-8)]
        distances = np.linalg.norm(x[:, None] - x[None, :], axis=2)
        np.fill_diagonal(distances, np.inf)
        self.support_radius = min(0.35, 1.5 * float(np.median(np.min(distances, axis=1))))
        return self

    def predict(self, params, *, band_ghz=None, threshold_db=-10.0):
        if self.model is None:
            raise ValueError(self.diagnostics.get("reason", "response model is not fitted"))
        x = np.array([float(params[name]) for name in self.names])
        unit = (x - self.low) / self.span
        if not np.all(np.isfinite(unit)) or np.any(unit < 0) or np.any(unit > 1):
            raise ValueError("response prediction is outside the case bounds")
        band = tuple(band_ghz or (float(self.axis[0]), float(self.axis[-1])))
        if band[0] < self.axis[0] - 1e-9 or band[1] > self.axis[-1] + 1e-9 or band[1] < band[0]:
            raise ValueError("requested band exceeds the measured frequency axis")
        mean, std = self.model.predict(unit[None, :], return_std=True)
        mean, std = mean[0], std[0]
        selected = (self.axis >= band[0]) & (self.axis <= band[1])
        if not np.any(selected):
            raise ValueError("no measured frequencies in the requested band")
        within_coverage = bool(np.all(unit >= np.min(self.x, axis=0) - 1e-12)
                               and np.all(unit <= np.max(self.x, axis=0) + 1e-12))
        centered = unit - self.center
        projection = (centered @ self.support_basis.T) @ self.support_basis
        within_subspace = bool(np.linalg.norm(centered - projection) <= 1e-7)
        nearest = float(np.min(np.linalg.norm(self.x - unit, axis=1)))
        locally_supported = nearest <= self.support_radius + 1e-12
        usable = self.diagnostics["usable_for_selection"] and within_coverage and within_subspace and locally_supported
        return {"source": "PREDICTED", "quantity": "S11_dB", "params": dict(params),
                "frequency_ghz": self.axis.tolist(), "mean_db": mean.tolist(), "std_db": std.tolist(),
                "predicted_bandwidth": contiguous_bandwidth(list(zip(self.axis, mean)), threshold_db,
                                                           band, max_gap_ghz=float(np.max(np.diff(self.axis))) * 1.01),
                "sampled_worst_violation_db": max(0.0, float(np.max(mean[selected] - threshold_db))),
                "usable_for_selection": bool(usable), "within_measured_parameter_coverage": within_coverage,
                "within_measured_subspace": within_subspace, "local_support": locally_supported,
                "nearest_normalized_distance": nearest, "support_radius": self.support_radius,
                "diagnostics": self.diagnostics, "learning_contract_identity": self.contract["identity"],
                "warning": "Predicted sampled response; not proof of continuous-band matching or measured performance"}
