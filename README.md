<p align="center">
  <img src="assets/banner.svg" alt="aster — an antenna design bench, evidence first" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Hawse1337/aster/actions/workflows/ci.yml"><img alt="tests" src="https://github.com/Hawse1337/aster/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/Hawse1337/aster/releases/latest"><img alt="release" src="https://img.shields.io/github/v/release/Hawse1337/aster?color=e4572e"></a>
  <a href="https://hawse1337.github.io/aster/"><img alt="docs" src="https://img.shields.io/badge/docs-online-2a9d8f"></a>
  <a href="LICENSE"><img alt="license: MIT" src="https://img.shields.io/badge/license-MIT-2a9d8f"></a>
  <img alt="python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-23262b">
</p>

# Keep the measurement. Change the target.

> **aster** was previously published as **antopt**. The package import, CLI and
> environment prefix are now `aster` / `ASTER_*`; the old repository URL redirects.

`aster` is a design bench for antenna studies. You describe a parameter space and what "good"
means, the library drives candidates through a guarded AEDT session, and every solve leaves its raw
artifacts behind. When the requirement changes — a different band, a tighter ripple, a flatter
elevation cut — you re-judge the history instead of burning another night of solver time.

> **中文简介**：[README.zh-CN.md](README.zh-CN.md)  
> The web GUI / desktop application is under validation and is **not** part of this repository.

![The five-station loop](assets/loop.svg)

## Three forms, one bench

| form | how you get it | use it when |
|---|---|---|
| **Python library + CLI** | wheel/sdist from [Releases](https://github.com/Hawse1337/aster/releases), then `aster doctor` | you script your own loop, run CI, or work in a notebook |
| **Agent skill** | `aster skill install` (or copy `skills/aster/`) | an AI agent should run design studies with evidence rules |
| **Docs + releases** | [hawse1337.github.io/aster](https://hawse1337.github.io/aster/) | you want the contract, the workflow and pinned artifacts |

## The bench

Four verbs, one loop:

- **Design** — `ParamSpec` / `ParamSpace` describe the knobs, `Objective` / `Constraint` describe the
  target. Nothing here knows about AEDT.
- **Solve** — a candidate becomes a real run through the companion guard
  ([ansys-aedt-mcp](https://github.com/Hawse1337/ansys-aedt-mcp)). Sessions are addressed explicitly;
  budgets are spent, not assumed.
- **Keep** — S11 Touchstone, far-field grids and native report images stay with the run record. A
  number without its artifact is a rumour.
- **Judge** — acceptance rules are code (`band_acceptance`, `pattern_acceptance`, `acceptance`), so a
  verdict can be recomputed, compared and challenged.

The archive is the point. A *campaign* is just a Python recipe over that loop; swap the evaluator
(dry model or real solve) and the same recipe still runs.

## What it refuses to hide

| risk | how aster handles it |
|---|---|
| "the result looks great" | every measured run keeps `result.s1p`, `farfield_views.json` and the native report PNGs |
| "we changed the target, re-run everything" | records are re-scored; the solver is not called again unless you ask |
| "the surrogate says it is good" | dry/surrogate results are labelled `PREDICTED`; validation state is explicit |
| "the solve died, we lost the point" | failed runs are recorded as failed, with the reason, not dropped |
| "which AEDT session was that?" | the guard requires one explicit PID or gRPC port per call |

## Try it in one minute

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[optimizer,surrogate,plot]"

aster version
aster doctor              # what is missing before a real campaign
aster dry-demo --samples 64
```

`dry-demo` is a complete study on an analytic objective — Latin-hypercube design, Pareto archive,
no AEDT required. The same shape in code:

```python
from aster.core import Objective, ParamSpace, ParamSpec, latin_hypercube
from aster.optimize import Evaluation, pareto_archive

space = ParamSpace([
    ParamSpec("ring_radius", "mm", 20.0, 60.0, 40.0),
    ParamSpec("ring_height", "mm",  1.0, 10.0,  4.0),
])
objectives = [Objective("gain", "peak_gain", "maximize"),
              Objective("bw", "s11_bandwidth", "maximize")]

runs = [Evaluation("c%02d" % i, "ring", p,
                   metrics={"peak_gain": predict_gain(p),
                            "s11_bandwidth": predict_bw(p)}, feasible=True)
        for i, p in enumerate(latin_hypercube(space, 32, seed=1))]
front = pareto_archive(runs, objectives)
```

- 系统理论与优化方法（中文）：[docs/THEORY_AND_OPTIMIZATION_ZH.md](docs/THEORY_AND_OPTIMIZATION_ZH.md)

## Runtime safety, by construction

Real solves go through the companion guard
[ansys-aedt-mcp](https://github.com/Hawse1337/ansys-aedt-mcp), not through a loose PyAEDT call in
the same process:

- the guard records the AEDT sessions **it** launched and refuses to attach to unmanaged ones
  (`guard_status` classifies every live `ansysedt.exe`);
- `run_python_code` / `run_python_script` are refused by default at the guard's MCP and worker
  layers; a human operator can enable them for a supervised session;
- run tokens bind analysis submissions to project/design/setup when
  `AEDT_GUARD_ENFORCE=1`;
- launch/release events are audited and PyAEDT app objects are checked against the verified desktop.

If the guard MCP tools are missing, **stop** — do not fall back to shell-level PyAEDT or
`ansysedt.exe`. Fail-closed DSH/Codex configurations and the threat model are in the guard repo.
See [user_doc/safety.md](user_doc/safety.md) for the boundary and residual risks.

## Real studies

1. Install the guard system and its virtual environment (PyAEDT 1.10.0, AEDT 2026 R1).
2. Point `ASTER_AEDT_EXE`, `ASTER_GUARD_REPO`, `ASTER_VENV_PYTHON`,
   `ASTER_REFERENCE_PROJECT`, `ASTER_CAMPAIGN_ROOT` at your machine.
3. Run the worker against a campaign directory; each candidate gets its own run folder and archive
   row.

The contract, the run lifecycle and the artifact layout are in
[user_doc/aedt_integration.md](user_doc/aedt_integration.md), with a walkthrough in
[examples/aedt_campaign.md](examples/aedt_campaign.md).

## Guard-approved optimisation adapter

`aster.approved_builder` is the no-arbitrary-code path for the AEDT guard:
`ApprovedBuilderExecutor` submits each candidate through `run_approved_builder`, reads the
returned `result.s1p` / optional `farfield_metrics.json`, and returns an `Evaluation`.
`run_approved_campaign` writes `optimization_manifest.json` and the completion gate
(`optimization_gate.py`): an aster manifest, real completed runs, a Pareto front and an
acceptance result are required before a campaign can be called complete. The guard records
campaign/candidate provenance so its approver GUI can show whether a run came from aster or
from a manual command.

```python
from aster.approved_builder import ApprovedBuilderConfig, run_approved_campaign

config = ApprovedBuilderConfig(
    campaign_id="lens-20261123",
    campaign_dir=r"C:\...\campaign",
    builder_id="p0_model",
    project_path=r"C:\...\base.aedt",
    outdir_root=r"C:\...\runs",
    port=51679,
    design="Trunc_10GHz",
)
result = run_approved_campaign(case, config, budget=2)
print(result["gate"])
```

## Drop it into an agent

The skill lives in [`skills/aster/`](skills/aster/): `SKILL.md` plus `references/` (API map, AEDT
contract, evidence rules) and a runnable `scripts/dry_demo.py`. Install it with one command:

```powershell
aster skill install            # → %USERPROFILE%\.dsh\skills\aster
aster skill path               # print the destination
```

Or copy `skills/aster/` into your agent's skill directory by hand. The skill does **not** cover the
GUI, which is not published.

## Repository map

```text
assets/        banner + loop illustrations
aster/        the library
  core.py        parameter spaces, objectives, constraints
  optimize.py    evaluations, dominance, Pareto archive
  campaign.py    campaign recipes
  runtime/       measured-run manager + job store (no web server)
  worker.py      command-line measured campaign worker
  aedt_host.py   AEDT process helpers
  skills/aster/  bundled skill (mirrored from skills/aster at build time)
  cases/  benchmark/  interop/  optimizer/  parametric/  physics/  surrogate/  topology/
skills/aster/  canonical agent skill (SKILL.md + references + scripts)
tools/         sync_skill_bundle.py (mirror the skill into the package)
user_doc/      installation, architecture, workflow, AEDT integration, skill, validation
examples/      dry study + measured-campaign walkthrough
tests/         offline suite (no AEDT needed)
mkdocs.yml     documentation site
```

## Status

- Offline suite: **782 passed, 80 subtests**; CI runs it on Windows / CPython 3.12.
- Measured runs are exercised on AEDT 2026 R1 through the guard; any solver number quoted from a
  study must be accompanied by its run artifacts.
- Wheel and sdist: [v1.54.0 release](https://github.com/Hawse1337/aster/releases/tag/v1.54.0);
  documentation: [hawse1337.github.io/aster](https://hawse1337.github.io/aster/).

## License

MIT — see [LICENSE](LICENSE). If you build a study on top of this bench, keep the artifacts; a bench
without evidence is just furniture.
