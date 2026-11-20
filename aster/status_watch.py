"""Notice a solve that is not progressing, from the guard's own status.

The CPU sampler catches a machine that has gone quiet, but it cannot tell "the solver is working"
from "something else on this machine is working": the Windows environment this runs in does not let
one process enumerate another reliably, so the sampler watches whole-machine CPU and has false
negatives exactly when the box is busy.

The guard's run status is a better signal for the specific failure we hit twice: a run submitted and
never advancing past CREATED/PREPARED. This module polls that status and reports a stall when it has
not moved for a while, which is deterministic and cheap.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["StatusWatch", "ACTIVE_STATUSES"]

ACTIVE_STATUSES = ("CREATED", "QUEUED", "PREPARED", "SOLVING", "EXPORTING")
"""Statuses a healthy run passes through; anything else is terminal."""

EARLY_STATUSES = ("CREATED", "QUEUED", "PREPARED")
"""Statuses that must advance quickly.

``SOLVING`` deliberately is not one of them: a real solve stays there for twenty-five minutes, so a
"no change" window would kill healthy work. Long solves are covered by the CPU window instead.
"""

EARLY_STAGES = ("CONNECTING", "MODEL")
"""Stages of this application where no progress for minutes means trouble."""


@dataclass
class StatusWatch:
    """Poll a run's status and flag one that has stopped moving.

    ``active_statuses`` decides when a lack of change is suspicious at all: the default is the early
    set, because a run sitting in SOLVING is normal and is watched by the CPU signal instead.
    """

    status_provider: Callable[[], str]
    stale_after_s: float = 600.0
    clock: Callable[[], float] = time.monotonic
    active_statuses: tuple[str, ...] = EARLY_STATUSES
    history: list[tuple[float, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._last_status = ""
        self._last_change = self.clock()

    def sample(self) -> bool:
        status = str(self.status_provider() or "").upper()
        now = self.clock()
        if status != self._last_status:
            self._last_status = status
            self._last_change = now
            self.history.append((now, status))
            return False
        if status not in self.active_statuses:
            return False
        return (now - self._last_change) >= self.stale_after_s

    @property
    def status(self) -> str:
        return self._last_status


class StatusMonitor:
    """Run a :class:`StatusWatch` on a thread for the lifetime of one solve."""

    def __init__(self, watch: StatusWatch, interval_s: float = 30.0,
                 on_stall: Callable[[str], None] | None = None, spawn: bool = True):
        self.watch = watch
        self.interval_s = interval_s
        self.on_stall = on_stall
        self.spawn = spawn
        self.stalled = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "StatusMonitor":
        if self.spawn:
            self._thread = threading.Thread(target=self._loop, name="status-monitor",
                                            daemon=True)
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
                self.on_stall(self.watch.status)
            except Exception:  # noqa: BLE001 - a callback must never break the monitor
                pass
        return self.stalled

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def guard_status_reader(bridge: Any, campaign: str, run_id: str) -> Callable[[], str]:
    """A status provider backed by the guard's status call, tolerant of errors."""

    def read() -> str:
        try:
            payload = bridge.status(str(campaign), run_id) or {}
        except Exception:  # noqa: BLE001 - a missing status is not a stall signal
            return ""
        return str(payload.get("status") or "")

    return read
