# aster skill

This folder is the agent-skill form of the [aster](https://github.com/Hawse1337/aster) antenna
design bench.

```text
skills/aster/
├── SKILL.md                 # when to use the bench, the loop, evidence rules
├── references/
│   ├── api-map.md           # task → API index
│   ├── aedt-contract.md     # guard + AEDT configuration and run lifecycle
│   └── evidence-rules.md    # labels, kept artifacts, re-scoring rules
└── scripts/
    └── dry_demo.py          # runnable starter (no AEDT needed)
```

## Install

With the package installed:

```powershell
aster skill install            # copies this folder to %USERPROFILE%\.dsh\skills\aster
```

Without installing, copy the folder into the agent's skill directory:

```powershell
Copy-Item -Recurse skills\aster "$env:USERPROFILE\.dsh\skills\aster"
```

## Check

```powershell
aster skill path               # where the skill lives
python skills\aster\scripts\dry_demo.py --samples 32
```

The canonical copy lives here; `tools/sync_skill_bundle.py` mirrors it into the installed package
(`aster/skills/aster`) so `aster skill install` also works from a wheel.
