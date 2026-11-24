# Standard optimisation workflow

`aster.opt_workflow` fixes the *process* around an optimisation study.  The
algorithm remains selectable, but an agent cannot silently skip baseline,
screening evidence or the completion gate.

## Plan contract

A study starts with a machine-readable plan, for example
[`examples/optimization_workflow_plan.json`](../examples/optimization_workflow_plan.json):

```json
{
  "case_id": "lens2g4_10g",
  "route": "structural_search",
  "strategy": {
    "screening": "lhs",
    "optimizer": "nsga2",
    "verification": "full_model"
  },
  "budget": {
    "screening_candidates": 8,
    "optimizer_evaluations": 24,
    "full_model_runs": 4
  },
  "parameter_space": {"H_RIM": {"low": 0.15, "high": 1.56, "step": 0.05}},
  "objectives": [{"kind": "peak_gain", "direction": "maximize"}],
  "constraints": [{"kind": "s11", "operator": "<=", "target": -10.0}]
}
```

`load_plan()` validates the plan against a `CaseSpec`: case id, parameter names,
objective kinds and stage/strategy names must match.

## Stage machine

```text
definition -> baseline -> screening -> search -> acceptance -> delivery
```

The runner records each stage and its artifacts in
`<campaign_dir>/optimization_workflow.json`:

| Stage | Required evidence |
|---|---|
| baseline | real baseline run id |
| screening | campaign dir, manifest, gate |
| search | NSGA archive, manifest, gate |
| acceptance | acceptance report |
| delivery | delivery report path |

Screening cannot run before a baseline is recorded. Search cannot run before
screening evidence exists. The final `gate()` reports every missing stage.

## Usage

```python
from aster.approved_builder import ApprovedBuilderConfig
from aster.opt_workflow import WorkflowRunner, load_plan

plan = load_plan(r"examples/optimization_workflow_plan.json", case=case)
# final_profile is the case owner's measured band and/or angular coverage
# specification, with the approved full-model recipe_id from the guard history.
plan.verification_profile = final_profile
config = ApprovedBuilderConfig(
    campaign_id="lens-20261123",
    campaign_dir=r"C:\...\campaign",
    builder_id="p0_model",
    project_path=r"C:\...\base.aedt",
    outdir_root=r"C:\...\runs",
    port=50175,
    design="Trunc_10GHz",
)
runner = WorkflowRunner(case, config, plan)
runner.record_baseline("baseline-run-id", {"campaign_dir": r"C:\...\baseline-campaign"})
screening = runner.run_screening()       # LHS/DoE through the approved builder
search = runner.run_search()             # NSGA-II through the approved builder
runner.record_acceptance({"campaign_dir": r"C:\...\full-model-campaign", "run_id": full_model_run_id})
runner.record_delivery(r"C:\...\report.md")
print(runner.gate())
```

The screening and search paths use `ApprovedBuilderExecutor` and the
`run_approved_builder` guard capability; they never require arbitrary code.

## What this fixes

- the runner relates its own search records to an explicit campaign and plan;
- every stage leaves a file the GUI/operator can inspect;
- screening vs search vs acceptance are distinct;
- an algorithm can be changed explicitly in the plan, not silently improvised.


### Projects that need a real solve

The stock `ApprovedBuilderExecutor` only calls the approved builder (build/save). For a project that
must submit a guarded HFSS solve and export native results, use the optional
`aster.solve_adapter.GuardedSolveExecutor`:

```python
from aster.solve_adapter import GuardedSolveConfig, GuardedSolveExecutor

solve = GuardedSolveConfig(
    guard_dir=r"C:\ProgramData\ansys-aedt-mcp",
    design="Trunc_10GHz",
    fixed_params={"gap_mm": 0.384},
    frequencies_ghz=(5.0, 8.0),
    num_tasks=1,
)
runner = WorkflowRunner(
    case, config, plan,
    executor_factory=lambda c, cfg: GuardedSolveExecutor(c, cfg, solve, bridge=bridge),
)
```

The adapter still goes through `submit_guarded_run`/the guard bridge, waits for a terminal run,
copies `result.s1p` and far-field files, and records the guard campaign/run identity in
`Evaluation.origin`. A project-specific spec/metric mapping can be supplied with
`spec_factory`/`metric_hook`. `run_approved_campaign(..., executor=...)` accepts the same object.
## Evidence registration and native sampling

For a guarded run that was launched by a project adapter, record it with
`workflow_evidence.attach_run(...)` instead of hand-writing `real_runs.jsonl`.
It writes the real-job identity, the pre-solve `case_contract` recipe, builder
SHA and an archive entry; `replay_selected` remains the authority for the
verdict.

`GuardedSolveConfig.run_params` is copied into the guarded submit `params`
(e.g. `symmetry="none"` plus recipe/design metadata). Put geometry-fixed values
in `fixed_params` for the builder and the same identity fields in `run_params`
for the guard run; a project-native check can then verify the full-model
geometry from `run.json`.

True native 5° patterns require the builder's far-field sampling parameter
(`ff_step_deg`) to be approved once and then supplied as a normal builder
parameter. Exported reports cannot turn a 10° solved sphere into true 5° data.
## Current scope

- Screening (LHS / small lattice) is exercised offline.
- Search wraps `optimizer.nsga2` / `nsga3`; it requires `pymoo` and a real
  approved builder session for measured runs.
- A project-specific plan can declare `verification_profile` with the exact
  full-model `recipe_id`, optional `builder_sha256`, and `band` and/or `coverage`
  using the band and angular acceptance specifications. A registered project
  `native_check` can implement a discrete-frequency or project-specific pattern
  requirement. Every declared check must pass. A positive verdict
  needs a completed matching geometry, guarded run and worker record. The
  recipe identity must identify the intended physical model. The acceptance
  operation reads an existing full-model campaign and spends no solver budget.
- A negative or incomplete result may be delivered as a diagnostic report.
  `gate()["workflow_complete"]` describes process evidence and `gate()["passed"]`
  also requires the baseline guard evidence and a reproducible positive replay.
  `optimizer="none"` permits a screening-only study to verify a screened point.
- The approved adapter reads exports produced during one builder attempt.
  A builder that returns only a saved project needs an additional guarded
  solve/export recipe before it can supply measured outcomes.
- An interrupted externally submitted stage is recorded as `in_flight`. Consult
  the guard ledger and stage manifest before allocating the unused budget to a
  new campaign revision. Process termination does not cancel an external solve.



