---
name: aster
description: Physics-guided antenna design and optimisation library and agent skill. Use when an agent must define a parameterized antenna design space, run dry or real AEDT-backed design studies through a guarded solver, keep measured artifacts as evidence, apply explicit bandwidth/pattern acceptance rules, or re-score recorded runs without re-solving. The web GUI is not included; real runs require the ansys-aedt-mcp guard plus AEDT 2026 R1.
---

# aster · antenna design bench

`aster` is a design bench for antenna studies: a Python library, a CLI and this agent skill in one
repository. The bench keeps every solve's artifacts so a changed requirement re-judges history
instead of re-solving it.

The skill is thin on purpose: it tells the agent **when** to use the bench, **where** the APIs are,
and **which evidence rules** to follow. The code lives in the package; the workflow references are in
[`references/`](references/) and runnable examples in [`scripts/`](scripts/).

## Install this skill

```powershell
# with the package installed:
aster skill install            # → %USERPROFILE%\.dsh\skills\aster

# or from a checkout without installing:
Copy-Item -Recurse skills\aster "$env:USERPROFILE\.dsh\skills\aster"
```

For another agent platform, keep the same layout: `SKILL.md` with YAML front matter (`name`,
`description`), a `references/` folder and a `scripts/` folder; point the agent at this directory.

## When to use it

- Define a parameter space, objectives and constraints for a parameterized antenna design.
- Run a dry (analytic/surrogate) study without AEDT for loop wiring and smoke tests.
- Submit real candidates to AEDT through the `ansys-aedt-mcp` guard and keep the raw artifacts.
- Apply explicit acceptance rules (S11 band, far-field ring pattern) and record verdicts.
- Re-score recorded runs when the target changes, without starting the solver.

Do **not** use it for GUI/HTTP work: the GUI is under validation and is not published.

## Quick checks

```powershell
aster version
aster doctor                  # what is missing before a real campaign
aster dry-demo --samples 64
aster cases
```

## The loop

| station | API | reference |
|---|---|---|
| Design | `aster.core.ParamSpec`, `ParamSpace`, `Objective`, `Constraint` | [references/api-map.md](references/api-map.md) |
| Solve | `aster.runtime.runner.RealRunManager` + guard session | [references/aedt-contract.md](references/aedt-contract.md) |
| Keep | run `artifacts/result.s1p`, `farfield_views.json`, native PNGs | [references/evidence-rules.md](references/evidence-rules.md) |
| Judge | `aster.band_acceptance.assess_touchstone_band`, `aster.pattern_acceptance.assess_ring_pattern` | [references/api-map.md](references/api-map.md) |
| Re-target | re-score the archive; no solver call | [references/evidence-rules.md](references/evidence-rules.md) |

Runnable starter: [`scripts/dry_demo.py`](scripts/dry_demo.py).

## Non-negotiables

- A dry/surrogate result is `PREDICTED`, never "measured".
- Keep the artifacts beside the run; a metric without its artifact is not evidence.
- A failed solve is recorded as failed, with its reason — never silently dropped.
- When quoting a measured number, state the AEDT/PyAEDT version and the run id.

## License

MIT. Library needs NumPy only; extras add the optimiser, surrogate, acquisition and plotting stack.
