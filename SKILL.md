---
name: antopt
description: Physics-guided antenna design and optimisation library. Use when an agent must define a parameterized antenna design space, run dry or real AEDT-backed design studies through a guarded solver, keep measured artifacts as evidence, apply explicit bandwidth/pattern acceptance rules, or re-score recorded runs without re-solving. The web GUI is not included; real runs require the ansys-aedt-mcp guard plus AEDT 2026 R1.
---

# antopt

`antopt` is the design-side library of a guarded antenna optimisation stack. It is a Python package,
a command line entry point and a repository-level agent skill.

## When to use this skill

- Define a parameter space, objectives and constraints for a parameterized antenna design.
- Run a dry (analytic/surrogate) study without AEDT for loop wiring and smoke tests.
- Submit real candidates to AEDT through the `ansys-aedt-mcp` guard and keep the raw artifacts.
- Apply explicit acceptance rules (S11 bandwidth, far-field ring pattern) and record verdicts.
- Re-score recorded runs when the target changes, without re-solving.

Do **not** use it for GUI/HTTP work: the GUI is under validation and is not part of this release.

## Install

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[optimizer,surrogate,dev]"
```

## Quick checks

```powershell
python -m antopt version
python -m antopt cases
python -m antopt dry-demo --samples 64
python -m pytest tests -q
```

## Core API map

| task | API |
|---|---|
| search space | `antopt.core.ParamSpec`, `ParamSpace` |
| goals/constraints | `antopt.core.Objective`, `Constraint`, `CaseSpec` |
| sampling | `antopt.core.latin_hypercube`, `sample_population` |
| population comparison | `antopt.optimize.Evaluation`, `pareto_archive`, `fast_non_dominated_sort` |
| campaigns | `antopt.campaign`, `antopt.campaigns`, `antopt.worker` |
| measured runs | `antopt.runtime.runner.RealRunManager`, `antopt.runtime.store.JobStore` |
| S11 acceptance | `antopt.band_acceptance.assess_touchstone_band` |
| pattern acceptance | `antopt.pattern_acceptance.assess_ring_pattern` |
| combined verdict | `antopt.acceptance` |

## Real-run contract

1. Configure `ANTOPT_AEDT_EXE`, `ANTOPT_GUARD_REPO`, `ANTOPT_VENV_PYTHON`,
   `ANTOPT_REFERENCE_PROJECT`, `ANTOPT_CAMPAIGN_ROOT`.
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
