"""Touchstone reading helpers (S-parameter curves for the GUI and reports).

The export format differs between AEDT versions and settings (``MA``, ``DB`` or
``RI``), and a sparse export can silently shorten a curve, so both the parser and a
row-count check live here and are unit tested.
"""
from __future__ import annotations

import math
from pathlib import Path

FORMATS = {"MA", "DB", "RI"}
FREQUENCY_TO_GHZ = {"HZ": 1e-9, "KHZ": 1e-6, "MHZ": 1e-3, "GHZ": 1.0}


def read_touchstone(path: str | Path) -> list[tuple[float, float]]:
    """Return ``[(freq_ghz, s11_db), ...]`` sorted by frequency.

    Only data rows with at least three columns are used; comment and option lines
    are skipped. A row that cannot be parsed is ignored rather than aborting the
    whole curve, but the caller can compare ``count_rows`` with the expected count.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return _parse_touchstone(text)


def _parse_touchstone(text: str) -> list[tuple[float, float]]:
    """Parse a single captured file image; callers may validate that same image."""
    freqs: list[float] = []
    values: list[float] = []
    fmt = "MA"
    frequency_scale = 1.0  # Touchstone option defaults: GHz S MA R 50.
    for line in text.splitlines():
        stripped = line.split("!", 1)[0].strip()
        if not stripped or stripped.startswith("!"):
            continue
        if stripped.startswith("#"):
            options = stripped[1:].upper().split()
            fmt = next((token for token in options if token in FORMATS), "MA")
            frequency_scale = next((FREQUENCY_TO_GHZ[token] for token in options
                                    if token in FREQUENCY_TO_GHZ), 1.0)
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        try:
            freq = float(parts[0]) * frequency_scale
            first, second = float(parts[1]), float(parts[2])
        except ValueError:
            continue
        if not all(math.isfinite(value) for value in (freq, first, second)) or freq < 0:
            continue
        if fmt == "DB":
            value = first
        elif fmt == "RI":
            value = 20.0 * math.log10(max(math.hypot(first, second), 1e-12))
        else:  # MA
            value = 20.0 * math.log10(max(abs(first), 1e-12))
        freqs.append(freq)
        values.append(value)
    order = sorted(range(len(freqs)), key=lambda index: freqs[index])
    return [(freqs[i], values[i]) for i in order]


def count_rows(path: str | Path) -> int:
    """Number of data rows (used to detect sparse sweeps)."""
    return len(read_touchstone(path))


def contiguous_bandwidth(points: list[tuple[float, float]], target_db: float = -10.0,
                         band: tuple[float, float] | None = None, *,
                         max_gap_ghz: float = 1.5) -> dict[str, float | None]:
    """Largest contiguous span where ``s11 <= target_db``, in GHz.

    Failing/nonfinite samples break a band. Duplicate frequencies use the worst
    sample. The default gap limit preserves the legacy 1 GHz sweep policy;
    callers with another grid should supply its gap limit explicitly. Missing
    frequencies cannot be inferred from the surviving points alone.
    """
    if not math.isfinite(max_gap_ghz) or max_gap_ghz <= 0:
        raise ValueError("max_gap_ghz must be finite and positive")
    by_frequency: dict[float, float] = {}
    for freq, value in points:
        if not math.isfinite(freq) or freq < 0:
            continue
        if band is not None and not band[0] - 1e-9 <= freq <= band[1] + 1e-9:
            continue
        value = value if math.isfinite(value) else math.inf
        by_frequency[freq] = max(value, by_frequency.get(freq, -math.inf))
    rows = sorted(by_frequency.items())
    best = {"value": 0.0, "low_ghz": None, "high_ghz": None}
    start = previous = None
    for freq, value in rows:
        if value > target_db:
            start = previous = None
            continue
        if start is None or (previous is not None and freq - previous > max_gap_ghz + 1e-9):
            start = freq
        previous = freq
        span = freq - start
        if span > float(best["value"]):
            best = {"value": span, "low_ghz": start, "high_ghz": freq}
    return best
