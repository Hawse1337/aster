# Antenna Optimizer — User Manual

This is the English counterpart of [user_manual.md](user_manual.md); the two are kept in step.

## 1. Install

1. Either:
   - **installer**: run `AntennaOptimizer-1.21.0-setup.exe` — per-user, no administrator rights,
     Start Menu entry, uninstaller and version number;
   - **portable**: unzip `AntennaOptimizer-1.21.0-portable.zip` anywhere and run
     `AntennaOptimizer.exe` from it.
2. Double-click `AntennaOptimizer.exe`.
3. On the first start, if something essential is missing, the console asks for the AEDT executable,
   the guard checkout and the reference project; the answers are written to
   `%APPDATA%\aster\config.toml` and not asked again.

**Python is not required**: the executable carries its own. Real solver runs do need:

| Dependency | Purpose |
|---|---|
| AEDT 2026 R1 or newer | the electromagnetic simulation |
| `ansys-aedt-mcp` checkout and its `.venv` (holds PyAEDT) | guarded execution and export |

## 2. First run

The browser opens `http://127.0.0.1:8765/` (a different port if that one is taken — the console
prints the real one).

- **dry mode** (default): the "trial" button interpolates on the measured surface, no AEDT involved;
- **real runs**: start with `--real`, or use the Start Menu entry that adds it. The application
  starts AEDT itself if none is running.

## 3. The page

| Area | What it does |
|---|---|
| Parameters | ring diameter, ring height, feed and so on; each has editable low/high bounds |
| Trial / real run | trial interpolates, real run submits to AEDT |
| Batch sweep | queue several candidates, watch progress |
| Recommend next batch | fit on measured data and propose candidates; set the count and a budget (minutes), pick a backend, and the table shows parameters, estimated solve time, feasibility (from the nearest measurement) and the run it was judged by. "Queue the proposal" submits them |
| Bandwidth / gain weights | say which objective matters more; saved weights feed the recommendation and appear in its reason |
| Save bounds and weights | writes `%APPDATA%\aster\preferences.json`; it survives restarts |
| Results table | bandwidth, gain, ripple, feasibility per candidate |
| Export Pareto | CSV/JSON of the non-dominated front, including the per-generation progress |
| Run status | run count, success rate, per-stage medians, slowest runs, from `GET /api/observability` |
| S-parameter plot | the measured touchstone of a finished real run |
| Unavailable metrics | if a design cannot produce a metric (radiation efficiency here), the reason is shown under 约束 instead of being discovered by a failed run |

### How a recommendation is produced

1. only `COMPLETED` rows with complete parameters and objectives train the surrogate; fewer than four rows gets an honest "not enough measurements" answer;
2. the search stays inside the measured envelope: parameters that never varied are held at their measured value, the others are searched only between their measured minimum and maximum;
3. designs the model's geometry check rejects are dropped, and so is anything predicted to exceed the budget;
4. every candidate carries `predicted_cost` - estimated minutes, spread, sample count, failure rate and method - which is what the "estimated time" column shows.

## 4. Continuing after a restart

Every run's state is written to the campaign's `queue.sqlite`.

- `GET /api/queue` lists the batches that still owe work;
- the page's resume action re-queues the unfinished candidates with their original parameters.

## 5. Error codes

The application translates each code into a sentence, a suggested action and whether a rerun can
help.

| Code | Meaning | What to do |
|---|---|---|
| `E_NOT_CONVERGED` | the solve did not converge after the adaptive passes | raise the passes or relax MaxDeltaS |
| `E_SWEEP_EXPORT_INCOMPLETE` | the sweep had not finished when the export ran | the application waits and retries; if it keeps failing, use a coarser sweep |
| `INVALID_PARAMS` | a parameter is outside its bounds | adjust it; the page marks the offending field |
| `GEOMETRY_REJECTED` | this parameter set produces self-intersecting bodies | change the ring diameters/heights |
| `NO_AEDT` | no AEDT with a gRPC server is running | set `aedt_exe` in the config, or start AEDT first |
| `REAL_DISABLED` | the application is in dry mode | start it with `--real` |
| `STALLED` | the solver is alive but not progressing | the application kills it and retries once; repeated stalls mean the mesh is too large |

## 6. Configuration

`%APPDATA%\aster\config.toml` (override with `ASTER_CONFIG`):

```toml
[paths]
aedt_exe = "D:\\ANSYS Inc\\v261\\AnsysEM\\ansysedt.exe"
guard_repo = "C:\\ProgramData\\ansys-aedt-mcp"
venv_python = "C:\\ProgramData\\ansys-aedt-mcp\\.venv\\Scripts\\python.exe"
reference_project = "C:\\Users\\me\\Desktop\\lensfinal\\LensFinal.aedt"
campaign_root = "D:\\AgentAEDT_Collab\\aster_bench"
```

Precedence: **discovery < config file < environment variables** (`ASTER_AEDT_EXE` and so on).
Current values: `AntennaOptimizer.exe --selftest`.

Preferences (bounds, weights) live in `%APPDATA%\aster\preferences.json`; a bound may narrow the
case's own limits but never widen them.

## 7. Command line

| Command | Effect |
|---|---|
| `AntennaOptimizer.exe` | start the page (dry mode) |
| `AntennaOptimizer.exe --real` | enable solver runs |
| `AntennaOptimizer.exe --wizard` | run the configuration wizard again |
| `AntennaOptimizer.exe --selftest` | check the installation (imports, page, config) |
| `AntennaOptimizer.exe --version` | version |
| `--port 8765` / `--campaign DIR` / `--project X.aedt` | override the defaults |
| `--stall-seconds 600` / `--stage-timeout 900` | stall thresholds |

## 8. Uninstall

1. Delete the program directory (or use the uninstaller the setup installed);
2. optionally delete `%APPDATA%\aster\` (configuration and preferences) and the campaign
   directories (results).

AEDT and `ansys-aedt-mcp` are not affected.

## 9. When something goes wrong

1. the page's error message (already translated, with a suggested action);
2. the console window's startup log;
3. `queue.sqlite` in the campaign, and `GET /api/observability` for timings and failure counts;
4. when reporting a problem, include the `--selftest` output and the run id.
