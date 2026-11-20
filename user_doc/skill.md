# Agent skill

The `aster` agent skill is the agent-facing form of the bench. It is deliberately thin: it teaches
an agent when to use the bench, where the APIs are, and which evidence rules must survive the loop.

## What is in the skill

```text
skills/aster/
├── SKILL.md                 # trigger conditions, loop, non-negotiables
├── references/
│   ├── api-map.md           # task → API index
│   ├── aedt-contract.md     # guard/AEDT configuration + run lifecycle
│   └── evidence-rules.md    # labels, kept artifacts, re-scoring
└── scripts/
    └── dry_demo.py          # runnable starter, no AEDT
```

## Install

With the library installed:

```powershell
aster skill install            # → %USERPROFILE%\.dsh\skills\aster
aster skill path               # print that destination
```

Without installing, copy the folder into the agent's skill directory:

```powershell
Copy-Item -Recurse skills\aster "$env:USERPROFILE\.dsh\skills\aster"
```

For other agent platforms, keep the same layout (`SKILL.md` + `references/` + `scripts/`) and point
the agent at the folder. The YAML front matter carries the skill `name` and `description`; the body
is the instruction.

## Try it

```powershell
python skills\aster\scripts\dry_demo.py --samples 32
```

The script exercises the full dry loop: parameter space → Latin hypercube → evaluations → Pareto
archive.

## What the skill will not do

- It will not present a `PREDICTED` score as measured.
- It will not drop a failed run silently.
- It will not drive the web GUI (not published yet).
