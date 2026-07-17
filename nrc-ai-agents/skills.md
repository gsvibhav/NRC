# Skills Registry

Human-readable index of Claude Code skills installed in this project. This file is documentation only — it is **not** loaded or executed by Claude Code. The actual, executable skill definitions live under `.claude/skills/<skill-name>/SKILL.md`.

As of the cleanup recorded in [`skill-cleanup-inventory.md`](skill-cleanup-inventory.md), exactly four approved skills remain installed. Ten other skills that were previously installed from the Taste Skill bundle repository were removed as unapproved for this project; see that inventory file for the full before/after record and rollback source.

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
