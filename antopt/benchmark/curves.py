"""Learning-curve comparison for expensive evaluations (S6).

Real HFSS evaluations cost minutes, so a strategy is judged by *how fast* it
finds good feasible designs, not only by its final state. These helpers build
best-so-far curves and compare them at fixed evaluation counts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np


def best_so_far(values: Sequence[float | None], *, maximize: bool = True) -> list[float]:
    """Running best of a metric; ``None`` means "not measured" and is skipped."""
    best: float | None = None
    curve: list[float] = []
    for value in values:
        if value is None:
            curve.append(np.nan if best is None else best)
            continue
        numeric = float(value)
        if best is None or (numeric > best if maximize else numeric < best):
            best = numeric
        curve.append(best)
    return curve


def feasible_only_curve(entries: Sequence[Any], kind: str, *, maximize: bool = True) -> list[float]:
    """Best metric among *feasible* entries only, indexed by evaluation order."""
    values: list[float | None] = []
    for entry in entries:
        if not getattr(entry, "feasible", False):
            values.append(None)
            continue
        value = (getattr(entry, "metrics", {}) or {}).get(kind)
        values.append(None if value is None else float(value))
    return best_so_far(values, maximize=maximize)


@dataclass
class CurveComparison:
    counts: list[int]
    rows: dict[str, list[float | None]]
    auc: dict[str, float]
    best: dict[str, float | None]

    def to_dict(self) -> dict[str, Any]:
        return {"counts": self.counts, "rows": self.rows, "auc": self.auc, "best": self.best}


def compare_curves(curves: dict[str, Sequence[float]], *,
                   counts: Sequence[int] | None = None) -> CurveComparison:
    """Sample every curve at the same evaluation counts and compare areas."""
    if not curves:
        raise ValueError("compare_curves needs at least one curve")
    longest = max(len(curve) for curve in curves.values())
    if counts is None:
        counts = []
        point = 1
        while point < longest:
            counts.append(point)
            point *= 2
        counts.append(longest)
    counts = sorted({int(c) for c in counts if 0 < int(c) <= longest})
    rows: dict[str, list[float | None]] = {}
    auc: dict[str, float] = {}
    best: dict[str, float | None] = {}
    for name, curve in curves.items():
        sampled = []
        for count in counts:
            value = curve[min(count, len(curve)) - 1] if curve else None
            sampled.append(None if value is None or np.isnan(value) else float(value))
        rows[name] = sampled
        numeric = [value for value in sampled if value is not None]
        auc[name] = float(np.mean(numeric)) if numeric else float("nan")
        best[name] = max(numeric) if numeric else None
    return CurveComparison(counts=list(counts), rows=rows, auc=auc, best=best)


def relative_area_gain(curves: dict[str, Sequence[float]], baseline: str,
                       counts: Sequence[int] | None = None) -> dict[str, float]:
    """Area under the curve relative to a baseline (0 = equal, 1 = twice as good)."""
    comparison = compare_curves(curves, counts=counts)
    reference = abs(comparison.auc.get(baseline) or 0.0) or 1.0
    return {
        name: float((comparison.auc[name] - comparison.auc[baseline]) / reference)
        for name in comparison.auc
    }
