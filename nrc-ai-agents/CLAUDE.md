# CLAUDE.md

## Skill conventions

Claude Code skills for this project live under `.claude/skills/<skill-name>/SKILL.md`. That is the only executable skill definition location.

`skills.md` at the project root is a human-readable registry that indexes installed skills (purpose, source, triggers) for quick reference — it is documentation only and is not executed. See it for the current list of installed skills.

## Approved skill selection (NRC website work)

Only four skills are installed: `liquid-glass`, `redesign-existing-projects`, `design-taste-frontend`, `full-output-enforcement`. See `skills.md` for full detail per skill; the rules below are the quick reference.

- Use `redesign-existing-projects` as the **primary** skill for NRC website refinement.
- Use `design-taste-frontend` only as a **narrow supporting reference** for copy hygiene and redesign preservation — never for its stack assumptions or literal palette-ban rules, and never as the primary implementation skill.
- Use `full-output-enforcement` only during approved implementation work, to ensure completeness — it is not a design authority.
- Use `liquid-glass` only for restrained, already-scoped glass-related work — do not expand glass effects to new sections automatically.
- Never combine multiple competing aesthetic directions automatically.
- Do not change the NRC brand system (palette, typography, editorial identity) or technology stack (vanilla HTML/CSS/JS, Vite, Three.js) unless explicitly approved.
