"""Per-case preferences: parameter bounds and what the engineer cares about.

Two things a person wants to change without editing code: how wide the search may go, and which
trade-off matters. Both are stored per case in one JSON file under the user's config directory, so
they survive restarts and do not live inside a campaign (a campaign is evidence; a preference is
how you like to work).

Validation is deliberately strict, because a bad bound silently produces a bad optimisation:

* ``low < high``;
* a bound may narrow the case's own limits but never widen them - the case's limits come from the
  physics and the manufacturing rules, and a preference is not allowed to overrule them.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

__all__ = ["Preferences", "default_path", "load", "save", "apply_bounds", "validate_bounds"]


def default_path() -> Path:
    override = os.environ.get("ASTER_PREFERENCES")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if appdata:
        return Path(appdata) / "aster" / "preferences.json"
    return Path.home() / ".config" / "aster" / "preferences.json"


@dataclass
class Preferences:
    bounds: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    """``{case: {parameter: [low, high]}}`` - narrower than the case allows."""

    reference_point: dict[str, list[float]] = field(default_factory=dict)
    """``{case: [bandwidth_ref, gain_ref]}`` - what counts as worthless for the hypervolume."""

    weights: dict[str, list[float]] = field(default_factory=dict)
    """``{case: [w_bandwidth, w_gain]}`` - used by the scalarising backend (believer)."""

    def to_dict(self) -> dict[str, Any]:
        return {"bounds": self.bounds, "reference_point": self.reference_point,
                "weights": self.weights}


def load(path: str | Path | None = None) -> Preferences:
    target = Path(path) if path else default_path()
    if not target.is_file():
        return Preferences()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Preferences()
    return Preferences(
        bounds={str(case): {str(name): [float(pair[0]), float(pair[1])]
                            for name, pair in (values or {}).items()}
                for case, values in (payload.get("bounds") or {}).items()},
        reference_point={str(case): [float(value) for value in values]
                         for case, values in (payload.get("reference_point") or {}).items()},
        weights={str(case): [float(value) for value in values]
                 for case, values in (payload.get("weights") or {}).items()},
    )


def save(preferences: Preferences, path: str | Path | None = None) -> Path:
    target = Path(path) if path else default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(preferences.to_dict(), indent=2, ensure_ascii=False),
                      encoding="utf-8")
    return target


def validate_bounds(name: str, bounds: Sequence[float], limits: Sequence[float]) -> list[float]:
    """Return ``[low, high]`` or raise ``ValueError`` explaining why it is not acceptable."""
    if len(bounds) != 2:
        raise ValueError("%s: bounds must be [low, high]" % name)
    low, high = float(bounds[0]), float(bounds[1])
    limit_low, limit_high = float(limits[0]), float(limits[1])
    if not low < high:
        raise ValueError("%s: low (%.4g) must be below high (%.4g)" % (name, low, high))
    if low < limit_low - 1e-9 or high > limit_high + 1e-9:
        raise ValueError("%s: [%.4g, %.4g] is outside the case limits [%.4g, %.4g]"
                         % (name, low, high, limit_low, limit_high))
    return [round(low, 6), round(high, 6)]


def apply_bounds(case: str, params: Sequence[dict[str, Any]],
                 preferences: Preferences) -> list[dict[str, Any]]:
    """Return the parameter list with the case's bounds replaced by the preferred ones."""
    overrides = preferences.bounds.get(case) or {}
    if not overrides:
        return [dict(item) for item in params]
    result: list[dict[str, Any]] = []
    for item in params:
        copy = dict(item)
        pair = overrides.get(str(copy.get("name")))
        if pair:
            try:
                copy["low"], copy["high"] = validate_bounds(str(copy["name"]), pair,
                                                            [copy["low"], copy["high"]])
            except ValueError:
                pass  # a stored value that no longer fits is ignored rather than breaking the page
        result.append(copy)
    return result
