"""Radiation-efficiency metric (GPT module A: "maximise radiation efficiency").

Two routes are supported because that is what the solver gives us:

* powers - HFSS reports radiated and accepted power; the ratio is the efficiency directly;
* far-field levels - when only the directivity and the gain are exported, the efficiency is
  the difference between them: ``eta = 10 ** ((gain_db - directivity_db) / 10)``.

The distinction matters: a realized-gain figure already folds in the mismatch loss, so using it
against the directivity yields the *total* efficiency, not the radiation efficiency. The helper
therefore names its inputs explicitly and refuses to guess.
"""
from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "efficiency_from_powers",
    "efficiency_from_db",
    "radiation_efficiency_metric",
    "EFFICIENCY_METRIC",
]

EFFICIENCY_METRIC = "radiation_efficiency"


def efficiency_from_powers(radiated_w: float, accepted_w: float) -> float:
    """eta = P_rad / P_acc, clamped to [0, 1] against numerical noise."""
    radiated = float(radiated_w)
    accepted = float(accepted_w)
    if accepted <= 0.0:
        raise ValueError("accepted power must be positive")
    if radiated < 0.0:
        raise ValueError("radiated power must not be negative")
    return max(0.0, min(1.0, radiated / accepted))


def efficiency_from_db(gain_db: float, directivity_db: float) -> float:
    """eta = 10 ** ((gain_db - directivity_db) / 10), clamped to [0, 1].

    ``gain_db`` must be the *radiated* gain (mismatch excluded). Feeding a realized gain here
    silently measures total efficiency instead, so callers should use
    :func:`radiation_efficiency_metric`, which checks the key names.
    """
    ratio = (float(gain_db) - float(directivity_db)) / 10.0
    return max(0.0, min(1.0, 10.0 ** ratio))


def radiation_efficiency_metric(metrics: Mapping[str, Any]) -> float | None:
    """Extract the efficiency from a metric dict, or ``None`` when it cannot be computed.

    Accepted shapes, in order of preference:

    * ``radiation_efficiency`` already present -> returned as is;
    * ``radiated_power_w`` + ``accepted_power_w`` -> ratio;
    * ``directivity_db`` + ``gain_db`` -> level difference (radiated gain);
    * ``directivity_db`` + ``realized_gain_h``/``realized_gain_db`` + ``s11`` -> the realized
      gain is corrected for the mismatch first, so the result is still the radiation efficiency.
    """
    existing = metrics.get(EFFICIENCY_METRIC)
    if existing is not None:
        return float(existing)

    radiated = metrics.get("radiated_power_w")
    accepted = metrics.get("accepted_power_w")
    if radiated is not None and accepted is not None:
        try:
            return efficiency_from_powers(radiated, accepted)
        except ValueError:
            return None

    directivity = metrics.get("directivity_db")
    if directivity is None:
        return None

    gain = metrics.get("gain_db")
    if gain is not None:
        return efficiency_from_db(gain, directivity)

    realized = metrics.get("realized_gain_h", metrics.get("realized_gain_db"))
    s11 = metrics.get("s11")
    if realized is None or s11 is None:
        return None
    # realized = radiated + 10*log10(1 - |S11|^2)  ->  undo the mismatch before differencing
    reflection = 10.0 ** (float(s11) / 10.0)
    mismatch_db = 10.0 * _log10(max(1e-12, 1.0 - reflection))
    return efficiency_from_db(float(realized) - mismatch_db, directivity)


def _log10(value: float) -> float:
    import math

    return math.log10(value)
