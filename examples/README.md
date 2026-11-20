# Examples

- [`dry_run_demo.py`](dry_run_demo.py) — a complete dry study with no AEDT: Latin-hypercube design,
  analytic objective, Pareto archive.
- [`aedt_campaign.md`](aedt_campaign.md) — the real-run workflow through the guard system.

Run the dry example from the repository root:

```powershell
python examples/dry_run_demo.py --samples 128
```

## Using the library directly

```python
from aster.core import ParamSpace, ParamSpec, latin_hypercube
from aster.optimize import Evaluation, pareto_archive

space = ParamSpace([ParamSpec("r", "mm", 30.0, 70.0, 50.0)])
candidates = latin_hypercube(space, 32, seed=1)
```

The library is importable without AEDT; only the measured path needs the guard system.
