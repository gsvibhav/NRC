# Changelog

All architecturally significant changes to NRC-COS are recorded here. This changelog records architectural evolution — what was established, reviewed, revised, or frozen — not the conversations that produced it.

## 2026-07-24 — Foundation: Phase 0 and Phase 1 established and frozen

### Philosophy layer created (Phase 0)
Added `philosophy/brand-philosophy.md`, `philosophy/design-constitution.md`, `philosophy/design-language.md`, and `NORTH_STAR.md` — establishing why NRC exists, the enforceable constitutional laws every creative decision must satisfy, the creative character that results from following them, and a one-page compression of all three.

### Phase 0 architectural review
Reviewed the four Philosophy documents against single responsibility, duplication, hierarchy integrity, missing foundations, future stability, and internal consistency. Identified two blocking findings and four non-blocking findings deferred to later phases.

### Phase 0 revisions applied
Removed a duplicated "What NRC Never Does" section from `brand-philosophy.md` (its one non-redundant idea preserved and folded into "NRC's Role"). Folded the ungrounded Proportion Law into the Restraint Law in `design-constitution.md`, removing it as a standalone, unsupported law.

### Phase 0 frozen
`brand-philosophy.md`, `design-constitution.md`, `design-language.md`, and `NORTH_STAR.md` declared immutable except through the ADR process.

### Principles layer created (Phase 1)
Added nine documents in dependency order: `visual-language.md`, `motion-language.md`, `animation-principles.md`, `interaction-principles.md`, `typography-principles.md`, `spacing-principles.md`, `color-principles.md`, `accessibility.md`, `performance-standards.md`.

### Phase 1 architectural review
Reviewed all nine Principles documents as one system. Identified two coverage gaps — no document governing written language or visual representation — and a terminology collision between Motion Language and Performance Standards, both independently claiming the same "hierarchy across time" framing.

### Voice Principles added
Added `principles/voice-principles.md`, governing the craft of written language — sentence construction, honesty, restraint — distinct from Typography's governance of how already-written language becomes visible.

### Representation Principles added
Added `principles/representation-principles.md`, governing the truthfulness of photography, illustration, renders, and generated imagery.

### Performance Standards refined
Edited the "Response Speed Is a Form of Weight" section of `performance-standards.md` to explicitly attribute its hierarchy-across-time framing to Motion Language and cross-reference Interaction Principles' "Response Must Match What Was Asked," resolving the terminology collision identified in review.

### Phase 1 frozen
All eleven Principles documents declared immutable except through the ADR process.

### Framework layer architecture defined
Established the Framework layer's definition, scope, exclusions, characteristics, and dependency order (Discovery → Concept Exploration → Narrative → Proof / Invitation). Confirmed Patterns as a reserved, independently-populated layer rather than a sequential phase, populated only after a Product's decision is proven twice.

### Repository documentation synchronized
Added `README.md`, `GLOSSARY.md`, `CHANGELOG.md`, and `ROADMAP.md`, bringing repository metadata into alignment with the frozen Philosophy and Principles layers and the approved Framework architecture.

## v1.0.0 — 2026-07-24

### Framework layer completed
Added `frameworks/discovery-framework.md`, `concept-exploration-framework.md`, `narrative-framework.md`, `proof-framework.md`, and `invitation-framework.md`. Each was independently reviewed, revised for targeted findings, validated, and frozen in dependency order.

### Product layer architecture established
Added `products/README.md` and `products/product-lifecycle.md`. Each was reviewed, revised, validated, and frozen.

### Reference and validation Products constructed
Added Products 001–005.

Product 001 completed Draft → Architectural Review → Targeted Revision → Validation → Freeze.

Products 002–005 completed Draft Construction → Internal Constitutional Validation → Batch Architectural Stability Assessment. They were intentionally not subjected to separate independent architectural review during the validation batches.

### v1.0 validated and closed
Added `docs/NRC-COS-v1.0-Validation-Report.md`, recording that the frozen constitution required no amendment across five independently derived Products spanning Identity, Understanding, Evidence, Action, and Collection.
