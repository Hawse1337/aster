"""One place for every path the system needs, so nothing is hardcoded in a script.

The tool drives three external things: AEDT itself, the guard checkout (whose virtual environment
holds PyAEDT), and a scratch area for campaigns. Each was a constant inside some script, which
means moving the tool to another machine - or another AEDT version - meant editing code. This
module discovers what it can, reads ``config.toml`` for what it cannot, and lets environment
variables override both, in that order of increasing precedence:

    discovery  <  config file  <  environment

Everything is optional: :meth:`Environment.validate` reports what is missing and why it matters,
and callers decide whether that is fatal.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

__all__ = ["Environment", "discover", "load", "default_config_path", "AEDT_DIR_GLOBS",
           "ENV_PREFIX"]

ENV_PREFIX = "ANTOPT_"
"""``ANTOPT_AEDT_EXE`` overrides ``aedt_exe``, and so on for every field."""

AEDT_DIR_GLOBS = (
    r"D:\ANSYS Inc\v*\AnsysEM\ansysedt.exe",
    r"C:\Program Files\ANSYS Inc\v*\AnsysEM\ansysedt.exe",
    r"C:\Program Files\Ansys Inc\v*\AnsysEM\ansysedt.exe",
)
"""Where an AEDT installation is looked for, newest version first."""

GUARD_CANDIDATES = (
    r"C:\ProgramData\ansys-aedt-mcp",
)
CAMPAIGN_CANDIDATES = ()
"""Optional discovery roots; configure them with the ANTOPT_* environment variables."""
YADOF_CANDIDATES = ()
"""Optional discovery roots; configure them with the ANTOPT_* environment variables."""
PROJECT_CANDIDATES = ()
"""Optional discovery roots; configure them with the ANTOPT_* environment variables."""


@dataclass
class Environment:
    """Resolved locations for one machine, plus the version of the tool itself."""

    aedt_exe: Path | None = None
    guard_repo: Path | None = None
    venv_python: Path | None = None
    reference_project: Path | None = None
    campaign_root: Path | None = None
    yadof_workspace: Path | None = None
    aedt_version: str = ""
    source: dict[str, str] = field(default_factory=dict)
    """Where each value came from: ``discovered``, ``config`` or ``env``."""

    # -- derived ---------------------------------------------------------
    @property
    def guard_venv(self) -> Path | None:
        return self.venv_python.parent.parent if self.venv_python else None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for item in fields(self):
            if item.name == "source":
                continue
            value = getattr(self, item.name)
            payload[item.name] = str(value) if isinstance(value, Path) else value
        payload["source"] = dict(self.source)
        return payload


def _version_key(path: Path) -> tuple[int, ...]:
    """Sort key from the ``v261`` segment of an AEDT path, so the newest wins."""
    digits = "".join(character for character in path.parent.parent.name if character.isdigit())
    try:
        return tuple(int(character) for character in digits) or (0,)
    except ValueError:
        return (0,)


def _first_existing(candidates) -> Path | None:
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _find_aedt() -> Path | None:
    import glob  # noqa: PLC0415 - only used during discovery

    found: list[Path] = []
    for pattern in AEDT_DIR_GLOBS:
        for match in glob.glob(pattern):
            path = Path(match)
            if path.is_file():
                found.append(path)
    if not found:
        return None
    return sorted(found, key=_version_key)[-1]


def _find_guard() -> Path | None:
    for candidate in GUARD_CANDIDATES:
        path = Path(candidate)
        if (path / ".venv" / "Scripts" / "python.exe").is_file():
            return path
    return None


def discover() -> Environment:
    """Best effort: fill in whatever exists on this machine."""
    env = Environment()
    aedt = _find_aedt()
    if aedt:
        env.aedt_exe = aedt
        env.aedt_version = aedt.parent.parent.name
        env.source["aedt_exe"] = "discovered"

    guard = _find_guard()
    if guard:
        env.guard_repo = guard
        env.venv_python = guard / ".venv" / "Scripts" / "python.exe"
        env.source["guard_repo"] = "discovered"
        env.source["venv_python"] = "discovered"

    project = _first_existing(PROJECT_CANDIDATES)
    if project:
        env.reference_project = project
        env.source["reference_project"] = "discovered"

    campaigns = _first_existing(CAMPAIGN_CANDIDATES)
    if campaigns:
        env.campaign_root = campaigns
        env.source["campaign_root"] = "discovered"

    yadof = _first_existing(YADOF_CANDIDATES)
    if yadof:
        env.yadof_workspace = yadof
        env.source["yadof_workspace"] = "discovered"
    return env


def default_config_path() -> Path:
    """``%APPDATA%\\antopt\\config.toml`` (or the XDG equivalent), overridable."""
    override = os.environ.get(ENV_PREFIX + "CONFIG")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if appdata:
        return Path(appdata) / "antopt" / "config.toml"
    return Path.home() / ".config" / "antopt" / "config.toml"


def _read_toml(path: Path) -> dict[str, Any]:
    import tomllib  # noqa: PLC0415 - 3.11+

    with path.open("rb") as handle:
        data = tomllib.load(handle)
    # accept both a flat table and a [paths] section
    merged: dict[str, Any] = {}
    for key, value in (data.get("paths") or {}).items():
        merged[key] = value
    for key, value in data.items():
        if key != "paths" and not isinstance(value, dict):
            merged[key] = value
    return merged


def _apply_mapping(env: Environment, mapping: dict[str, Any], origin: str) -> Environment:
    known = {item.name for item in fields(Environment)} - {"source"}
    for key, value in mapping.items():
        if key not in known or value in (None, ""):
            continue
        if key in {"aedt_version"}:
            setattr(env, key, str(value))
            env.source[key] = origin
            continue
        path = Path(str(value)).expanduser()
        setattr(env, key, path)
        env.source[key] = origin
    return env


def parse_frequency_list(text: str | None) -> tuple[float, ...]:
    """Parse a comma/semicolon separated GHz list; invalid entries raise ValueError.

    Both the GUI and the queue worker accept --verify-frequencies, so the parsing lives here
    instead of in either CLI: a typo must fail loudly rather than silently disabling a check.
    """
    values: list[float] = []
    for item in str(text or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        value = float(item)
        if value <= 0:
            raise ValueError("frequencies must be positive: %r" % item)
        values.append(value)
    return tuple(values)


def load(path: Path | str | None = None) -> Environment:
    """Discover, then overlay the config file, then the environment variables."""
    env = discover()
    config_path = Path(path) if path is not None else default_config_path()
    if config_path.is_file():
        _apply_mapping(env, _read_toml(config_path), "config")
        env.source["config_file"] = str(config_path)

    overrides = {}
    for item in fields(Environment):
        if item.name == "source":
            continue
        value = os.environ.get(ENV_PREFIX + item.name.upper())
        if value:
            overrides[item.name] = value
    if overrides:
        _apply_mapping(env, overrides, "env")
    return env


def write(path: Path | str, env: Environment) -> Path:
    """Write the environment as TOML so it can be edited by hand or by the wizard."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# antopt configuration - written by the first-run wizard", "[paths]"]
    for item in fields(Environment):
        if item.name in {"source", "aedt_version"}:
            continue
        value = getattr(env, item.name)
        if value:
            lines.append('%s = "%s"' % (item.name, str(value).replace("\\", "\\\\")))
    lines.append("aedt_version = %s" % ('"%s"' % env.aedt_version if env.aedt_version else '""'))
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def validate(env: Environment) -> list[str]:
    """Human-readable problems, most important first. Empty means everything is usable."""
    problems: list[str] = []
    if not env.aedt_exe or not env.aedt_exe.is_file():
        problems.append("AEDT executable not found: set aedt_exe in the config file")
    if not env.guard_repo or not env.guard_repo.is_dir():
        problems.append("guard checkout not found: set guard_repo (needs .venv with PyAEDT)")
    elif not env.venv_python or not env.venv_python.is_file():
        problems.append("virtual environment python not found: set venv_python to "
                        "<guard_repo>\\.venv\\Scripts\\python.exe")
    if not env.reference_project or not env.reference_project.is_file():
        problems.append("no reference .aedt project: set reference_project")
    if not env.campaign_root:
        problems.append("campaign_root is unset: set it to a writable directory")
    return problems


def describe(env: Environment) -> str:
    """A short block for the console and the log file."""
    lines = ["antopt environment:"]
    for item in fields(Environment):
        if item.name == "source":
            continue
        value = getattr(env, item.name)
        if value:
            origin = env.source.get(item.name, "")
            lines.append("  %-18s %s%s" % (item.name, value, "  (%s)" % origin if origin else ""))
    problems = validate(env)
    if problems:
        lines.append("  missing:")
        lines.extend("    - %s" % problem for problem in problems)
    return "\n".join(lines)
