"""Fault injection for the disciplines we claim: exports, locks, licences, worker death.

Each scenario is a small deterministic experiment against real code paths (a fake or real component
where the fault belongs) that records what happened and whether the invariant held. The invariant
lines are the point: a short export must never become a bandwidth, a repeated frequency must break
the band, a locked database must not lose a finished run, a licence refusal must be classified as
environment with an actionable hint rather than as physics.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .failures import classify
from .runtime.store import JobStore
from .touchstone import contiguous_bandwidth, read_touchstone

SHORT_FILE = """# Hz S dB R 50.0
! one row is not a sweep
28000000000 -12.0 0.0
"""

DUPLICATE_ROWS = """# GHz S dB R 50.0
24.0 -12.0 0.0
25.0 -12.0 0.0
26.0 -4.0 0.0
26.0 -12.0 0.0
27.0 -12.0 0.0
28.0 -12.0 0.0
"""


def _write(tmp: Path, name: str, text: str) -> Path:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return path


def scenario_short_export(tmp: Path) -> dict[str, Any]:
    path = _write(tmp, "short.s1p", SHORT_FILE)
    points = read_touchstone(path)
    band = contiguous_bandwidth(points)
    return {"name": "short_export", "rows": len(points),
            "value_ghz": band.get("value"),
            "invariant": "a one-row export must not produce a nonzero bandwidth",
            "held": len(points) <= 1 and not float(band.get("value") or 0.0)}


def scenario_duplicate_frequency(tmp: Path) -> dict[str, Any]:
    path = _write(tmp, "dupes.s1p", DUPLICATE_ROWS)
    points = read_touchstone(path)
    band = contiguous_bandwidth(points)
    # the 26 GHz failure must split the sweep: the widest contiguous span is 1 GHz
    return {"name": "duplicate_frequency", "rows": len(points),
            "value_ghz": band.get("value"),
            "invariant": "a failing point breaks the band; duplicates take the worst value",
            "held": float(band.get("value") or 0.0) <= 1.0 + 1e-9}


def scenario_mhz_units(tmp: Path) -> dict[str, Any]:
    """An MHz file must be read as GHz, not as a 1000x frequency axis."""
    path = _write(tmp, "mhz.s1p", "# MHz S dB R 50\n24000 -12 0\n25000 -12 0\n26000 -12 0\n")
    points = read_touchstone(path)
    lowest = min(freq for freq, _value in points) if points else None
    return {"name": "mhz_units", "lowest_ghz": lowest,
            "invariant": "an MHz file yields GHz frequencies (24, not 24000)",
            "held": lowest is not None and abs(lowest - 24.0) < 1e-9}


def scenario_sqlite_lock(tmp: Path, hold_seconds: float = 2.0) -> dict[str, Any]:
    store = JobStore(tmp / "queue.sqlite")
    store.save_job({"run_id": "before", "status": "RUNNING", "params": {}, "metrics": {}})
    holder = sqlite3.connect(store.path, timeout=0.1, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("UPDATE jobs SET status = 'RUNNING' WHERE run_id = 'before'")
    finished: dict[str, Any] = {}

    def write() -> None:
        try:
            store.save_job({"run_id": "after", "status": "COMPLETED", "params": {},
                            "metrics": {"peak_gain": 6.0}})
            finished["ok"] = True
        except Exception as exc:  # noqa: BLE001 - recorded, the scenario is about behaviour
            finished["ok"] = False
            finished["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])

    started = time.monotonic()
    thread = threading.Thread(target=write)
    thread.start()
    time.sleep(hold_seconds)
    holder.execute("ROLLBACK")
    holder.close()
    thread.join(timeout=20)
    rows = {row["run_id"]: row["status"] for row in store.jobs()}
    return {"name": "sqlite_lock", "held_seconds": hold_seconds,
            "waited_seconds": round(time.monotonic() - started, 2),
            "recorded": rows.get("after"),
            "invariant": "a finished run is recorded once the lock clears, not lost",
            "held": finished.get("ok") is True and rows.get("after") == "COMPLETED"}


def scenario_licence_refusal(_tmp: Path | None = None) -> dict[str, Any]:
    failure = classify("LICENSE_ERROR", "Failed to check out license hfss_solve")
    text = "%s %s" % (failure.title, failure.action)
    return {"name": "licence_refusal", "title": failure.title,
            "retryable": failure.retryable,
            "invariant": "a licence problem is an environment failure with an actionable hint",
            "held": ("许可" in text or "license" in text.lower()) and bool(failure.action)}


def scenario_worker_death(tmp: Path) -> dict[str, Any]:
    """A worker that claims a task and dies must not lose it, and must not double count it."""
    from .distributed import DistributedScheduler

    scheduler = DistributedScheduler(tmp / "dist.sqlite")
    scheduler.enqueue("lens28_var", [{"ring_outer": 2.3}])
    first = scheduler.claim("worker-that-dies", lease_seconds=0.05)
    time.sleep(0.1)
    second = scheduler.claim("worker-alive", lease_seconds=30)
    outcome = scheduler.finish(second.task_id, "worker-alive",
                               result={"status": "COMPLETED", "peak_gain": 6.1})
    snapshot = scheduler.snapshot()
    return {"name": "worker_death",
            "attempts": second.attempts if second else None,
            "state": snapshot["by_state"],
            "invariant": "the task survives the death, is attempted twice, recorded once",
            "held": (second is not None and second.attempts == 2
                     and snapshot["by_state"].get("DONE") == 1 and outcome.get("ok"))}


SCENARIOS: tuple[Callable[..., dict[str, Any]], ...] = (
    scenario_short_export,
    scenario_duplicate_frequency,
    scenario_mhz_units,
    scenario_sqlite_lock,
    scenario_licence_refusal,
    scenario_worker_death,
)


def run_all() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for scenario in SCENARIOS:
            try:
                results.append(scenario(root))
            except Exception as exc:  # noqa: BLE001 - a crashing scenario is a failed invariant
                results.append({"name": scenario.__name__, "held": False,
                                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
    held = sum(1 for item in results if item.get("held"))
    return {"scenarios": results, "total": len(results), "held": held,
            "all_held": held == len(results)}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="offline fault injection")
    parser.add_argument("--out", default="docs/evidence/fault_injection.json")
    args = parser.parse_args()
    report = run_all()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_held"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
