# Validation and claims

This release separates what was verified from what is design intent.

## Verified in this checkout

- Offline test suite: **778 passed, 80 subtests, 1 skipped** (`python -m pytest tests -q`), with
  NumPy/SciPy/scikit-learn/matplotlib and the optional optimiser installed.
- CLI smoke checks: `python -m aster version`, `python -m aster cases`,
  `python -m aster dry-demo --samples 64`.
- Package build: wheel and sdist are built from this tree and attached to the GitHub release.
- The guarded AEDT path is exercised on AEDT 2026 R1 with the guard repository (PyAEDT 1.10.0);
  see the guard's own validation notes for the session-level checks.

## Not claimed

- No single solver result is presented as universally valid. Antenna results depend on the project,
  mesh, solver settings and machine; `aster` records each run's artifacts so results can be
  inspected per candidate.
- The web GUI, desktop installer and HTTP API are not part of this release and are not validated
  here.
- The build matrix is currently Windows + CPython 3.10+; Linux has not been validated with AEDT.

## Reproducing the offline suite

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[optimizer,surrogate,plot,dev]"
.\.venv\Scripts\python.exe -m pytest tests -q
```

The suite is deterministic (fixed seeds) and does not start AEDT.
