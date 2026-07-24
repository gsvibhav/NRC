# ADR-003 — Phase 1 Principles Expansion

## Status

Accepted

## Date

2026-07-24

## Context

Before this decision, the Principles layer contained nine documents. The Phase 1 architectural review identified two coverage gaps that a downstream Framework would have no constitutional basis for: no document governed the craft of written language itself, as distinct from Typography's governance of how already-written language becomes visible, and no document governed the truthfulness of visual representation — photography, illustration, renders, and generated imagery. The same review identified a terminology collision: Motion Language's "Motion Is Hierarchy Across Time" and Performance Standards' independent claim that speed is "hierarchy, expressed in time instead of in size" asserted the same framing without either document acknowledging the other.

## Decision

Two new Principles documents were added — `voice-principles.md`, governing sentence-level writing craft, and `representation-principles.md`, governing the truthfulness of images regardless of how they are produced. Both were written as Principles, not Frameworks, because each establishes enduring, medium-agnostic rules about what must always be true of a domain, independent of any specific recurring structure or sequence — the defining trait of a Principle, as distinct from a Framework, which organizes multiple Principles into a reusable process for a category of problem. Separately, `performance-standards.md` was edited to explicitly attribute its hierarchy-across-time framing to Motion Language and to cross-reference Interaction Principles' "Response Must Match What Was Asked," resolving the collision without altering either upstream document.

## Consequences

The Principles layer grew from nine to eleven documents, closing both coverage gaps before Framework work began. The terminology collision was resolved with a single attribution edit rather than a rewrite of either document. Tradeoff: the Principles layer is less uniform in count than originally scoped, slightly increasing the surface area every future Framework must remain consistent with.

## Alternatives Considered

Treating voice and representation as Framework-layer concerns was considered and rejected — a Framework requires a recurring structural shape to organize, and "how should a sentence be written" or "when is an image honest" are domain rules, not structural sequencing problems, matching every other document already in the Principles layer.

## Related Documents

principles/voice-principles.md; principles/representation-principles.md; principles/performance-standards.md; principles/motion-language.md; principles/interaction-principles.md; principles/typography-principles.md; principles/color-principles.md.
