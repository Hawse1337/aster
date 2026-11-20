# Installation

Python 3.10+ is required. The library itself needs only NumPy; the optional extras add the
optimiser, surrogate and plotting stack.

## From the repository

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[optimizer,surrogate,plot,dev]"
```

## From a wheel

```powershell
python -m pip install antopt-1.51.0-py3-none-any.whl[optimizer,surrogate]
```

## Optional dependencies

| extra | packages | used for |
|---|---|---|
| `optimizer` | `pymoo`, `scipy` | NSGA-II and numeric helpers |
| `surrogate` | `scikit-learn` | Gaussian-process surrogates |
| `acquisition` | `torch`, `botorch`, `gpytorch` | BoTorch acquisition policies |
| `plot` | `matplotlib` | result plots |
| `dev` | `pytest` | offline test suite |

## Verify the install

```powershell
python -m antopt version        # 1.51.0
python -m pytest tests -q       # offline suite, no AEDT needed
```

For measured runs you additionally need an AEDT installation and the companion
[ansys-aedt-mcp](https://github.com/Hawse1337/ansys-aedt-mcp) guard, including its virtual
environment with PyAEDT 1.10.0 (see [AEDT integration](aedt_integration.md)).
