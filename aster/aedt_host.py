"""Standalone AEDT process helpers used by the measured-run worker.

Extracted from the application entry point so the core library does not depend on the GUI
launcher (which is not part of this release).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def running_aedt() -> list[int]:
    """PIDs of AEDT processes that expose a gRPC server."""
    try:
        import psutil  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    found: list[int] = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (process.info.get("name") or "").lower()
            if name != "ansysedt.exe":
                continue
            if "-grpcsrv" in (process.info.get("cmdline") or []):
                found.append(int(process.info["pid"]))
        except Exception:  # noqa: BLE001 - the process vanished
            continue
    return found


def start_aedt(aedt: Path, port: int, wait_s: float = 60.0,
               launch: Callable[[list[str]], Any] | None = None,
               probe: Callable[[], list[int]] | None = None,
               non_graphical: bool = False) -> bool:
    """Launch AEDT with a gRPC port and wait until it is usable.

    The packaged application should look after this: asking a user to start AEDT by hand before
    opening the GUI is exactly the kind of step that turns "it does not work" into a support
    question.
    """
    import subprocess
    import time

    if not Path(aedt).is_file():
        return False
    # -ng keeps AEDT out of a desktop window: fewer GUI-related interruptions on long runs,
    # at the cost of not being able to look at the model while it solves
    command = [str(aedt)] + (["-ng"] if non_graphical else []) + ["-grpcsrv", str(port)]
    try:
        if launch is not None:
            launch(command)
        else:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001 - reported to the caller as "no AEDT"
        return False
    seen = probe or running_aedt
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if seen():
            return True
        time.sleep(2.0)
    return False


def aedt_reachable(pid: int, timeout_s: float = 20.0) -> bool:
    """Can the guard actually drive this AEDT?

    A leftover AEDT can be running with a gRPC port that the guard refuses to attach to ("cannot
    verify existing AEDT PID"), and every job then fails before doing any work. Connecting and
    running a trivial snippet is the cheapest way to find out.
    """
    try:
        import threading

        from .bridge import InProcessBridge
    except Exception:  # noqa: BLE001
        return False
    outcome: dict[str, bool] = {}

    def probe() -> None:
        try:
            bridge = InProcessBridge()
            bridge.connect(pid=int(pid))
            bridge.execute_code("result = 1\n", pid=int(pid))
            outcome["ok"] = True
        except Exception:  # noqa: BLE001 - unreachable is a normal outcome here
            outcome["ok"] = False

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(timeout_s)
    return bool(outcome.get("ok"))
