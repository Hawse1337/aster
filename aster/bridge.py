"""Bridges to the AEDT guard service.

The design system never talks to AEDT directly. It submits candidates to a
guard service that owns the simulation discipline (preflight, tokens, save and
convergence gates, daemon, audit). Two transports are supported:

* ``InProcessBridge`` — imports the installed guard package in this Python
  process (fast, used on the machine that has the patch installed);
* ``StdioBridge`` — starts the guard MCP server as a subprocess and speaks MCP
  over stdio (fully decoupled deployment; requires the ``mcp`` client package).

Both expose the same four calls used by the optimisation loop.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import ExitStack, asynccontextmanager
from typing import Any, Protocol


class GuardBridge(Protocol):
    def run_approved_builder(self, *, builder_id: str, project_path: str, outdir: str,
                             params: dict[str, Any], pid: int | None = None,
                             port: int | None = None, timeout: float | None = None) -> dict[str, Any]: ...

    def create_spec(self, campaign_dir: str, spec: dict[str, Any]) -> dict[str, Any]: ...

    def submit(self, **kwargs: Any) -> dict[str, Any]: ...

    def status(self, campaign_dir: str, run_id: str) -> dict[str, Any]: ...

    def archive(self, campaign_dir: str) -> dict[str, Any]: ...

    def execute_code(self, code: str, *, pid: int | None = None,
                     port: int | None = None, timeout: float | None = None) -> dict[str, Any]: ...

    def connect(self, *, pid: int | None = None, port: int | None = None) -> dict[str, Any]: ...


class InProcessBridge:
    """Imports the installed guard package (``AEDT_MCP_REPO/mcp_server.py``)."""

    def __init__(self, repo_path: str | None = None):
        repo = repo_path or os.environ.get("AEDT_MCP_REPO") or r"C:\ProgramData\ansys-aedt-mcp"
        if repo not in sys.path:
            sys.path.insert(0, repo)
        import mcp_server  # noqa: PLC0415

        self._mcp = mcp_server

    def run_approved_builder(self, *, builder_id: str, project_path: str, outdir: str,
                             params: dict[str, Any], pid: int | None = None,
                             port: int | None = None, timeout: float | None = None) -> dict[str, Any]:
        import asyncio

        result = self._mcp.run_approved_builder(builder_id=builder_id, project_path=project_path,
            outdir=outdir, params=params, pid=pid, port=port, timeout=timeout)
        return asyncio.run(result) if asyncio.iscoroutine(result) else result

    def create_spec(self, campaign_dir: str, spec: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path  # noqa: PLC0415

        path = Path(campaign_dir) / "design_spec.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"spec_path": str(path)}

    def submit(self, **kwargs: Any) -> dict[str, Any]:
        return self._mcp.submit_guarded_run(**kwargs)

    def status(self, campaign_dir: str, run_id: str) -> dict[str, Any]:
        return self._mcp.get_run_status(campaign_dir, run_id)

    def archive(self, campaign_dir: str) -> dict[str, Any]:
        return self._mcp.archive_query(campaign_dir)

    def execute_code(self, code: str, *, pid: int | None = None,
                     port: int | None = None, timeout: float | None = None) -> dict[str, Any]:
        """Run a modelling snippet in the guarded broker (never a solve)."""
        import asyncio  # noqa: PLC0415

        coro = self._mcp.run_python_code(code=code, pid=pid, port=port,
                                        **({"timeout": timeout} if timeout is not None else {}))
        if asyncio.iscoroutine(coro):
            return asyncio.run(coro)
        return coro

    def connect(self, *, pid: int | None = None, port: int | None = None) -> dict[str, Any]:
        import asyncio  # noqa: PLC0415

        coro = self._mcp.connect_to_aedt(pid=pid, port=port, non_graphical=False)
        if asyncio.iscoroutine(coro):
            return asyncio.run(coro)
        return coro

    def clear_stale_lock(self, project_path: str) -> dict[str, Any]:
        return self._mcp.clear_stale_lock(project_path)

    def resume(self, campaign_dir: str, run_id: str, *, pid: int | None = None,
               port: int | None = None) -> dict[str, Any]:
        import asyncio  # noqa: PLC0415

        coro = self._mcp.resume_guarded_run(campaign_dir, run_id, pid=pid, port=port)
        if asyncio.iscoroutine(coro):
            return asyncio.run(coro)
        return coro


class StdioBridge:
    """Speaks MCP over stdio to a guard server process (deployment transport)."""

    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ):
        repo = os.environ.get("AEDT_MCP_REPO") or r"C:\ProgramData\ansys-aedt-mcp"
        self.command = command or os.environ.get("AEDT_MCP_PYTHON") or sys.executable
        self.args = args or [os.path.join(repo, "mcp_server.py")]
        self.env = dict(os.environ)
        self.env.update(env or {})
        self._stack = None
        self._portal = None
        self._client = None

    # --- context management -------------------------------------------------
    def __enter__(self) -> "StdioBridge":
        if self._stack is not None:
            raise RuntimeError("StdioBridge is already open")
        try:
            from anyio.from_thread import start_blocking_portal
            from mcp import ClientSession, StdioServerParameters  # noqa: PLC0415
            from mcp.client.stdio import stdio_client  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "StdioBridge requires the 'mcp' client package; install it or use "
                "InProcessBridge"
            ) from exc
        @asynccontextmanager
        async def connected():
            params = StdioServerParameters(command=self.command, args=self.args, env=self.env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

        # The two MCP context managers own AnyIO cancel scopes. Enter and
        # exit them in one task, including when the GUI calls from a worker.
        with ExitStack() as stack:
            portal = stack.enter_context(start_blocking_portal())
            client = stack.enter_context(portal.wrap_async_context_manager(connected()))
            self._portal = portal
            self._client = client
            self._stack = stack.pop_all()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._stack is not None:
            try:
                self._stack.__exit__(*exc)
            finally:
                self._stack = self._portal = self._client = None

    # --- calls --------------------------------------------------------------
    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("StdioBridge must be used as a context manager")
        result = self._portal.call(self._client.call_tool, name, arguments)
        text = "".join(getattr(item, "text", "") or "" for item in result.content)
        if result.isError:
            raise RuntimeError(f"guard tool {name} failed: {text[:400]}")
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            return structured
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    def create_spec(self, campaign_dir: str, spec: dict[str, Any]) -> dict[str, Any]:
        metrics = spec.get("metrics") or []
        objectives = spec.get("objectives") or []
        result = self._call(
            "spec_create",
            {
                "campaign_dir": campaign_dir,
                "project": spec["project"],
                "design_type": spec["design_type"],
                "frequency_ghz": list(spec["frequency_ghz"]),
                "metrics": metrics,
                **({"objectives": objectives} if objectives else {}),
                "evaluation": spec.get("evaluation"),
                "execution_policy": spec.get("execution_policy"),
            },
        )
        if objectives and result.get("spec", {}).get("objectives") != objectives:
            raise RuntimeError("Guard spec_create did not preserve the requested objectives")
        return result

    def run_approved_builder(self, *, builder_id: str, project_path: str, outdir: str,
                             params: dict[str, Any], pid: int | None = None,
                             port: int | None = None, timeout: float | None = None) -> dict[str, Any]:
        return self._call("run_approved_builder", {"builder_id": builder_id,
            "project_path": project_path, "outdir": outdir, "params": params,
            "pid": pid, "port": port, "timeout": timeout})

    def submit(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("submit_guarded_run", kwargs)

    def status(self, campaign_dir: str, run_id: str) -> dict[str, Any]:
        return self._call("get_run_status", {"campaign_dir": campaign_dir, "run_id": run_id})

    def archive(self, campaign_dir: str) -> dict[str, Any]:
        return self._call("archive_query", {"campaign_dir": campaign_dir})

    def execute_code(self, code: str, *, pid: int | None = None,
                     port: int | None = None, timeout: float | None = None) -> dict[str, Any]:
        arguments: dict[str, Any] = {"code": code}
        if pid is not None:
            arguments["pid"] = pid
        if port is not None:
            arguments["port"] = port
        if timeout is not None:
            arguments["timeout"] = timeout
        return self._call("run_python_code", arguments)

    def connect(self, *, pid: int | None = None, port: int | None = None) -> dict[str, Any]:
        arguments: dict[str, Any] = {"non_graphical": False}
        if pid is not None:
            arguments["pid"] = pid
        if port is not None:
            arguments["port"] = port
        return self._call("connect_to_aedt", arguments)

    def clear_stale_lock(self, project_path: str) -> dict[str, Any]:
        return self._call("clear_stale_lock", {"project_path": project_path})

    def resume(self, campaign_dir: str, run_id: str, *, pid: int | None = None,
               port: int | None = None) -> dict[str, Any]:
        arguments: dict[str, Any] = {"campaign_dir": campaign_dir, "run_id": run_id}
        if pid is not None:
            arguments["pid"] = pid
        if port is not None:
            arguments["port"] = port
        return self._call("resume_guarded_run", arguments)


def make_bridge(kind: str = "inprocess", **kwargs: Any) -> Any:
    if kind == "inprocess":
        return InProcessBridge(**kwargs)
    if kind == "stdio":
        return StdioBridge(**kwargs)
    raise ValueError(f"unknown bridge kind: {kind}")


class OpenedStdioBridge:
    """A StdioBridge whose session is opened once and kept open.

    `StdioBridge` is a context manager because it owns a server subprocess. The run manager keeps
    one bridge for its lifetime, so this wraps it: the first `connect` enters the context, the rest
    of the calls are forwarded, and `close` exits it.
    """

    def __init__(self, bridge: Any = None):
        self._bridge = bridge
        self._context = None

    def _open(self) -> Any:
        if self._bridge is not None:
            return self._bridge
        from .bridge import StdioBridge

        self._context = StdioBridge()
        self._bridge = self._context.__enter__()
        return self._bridge

    def connect(self, **kwargs: Any) -> Any:
        return self._open().connect(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._open(), name)

    def close(self) -> None:
        if self._context is not None:
            try:
                self._context.__exit__(None, None, None)
            finally:
                self._context = None
                self._bridge = None
