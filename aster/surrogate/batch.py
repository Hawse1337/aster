"""EI with parameter-space diversity, not an approximation to GP conditioning."""
from __future__ import annotations
from typing import Sequence
import numpy as np
from .selection import Selection, expected_improvement, upper_confidence_bound

def diverse_batch(mean: Sequence[float], std: Sequence[float], *, points=None,
                  count=1, strategy="ei", best=None, beta=2.0, pending=None, length=0.15,
                  utility=None):
    """Greedy local penalisation on unit-box points; invariant to pool ordering."""
    mean, std = np.asarray(mean, float), np.asarray(std, float)
    if mean.ndim != 1 or mean.shape != std.shape or not np.all(np.isfinite(mean)) or not np.all(np.isfinite(std)):
        raise ValueError("mean/std must be finite equally sized vectors")
    if count <= 0:
        return []
    if strategy == "ei":
        base = expected_improvement(mean, std, float(np.max(mean)) if best is None else float(best))
    elif strategy == "ucb":
        base = upper_confidence_bound(mean, std, beta)
        base = base - base.min() + 1e-12
    elif strategy == "explore":
        base = np.maximum(std, 0)
    else:
        raise ValueError("unknown acquisition strategy: " + strategy)
    if utility is not None:
        base = np.asarray(utility, float)
        if base.shape != mean.shape or not np.all(np.isfinite(base)) or np.any(base < 0):
            raise ValueError("utility must be finite nonnegative scores for every candidate")
    if points is None:
        if count > 1 or pending is not None:
            raise ValueError("batch diversity requires candidate parameter coordinates")
        points = np.zeros((len(mean), 1))
    points = np.asarray(points, float)
    if points.ndim != 2 or len(points) != len(mean) or not np.all(np.isfinite(points)):
        raise ValueError("points must be finite X[N,D]")
    occupied = [] if pending is None else list(np.atleast_2d(pending))
    if occupied and any(len(point) != points.shape[1] for point in occupied):
        raise ValueError("pending dimension differs from candidate dimension")
    picks, used = [], set()
    for _ in range(min(int(count), len(points))):
        scores = base.copy()
        if occupied:
            distance = np.min(np.linalg.norm(points[:, None, :] - np.asarray(occupied)[None, :, :], axis=2), axis=1)
            scores *= 1.0 - np.exp(-0.5 * (distance / max(float(length), 1e-9)) ** 2)
            scores[distance < 1e-8] = -np.inf
        if used:
            scores[list(used)] = -np.inf
        valid = np.flatnonzero(np.isfinite(scores))
        if not len(valid):
            break
        index = min(valid.tolist(), key=lambda i: (-float(scores[i]), tuple(points[i])))
        picks.append(Selection(index, float(scores[index]), float(mean[index]), float(std[index])))
        used.add(index)
        occupied.append(points[index])
    return picks

def kriging_believer(mean, std, **kwargs):
    """Legacy alias for diverse_batch; does not condition GP fantasies."""
    return diverse_batch(mean, std, **kwargs)
