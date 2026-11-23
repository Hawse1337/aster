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
runner.record_baseline("baseline-run-id", {"project": r"C:\...\baseline.aedt"})
screening = runner.run_screening()       # LHS/DoE through the approved builder
search = runner.run_search()             # NSGA-II through the approved builder
runner.record_acceptance({"passed": True, "source": "full-model replay"})
runner.record_delivery(r"C:\...\report.md")
print(runner.gate())
```

The screening and search paths use `ApprovedBuilderExecutor` and the
`run_approved_builder` guard capability; they never require arbitrary code.

## What this fixes

- the agent cannot call a hand-written loop and call it an optimization;
- every stage leaves a file the GUI/operator can inspect;
- screening vs search vs acceptance are distinct;
- an algorithm can be changed explicitly in the plan, not silently improvised.

## Current scope

- Screening (LHS / small lattice) is exercised offline.
- Search wraps `optimizer.nsga2` / `nsga3`; it requires `pymoo` and a real
  approved builder session for measured runs.
- Acceptance and delivery are evidence-recording gates; they do not invent
  acceptance data. Use `band_acceptance` / `pattern_acceptance` or an explicit
  full-model replay before recording them.
