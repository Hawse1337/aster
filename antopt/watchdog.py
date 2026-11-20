"""Detect a stalled solver instead of waiting out the timeout.

Two different things go wrong on the solver machine:

* **fast failures** hand back an error code (not converged, incomplete export) - they cost
  seconds and are easy to diagnose;
* **stalls** hand back nothing: the HFSS workers stay alive, the status stays SOLVING, and CPU
  barely moves. Waiting for the 1800 s broker timeout wastes half an hour per attempt and the
  log says nothing useful afterwards.

This module samples the solver's consumed CPU time and decides whether real work is happening.
It is deliberately dependency-free from the caller's point of view: pass any sampler, so tests do
not need a running solver and the CLI can feed it psutil.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

__all__ = ["cpu_seconds", "StallDetector", "watch"]


def cpu_seconds(process_names: Sequence[str] = ("hf3d", "ansysedt")) -> float:
    """Busy CPU seconds accumulated by the machine so far.

    Per-process sampling was the obvious choice and did not work: inside this environment
    `psutil.process_iter` cannot see the AEDT/HF3D processes at all (it matches zero of them
    while the solver is running at full tilt), so a per-process sum reports a permanent zero and
    every run looks stalled. The system-wide busy time is readable everywhere and, on a machine
    dedicated to the solver, distinguishes 'the solver is working' from 'nothing is happening':
    a healthy adaptive pass adds hundreds of CPU seconds per minute, a stalled run adds a couple.
    """
    import psutil  # noqa: PLC0415 - optional at import time

    times = psutil.cpu_times()
    return float(times.user) + float(times.system) + float(getattr(times, "nice", 0.0)) \
        + float(getattr(times, "irq", 0.0))


@dataclass
class StallDetector:
    """Decide whether the solver is stalled from two CPU samples.

    ``window_s`` is how much wall time the two samples should be apart; ``min_delta_s`` is the
    minimum CPU-second increase that counts as work. With nine workers on sixteen cores a healthy
    adaptive pass burns tens of CPU seconds per minute, while a stalled run was observed at 1.4 -
    so five seconds is a forgiving floor.
    """

    window_s: float = 60.0
    min_delta_s: float = 5.0
    consecutive: int = 2
    sampler: Callable[[], float] = field(default=cpu_seconds)
    _last_value: float | None = None
    _hit_count: int = 0

    def sample(self) -> float:
        value = float(self.sampler())
        self._last_value = value
        return value

    def stalled(self) -> bool:
        """Take one sample, compare with the previous one, and report a sustained stall."""
        previous = self._last_value
        current = self.sample()
        if previous is None:
            return False
        if current - previous >= self.min_delta_s:
            self._hit_count = 0
            return False
        self._hit_count += 1
        return self._hit_count >= self.consecutive

    def reset(self) -> None:
        self._last_value = None
        self._hit_count = 0


def watch(
    *,
    sampler: Callable[[], float] = cpu_seconds,
    window_s: float = 60.0,
    min_delta_s: float = 5.0,
    consecutive: int = 2,
    max_checks: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_sample: Callable[[float, bool], None] | None = None,
) -> bool:
    """Sample until a stall is detected (True) or ``max_checks`` runs out (False)."""
    detector = StallDetector(window_s=window_s, min_delta_s=min_delta_s,
                             consecutive=consecutive, sampler=sampler)
    checks = 0
    while max_checks is None or checks < max_checks:
        stalled = detector.stalled()
        if on_sample is not None:
            on_sample(detector._last_value or 0.0, stalled)  # noqa: SLF001 - internal sample
        if stalled:
            return True
        checks += 1
        sleep(window_s)
    return False
