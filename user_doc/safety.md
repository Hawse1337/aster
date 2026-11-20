# Runtime safety and the guard boundary

`aster` is the design-side library. Real AEDT solves are executed by the companion guard
[ansys-aedt-mcp](https://github.com/Hawse1337/ansys-aedt-mcp), and the two are designed to be used
together with a fail-closed policy.

## What the guard enforces (v0.3.2+)

- **Managed sessions only**: AEDT sessions launched through the guard are recorded; attaching to a
  session the guard did not launch is refused. `guard_status` classifies every live `ansysedt.exe`
  as managed or unmanaged.
- **No arbitrary broker code by default**: `run_python_code` / `run_python_script` are refused at the
  MCP surface and the worker backend unless a human operator explicitly enables them.
- **Token-scoped solves**: when `AEDT_GUARD_ENFORCE=1`, analysis submissions need a run token bound
  to project/design/setup; guarded-run adapter commands are audited.
- **Launch/release audit** and PyAEDT app identity checks against the verified desktop.

## Client rule (mandatory)

If the guard MCP tools (`mcp__aedt__*` in DeepSeek Harness, `mcp__ansys-aedt__*` in Codex) are not
available, **stop**. Do not launch `ansysedt.exe`, do not import PyAEDT, and do not edit `.aedt`
files. Ask the operator to enable the guard. Ready-to-use fail-closed configurations live in the
guard repository under `examples/clients/` (DeepSeek Harness MCP bundle and Codex config/AGENTS).

## aster's trusted bridge

`aster` executes its modelling snippets through the guard, which refuses `run_python_code` by
default. The aster bridge therefore enables `AEDT_MCP_ALLOW_ARBITRARY_CODE` only for the duration
of its own trusted broker call (the child broker inherits it and the environment is restored
afterwards); DSH and Codex MCP servers are separate processes and stay default-deny. Do not
co-host aster and DSH/Codex in one Python process: that would share the environment and defeat
the scoping. This is an operator-side trusted-tool decision, not something an agent should enable
for a client session.

## Residual risk

The guard is not an OS sandbox. A same-user process with unrestricted shell can still start AEDT
directly; the guard will mark that session unmanaged and refuse to attach, but cannot physically
prevent it. For a hard boundary, run the solver under a separate account or VM with
AppLocker/WDAC and expose only the guard service. The guard registry and audit log are
same-user-writable integrity controls, not a security boundary.

See the guard's [THREAT_MODEL](https://github.com/Hawse1337/ansys-aedt-mcp/blob/main/docs/THREAT_MODEL.md)
and [CLIENTS](https://github.com/Hawse1337/ansys-aedt-mcp/blob/main/docs/CLIENTS.md) for the full
contract.
