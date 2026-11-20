"""Separate the four signals a long solve needs: heartbeat, progress, resources, budget.

Astra's review was blunt about mixing them: a heartbeat only proves the supervisor is alive; log
growth or CPU growth is not proof of numerical convergence; low CPU does not prove a deadlock; and a
wall-clock budget expiring proves only that time ran out. So this module keeps them apart and lets the
caller decide, with one rule enforced here - ``classify`` never returns a verdict strong enough to kill
a run on its own.

The resource sampler uses the known pid rather than enumerating the machine, because process
enumeration is unreliable in this environment (psutil sees none of the AEDT/HF3D processes there).
An unreadable pid is reported as ``None`` (unknown), never as zero activity.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ResourceSample", "Telemetry", "sample_process_tree", "classify", "PHASES"]

PHASES = ("connect", "prepare", "solve", "export", "recovery")


@dataclass
class ResourceSample:
    at: float
    pid: int
    cpu_seconds: float | None
    memory_mb: float | None
    children: int

    def to_dict(self) -> dict[str, Any]:
        return {"at": round(self.at, 2), "pid": self.pid,
                "cpu_seconds": round(self.cpu_seconds, 3) if self.cpu_seconds is not None else None,
                "memory_mb": round(self.memory_mb, 1) if self.memory_mb is not None else None,
                "children": self.children}


def sample_process_tree(pid: int) -> ResourceSample | None:
    """CPU/memory of one process tree, or None when it cannot be read (never a fake zero)."""
    try:
        import psutil  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - no psutil means no resource signal
        return None
    try:
        process = psutil.Process(int(pid))
        cpu = 0.0
        memory = 0.0
        children = process.children(recursive=True)
        for item in [process] + list(children):
            try:
                times = item.cpu_times()
                cpu += float(times.user) + float(times.system)
                memory += float(item.memory_info().rss) / (1024.0 * 1024.0)
            except Exception:  # noqa: BLE001 - one unreadable child must not hide the tree
                continue
        return ResourceSample(at=time.time(), pid=int(pid), cpu_seconds=cpu,
                              memory_mb=memory, children=len(children))
    except Exception:  # noqa: BLE001 - unknown, not zero
        return None


@dataclass
class Telemetry:
    """Everything a run knows about its own liveness, kept apart by meaning."""

    heartbeat_utc: str = ""
    solve_started: float = 0.0
    resources: list[ResourceSample] = field(default_factory=list)
    progress: dict[str, Any] = field(default_factory=dict)
    budgets: dict[str, float] = field(default_factory=dict)

    def touch(self) -> None:
        self.heartbeat_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def add_resource(self, sample: ResourceSample | None) -> None:
        if sample is None:
            return
        self.resources.append(sample)
        if len(self.resources) > 240:
            del self.resources[:-240]

    def add_budget(self, phase: str, seconds: float) -> None:
        if phase in PHASES and seconds > 0:
            self.budgets[phase] = round(self.budgets.get(phase, 0.0) + float(seconds), 2)

    def to_dict(self) -> dict[str, Any]:
        return {"heartbeat_utc": self.heartbeat_utc,
                "progress": dict(self.progress),
                "budgets": dict(self.budgets),
                "resources": [sample.to_dict() for sample in self.resources[-12:]],
                "resource_samples": len(self.resources)}


def classify(telemetry: Telemetry) -> dict[str, Any]:
    """Describe liveness from the separate signals - never a kill verdict.

    * ``no_signal``: nothing to judge (no heartbeat or no resource samples);
    * ``progressing``: CPU is growing across the window;
    * ``suspected_slow``: the window is flat but the run keeps heartbeating - "slow or hung", not
      "hung". Ending it is the wall budget's job, and the reason recorded there says "time", not
      "deadlock".
    """
    samples = [sample for sample in telemetry.resources if sample.cpu_seconds is not None]
    if not telemetry.heartbeat_utc or len(samples) < 2:
        return {"state": "no_signal", "reason": "not enough samples to judge",
                "cpu_delta": None, "window_s": 0.0}
    first, last = samples[0], samples[-1]
    delta = float(last.cpu_seconds or 0.0) - float(first.cpu_seconds or 0.0)
    window = max(0.0, float(last.at - first.at))
    if delta > 0.5:
        return {"state": "progressing", "reason": "cpu grew inside the window",
                "cpu_delta": round(delta, 2), "window_s": round(window, 1)}
    return {"state": "suspected_slow",
            "reason": "cpu flat while the supervisor kept heartbeating; slow or hung, not proven",
            "cpu_delta": round(delta, 2), "window_s": round(window, 1)}
