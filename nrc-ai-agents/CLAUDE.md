# CLAUDE.md

## Skill conventions

Claude Code skills for this project live under `.claude/skills/<skill-name>/SKILL.md`. That is the only executable skill definition location.

`skills.md` at the project root is a human-readable registry that indexes installed skills (purpose, source, triggers) for quick reference — it is documentation only and is not executed. See it for the current list of installed skills.

## Approved skill selection (NRC website work)

Four skills are approved for NRC website work: `liquid-glass`, `redesign-existing-projects`, `design-taste-frontend`, `full-output-enforcement`. See `skills.md` for full detail per skill; the rules below are the quick reference.

- Use `redesign-existing-projects` as the **primary** skill for NRC website refinement.
- Use `design-taste-frontend` only as a **narrow supporting reference** for copy hygiene and redesign preservation — never for its stack assumptions or literal palette-ban rules, and never as the primary implementation skill.
- Use `full-output-enforcement` only during approved implementation work, to ensure completeness — it is not a design authority.
- Use `liquid-glass` only for restrained, already-scoped glass-related work — do not expand glass effects to new sections automatically.
- Never combine multiple competing aesthetic directions automatically.
- Do not change the NRC brand system (palette, typography, editorial identity) or technology stack (vanilla HTML/CSS/JS, Vite, Three.js) unless explicitly approved.

## UI/UX Pro Max bundle (installed, not approved for automatic NRC use)

Seven additional skills (`banner-design`, `brand`, `design-system`, `design`, `slides`, `ui-styling`, `ui-ux-pro-max`) were installed from `nextlevelbuilder/ui-ux-pro-max-skill` on explicit request. They are **not** part of the approved NRC selection above and have not been through a curation review.

- Do not use any of them as an automatic or default design authority for NRC website work — `redesign-existing-projects` remains primary.
- `ui-styling` assumes Tailwind/shadcn/Radix; `design-system`/`design`/`banner-design` assume similar non-NRC stacks in places — none of that applies to NRC's vanilla HTML/CSS/JS + Vite + Three.js codebase.
- `design` and `banner-design`'s AI-generation features require a `GEMINI_API_KEY` and `pip install google-genai pillow` — an external API dependency unique to this bundle.
- Only invoke a skill from this bundle when explicitly asked for the specific deliverable it produces (e.g., a slide deck, a logo, a banner) — never to silently reinterpret NRC's existing design.

## Hallmark (installed, not approved for automatic NRC use)

`hallmark` (source: `nutlope/hallmark`) was installed via `npx skills add` — the actual skill lives at `.agents/skills/hallmark/`, symlinked into `.claude/skills/hallmark/`. It is a design-authority skill (page macrostructures, nav/footer archetypes, OKLCH theme catalog, its own `tokens.css`/`.hallmark/log.json`/`design.md`) with `build`/`audit`/`redesign`/`study` verbs. Not part of the approved NRC selection above and not curated.

- Do not use it as an automatic or default design authority for NRC website work — `redesign-existing-projects` remains primary; never combine competing aesthetic directions automatically.
- Do not let it introduce its own macrostructure/theme/nav/footer picks, or write `tokens.css`/`design.md`, in a way that overrides or fragments NRC's established champagne/ivory/sage/bronze/charcoal/dark palette, typography, or single-file vanilla HTML/CSS/JS + Vite + Three.js stack.
- Only invoke it when explicitly asked by name or for one of its specific verbs. Its `audit` verb is read-only (no edits) and is the lowest-risk entry point if ever exercised on NRC.
- See `skills.md` for full detail, including its different install mechanism (the `skills` CLI, not a manual copy) and its optional third-party image-generation tier (no API keys configured in this project).
