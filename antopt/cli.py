"""Small command line entry point for the public ``antopt`` package."""
from __future__ import annotations

import argparse
import json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="antopt", description="Physics-guided antenna optimisation library")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="print the version")
    cases = sub.add_parser("cases", help="list the registered design cases")
    cases.add_argument("--json", action="store_true")
    demo = sub.add_parser("dry-demo", help="run a small dry optimisation on a synthetic objective")
    demo.add_argument("--samples", type=int, default=64)

    args = parser.parse_args(argv)
    from .version import __version__

    if args.version or args.command == "version":
        print(__version__)
        return 0
    if args.command == "cases":
        from .registry_bootstrap import ensure_registry
        from .parametric.registry import list_cases

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
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
