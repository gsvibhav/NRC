# Skills Registry

Human-readable index of Claude Code skills installed in this project. This file is documentation only — it is **not** loaded or executed by Claude Code. The actual, executable skill definitions live under `.claude/skills/<skill-name>/SKILL.md`.

As of the cleanup recorded in [`skill-cleanup-inventory.md`](skill-cleanup-inventory.md), four skills were approved and installed for NRC website work. Ten other skills that were previously installed from the Taste Skill bundle repository were removed as unapproved for this project; see that inventory file for the full before/after record and rollback source.

A separate seven-skill bundle (**UI/UX Pro Max**, see below) was installed afterward on explicit request. It has **not** been through the same approval/curation review as the four NRC skills above — see its own restrictions section before using any of it on the NRC website.

---

## Liquid Glass

- **Actual skill name (frontmatter):** `liquid-glass`
- **Folder location:** [`.claude/skills/liquid-glass/SKILL.md`](.claude/skills/liquid-glass/SKILL.md)
- **Purpose:** Build and migrate SwiftUI apps (iOS, macOS, iPadOS, watchOS, tvOS, visionOS) using Apple's Liquid Glass design system introduced at WWDC 2025 (iOS 26+, macOS 26 Tahoe+). Covers `.glassEffect()`, `.backgroundExtensionEffect()`, `.buttonStyle(.glass)`, `GlassEffectContainer`, glass toolbars/tab bars/sheets, morphing transitions, platform differences, and a 5-phase migration workflow for existing apps.
- **When it should be used:** When creating new SwiftUI apps targeting Apple's 2025+ platform releases; when migrating an existing native app to Liquid Glass; when applying any native glass modifier/component listed above. For this project's own NRC website (a web project, not SwiftUI), it applies only by analogy — restrained, CSS-based "glass" work already implemented in the nav and mobile CTA — never for native SwiftUI work itself, since there is no Apple app in this project.
- **When it should not be used:** Not for web/CSS glass effects directly (its APIs are native SwiftUI/UIKit only, not portable to CSS) and not as a license to expand glass effects on the NRC website beyond what's already shipped — see restrictions below.
- **Source repository:** https://github.com/haider-nawaz/liquid-glass-skill (MIT license)
- **Important usage restrictions:** Use only for restrained, already-scoped glass-related work (the existing nav and mobile CTA glass treatment). Do not use it to justify expanding glass effects to new sections of the NRC website automatically.
- **Supporting files (installed alongside `SKILL.md`):**
  - `references/api-reference.md` — full API reference for glass modifiers/types
  - `references/migration-guide.md` — 5-phase migration workflow
  - `references/platform-specifics.md` — per-platform details
  - `references/pitfalls-and-solutions.md` — known issues and fixes
  - `examples/landmarks-patterns.md` — real code patterns from Apple's Landmarks sample app

---

## Redesign Existing Projects

- **Actual skill name (frontmatter):** `redesign-existing-projects`
- **Folder location:** [`.claude/skills/redesign-skill/SKILL.md`](.claude/skills/redesign-skill/SKILL.md)
- **Purpose:** Upgrades existing websites/apps to premium quality via a scan → diagnose → fix workflow. Framework-agnostic — explicitly works with vanilla CSS as well as any CSS framework. Audits typography, color, layout, interactivity/states, content, component patterns, iconography, and code quality; prioritizes small, targeted, reviewable improvements over rewrites.
- **When it should be used:** As the **primary skill** for any refinement work on the NRC website — it is the only one of the four whose entire design is "audit and improve what exists," matching NRC's vanilla HTML/CSS/JS/Vite/Three.js stack and established brand identity.
- **When it should not be used:** Not for greenfield builds with no existing design to audit, and not in a way that migrates the project to a different framework or stack (its own rules explicitly forbid that).
- **Source repository:** https://github.com/leonxlnx/taste-skill (MIT license)
- **Important usage restrictions:** None beyond its own built-in rules (work with the existing stack, don't rewrite from scratch, keep changes small and reviewable).

---

## Design Taste Frontend (v2)

- **Actual skill name (frontmatter):** `design-taste-frontend`
- **Folder location:** [`.claude/skills/taste-skill/SKILL.md`](.claude/skills/taste-skill/SKILL.md)
- **Purpose:** Flagship "anti-slop" frontend skill — brief inference and a one-line "design read," design-variance/motion/density dials, real-design-system mapping, layout/typography/color anti-default rules, an extensive AI-tells vocabulary (banned em-dashes, filler copy, generic section-numbering, etc.), and a redesign-preserve protocol (audit brand tokens first, evolve gradually, never touch IA/SEO/legal copy silently).
- **When it should be used:** Only as a **narrow supporting reference** for the NRC website, and only for three specific parts of it: brief-inference framing (Section 0), copy-hygiene / AI-tells vocabulary (Section 9 — em-dash ban, filler-word ban, eyebrow restraint, fake-precise-number ban, quote-length caps), and its redesign-preservation methodology (Section 11 — audit first, "a brand that already has X keeps X").
- **When it should not be used:** Never as NRC's primary implementation skill (`redesign-existing-projects` fills that role). Never for its default technology stack. Never for its literal palette-ban rules on this project.
- **Source repository:** https://github.com/leonxlnx/taste-skill (MIT license)
- **NRC-specific restrictions (important):**
  - Use only its brief-inference, copy-hygiene, and redesign-preservation guidance (Sections 0, 9, 11).
  - Do **not** adopt its React, Next.js, Tailwind, Motion, or GSAP stack assumptions (Sections 2-3, 5, 12) — NRC is vanilla HTML/CSS/JS + Vite + Three.js and stays that way.
  - Do **not** apply its literal banned-palette rules (Section 4.2) to NRC. That section's banned "premium-consumer" hex list happens to closely match NRC's actual, deliberate brand colors (its banned near-black text tone is an exact match to NRC's `--dark: #1A1714`) — this is NRC's real established identity, not a generic AI default to replace.
  - **Preserve NRC's existing champagne, ivory, sage, bronze, charcoal, and dark palette** exactly as-is.
  - Do **not** use this skill as the primary implementation skill for NRC — `redesign-existing-projects` is primary; this is supporting only.

---

## Full Output Enforcement

- **Actual skill name (frontmatter):** `full-output-enforcement`
- **Folder location:** [`.claude/skills/output-skill/SKILL.md`](.claude/skills/output-skill/SKILL.md)
- **Purpose:** An execution-completeness skill, **not a visual-design direction**. Overrides truncation behavior, bans placeholder code patterns (`// ... rest of code`, `// TODO`, etc.), and defines a clean pause/resume protocol for outputs that approach the token limit.
- **When it should be used:** Only during approved implementation work on the NRC website (or anywhere else), to ensure deliverables are complete and unabridged — never as a source of design direction or aesthetic judgment.
- **When it should not be used:** Never as a design authority — it has no opinion on typography, color, or layout. Do not invoke it to justify or evaluate visual choices.
- **Source repository:** https://github.com/leonxlnx/taste-skill (MIT license)
- **Important usage restrictions:** Orthogonal to the other three — safe to use alongside any of them, but only governs output completeness, never design decisions.

---

# UI/UX Pro Max bundle (installed 2026-07-18, not yet curated)

Seven skills installed together as a single plugin repository, `nextlevelbuilder/ui-ux-pro-max-skill` (MIT license), at the explicit request "install this skill and place where you did for previous skills." Unlike the four skills above, **this bundle has not been through a scoping/approval review** — it was installed in full, matching how the original 13-skill Taste Skill bundle was first installed before its later cleanup. Treat the restrictions below as provisional until a similar curation pass happens for this bundle, if wanted.

**Important characteristics that differ from every other skill in this project:**
- It is an **aesthetic-direction / design-authority bundle** (styles, color palettes, font pairings, UX guidelines, stack-specific patterns) — this project's existing rule to "never combine multiple competing aesthetic directions automatically" and to treat `design-taste-frontend` as narrow-supporting-only applies here with even more force, since this bundle is far larger and more opinionated.
- Several of its sub-skills assume **Tailwind CSS, shadcn/ui, React/Next.js/Vue/Svelte/Astro**, and similar frameworks as their default target stack — none of which NRC's website uses (vanilla HTML/CSS/JS + Vite + Three.js). Its stack-specific guidance for those frameworks does not apply here.
- Several sub-skills (`design`, `banner-design`, and the logo/icon/CIP routines inside `design`) run **executable Python/JS scripts**, not just reference documentation, and the AI-generation features (logo, icon, banner, social-photo generation) require a **`GEMINI_API_KEY`** environment variable plus `pip install google-genai pillow` — an external Google API dependency that does not exist anywhere else in this project's skill set.
- Do **not** use any part of this bundle as a primary or automatic design authority for the NRC website. NRC's brand palette (champagne/ivory/sage/bronze/charcoal/dark), typography, and vanilla stack are established and must not be silently changed by this bundle's style/palette/stack databases.

## Banner Design

- **Actual skill name (frontmatter):** `banner-design`
- **Folder location:** [`.claude/skills/banner-design/SKILL.md`](.claude/skills/banner-design/SKILL.md)
- **Purpose:** Generates banner creative (social, ads, web hero, print) across multiple art-direction styles, using Python scripts and AI-generated visuals.
- **When it should be used:** Only if explicitly asked to design a new marketing/social banner asset — not for the NRC website's existing hero or layout.
- **When it should not be used:** Not for any existing NRC page layout or hero treatment; requires the Gemini API setup described above to actually generate visuals.
- **Supporting files:** `references/banner-sizes-and-styles.md`, Python scripts under `scripts/`.

## Brand

- **Actual skill name (frontmatter):** `brand`
- **Folder location:** [`.claude/skills/brand/SKILL.md`](.claude/skills/brand/SKILL.md)
- **Purpose:** Brand voice, visual identity, messaging frameworks, asset management, and brand-consistency checklists, with scripts to extract colors from assets and sync brand tokens.
- **When it should be used:** Only if explicitly asked to produce or review brand-guideline documentation — not to redefine NRC's already-established brand identity.
- **When it should not be used:** Never to silently rewrite or "correct" NRC's existing champagne/ivory/sage/bronze/charcoal/dark palette or voice; its `sync-brand-to-tokens.cjs`/`extract-colors.cjs` scripts should not be run against NRC's codebase without explicit approval.
- **Supporting files:** `references/` (checklists, frameworks, templates), `scripts/` (`.cjs` color/token tooling, with a Python test suite), `templates/brand-guidelines-starter.md`.

## Design System

- **Actual skill name (frontmatter):** `design-system`
- **Folder location:** [`.claude/skills/design-system/SKILL.md`](.claude/skills/design-system/SKILL.md)
- **Purpose:** Three-layer design-token architecture (primitive → semantic → component), component specifications, and a strategic HTML/Chart.js slide-generation system.
- **When it should be used:** Only if explicitly asked to build a formal token architecture or a slide deck — not applicable to NRC's existing hand-authored CSS custom-property palette.
- **When it should not be used:** Not to restructure NRC's existing `:root` CSS variables into a different token architecture unless explicitly requested.
- **Supporting files:** `references/` (token/component docs, Tailwind integration), `data/*.csv` (slide layout/strategy tables), `scripts/` (token generation/validation, slide search/generation), `templates/design-tokens-starter.json`.

## Design (unified)

- **Actual skill name (frontmatter):** `design`
- **Folder location:** [`.claude/skills/design/SKILL.md`](.claude/skills/design/SKILL.md)
- **Purpose:** A unified router across logo design, corporate-identity-program (CIP) deliverables, HTML slide decks, banner design, icon design, and AI-generated social photos — the largest and most generation-heavy skill in the bundle.
- **When it should be used:** Only if explicitly asked for one of the above deliverable types — none of which are current NRC website tasks.
- **When it should not be used:** Not for any NRC website layout/component work. Requires the `GEMINI_API_KEY` + `google-genai`/`pillow` setup for its generation features to function at all.
- **Supporting files:** Extensive `references/`, `data/*.csv` (logo/icon/CIP style tables), `scripts/` (Python generation/search tooling for logo, icon, CIP).

## Slides

- **Actual skill name (frontmatter):** `slides`
- **Folder location:** [`.claude/skills/slides/SKILL.md`](.claude/skills/slides/SKILL.md)
- **Purpose:** Focused slide-deck creation (a narrower standalone version of the slide functionality also present inside `design-system`/`design`) — HTML presentations with Chart.js, copywriting formulas, and layout patterns.
- **When it should be used:** Only if explicitly asked to build a presentation deck — unrelated to the NRC website itself.
- **When it should not be used:** Not for any NRC website page.
- **Supporting files:** `references/` (copywriting formulas, layout patterns, HTML template, strategies).

## UI Styling

- **Actual skill name (frontmatter):** `ui-styling`
- **Folder location:** [`.claude/skills/ui-styling/SKILL.md`](.claude/skills/ui-styling/SKILL.md)
- **Purpose:** Building UIs with shadcn/ui (Radix UI + Tailwind) and Tailwind CSS utility styling, plus a canvas-based visual-design system with ~40 bundled font families.
- **When it should be used:** Only for a project actually built on React + Tailwind + shadcn/ui — **not NRC**, which is vanilla HTML/CSS/JS.
- **When it should not be used:** Never for NRC website work — its entire premise (Tailwind utility classes, shadcn component library, Radix primitives) is a different stack than NRC's hand-written CSS.
- **Supporting files:** `references/` (shadcn components/theming/accessibility, Tailwind customization/responsive/utilities), `canvas-fonts/` (~40 bundled `.ttf` font files with OFL licenses), `scripts/` (shadcn/Tailwind tooling with its own test suite), its own bundled `LICENSE.txt`.

## UI/UX Pro Max (flagship)

- **Actual skill name (frontmatter):** `ui-ux-pro-max`
- **Folder location:** [`.claude/skills/ui-ux-pro-max/SKILL.md`](.claude/skills/ui-ux-pro-max/SKILL.md)
- **Purpose:** The bundle's flagship skill — a searchable local database (84 styles, ~192 color palettes, ~74 font pairings, ~192 product types, ~98 UX guidelines, ~104 icon entries, 16 GSAP motion presets, 25 chart types) with priority-ranked recommendations across 22 technology stacks (React, Next.js, Vue, Nuxt, Svelte, Astro, SwiftUI, React Native, Flutter, Tailwind, shadcn/ui, Jetpack Compose, Angular, Laravel, JavaFX, WPF, WinUI, Avalonia, Uno, UWP, Three.js, HTML/CSS).
- **When it should be used:** Only if explicitly asked to consult it for a specific, scoped question (e.g., "what does this database say about X") — not as an automatic or default design authority for NRC.
- **When it should not be used:** Never as NRC's primary or automatic design-decision source. NRC already has `redesign-existing-projects` as its primary skill and an established, deliberate brand identity that this database's generic style/palette recommendations must not silently override.
- **Supporting files:** `data/*.csv` (styles, colors, typography, products, UX guidelines, icons, motion, charts, per-stack guidance), `references/pro-rules.md`, `references/quick-reference.md`, `scripts/` (Python search/validation tooling with its own test suite).

## Bundle-wide provenance files

- **Source repository:** https://github.com/nextlevelbuilder/ui-ux-pro-max-skill (MIT license, copyright Next Level Builder)
- **Plugin metadata:** `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` were part of the source repo but were **not** copied into this project (Claude Code skills here are consumed directly from `.claude/skills/<name>/SKILL.md`, not via the plugin/marketplace mechanism) — only the `.claude/skills/` contents and the `LICENSE` file (saved as `.claude/skills/ui-ux-pro-max-LICENSE.txt`) were installed.
