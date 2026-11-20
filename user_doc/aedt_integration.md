# AEDT integration

`aster` does not talk to AEDT directly. Real runs go through the companion guard system
[ansys-aedt-mcp](https://github.com/Hawse1337/ansys-aedt-mcp), which owns the AEDT session, the
solve budget and the evidence trail.

## What the guard provides

- Explicit session targeting: every call takes exactly one AEDT PID or gRPC port; no implicit
  sessions and no fallback launch.
- A preflight/audit layer, solver budget accounting, archive and export gates.
- Touchstone and far-field export with the artifacts kept on disk.

The guard pins PyAEDT **1.10.0** and is validated on AEDT 2026 R1. From guard **v0.3.2** it also
enforces managed-only sessions (unmanaged AEDT is refused and reported), denies arbitrary broker
code by default, and binds run tokens to project/design/setup when `AEDT_GUARD_ENFORCE=1`.
See [runtime safety](safety.md) for the boundary.

aster's own trusted bridge enables the guard's arbitrary-code opt-in **for its own broker**
(`InProcessBridge`/`StdioBridge` set `AEDT_MCP_ALLOW_ARBITRARY_CODE=1` locally) because measured
runs execute modelling snippets. The DSH/Codex MCP server processes are separate and keep the
guard's default-deny gate; run aster only from a trusted operator account. PyAEDT documentation:
<https://aedt.docs.pyansys.com/version/stable/index.html>.

## Configuration

`aster` discovers the guard checkout and its interpreter through `Environment` (see
`aster/config.py`). Values can be set with `ASTER_*` environment variables or a config file:

| field | meaning |
|---|---|
| `aedt_exe` | path to `ansysedt.exe` |
| `guard_repo` | checkout of `ansys-aedt-mcp` |
| `venv_python` | `<guard_repo>\.venv\Scripts\python.exe` |
| `reference_project` | the `.aedt` project copied for each candidate |
| `campaign_root` | writable campaign directory |
| `aedt_version` | default `2026.1` |

`python -m aster` reports missing fields before a campaign starts; validation is explicit rather
than a stack trace halfway through a solve.

## Run lifecycle

1. **Preflight** — verify the reference project and target design exist and the session is the one
   the caller named.
2. **Model** — copy the reference project, apply the candidate's parameter values to the AEDT
   design.
3. **Solve** — start the analysis with the guard's budget accounting.
4. **Post-process** — export S11 (Touchstone) and the far-field grid; store both under the run
   directory.
5. **Score** — evaluate the current acceptance rules from the exported artifacts.
6. **Record** — append the run to the campaign SQLite archive with status, metrics and paths.

Failed solves leave a failed run record; they are never silently dropped. A stale solver process is
handled by the guard, not by the design layer.

## Evidence

Each real run directory keeps:

```text
<run-id>/
├── artifacts/result.s1p
├── artifacts/farfield_views.json
├── artifacts/native_reports/*.png
└── run.json
```

The archive stores the metric values that were computed from these files, so an acceptance change
can be applied later without a new solve (provided the recorded data is still compatible with the
task).
