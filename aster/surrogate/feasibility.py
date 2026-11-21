"""Locally supported estimates of joint feasibility from complete solved designs."""
from __future__ import annotations

import math

import numpy as np


# Distances are Euclidean in continuous/integer coordinates divided by case spans.
_RADIUS = 0.35
_MIN_GEOMETRIES = 8


class FeasibilityModel:
    def __init__(self, space):
        self.space = space
        self.fitted = {}
        self.diagnostics = {}

    def _encode(self, params):
        checked = self.space.validate(params)
        choices, vector = [], []
        for spec in self.space:
            value = checked[spec.name]
            if spec.kind == "choice":
                choices.append(spec.choices.index(value))
            else:
                if spec.kind == "int" and params[spec.name] != value:
                    raise ValueError("fractional integer design parameter")
                vector.append(spec.normalize(value))
        return tuple(choices), tuple(vector)

    def predict(self, params):
        """Return a smoothed probability for acquisition, or None outside validated support."""
        try:
            choices, vector = self._encode(params)
        except (TypeError, ValueError, KeyError):
            return None
        if choices not in self.fitted:
            return None
        x, y = self.fitted[choices]
        return _local_probability(x, y, np.asarray(vector))


def _local_probability(x, y, point):
    distances = np.linalg.norm(x - point, axis=1)
    order = np.argsort(distances, kind="stable")
    nearby = order[distances[order] <= _RADIUS]
    if len(nearby) < 3:
        return None
    # Exact observations and replicate counts never become empirical certainties.
    # Include ties at the fifth neighbour to avoid depending on row order.
    cutoff = distances[nearby[min(5, len(nearby))-1]]
    nearest = nearby[distances[nearby] <= cutoff + 1e-12]
    offsets = x[nearest] - point
    # A nonnegative barycentric solution certifies interpolation on measured
    # support, including lower-dimensional tracks in a larger parameter space.
    # A failed certificate abstains even if another representation might exist.
    system = np.vstack((offsets.T, np.ones(len(nearest))))
    rhs = np.zeros(system.shape[0])
    rhs[-1] = 1
    weights, *_ = np.linalg.lstsq(system, rhs, rcond=None)
    if np.max(np.abs(system @ weights - rhs)) > 1e-9 or np.min(weights) < -1e-9:
        return None
    return float((1 + y[nearest].sum()) / (2 + len(nearest)))


def fit_feasibility(space, labelled_points):
    """Fit each choice stratum separately, with one independent vote per geometry.

    Callers supply only true/false labels from complete matching-recipe evidence.
    Brier scores and binary mistakes at 0.5 use held-out geometries only. Coverage
    is tracked because abstained rows do not enter either Brier score.
    """
    model = FeasibilityModel(space)
    strata = {}
    received = ignored = 0
    for params, label in labelled_points:
        received += 1
        if not isinstance(label, (bool, np.bool_)):
            ignored += 1
            continue
        try:
            choices, vector = model._encode(params)
        except (TypeError, ValueError, KeyError):
            ignored += 1
            continue
        strata.setdefault(choices, {}).setdefault(vector, set()).add(bool(label))

    reports = []
    for choice, groups in sorted(strata.items()):
        n = len(groups)
        conflicting = sum(len(labels) != 1 for labels in groups.values())
        report = {"choice_indices": list(choice), "geometries": n, "conflicting_geometries": conflicting,
                  "enabled": False, "validated": 0, "abstained": n, "brier": None, "baseline_brier": None,
                  "false_pass": 0, "missed_pass": 0}
        reports.append(report)
        if conflicting:
            report["reason"] = "inconsistent replicate outcomes in this choice stratum"
            continue
        points = sorted(groups)
        x = np.asarray(points, dtype=float)
        y = np.array([int(next(iter(groups[point]))) for point in points])
        if n < _MIN_GEOMETRIES or min(int(y.sum()), n-int(y.sum())) < 2:
            report["reason"] = "insufficient distinct geometries or class diversity"
            continue
        predictions, actual, priors = [], [], []
        for i, point in enumerate(x):
            # Each fold removes all replicates of this geometry before predicting.
            train = np.arange(n) != i
            probability = _local_probability(x[train], y[train], point)
            if probability is not None:
                predictions.append(probability)
                actual.append(y[i])
                priors.append((1 + int(y[train].sum())) / (2 + n - 1))
        validated = len(actual)
        report.update(validated=validated, abstained=n-validated)
        if not validated:
            report["reason"] = "no held-out geometry has local interpolation support"
            continue
        p = np.asarray(predictions)
        observed = np.asarray(actual)
        score = float(np.mean((p-observed)**2))
        baseline = float(np.mean((np.asarray(priors)-observed)**2))
        report.update(brier=score, baseline_brier=baseline,
                      false_pass=int(np.sum((p >= .5) & (observed == 0))),
                      missed_pass=int(np.sum((p < .5) & (observed == 1))))
        if validated < max(5, math.ceil(n/2)) or min(sum(actual), validated-sum(actual)) < 2:
            report["reason"] = "insufficient two-class validation coverage"
        elif score >= baseline * .95:
            report["reason"] = "held-out Brier improvement below five percent"
        else:
            report.update(enabled=True, reason="validated for acquisition")
            model.fitted[choice] = (x, y)

    scored = sum(r["validated"] for r in reports)
    def weighted(key):
        return (sum(r["validated"] * r[key] for r in reports if r[key] is not None) / scored
                if scored else None)
    model.diagnostics = {
        "method": "smoothed_local_class_fraction", "validation": "leave_one_geometry_out_by_choice",
        "probability_basis": "Laplace-smoothed fraction of nearby unique measured designs",
        "baseline": "Laplace-smoothed training-fold class prevalence on the same held-out points",
        "role": "acquisition_only", "enabled": bool(model.fitted), "input_rows": received,
        "ignored_rows": ignored, "geometries": sum(r["geometries"] for r in reports),
        "validated": scored, "abstained": sum(r["abstained"] for r in reports),
        "false_pass": sum(r["false_pass"] for r in reports), "missed_pass": sum(r["missed_pass"] for r in reports),
        "brier": weighted("brier"), "baseline_brier": weighted("baseline_brier"), "strata": reports,
        "radius_in_normalized_space": _RADIUS, "classification_threshold": .5,
    }
    return model
