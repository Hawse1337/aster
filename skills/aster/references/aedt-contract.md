# AEDT contract

`aster` never talks to AEDT directly. Real solves go through the companion guard system
[ansys-aedt-mcp](https://github.com/Hawse1337/ansys-aedt-mcp), which owns the session, the budget and
the evidence gate.

## Minimum environment

| item | requirement |
|---|---|
| AEDT | 2026 R1 installed; `ansysedt.exe` reachable |
| guard | checkout of `ansys-aedt-mcp` with its `.venv` |
| PyAEDT | 1.10.0 (pinned by the guard) |
| project | a reference `.aedt` with a named design and setup |
| campaign | a writable campaign directory |

## Configuration fields

Use environment variables or a config file. Prefix: `ASTER_`.

| variable | maps to |
|---|---|
| `ASTER_AEDT_EXE` | `ansysedt.exe` |
| `ASTER_GUARD_REPO` | guard checkout |
| `ASTER_VENV_PYTHON` | guard virtual-environment python |
| `ASTER_REFERENCE_PROJECT` | reference `.aedt` |
| `ASTER_CAMPAIGN_ROOT` | campaign directory |
| `ASTER_AEDT_VERSION` | e.g. `2026.1` |

`aster doctor` prints the resolved state and which of these are missing.

## Run lifecycle

1. **Preflight** — reference project and target design exist; the AEDT session is the one named by
   PID or gRPC port (the guard refuses implicit sessions).
2. **Model** — copy the reference project; apply the candidate's parameter values.
3. **Solve** — start the analysis under the guard's budget accounting.
4. **Post-process** — export S11 (Touchstone) and the far-field grid; keep native report images.
5. **Score** — evaluate the current acceptance rules from the exported artifacts.
6. **Record** — append the run to the campaign archive with status, metrics and artifact paths.

A failed solve is a recorded failed run, with the failure reason. It is never dropped.

## Evidence layout

```text
<run-id>/
├── artifacts/result.s1p
├── artifacts/farfield_views.json
├── artifacts/native_reports/*.png
└── run.json
```

Re-scoring reads these files; if the recorded data is no longer compatible with the task, say so
and re-solve rather than silently extrapolating.
