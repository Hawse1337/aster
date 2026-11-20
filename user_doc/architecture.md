# Architecture

`aster` is split into small layers that can be used independently.

## Core contracts (`aster/core.py`, `aster/optimize.py`)

| concept | role |
|---|---|
| `ParamSpec` / `ParamSpace` | one numeric design variable and the joint search space |
| `Objective` | a metric kind plus a maximise/minimise direction |
| `Constraint` | a feasibility rule over metrics |
| `Evaluation` | one candidate: parameters, metrics, feasibility, violations, run id |
| `pareto_archive` / `fast_non_dominated_sort` | population-level comparison |

Metric kinds are strings (`peak_gain`, `s11`, `s11_bandwidth`, `ripple`, `sll`, `beam_theta`, …) so
the acceptance layer can be replaced without changing the recorded runs.

## Campaign layer (`aster/campaign.py`, `aster/campaigns.py`)

A campaign owns:

- the case (`CaseSpec`: parameter space, objectives, constraints, reference project, setup name);
- the evaluator (dry model or guarded real runner);
- a budget and a stopping rule;
- the run archive (SQLite job store under `aster/runtime/store.py`).

Campaign recipes are plain Python: generate candidates, evaluate, update the surrogate, choose the
next batch, repeat. The same recipe runs with the dry evaluator or the real one.

## Runtime engine (`aster/runtime/`)

`runtime/runner.py` (real-run manager) and `runtime/store.py` (job store) are the measured-run
engine. They are the same code the (unpublished) GUI uses, but they do not depend on a web server.
`aster/worker.py` wraps the manager for command-line campaign runs.

## Measurement and acceptance

- `aster/farfield.py` exports and reads far-field views; `pattern_acceptance.py` evaluates ring
  patterns (main-lobe elevation, ripple, axial leakage, power fraction).
- `aster/band_acceptance.py` evaluates S11 bandwidth from Touchstone files.
- `aster/acceptance.py` combines the two into a verdict for a run.

## Surrogates (`aster/surrogate/`)

Gaussian-process and multi-fidelity models with explicit validation state. The library does not hide
whether a prediction was validated; acquisition decisions see that state.

## What is not here

The local web GUI (`aster.gui.server`, static assets), the desktop launcher (`aster/app.py`),
PyInstaller/Inno packaging and the HTTP API are intentionally excluded while the GUI is under
validation.
