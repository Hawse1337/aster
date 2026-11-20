# antopt user guide

`antopt` is a library for simulation-driven antenna design studies. This guide covers installation,
the core concepts, the campaign workflow and the AEDT integration contract.

- [Installation](installation.md)
- [Architecture](architecture.md)
- [Campaign workflow](workflow.md)
- [AEDT integration](aedt_integration.md)
- [Validation and claims](validation.md)

The web GUI and desktop packaging are not part of this release; only the library, its offline tests
and the guarded-run integration are published.

## Five-minute tour

1. Install the package and the optional extras (see [installation](installation.md)).
2. Run the dry demo:

   ```powershell
   python -m antopt dry-demo --samples 64
   ```

3. Read [`examples/dry_run_demo.py`](../examples/dry_run_demo.py) and
   [`examples/aedt_campaign.md`](../examples/aedt_campaign.md).
4. Define a parameter space with `ParamSpec`/`ParamSpace`, describe acceptance rules with an
   `Objective` list, and evaluate candidates with an `Evaluation` population.
5. For real runs, install the guard system and follow [AEDT integration](aedt_integration.md).
