"""Open a saved project's native reports in one explicitly selected AEDT GUI.

The companion launcher uses this module rather than COM's first running AEDT.
It reads saved results only and never starts a solver or closes other projects.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any

from . import config
from .bridge import InProcessBridge


def _native_reports(project_path, design_name, report_names, release_only=False):
    # Serialized into the explicitly addressed guard broker.
    from pathlib import Path

    expected = Path(project_path).resolve()
    project = None
    for name in odesktop.GetProjectList():
        if str(name).casefold() != expected.stem.casefold():
            continue
        candidate = odesktop.SetActiveProject(name)
        actual = (Path(candidate.GetPath()) / (str(candidate.GetName()) + ".aedt")).resolve()
        if str(actual).casefold() == str(expected).casefold():
            project = candidate
        elif not release_only:
            raise ValueError("A different project with the same name is open: " + str(actual))
    if release_only:
        if project is not None:
            try:
                busy = odesktop.AreThereSimulationsRunning()
            except Exception as exc:
                raise RuntimeError("Cannot verify whether AEDT is busy; the project was left open") from exc
            if busy:
                raise RuntimeError("AEDT is running a simulation; wait until it finishes before opening reports in the GUI")
            name = str(project.GetName())
            project.Save()
            odesktop.CloseProject(name)
            return {"released": True, "project": str(expected)}
        return {"released": False, "project": str(expected)}
    if project is None:
        project = odesktop.OpenProject(str(expected))
        if not project:
            raise RuntimeError("AEDT did not open the saved project")
        project = odesktop.SetActiveProject(project.GetName())
    actual = (Path(project.GetPath()) / (str(project.GetName()) + ".aedt")).resolve()
    if str(actual).casefold() != str(expected).casefold():
        raise ValueError("AEDT opened a different project: " + str(actual))
    design = project.SetActiveDesign(design_name)
    if not design:
        raise ValueError("Saved design was not found: " + design_name)
    reporter = design.GetModule("ReportSetup")
    available = list(reporter.GetAllReportNames())
    missing = [name for name in report_names if name not in available]
    if missing:
        raise ValueError("Saved reports were not found: " + ", ".join(missing))
    reporter.OpenWindowForReports(report_names)
    odesktop.RestoreWindow()
    return {"opened": list(report_names), "project": str(expected), "design": design_name}


def report_code(project: Path, design: str, reports: list[str], *, release_only: bool = False) -> str:
    return inspect.getsource(_native_reports) + "\nresult = _native_reports(%r, %r, %r, %r)\n" % (
        str(project), design, reports, release_only)


def _process(pid: int | None):
    """Return only the manifest's original AEDT process, never a replacement PID."""
    if not pid:
        return None
    import psutil

    try:
        process = psutil.Process(int(pid))
        if process.name().casefold() != "ansysedt.exe" or not process.is_running():
            return None
        # Reading cmdline is also a permission check; fail closed if it cannot be read.
        process.cmdline()
        return process
    except psutil.NoSuchProcess:
        return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _result(response: Any) -> dict[str, Any]:
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, dict):
        raise RuntimeError("Guard returned no report-opening result")
    return result


def _wait_ready(bridge: Any, pid: int, *, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "AEDT is starting"
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            response = bridge.execute_code(
                "result = {'ready': bool(odesktop.GetVersion())}\n",
                pid=pid, timeout=min(12.0, remaining))
            if _result(response).get("ready"):
                return
            last_error = "AEDT returned no readiness confirmation"
        except Exception as exc:
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.5, remaining))
    raise TimeoutError("AEDT GUI did not become ready within %.0fs: %s" % (timeout, last_error))


def open_saved_reports(manifest: dict[str, Any], *, environment=None,
                       bridge=None, readiness_timeout: float = 45.0) -> dict[str, Any]:
    """Select the recorded GUI, or launch a separate GUI after releasing its NG copy."""
    raw_project = manifest.get("project")
    if not isinstance(raw_project, str) or not Path(raw_project).is_absolute():
        raise ValueError("Manifest project must be an absolute path")
    project = Path(raw_project).resolve()
    if not project.is_file() or project.suffix.casefold() != ".aedt":
        raise ValueError("Saved AEDT project does not exist: " + str(project))
    expected_hash = manifest.get("project_sha256")
    if expected_hash is not None:
        if (not isinstance(expected_hash, str) or len(expected_hash) != 64
                or any(char not in "0123456789abcdefABCDEF" for char in expected_hash)):
            raise ValueError("Manifest project_sha256 must contain 64 hexadecimal characters")
        digest = hashlib.sha256()
        with project.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_hash.casefold():
            raise ValueError("Saved project has changed since these reports were captured; open the latest run's report launcher")
    design = manifest.get("design")
    reports = manifest.get("reports")
    if not isinstance(design, str) or not design.strip():
        raise ValueError("Manifest design must be a nonempty string")
    if (not isinstance(reports, list) or not reports
            or any(not isinstance(name, str) or not name for name in reports)):
        raise ValueError("Manifest reports must be a nonempty list of names")
    pid_value = manifest.get("pid")
    if pid_value is not None and (isinstance(pid_value, bool) or not isinstance(pid_value, int) or pid_value <= 0):
        raise ValueError("Manifest pid must be a positive integer or null")
    env = environment or config.load()
    broker = bridge or InProcessBridge(repo_path=str(env.guard_repo) if env.guard_repo else None)
    process = _process(pid_value)
    pid = int(pid_value) if process is not None else None
    if process is not None and "-ng" in [str(arg).casefold() for arg in process.cmdline()]:
        released = _result(broker.execute_code(
            report_code(project, design, reports, release_only=True), pid=pid, timeout=30.0))
        if "released" not in released:
            raise RuntimeError("Guard did not confirm release of the non-graphical project")
        pid = None
    if pid is None:
        executable = Path(env.aedt_exe) if env.aedt_exe else None
        if executable is None or not executable.is_file():
            raise ValueError("AEDT executable not found; configure aedt_exe")
        # The new process and its port are both unique; never use port 0 or first-session discovery.
        port = _free_port()
        launched = subprocess.Popen(
            [str(executable), "-grpcsrv", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid = int(launched.pid)
        _wait_ready(broker, pid, timeout=readiness_timeout)
    response = _result(broker.execute_code(
        report_code(project, design, reports), pid=pid, timeout=30.0))
    if response.get("opened") != reports:
        raise RuntimeError("Guard did not confirm all requested report windows")
    return {**response, "pid": pid}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--design")
    parser.add_argument("--reports-json", type=Path)
    parser.add_argument("--pid", type=int)
    args = parser.parse_args(argv)
    try:
        if args.manifest is not None:
            manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        else:
            if args.project is None or args.design is None or args.reports_json is None:
                parser.error("use --manifest, or --project --design --reports-json")
            manifest = {"project": str(args.project), "design": args.design,
                        "reports": json.loads(args.reports_json.read_text(encoding="utf-8-sig")),
                        "pid": args.pid}
        if not isinstance(manifest, dict):
            raise ValueError("Report manifest must be a JSON object")
        result = open_saved_reports(manifest)
    except Exception as exc:
        print("Could not open AEDT reports: " + str(exc), file=sys.stderr)
        return 1
    if args.manifest is not None:
        temporary = args.manifest.with_name(args.manifest.name + ".%d.tmp" % os.getpid())
        try:
            if json.loads(args.manifest.read_text(encoding="utf-8-sig")) == manifest:
                updated = {**manifest, "pid": result["pid"]}
                temporary.write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")
                temporary.replace(args.manifest)
        except (OSError, ValueError) as exc:
            print("Reports opened, but could not remember the GUI session: " + str(exc), file=sys.stderr)
        finally:
            temporary.unlink(missing_ok=True)
    print("Opened %d saved reports in AEDT GUI (PID %d)." % (len(result["opened"]), result["pid"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
