---
name: aster
description: Physics-guided antenna design and optimisation library and agent skill. Use when an agent must define a parameterized antenna design space, run dry or real AEDT-backed design studies through a guarded solver, keep measured artifacts as evidence, apply explicit bandwidth/pattern acceptance rules, or re-score recorded runs without re-solving. The web GUI is not included; real runs require the ansys-aedt-mcp guard plus AEDT 2026 R1.
---

# aster

`aster` is a design bench for antenna studies, packaged in this repository as a Python library, a
command-line entry point and this agent skill. The bench keeps every solve's artifacts so that a
changed requirement re-judges history instead of re-solving it.

Hero and loop diagrams live in [`assets/`](assets/): `banner.svg`, `loop.svg`.

## Install as a skill

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.dsh\skills\aster" | Out-Null
Copy-Item SKILL.md "$env:USERPROFILE\.dsh\skills\aster\SKILL.md"
```

For other agents, use the same `SKILL.md` convention (YAML front matter with `name` and
`description`, Markdown body) and place it in the agent's skill directory.

## When to use this skill

- Define a parameter space, objectives and constraints for a parameterized antenna design.
- Run a dry (analytic/surrogate) study without AEDT for loop wiring and smoke tests.
- Submit real candidates to AEDT through the `ansys-aedt-mcp` guard and keep the raw artifacts.
- Apply explicit acceptance rules (S11 bandwidth, far-field ring pattern) and record verdicts.
- Re-score recorded runs when the target changes, without re-solving.

Do **not** use it for GUI/HTTP work: the GUI is under validation and is not part of this release.

## Install the library

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[optimizer,surrogate,dev]"
```

## Quick checks

```powershell
python -m aster version
python -m aster cases
python -m aster dry-demo --samples 64
python -m pytest tests -q
```

## Core API map

| task | API |
|---|---|
| search space | `aster.core.ParamSpec`, `ParamSpace` |
| goals/constraints | `aster.core.Objective`, `Constraint`, `CaseSpec` |
| sampling | `aster.core.latin_hypercube`, `sample_population` |
| population comparison | `aster.optimize.Evaluation`, `pareto_archive`, `fast_non_dominated_sort` |
| campaigns | `aster.campaign`, `aster.campaigns`, `aster.worker` |
| measured runs | `aster.runtime.runner.RealRunManager`, `aster.runtime.store.JobStore` |
| S11 acceptance | `aster.band_acceptance.assess_touchstone_band` |
| pattern acceptance | `aster.pattern_acceptance.assess_ring_pattern` |
| combined verdict | `aster.acceptance` |

## Real-run contract

1. Configure `ASTER_AEDT_EXE`, `ASTER_GUARD_REPO`, `ASTER_VENV_PYTHON`,
   `ASTER_REFERENCE_PROJECT`, `ASTER_CAMPAIGN_ROOT`.
2. Target one AEDT session explicitly (PID or gRPC port); the guard refuses implicit sessions.
3. Submit a `RealJob`; the runner copies the project, applies parameters, solves under the guard's
   budget, exports S11/far field and stores the artifacts.
4. Score with the acceptance functions and keep the metrics beside the artifacts.

Full contract: [user_doc/aedt_integration.md](user_doc/aedt_integration.md) and
[examples/aedt_campaign.md](examples/aedt_campaign.md).

## Evidence rules

- Never present a dry/surrogate result as measured; mark it `PREDICTED`.
- Keep `artifacts/result.s1p`, `artifacts/farfield_views.json` and native report images with the run.
- Report a failed solve as failed; do not silently drop it from the campaign.
- State the machine/AEDT/PyAEDT version when quoting a measured result.

## License

MIT. The package depends on NumPy only; optional extras add the optimiser, surrogate, acquisition
and plotting stack.
