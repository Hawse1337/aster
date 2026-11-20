"""Training data for surrogate models (module B).

Every sample carries provenance and a fidelity tag. Low-fidelity data (coarse
mesh, analytic estimates) must never be silently mixed into high-fidelity
labels: ``high_fidelity()`` is the only view the surrogate trains on by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

FIDELITY_HIGH = "high"
FIDELITY_LOW = "low"
_VALID_FIDELITY = {FIDELITY_HIGH, FIDELITY_LOW}


@dataclass
class Sample:
    candidate_id: str
    params: dict[str, float]
    targets: dict[str, float]
    fidelity: str = FIDELITY_HIGH
    source: str = "measured"
    run_id: str | None = None
    status: str = "COMPLETED"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.fidelity not in _VALID_FIDELITY:
            raise ValueError(f"unknown fidelity: {self.fidelity}")
        if self.source not in {"measured", "predicted", "analytic"}:
            raise ValueError(f"unknown source: {self.source}")

    @property
    def is_measured(self) -> bool:
        return self.source == "measured"


class Dataset:
    def __init__(self) -> None:
        self._samples: dict[str, Sample] = {}

    def __len__(self) -> int:
        return len(self._samples)

    def add(self, sample: Sample) -> None:
        existing = self._samples.get(sample.candidate_id)
        if existing is not None and existing.fidelity == FIDELITY_HIGH and sample.fidelity == FIDELITY_LOW:
            return  # never downgrade a measured sample
        self._samples[sample.candidate_id] = sample

    def extend(self, samples: Iterable[Sample]) -> None:
        for sample in samples:
            self.add(sample)

    def samples(self, fidelity: str | None = FIDELITY_HIGH,
                target: str | None = None) -> list[Sample]:
        rows = [s for s in self._samples.values()
                if (fidelity is None or s.fidelity == fidelity)
                and (target is None or target in s.targets)]
        return sorted(rows, key=lambda s: s.candidate_id)

    def matrix(self, target: str, param_names: Sequence[str],
               fidelity: str | None = FIDELITY_HIGH, *, measured_only: bool = True) -> tuple[np.ndarray, np.ndarray]:
        rows = [s for s in self.samples(fidelity=fidelity, target=target)
                if not measured_only or (s.is_measured and s.status == "COMPLETED")]
        if not rows:
            raise ValueError(f"no samples for target {target!r} at fidelity {fidelity!r}")
        x = np.array([[float(s.params[name]) for name in param_names] for s in rows], dtype=float)
        y = np.array([float(s.targets[target]) for s in rows], dtype=float)
        return x, y

    def anomalies(self, target: str, z: float = 3.0,
                  fidelity: str | None = FIDELITY_HIGH) -> list[str]:
        rows = self.samples(fidelity=fidelity, target=target)
        if len(rows) < 4:
            return []
        values = np.array([s.targets[target] for s in rows], dtype=float)
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median))) * 1.4826
        scale = mad if mad > 0 else float(values.std(ddof=0))
        if scale == 0:
            return []
        zscores = np.abs((values - median) / scale)
        return [row.candidate_id for row, score in zip(rows, zscores) if score > z]

    def summary(self) -> dict[str, Any]:
        fidelities: dict[str, int] = {}
        sources: dict[str, int] = {}
        targets: dict[str, int] = {}
        for sample in self._samples.values():
            fidelities[sample.fidelity] = fidelities.get(sample.fidelity, 0) + 1
            sources[sample.source] = sources.get(sample.source, 0) + 1
            for name in sample.targets:
                targets[name] = targets.get(name, 0) + 1
        return {"samples": len(self._samples), "fidelity": fidelities,
                "sources": sources, "targets": targets}

