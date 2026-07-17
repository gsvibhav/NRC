# Skill Cleanup Inventory

Snapshot taken before deleting any skill folders, as a rollback record. All 14 folders below existed under `.claude/skills/` at the time of this cleanup. Folder identity was verified against each `SKILL.md`'s frontmatter `name:` field directly (not assumed from folder naming) before any decision was made.

| Folder name | Skill name (frontmatter `name:`) | Description | Source repository | Decision |
|---|---|---|---|---|
| `liquid-glass` | `liquid-glass` | Build and migrate iOS, macOS, iPadOS, watchOS, tvOS, and visionOS apps with Apple's Liquid Glass design system (iOS 26+, macOS 26 Tahoe+). | https://github.com/haider-nawaz/liquid-glass-skill | **Keep** |
| `redesign-skill` | `redesign-existing-projects` | Upgrades existing websites and apps to premium quality. Audits current design, identifies generic AI patterns, and applies high-end design standards without breaking functionality. Works with any CSS framework or vanilla CSS. | https://github.com/leonxlnx/taste-skill | **Keep** |
| `taste-skill` | `design-taste-frontend` | Anti-slop frontend skill for landing pages, portfolios, and redesigns. The agent reads the brief, infers the right design direction, and ships interfaces that do not look templated. Real design systems when applicable, audit-first on redesigns, strict pre-flight check. | https://github.com/leonxlnx/taste-skill | **Keep** |
| `output-skill` | `full-output-enforcement` | Overrides default LLM truncation behavior. Enforces complete code generation, bans placeholder patterns, and handles token-limit splits cleanly. | https://github.com/leonxlnx/taste-skill | **Keep** |
| `taste-skill-v1` | `design-taste-frontend-v1` | The original v1 taste-skill, preserved for projects depending on its exact behavior. Superseded by `design-taste-frontend` (v2). | https://github.com/leonxlnx/taste-skill | Delete |
| `brutalist-skill` | `industrial-brutalist-ui` | Raw mechanical interfaces fusing Swiss typographic print with military terminal aesthetics. Rigid grids, extreme type scale contrast, utilitarian color, analog degradation effects. | https://github.com/leonxlnx/taste-skill | Delete |
| `minimalist-skill` | `minimalist-ui` | Clean editorial-style interfaces. Warm monochrome palette, typographic contrast, flat bento grids, muted pastels. No gradients, no heavy shadows. | https://github.com/leonxlnx/taste-skill | Delete |
| `soft-skill` | `high-end-visual-design` | Teaches the AI to design like a high-end agency. Defines the exact fonts, spacing, shadows, card structures, and animations that make a website feel expensive. | https://github.com/leonxlnx/taste-skill | Delete |
| `stitch-skill` | `stitch-design-taste` | Semantic Design System Skill for Google Stitch. Generates agent-friendly DESIGN.md files that enforce premium, anti-generic UI standards. Ships with an additional `DESIGN.md` file alongside `SKILL.md`. | https://github.com/leonxlnx/taste-skill | Delete |
| `gpt-tasteskill` | `gpt-taste` | Elite UX/UI & Advanced GSAP Motion Engineer. Enforces Python-driven true randomization for layout variance, strict AIDA page structure, gapless bento grids, strict GSAP ScrollTriggers, inline micro-images, and massive section spacing. | https://github.com/leonxlnx/taste-skill | Delete |
| `image-to-code-skill` | `image-to-code` | Elite website image-to-code skill for Codex. Generates design image(s) first, analyzes them, then implements the website to match. | https://github.com/leonxlnx/taste-skill | Delete |
| `brandkit` | `brandkit` | Premium brand-kit image generation skill for creating high-end brand-guidelines boards, logo systems, identity decks, and visual-world presentations. | https://github.com/leonxlnx/taste-skill | Delete |
| `imagegen-frontend-web` | `imagegen-frontend-web` | Elite frontend image-direction skill for generating premium, conversion-aware website design references. Generates one image per section. | https://github.com/leonxlnx/taste-skill | Delete |
| `imagegen-frontend-mobile` | `imagegen-frontend-mobile` | Elite mobile app image-generation skill for creating premium, app-native screen concepts and flows. Images only, no code. | https://github.com/leonxlnx/taste-skill | Delete |

## Pre-deletion verification performed

- Confirmed all four "keep" folder names match their `SKILL.md` frontmatter `name:` field exactly (no naming assumptions relied upon).
- Confirmed all ten "delete" folder names match their `SKILL.md` frontmatter `name:` field exactly, and every folder in `.claude/skills/` at inventory time is accounted for above (14 total: 4 keep + 10 delete).
- Grepped the contents of the four retained skills (`liquid-glass`, `redesign-skill`, `taste-skill`, `output-skill`) for any reference to the folder names or frontmatter names of the ten skills being deleted — no cross-references found. It is safe to delete the ten folders without affecting the four retained skills.
- Recorded MD5 checksums of every file under `liquid-glass/` prior to cleanup, to be re-verified as byte-identical after cleanup completes:
  - `SKILL.md` = `fb3f767bc8c89b44deaa5dbd9a87cdf1`
  - `examples/landmarks-patterns.md` = `8af06e3b0ecac0d412d0cabec3e29dfa`
  - `references/api-reference.md` = `dae2cb341656e2523305a3bf2c2c077d`
  - `references/migration-guide.md` = `c46d60064bdf6d47981c94fac0e38763`
  - `references/pitfalls-and-solutions.md` = `864d08b984ef1e1d75f20c3be17167f0`
  - `references/platform-specifics.md` = `ef2b35481001ef2b89e5289a7600e484`

## Rollback

All ten deleted skills originate from `https://github.com/leonxlnx/taste-skill` (public repo, MIT license). If any deleted skill is needed again, it can be re-cloned and re-installed from `skills/<folder-name>/` in that repository using the same install process used originally (see this file's table above for the exact folder-name-to-skill-name mapping).
