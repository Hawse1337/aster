# Evidence rules

The bench is built on one rule: **a number is only as good as the artifact behind it.**

## Labels

| label | meaning |
|---|---|
| `MEASURED` | produced by a real AEDT solve; artifacts present |
| `PREDICTED` | produced by a dry/surrogate evaluator; no solver |
| `FAILED` | solver or export failed; reason recorded |
| `RECOVERY_REQUIRED` | solve finished but post-processing/export needs a clean rerun |

Never upgrade a label silently. A surrogate score that agrees with past measurements is still
`PREDICTED`.

## Keep with the run

- `artifacts/result.s1p` — the raw S11 export.
- `artifacts/farfield_views.json` — the far-field grid/cuts used for pattern metrics.
- `artifacts/native_reports/*.png` — the solver's own report images.
- `run.json` — parameters, status, timestamps, machine/AEDT/PyAEDT versions.

## Re-scoring

When acceptance rules change, re-evaluate the archive first. Record the new verdict next to the old
one (with the rule version), so a reviewer can see both. Only re-solve when:

- the recorded artifacts are missing or incompatible, or
- the candidate itself changes (parameters, project, setup), or
- the solver setting is part of the question (mesh, passes, frequency sweep).

## Quoting results

State: case name, parameter set (or run id), AEDT version, PyAEDT version, and where the artifacts
live. A gain number without these is not reproducible.
