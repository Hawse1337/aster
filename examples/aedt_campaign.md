# A measured campaign through the guard

This walkthrough shows the contract between `aster` (design side) and
[ansys-aedt-mcp](https://github.com/Hawse1337/ansys-aedt-mcp) (execution side). Numbers below are
placeholders for a real study; the flow is what matters.

## 0. Prerequisites

- AEDT 2026 R1 installed.
- `ansys-aedt-mcp` checkout with its virtual environment (PyAEDT 1.10.0).
- A reference `.aedt` project with a solved, named setup and a parameterized design.
- A writable campaign directory.

## 1. Configure the environment

```powershell
$env:ASTER_AEDT_EXE         = "D:\ANSYS Inc\v261\AnsysEM\ansysedt.exe"
$env:ASTER_GUARD_REPO       = "C:\ProgramData\ansys-aedt-mcp"
$env:ASTER_VENV_PYTHON      = "C:\ProgramData\ansys-aedt-mcp\.venv\Scripts\python.exe"
$env:ASTER_REFERENCE_PROJECT = "<path to reference project>\Reference.aedt"
$env:ASTER_CAMPAIGN_ROOT    = "<campaign directory>"
$env:ASTER_AEDT_VERSION     = "2026.1"
```

The exact field names are the `Environment` dataclass in `aster/config.py`.

## 2. Inspect the target session

Start AEDT with a gRPC port, or attach to an existing session, then list it through the guard. The
guard requires an explicit PID or port for every call; `aster` does not guess.

## 3. Prepare the case

```python
from aster.core import CaseSpec, FamilySpec, Objective, ParamSpec, ParamSpace

case = CaseSpec(
    name="ring_demo",
    family=FamilySpec("ring"),
    space=ParamSpace([ParamSpec("ring_radius", "mm", 20.0, 60.0, 40.0)]),
    objectives=[Objective("gain", "peak_gain", "maximize")],
    project="<reference project>",
    setup="Setup1",
)
```

## 4. Submit one real candidate

The real evaluator performs: copy project → apply parameters → solve with the guard's budget →
export S11 and far field → score → record. A failed solve is recorded as failed, not dropped.

```python
from aster.runtime.runner import RealRunManager, RealJob

manager = RealRunManager(campaign_root="<campaign directory>",
                         guard_repo="<guard checkout>",
                         venv_python="<guard venv python>")
job = RealJob(case="ring_demo",
              params={"ring_radius": 44.0},
              reference_project="<reference project>",
              setup="Setup1")
run_id = manager.submit(job)
```

The run directory receives `artifacts/result.s1p`, `artifacts/farfield_views.json` and the native
report images. Keep them next to the run record.

## 5. Re-score later

Because the artifacts are kept, a changed acceptance rule can be applied to the archive:

```python
from aster.band_acceptance import assess_touchstone_band
verdict = assess_touchstone_band("<run>/artifacts/result.s1p", low_ghz=24.0, high_ghz=32.0)
print(verdict["effective_bandwidth_ghz"], verdict["passed"])
```

No solver is started by this step.

## Notes

- The guard's own README covers AEDT/PyAEDT installation and session management.
- The examples above are wiring references, not measured results; measure on your own project and
  keep the evidence.
