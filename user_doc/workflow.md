# Campaign workflow

A campaign is a Python recipe with four moving parts: **case**, **evaluator**, **budget** and
**archive**.

## 1. Define the case

```python
from aster.core import CaseSpec, Constraint, FamilySpec, Objective, ParamSpec, ParamSpace

case = CaseSpec(
    name="ring_demo",
    family=FamilySpec("ring", description="radial ring on a ground plane"),
    space=ParamSpace([
        ParamSpec("ring_radius", "mm", 20.0, 60.0, 40.0),
        ParamSpec("ring_height", "mm", 1.0, 10.0, 4.0),
        ParamSpec("slot_depth", "mm", 0.0, 6.0, 2.0),
    ]),
    objectives=[
        Objective("gain", "peak_gain", "maximize"),
        Objective("bw", "s11_bandwidth", "maximize"),
    ],
    constraints=[Constraint("bw", ">=", 5.0)],
    project="<path to reference .aedt>",
    setup="Setup1",
)
```

The built-in cases under `aster/cases/` are references for real designs; their project paths are
left empty on purpose and must be configured for your own checkout.

## 2. Pick an evaluator

- **Dry evaluator** — a fitted measured surface or an analytic response. It is fast and is labelled
  `PREDICTED`; it is useful for wiring the loop and for smoke tests.
- **Real evaluator** — submits the candidate to the guard runner, solves, exports S11 and far field,
  and writes the metrics from the artifacts. It needs AEDT and the guard checkout.

## 3. Run and iterate

```python
candidates = latin_hypercube(case.space, 16, seed=1)
evaluations = [evaluator.evaluate(candidate) for candidate in candidates]
archive = pareto_archive(evaluations, case.objectives)
```

For longer studies, use the campaign driver:

```powershell
python -m aster.worker --case ring_demo --campaign <campaign-dir> --generations 8
```

The worker persists every run in `campaign.sqlite` with its parameters, metrics, status and artifact
paths.

## 4. Re-score without re-solving

Recorded runs carry their raw artifacts. A changed acceptance rule (for example a different
bandwidth definition) is applied to the archive with `band_acceptance`/`pattern_acceptance` and the
new verdicts are written next to the old ones — the solver is not called again.

## Acceptance rules

Use explicit verdict functions rather than ad-hoc thresholds. `band_acceptance.assess_touchstone_band`
and `pattern_acceptance.assess_ring_pattern` return a structured result with the measured values,
the rule and a pass/fail decision; `acceptance.py` combines them per run.
