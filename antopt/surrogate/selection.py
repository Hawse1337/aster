"""Acquisition functions for candidate selection (module B)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

try:  # scipy is an optional dependency of the surrogate extra
    from scipy.stats import norm as _norm
except Exception:  # pragma: no cover - fallback when scipy is absent
    _norm = None


def _phi(z: np.ndarray) -> np.ndarray:
    if _norm is not None:
        return _norm.cdf(z)
    return 0.5 * (1.0 + np.vectorize(np.math.erf)(z / np.sqrt(2.0)))


def expected_improvement(mean: np.ndarray, std: np.ndarray, best: float) -> np.ndarray:
    """EI for maximisation (higher is better)."""
    mean = np.asarray(mean, dtype=float)
    std = np.maximum(np.asarray(std, dtype=float), 1e-9)
    z = (mean - best) / std
    return (mean - best) * _phi(z) + std * np.maximum(
        np.exp(-0.5 * z ** 2) / np.sqrt(2.0 * np.pi), 0.0
    )


def upper_confidence_bound(mean: np.ndarray, std: np.ndarray, beta: float = 2.0) -> np.ndarray:
    return np.asarray(mean, dtype=float) + float(beta) * np.maximum(np.asarray(std, dtype=float), 0.0)


@dataclass
class Selection:
    index: int
    score: float
    mean: float
    std: float


def select_candidates(
    mean: np.ndarray,
    std: np.ndarray,
    *,
    best: float | None = None,
    strategy: str = "ei",
    count: int = 1,
    beta: float = 2.0,
) -> list[Selection]:
    """Rank a candidate pool; ``best`` is the current measured optimum."""
    mean = np.asarray(mean, dtype=float)
    std = np.asarray(std, dtype=float)
    if strategy == "ei":
        reference = float(np.max(mean)) if best is None else float(best)
        scores = expected_improvement(mean, std, reference)
    elif strategy == "ucb":
        scores = upper_confidence_bound(mean, std, beta)
    elif strategy == "explore":
        scores = std
    else:
        raise ValueError(f"unknown acquisition strategy: {strategy}")
    order = np.argsort(scores)[::-1][: max(1, int(count))]
    return [Selection(index=int(i), score=float(scores[i]), mean=float(mean[i]),
                      std=float(std[i])) for i in order]
