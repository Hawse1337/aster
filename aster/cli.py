"""Command line entry point for the public ``aster`` package."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path


def _skill_source() -> Path:
    """Bundled skill folder (present both in a checkout and in the built wheel)."""
    return Path(__file__).resolve().parent / "skills" / "aster"


def _skill_dest() -> Path:
    override = os.environ.get("ASTER_SKILL_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".dsh" / "skills" / "aster"


def _print_skill_install(dest: Path, force: bool) -> int:
    source = _skill_source()
    if not (source / "SKILL.md").is_file():
        print("error: bundled skill not found in this installation (%s)" % source)
        print("hint: run `python tools/sync_skill_bundle.py` in a checkout and rebuild the wheel")
        return 1
    if dest.exists() and any(dest.iterdir()):
        if not force:
            print("error: %s already exists; pass --force to replace it" % dest)
            return 1
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)
    print("installed skill → %s" % dest)
    print("  SKILL.md, references/, scripts/")
    return 0


def _doctor(strict: bool = False) -> int:
    from . import config
    from .version import __version__

    env = config.discover()
    rows: list[tuple[str, str, str]] = []  # (level, check, detail)
    errors = 0
    warnings = 0

    def add(level: str, check: str, detail: str) -> None:
        nonlocal errors, warnings
        rows.append((level, check, detail))
        if level == "ERROR":
            errors += 1
        elif level == "WARN":
            warnings += 1

    version = "%d.%d" % sys.version_info[:2]
    if sys.version_info >= (3, 10):
        add("OK", "python", version)
    else:
        add("ERROR", "python", "%s (need >= 3.10)" % version)

    for module, label, optional in (
        ("numpy", "numpy", False),
        ("scipy", "scipy (optimizer extra)", True),
        ("sklearn", "scikit-learn (surrogate extra)", True),
        ("pymoo", "pymoo (optimizer extra)", True),
        ("matplotlib", "matplotlib (plot extra)", True),
        ("torch", "torch (acquisition extra)", True),
        ("botorch", "botorch (acquisition extra)", True),
        ("ansys.aedt.core", "pyaedt (guard venv)", True),
    ):
        found = importlib.util.find_spec(module) is not None
        if found:
            add("OK", label, "importable")
        elif optional:
            add("WARN", label, "not installed (optional)")
        else:
            add("ERROR", label, "missing")

    add("OK" if env.aedt_exe and Path(env.aedt_exe).is_file() else "ERROR",
        "aedt_exe", str(env.aedt_exe or "unset"))
    add("OK" if env.guard_repo and Path(env.guard_repo).is_dir() else "ERROR",
        "guard_repo", str(env.guard_repo or "unset"))
    add("OK" if env.venv_python and Path(env.venv_python).is_file() else "ERROR",
        "venv_python", str(env.venv_python or "unset"))
    add("OK" if env.reference_project and Path(env.reference_project).is_file() else "ERROR",
        "reference_project", str(env.reference_project or "unset"))
    add("OK" if env.campaign_root else "WARN",
        "campaign_root", str(env.campaign_root or "unset"))
    add("OK", "aster", "v%s" % __version__)

    width = max(len(check) for _, check, _ in rows) + 2
    print("aster doctor — resolved environment")
    print("-" * (width + 60))
    for level, check, detail in rows:
        print("%-5s %-*s %s" % (level, width, check, detail))
    print("-" * (width + 60))
    print("%d error(s), %d warning(s)" % (errors, warnings))
    if errors:
        print("real runs need the ERROR items; see user_doc/aedt_integration.md")
    return 1 if (errors and strict) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aster", description="Physics-guided antenna design and optimisation bench")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="print the version")

    cases = sub.add_parser("cases", help="list the registered design cases")
    cases.add_argument("--json", action="store_true")

    demo = sub.add_parser("dry-demo", help="run a small dry optimisation on a synthetic objective")
    demo.add_argument("--samples", type=int, default=64)

    from .design_study import cli as study_arguments
    study_arguments(sub.add_parser("design-study", help="plan budgeted measurements for a versioned design profile"))

    doctor = sub.add_parser("doctor", help="check the environment needed for real runs")
    doctor.add_argument("--strict", action="store_true", help="exit non-zero on errors")

    skill = sub.add_parser("skill", help="manage the bundled agent skill")
    skill_sub = skill.add_subparsers(dest="skill_command")
    install = skill_sub.add_parser("install", help="copy the skill into an agent skill directory")
    install.add_argument("--dest", default=None, help="destination (default: ~/.dsh/skills/aster)")
    install.add_argument("--force", action="store_true", help="replace an existing installation")
    skill_sub.add_parser("path", help="print the default skill destination")
    skill_sub.add_parser("show", help="print the bundled skill location")

    args = parser.parse_args(argv)
    from .version import __version__

    if args.version or args.command == "version":
        print(__version__)
        return 0
    if args.command == "cases":
        from .parametric.registry import list_cases
        from .registry_bootstrap import ensure_registry

        ensure_registry()
        names = sorted(list_cases())
        if args.json:
            print(json.dumps(names, ensure_ascii=False, indent=2))
        else:
            for name in names:
                print(name)
        return 0
    if args.command == "dry-demo":
        from .examples_support import dry_demo

        return dry_demo(args.samples)
    if args.command == "design-study":
        from .design_study import run_cli
        try:
            return run_cli(args)
        except (ImportError, KeyError, ValueError, TypeError, OSError) as exc:
            parser.exit(2, f"{exc}\n")
    if args.command == "doctor":
        return _doctor(strict=args.strict)
    if args.command == "skill":
        command = args.skill_command
        if command == "install":
            dest = Path(args.dest).expanduser() if args.dest else _skill_dest()
            return _print_skill_install(dest, force=args.force)
        if command == "path":
            print(_skill_dest())
            return 0
        if command == "show":
            print(_skill_source())
            return 0
        skill.print_help()
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
