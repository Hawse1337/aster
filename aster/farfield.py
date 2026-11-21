"""Measured far-field snapshots, independent of optimisation KPI cuts.

The guarded broker reads existing solutions only. HTTP requests read snapshots;
they never query whatever design happens to be active in AEDT later.
"""
from __future__ import annotations

import csv
import inspect
import json
import math
from pathlib import Path
import re
from typing import Any

QUANTITY = "dB(GainTotal)"
SNAPSHOT = "farfield_views.json"


def _broker_read(project_path, design_name, setup_name):
    # This function is serialized into the guard broker, not run by the GUI.
    import math
    from pathlib import Path
    from ansys.aedt.core.visualization.post.solution_data import SolutionData

    expected = Path(project_path).resolve()
    if expected.stem not in list(odesktop.GetProjectList()):
        raise ValueError("Solved project is not open in the selected AEDT")
    project = odesktop.SetActiveProject(expected.stem)
    if Path(project.GetPath()).resolve() != expected.parent:
        raise ValueError("Open project path does not match this run")
    design = project.SetActiveDesign(design_name)
    radiation = design.GetChildObject("Radiation")
    spheres = [radiation.GetChildObject(name) for name in radiation.GetChildNames()
               if name != "ASTER_PrincipalCuts"
               and radiation.GetChildObject(name).GetPropValue("Type") == "Infinite Sphere"]
    if not spheres:
        raise ValueError("No Infinite Sphere exists in this design")
    if len(spheres) > 1:
        raise ValueError("Multiple Infinite Spheres: an explicit selection is required")
    sphere = spheres[0]
    sphere_name = str(sphere.GetPropValue("Name"))
    if sphere.GetPropValue("CS Definition") != "Theta-Phi":
        raise ValueError("This sphere does not use Theta-Phi coordinates")
    solution = setup_name + " : LastAdaptive"
    families = ["Theta:=", ["All"], "Phi:=", ["All"], "Freq:=", ["All"]]
    for owner in (project, design):
        for name in owner.GetVariables():
            families.extend([str(name) + ":=", [str(owner.GetVariableValue(name))]])
    # This is the same read-only native API used by PyAEDT get_solution_data.
    # Reuse the broker's desktop, avoiding another Hfss constructor/port-0 session.
    native = list(design.GetModule("ReportSetup").GetSolutionDataPerVariation(
        "Far Fields", solution, ["Context:=", sphere_name], families, ["dB(GainTotal)"]))
    if not native:
        raise ValueError("No saved far-field data for " + solution)
    data = SolutionData(native)
    names = list(data.variations[0]) + list(data.intrinsics)
    indexes = [names.index(name) for name in ("Freq", "Theta", "Phi")]
    units = data.units_sweeps
    scale = {"hz": 1e-9, "khz": 1e-6, "mhz": 1e-3, "ghz": 1.0}
    freq_scale = scale[str(units["Freq"]).lower()]
    angle_scales = []
    for key in ("Theta", "Phi"):
        unit = str(units[key]).lower()
        if unit not in ("deg", "rad"):
            raise ValueError("Unknown angle unit: " + unit)
        angle_scales.append(180 / math.pi if unit == "rad" else 1.0)
    matrix = data.full_matrix_real_imag[0]["dB(GainTotal)"]
    rows = []
    for row in matrix:
        if len(row) != len(names) + 1:
            raise ValueError("Unexpected PyAEDT solution matrix layout")
        values = [float(row[indexes[0]]) * freq_scale,
                  float(row[indexes[1]]) * angle_scales[0],
                  float(row[indexes[2]]) * angle_scales[1], float(row[-1])]
        if all(math.isfinite(value) for value in values):
            rows.append(values)
    if not rows:
        raise ValueError("Far-field solution contains no finite samples")
    return {"quantity": "dB(GainTotal)", "source": "MEASURED", "solution": solution,
            "sphere_name": sphere_name, "coordinate_system": str(sphere.GetPropValue("Coordinate System")),
            "angle_definition": "Theta-Phi", "samples": rows}


def _broker_reports(project_path, design_name, snapshot, output_dir, keep_reports=False,
                    open_window=True):
    """Native arrays match PyAEDT's report and image APIs, using the guard desktop.

    keep_reports=True leaves the four reports in the project (they are saved) instead of deleting
    them after the PNG export. The default keeps the old behaviour, which avoids accumulating plots
    that make later PyAEDT postprocessors emit 'Solution is not a property name' errors.
    """
    import math
    from pathlib import Path

    project = odesktop.SetActiveProject(Path(project_path).stem)
    design = project.SetActiveDesign(design_name)
    reporter = design.GetModule("ReportSetup")
    radiation = design.GetChildObject("Radiation")
    original = radiation.GetChildObject(snapshot["sphere_name"])
    coord = str(original.GetPropValue("Coordinate System"))
    cuts_sphere = "ASTER_PrincipalCuts"
    sphere_args = ["NAME:" + cuts_sphere, "UseCustomRadiationSurface:=", False,
                   "CustomRadiationSurface:=", "", "CSDefinition:=", "Theta-Phi",
                   "Polarization:=", "Linear", "SlantAngle:=", "45deg",
                   "ThetaStart:=", "-180deg", "ThetaStop:=", "180deg", "ThetaStep:=", "2deg",
                   "PhiStart:=", "0deg", "PhiStop:=", "360deg", "PhiStep:=", "5deg",
                   "UseLocalCS:=", coord != "Global", "CoordSystem:=", "" if coord == "Global" else coord]
    radfield = design.GetModule("RadField")
    if cuts_sphere not in list(radiation.GetChildNames()):
        radfield.InsertInfiniteSphereSetup(sphere_args)
    else:
        radfield.EditInfiniteSphereSetup(cuts_sphere, sphere_args)
    variables = []
    for owner in (project, design):
        for name in owner.GetVariables():
            variables.extend([str(name) + ":=", [str(owner.GetVariableValue(name))]])
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_sets = []
    for freq in sorted({r[0] for r in snapshot["samples"]}):
        key = format(freq, ".12g").replace(".", "p")
        names = ["ASTER_3D_Gain_" + key + "GHz", "ASTER_E_Plane_" + key + "GHz",
                 "ASTER_H_Plane_" + key + "GHz", "ASTER_EH_Overlay_" + key + "GHz"]
        # Refresh only these owned reports; leave every user report untouched.
        existing = [name for name in names if name in list(reporter.GetAllReportNames())]
        if existing:
            reporter.DeleteReports(existing)
        for index, name in enumerate(names):
            families = (["Phi:=", ["All"], "Theta:=", ["90deg"]] if index == 2 else
                        ["Theta:=", ["All"], "Phi:=", ["All"] if index == 0 else ["0deg"]])
            families += ["Freq:=", [format(freq, ".12g") + "GHz"]] + variables
            components = (["Phi Component:=", "Phi", "Theta Component:=", "Theta",
                           "Mag Component:=", ["dB(GainTotal)"]] if index == 0
                          else ["Ang Component:=", "Phi" if index == 2 else "Theta", "Mag Component:=", ["dB(GainTotal)"]])
            reporter.CreateReport(name, "Far Fields", "3D Polar Plot" if index == 0 else "Radiation Pattern",
                                  snapshot["solution"], ["Context:=", snapshot["sphere_name"] if index == 0 else cuts_sphere],
                                  families, components)
            if index == 3:
                reporter.AddTraces(name, snapshot["solution"], ["Context:=", cuts_sphere],
                                   ["Phi:=", ["All"], "Theta:=", ["90deg"],
                                    "Freq:=", [format(freq, ".12g") + "GHz"]] + variables,
                                   ["Ang Component:=", "Phi", "Mag Component:=", ["dB(GainTotal)"]])
            if index > 0:
                for trace_index, trace in enumerate(reporter.GetReportTraceNames(name)):
                    primary = "Phi" if index == 2 or (index == 3 and trace_index == 1) else "Theta"
                    native_trace = reporter.GetChildObject(name).GetChildObject(trace)
                    native_trace.SetPropValue("Primary Sweep", primary)
                    if str(native_trace.GetPropValue("Primary Sweep")) != primary:
                        raise ValueError("Native report has the wrong primary sweep: " + name)
        cuts = [r[3] for r in snapshot["samples"] if r[0] == freq
                and (abs(r[1] - 90) < 1e-6 or any(abs((r[2] % 360) - phi) < 1e-6 for phi in (0, 180)))]
        lower = math.floor(min(cuts) / 10) * 10 if cuts else -40
        upper = max(lower + 10, math.ceil(max(cuts) / 10) * 10) if cuts else 10
        for name in names[1:]:
            axis = reporter.GetChildObject(name).GetChildObject("CircleAxis")
            axis.SetPropValue("Auto Scale", False)
            axis.SetPropValue("Min Scale", str(lower))
            axis.SetPropValue("Max Scale", str(upper))
            axis.SetPropValue("Spacing", "10")
        image_names = []
        errors = []
        for index, name in enumerate(names):
            image_name = ("gain3d_" if index == 0 else "e_" if index == 1 else "h_" if index == 2 else "eh_") + key + ".png"
            try:
                reporter.ExportImageToFile(name, str(output / image_name), 1200, 900)
                if not (output / image_name).is_file() or (output / image_name).stat().st_size < 100:
                    raise ValueError("Native report image was not written")
                image_names.append("native_reports/" + image_name)
            except Exception as exc:
                image_names.append(None)
                errors.append(str(exc)[:300])
        report_sets.append({"frequency_ghz": freq, "names": names, "images": image_names,
                            "radial_scale_db": [lower, upper], "image_errors": errors})
    if report_sets and open_window:
        reporter.OpenWindowForReports(report_sets[0]["names"])
    if keep_reports:
        # Persist the four reports (3D / E / H / EH overlay) in the solved project and save it.
        # The caller explicitly asked for them, so this branch skips the cleanup deletion below.
        project.Save()
        kept = [name for report_set in report_sets for name in report_set["names"]]
        return {"sets": report_sets, "opened_reports": kept, "reports_deleted": False,
                "reports_kept": True, "saved": True,
                "plane_convention": "z_axis_E_phi0_H_theta90", "e_phi_deg": 0, "h_theta_deg": 90,
                "definition": "Z-axis antenna: E phi=0 theta=-180..180; H theta=90 phi=0..360"}
    # Delete the reports we just created: the images are already exported to the artifacts, and a
    # project that accumulates plots makes every later PyAEDT PostProcessor emit AEDT script-macro
    # errors while it enumerates them (seen as rows of "Solution is not a property name").
    owned = [name for report_set in report_sets for name in report_set["names"]]
    try:
        if owned:
            reporter.DeleteReports(owned)
    except Exception as exc:  # noqa: BLE001 - cleanup must not fail the capture
        return {"sets": report_sets, "opened_reports": [],
                "reports_deleted": False, "delete_error": str(exc)[:300],
                "saved": True, "plane_convention": "z_axis_E_phi0_H_theta90",
                "e_phi_deg": 0, "h_theta_deg": 90,
                "definition": "Z-axis antenna: E phi=0 theta=-180..180; H theta=90 phi=0..360"}
    project.Save()
    return {"sets": report_sets, "opened_reports": [], "reports_deleted": True,
            "saved": True, "plane_convention": "z_axis_E_phi0_H_theta90", "e_phi_deg": 0, "h_theta_deg": 90,
            "definition": "Z-axis antenna: E phi=0 theta=-180..180; H theta=90 phi=0..360"}


def snapshot_code(project: Path, design: str, setup: str, output_dir: Path | None = None) -> str:
    """Read existing adaptive far fields through the authorised guard code API."""
    code = inspect.getsource(_broker_read) + "\nresult = _broker_read(%r, %r, %r)\n" % (
        str(project), design, setup)
    if output_dir is not None:
        code = inspect.getsource(_broker_reports) + "\n" + code
        code += ("\ntry:\n    result['native_reports'] = _broker_reports(%r, %r, result, %r)\n"
                 "except Exception as exc:\n    result['native_report_error'] = str(exc)[:500]\n") % (
                     str(project), design, str(output_dir))
    return code


def persist_pattern_reports_code(project: Path, design: str, setup: str, output_dir: Path) -> str:
    """Code that rebuilds the four pattern reports in the solved project and KEEPS them."""
    code = inspect.getsource(_broker_read) + "\n" + inspect.getsource(_broker_reports) + "\n"
    code += "result = _broker_read(%r, %r, %r)\n" % (str(project), design, setup)
    code += ("try:\n"
             "    result['persisted'] = _broker_reports(%r, %r, result, %r,\n"
             "                                         keep_reports=True, open_window=False)\n"
             "except Exception as exc:\n"
             "    result['persist_error'] = str(exc)[:500]\n") % (
                 str(project), design, str(output_dir))
    return code


def persist_pattern_reports(bridge: Any, *, campaign: Path, run_id: str, project: Path,
                            design: str, setup: str, pid: int,
                            output_dir: Path | None = None,
                            timeout: float = 240.0) -> dict[str, Any]:
    """Create and KEEP the 3D / E-plane / H-plane / EH-overlay reports in the solved project.

    The four PNGs are exported next to a small JSON result (default:
    ``<campaign>/pattern_reports/<run_id>/``). Reports live in the project only if the project copy
    survives; the PNGs are the durable fallback. Failure is recorded, never raised, so a finished
    measurement stays accepted.
    """
    target = Path(output_dir) if output_dir is not None else Path(campaign) / "pattern_reports" / run_id
    target.mkdir(parents=True, exist_ok=True)
    try:
        response = bridge.execute_code(
            persist_pattern_reports_code(project, design, setup, target), pid=pid, timeout=timeout)
        raw = response.get("result") if isinstance(response, dict) else None
        if not isinstance(raw, dict):
            raise ValueError("guard returned no result for pattern reports")
        payload = {"ok": "persist_error" not in raw, "directory": str(target),
                   "reports_kept": bool((raw.get("persisted") or {}).get("reports_kept")),
                   "reports": (raw.get("persisted") or {}).get("opened_reports") or [],
                   "images": (raw.get("persisted") or {}).get("sets") or [],
                   "error": raw.get("persist_error")}
        if payload["ok"] and payload["reports"] and Path(project).is_file():
            try:
                payload["opener"] = str(write_report_opener(Path(project), design,
                                                            payload["reports"], pid))
            except (OSError, ValueError) as exc:  # noqa: BLE001 - convenience only
                payload["opener_error"] = str(exc)[:300]
    except Exception as exc:  # noqa: BLE001 - optional artifact, never fails the measurement
        payload = {"ok": False, "directory": str(target), "error": str(exc)[:500]}
    try:
        (target / "persist_result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        payload["write_error"] = str(exc)[:300]
    return payload


def artifact_dir(campaign: Path, run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", run_id):
        raise ValueError("Invalid run id")
    root = (Path(campaign) / "runs").resolve()
    target = (root / run_id / "artifacts").resolve()
    if not target.is_relative_to(root):
        raise ValueError("Run path escapes campaign")
    return target


def save_snapshot(campaign: Path, run_id: str, payload: dict[str, Any]) -> None:
    path = artifact_dir(campaign, run_id) / SNAPSHOT
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def capture(bridge: Any, *, campaign: Path, run_id: str, project: Path,
            design: str, setup: str, pid: int) -> dict[str, Any]:
    """Optional postprocessing: failure cannot change accepted simulation KPIs."""
    existing = artifact_dir(campaign, run_id) / SNAPSHOT
    if existing.is_file():
        try:
            previous = json.loads(existing.read_text(encoding="utf-8"))
            if (previous.get("samples") and previous.get("native_reports", {}).get("saved")
                    and previous["native_reports"].get("plane_convention") == "z_axis_E_phi0_H_theta90"):
                return previous
        except (OSError, ValueError, AttributeError):
            pass
    try:
        response = bridge.execute_code(snapshot_code(project, design, setup,
                                      artifact_dir(campaign, run_id) / "native_reports"), pid=pid, timeout=120.0)
        raw = response.get("result") if isinstance(response, dict) else None
        if not isinstance(raw, dict) or not raw.get("samples"):
            raise ValueError("Guard returned no measured far-field samples")
        raw.update({"run_id": run_id, "project": str(project), "design": design, "aedt_pid": pid})
    except Exception as exc:
        raw = {"run_id": run_id, "samples": [], "detail": str(exc)[:500],
               "quantity": QUANTITY, "source": "MEASURED"}
    if (raw.get("native_reports", {}).get("saved")
            and raw["native_reports"].get("opened_reports")):
        try:
            import hashlib
            raw["project_sha256"] = hashlib.sha256(Path(project).read_bytes()).hexdigest()
            raw["report_opener"] = str(write_report_opener(project, design, raw["native_reports"]["opened_reports"],
                                                         pid, raw["project_sha256"]))
        except OSError as exc:
            raw["opener_error"] = str(exc)[:300]
    save_snapshot(campaign, run_id, raw)
    return raw


def write_report_opener(project: Path, design: str, reports: list[str], pid: int | None = None,
                        project_sha256: str | None = None) -> Path:
    """Double-clickable entry explicitly reopens the four saved native reports."""
    import hashlib
    import sys
    from . import config
    project = Path(project).resolve()
    environment = config.load()
    python = str(environment.venv_python or sys.executable)
    source_root = str(Path(__file__).resolve().parent.parent)
    manifest = project.with_suffix(".aster-reports.json")
    project_sha256 = project_sha256 or hashlib.sha256(project.read_bytes()).hexdigest()
    try:
        previous = json.loads(manifest.read_text(encoding="utf-8"))
        previous_pid = previous.get("pid")
        if (previous.get("project") == str(project) and previous.get("design") == design
                and previous.get("capture_sha256", previous.get("project_sha256")) == project_sha256
                and type(previous_pid) is int and previous_pid > 0):
            pid = previous_pid  # Keep the GUI selected by a prior successful launcher invocation.
    except (OSError, ValueError, AttributeError):
        pass
    manifest.write_text(json.dumps({"project": str(project), "design": design, "reports": reports,
                                  "pid": pid, "source_root": source_root,
                                  "capture_sha256": project_sha256},
                                  ensure_ascii=False), encoding="utf-8")
    lines = ["@echo off", "@chcp 65001 >nul", "setlocal",
             'set "PYTHONPATH=' + source_root.replace("%", "%%") + ';%PYTHONPATH%"',
             '"' + python + '" -m aster.open_reports --manifest "%~dp0' + manifest.name + '"',
             "if errorlevel 1 pause", ""]
    path = project.with_name(project.stem + "_查看方向图.cmd")
    path.write_bytes("\r\n".join(lines).encode("utf-8"))
    return path


def open_saved_reports(campaign: Path, run_id: str, frequency_ghz: float | None = None) -> None:
    import hashlib
    import os
    import sys
    import subprocess
    from . import config

    raw = json.loads((artifact_dir(campaign, run_id) / SNAPSHOT).read_text(encoding="utf-8"))
    native = raw.get("native_reports") or {}
    if not native.get("saved") or not native.get("opened_reports"):
        raise ValueError("this run's native reports were deleted after their images were exported, "
                         "to keep the AEDT message window quiet; compare the saved images instead")
    reports = native["opened_reports"]
    if frequency_ghz is not None:
        selected = next((item for item in native.get("sets", [])
                         if abs(item.get("frequency_ghz", -1) - frequency_ghz) < 1e-8), None)
        if not selected or len(selected.get("names", [])) != 4:
            raise ValueError("Four native reports are unavailable at the selected frequency")
        reports = selected["names"]
    project = Path(raw["project"])
    expected_hash = raw.get("project_sha256")
    if expected_hash and hashlib.sha256(project.read_bytes()).hexdigest() != expected_hash:
        # AEDT rewrites window/layout state during an ordinary Save. The sidecar
        # identifies the latest captured run; a later capture replaces its token.
        try:
            latest = json.loads(project.with_suffix(".aster-reports.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            latest = {}
        if (latest.get("project") != str(project.resolve()) or latest.get("design") != raw["design"]
                or latest.get("capture_sha256", latest.get("project_sha256")) != expected_hash):
            raise ValueError("Saved project has changed since these reports were captured; open the latest run's report launcher")
    write_report_opener(project, raw["design"], reports, raw.get("aedt_pid"), raw.get("project_sha256"))
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + environment.get("PYTHONPATH", "")
    result = subprocess.run([str(config.load().venv_python or sys.executable), "-m", "aster.open_reports",
                             "--manifest", str(project.with_suffix(".aster-reports.json"))],
                            env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=120, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0) if os.name=="nt" else 0)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or "Could not open native reports")[-1000:])


def _csv_samples(directory: Path) -> list[list[float]]:
    """Read historical long-form AEDT CSVs; never infer unmeasured phi values."""
    result = []
    scales = {"hz": 1e-9, "khz": 1e-6, "mhz": 1e-3, "ghz": 1}
    for path in sorted(directory.glob("*.csv")):
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader, [])
                lookup = {re.sub(r"\s*\[.*", "", h).strip().lower(): i for i, h in enumerate(header)}
                indexes = [lookup[k] for k in ("freq", "theta", "phi", "db(gaintotal)")]
                unit = re.search(r"\[([^]]+)\]", header[indexes[0]])
                freq_scale = scales[unit.group(1).lower()] if unit else 1
                angle_scales = []
                for index in indexes[1:3]:
                    unit = re.search(r"\[([^]]+)\]", header[index])
                    name = unit.group(1).lower() if unit else "deg"
                    if name not in ("deg", "rad"):
                        raise ValueError("Unknown angle unit")
                    angle_scales.append(180 / math.pi if name == "rad" else 1)
                for row in reader:
                    try:
                        values = [float(row[i]) for i in indexes]
                        values[0] *= freq_scale
                        values[1] *= angle_scales[0]
                        values[2] *= angle_scales[1]
                        if all(math.isfinite(v) for v in values):
                            result.append(values)
                    except (ValueError, IndexError):
                        continue
        except (OSError, KeyError, ValueError):
            continue
    return result


def _normalise(samples: list) -> tuple[dict[float, dict[tuple[float, float], float]], set[float]]:
    groups: dict[float, dict[tuple[float, float], float]] = {}
    conflicts: set[float] = set()
    for row in samples:
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            continue
        try:
            freq, theta, phi, gain = map(float, row)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (freq, theta, phi, gain)) or freq <= 0:
            continue
        theta, phi = theta % 360, phi % 360
        if theta > 180:
            theta, phi = 360 - theta, (phi + 180) % 360
        point = (round(theta, 7), round(phi, 7))
        freq = round(freq, 9)
        group = groups.setdefault(freq, {})
        if abs(group.setdefault(point, gain) - gain) > 1e-6:
            conflicts.add(freq)
    return groups, conflicts


def view(campaign: Path, run_id: str, frequency_ghz: float | None = None) -> dict[str, Any]:
    directory = artifact_dir(campaign, run_id)
    path = directory / SNAPSHOT
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {"detail": "Saved far-field snapshot is unreadable"}
    samples = raw.get("samples") or _csv_samples(directory / "farfield")
    groups, conflicts = _normalise(samples)
    frequencies = sorted(groups)
    selected = (frequencies[0] if frequencies else None) if frequency_ghz is None else frequency_ghz
    selected = next((f for f in frequencies if abs(f - selected) < 1e-8), selected) if selected is not None else None
    data = groups.get(selected, {})
    native = raw.get("native_reports") or {}
    selected_set = next((item for item in native.get("sets", [])
                         if selected is not None and abs(item.get("frequency_ghz", -1) - selected) < 1e-8), {})
    source = raw.get("source", "MEASURED")
    if raw.get("run_id") not in (None, "", run_id):
        source = "UNVERIFIED"
    quantity = raw.get("quantity", QUANTITY)
    response = {"run_id": run_id, "artifact_run_id": run_id, "status": "unavailable", "source": source,
                "quantity": quantity, "frequencies_ghz": frequencies, "frequency_ghz": selected,
                "sphere": None, "e_plane": None, "h_plane": None, "warnings": [],
                "ring_pattern": None,
                "detail": raw.get("detail", ""), "sphere_name": raw.get("sphere_name", ""),
                "solution": raw.get("solution", ""),
                "coordinate_system": raw.get("coordinate_system", "Global"),
                "coordinate_system_recorded": bool(raw.get("coordinate_system")),
                "native_reports": native, "native_report_set": selected_set,
                "native_image_url": None, "opener_available": bool(native.get("saved"))}
    images = selected_set.get("images") or []
    if images and images[0]:
        from urllib.parse import urlencode
        picture = (directory / images[0]).resolve()
        if picture.is_relative_to(directory.resolve()) and picture.is_file():
            response["native_image_url"] = "/api/farfield-image?" + urlencode({"run_id": run_id, "frequency_ghz": selected})
    if raw.get("native_report_error"):
        response["warnings"].append(raw["native_report_error"])
    if selected in conflicts:
        response["detail"] = "Conflicting gain samples at equivalent angular coordinates"
        return response
    if not data:
        response["detail"] = response["detail"] or "No saved far-field data for this run/frequency"
        return response
    for key in ("e_plane", "h_plane"):
        if key == "e_plane":
            points = {t: gain for (t, p), gain in data.items() if abs(p) < 1e-6}
            points.update({-t: gain for (t, p), gain in data.items() if abs(p - 180) < 1e-6 and t > 0})
            definition = "φ=0°, θ=-180°…180°"
        else:
            points = {p: gain for (t, p), gain in data.items() if abs(t - 90) < 1e-6}
            if 0 in points and len(points) > 1:
                points[360.0] = points[0]
            definition = "θ=90°, φ=0°…360°"
        if len(points) >= 2:
            angles = sorted(points)
            response[key] = {"label": "E" if key == "e_plane" else "H", "angle_deg": angles,
                             "gain_db": [points[t] for t in angles],
                             "definition": definition}
    thetas = sorted({t for t, _p in data})
    phis = sorted({p for _t, p in data})
    full_grid = (len(thetas) >= 3 and len(phis) >= 3 and thetas[0] < 1e-6
                 and abs(thetas[-1] - 180) < 1e-6 and max(b-a for a,b in zip(phis, phis[1:] + [phis[0]+360])) <= 30
                 and all((t, p) in data for t in thetas for p in phis))
    if full_grid:
        response["sphere"] = {"theta_deg": thetas, "phi_deg": phis + [phis[0] + 360],
                              "gain_db": [[data[t, p] for p in phis] + [data[t, phis[0]]] for t in thetas]}
    else:
        response["warnings"].append("Full measured sphere grid is unavailable; 3D plot cannot be reconstructed from cuts")
    from .pattern_acceptance import assess_ring_pattern
    response["ring_pattern"] = assess_ring_pattern(response["sphere"],
                                                   quantity=quantity, source=source)
    count = sum(response[key] is not None for key in ("sphere", "e_plane", "h_plane"))
    response["status"] = "available" if count == 3 else "partial" if count else "unavailable"
    return response
