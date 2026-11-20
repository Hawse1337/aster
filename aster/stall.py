"""Notice a solve that has stopped working, instead of waiting out the timeout.

Twice in this project a solve kept ``hf3d`` processes alive with the status pinned on SOLVING and
burned about one CPU-second per minute for forty minutes. Nothing failed, so nothing reported;
the run simply never finished. The detector here samples how much CPU the machine is burning and
declares a stall when a long window passes with almost none.

It is deliberately conservative: a healthy adaptive pass burns tens of CPU-seconds per minute, so
"less than fifteen seconds in three minutes" cannot be normal work. The cost of a false positive is
one killed solve and one retry; the cost of a false negative is the forty minutes.
"""
from __future__ import annotations

import threading
import time
import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

__all__ = ["StallWatch", "system_cpu_seconds", "solver_processes", "kill_solvers", "kill_process_tree"]

DEFAULT_WINDOW_S = 180.0
DEFAULT_MIN_DELTA_S = 15.0


def system_cpu_seconds() -> float | None:
    """Busy CPU seconds accumulated by the machine.

    Per-process sampling was the obvious choice and does not work here: inside this environment
    ``psutil.process_iter`` cannot see the AEDT/HF3D processes at all, so a per-process sum reports
    a permanent zero and every healthy run looks stalled.
    """
    try:
        import psutil  # noqa: PLC0415
        times = psutil.cpu_times()
        return float(times.user) + float(times.system) + float(getattr(times, "nice", 0.0)) \
            + float(getattr(times, "irq", 0.0))
    except Exception:  # noqa: BLE001 - unavailable telemetry is not zero activity
        return None


def solver_processes() -> list[int]:
    """PIDs of AEDT and HF3D, best effort; empty when enumeration is blocked."""
    try:
        import psutil  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    found: list[int] = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            name = (process.info.get("name") or "").lower()
        except Exception:  # noqa: BLE001 - the process vanished
            continue
        if name in {"hf3d", "ansysedt"} or name in {"hf3d.exe", "ansysedt.exe"}:
            found.append(int(process.info["pid"]))
    return found


def kill_solvers() -> list[int]:
    """Terminate the solver processes so the next attempt starts clean; returns what it killed."""
    try:
        import psutil  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    killed: list[int] = []
    for pid in solver_processes():
        try:
            process = psutil.Process(pid)
            process.kill()
            killed.append(pid)
        except Exception:  # noqa: BLE001 - already gone
            continue
    return killed


@dataclass
class StallWatch:
    """Sample the machine's CPU use and answer whether work has stopped."""

    sampler: Callable[[], float | None] = system_cpu_seconds
    window_s: float = DEFAULT_WINDOW_S
    min_delta_s: float = DEFAULT_MIN_DELTA_S
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    samples: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._baseline: float | None = None
        self._baseline_at: float = 0.0

    def sample(self) -> bool:
        """Take one reading; True means the window elapsed with too little CPU used."""
        now = self.clock()
        try:
            raw = self.sampler()
            value = float(raw) if raw is not None else math.nan
        except Exception:  # noqa: BLE001 - monitoring must not abort a solve
            value = math.nan
        if not math.isfinite(value) or value < 0:
            self._baseline = None
            self.samples.clear()
            return False
        if self._baseline is None or (self.samples and value < self.samples[-1]):
            self.samples.clear()
            self._baseline = value
            self._baseline_at = now
            self.samples.append(value)
            return False
        self.samples.append(value)
        if now - self._baseline_at < self.window_s:
            return False
        used = value - self._baseline
        stalled = used < self.min_delta_s
        # either way the window restarts: a stall that ends should be given a fresh window
        self._baseline = value
        self._baseline_at = now
        return stalled


class StallMonitor:
    """Run a :class:`StallWatch` on a thread while a solve is in flight."""

    def __init__(self, watch: StallWatch, interval_s: float = 20.0,
                 on_stall: Callable[[], None] | None = None,
                 spawn: bool = True):
        self.watch = watch
        self.interval_s = interval_s
        self.on_stall = on_stall
        self.spawn = spawn
        self.stalled = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "StallMonitor":
        if not self.spawn:
            return self
        self._thread = threading.Thread(target=self._loop, name="stall-monitor", daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            if self.poll():
                break

    def poll(self) -> bool:
        self.stalled = self.watch.sample()
        if self.stalled and self.on_stall is not None:
            try:
                self.on_stall()
            except Exception:  # noqa: BLE001 - never let the callback break the monitor
                pass
        return self.stalled

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def sample_sequence(values: Sequence[float], watch: StallWatch | None = None) -> StallWatch:
    """Convenience for tests: a watch fed from a fixed list."""
    stream = iter(values)
    target = watch or StallWatch(window_s=0.0, sleep=lambda _s: None, clock=lambda: 0.0)
    target.sampler = lambda: next(stream)
    return target


def kill_process_tree(pid: int) -> bool:
    """Terminate one process and its children by pid.

    The old helper killed AEDT/HF3D by name, which is wrong as soon as a second instance exists or
    the enumeration is blocked: it can miss everything, or kill someone else's session.
    """
    import subprocess

    try:
        result = subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                                capture_output=True, text=True)
        return result.returncode == 0
    except Exception:  # noqa: BLE001 - reported by the caller as "could not confirm"
        return False
