---
name: aster
description: Pointer to the canonical aster agent skill in skills/aster/. Use when an agent must define a parameterized antenna design space, run dry or real AEDT-backed design studies through a guarded solver, keep measured artifacts as evidence, apply explicit bandwidth/pattern acceptance rules, or re-score recorded runs without re-solving. The web GUI is not included; real runs require the ansys-aedt-mcp guard plus AEDT 2026 R1.
---

# aster skill

The canonical skill (with `references/` and `scripts/`) lives in
[`skills/aster/`](skills/aster/SKILL.md).

Install it into an agent's skill directory:

```powershell
aster skill install            # → %USERPROFILE%\.dsh\skills\aster
```

Or copy `skills/aster` there manually. Full documentation: [README.md](README.md) ·
[user_doc/](user_doc/README.md) · [https://hawse1337.github.io/aster/](https://hawse1337.github.io/aster/)
