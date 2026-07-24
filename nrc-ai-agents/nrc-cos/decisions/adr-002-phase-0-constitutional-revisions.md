# ADR-002 — Phase 0 Constitutional Revisions

## Status

Accepted

## Date

2026-07-24

## Context

Before this decision, `philosophy/brand-philosophy.md` contained a "What NRC Never Does" section that substantially restated its own "Enduring Principles" section within the same document. Separately, `philosophy/design-constitution.md` contained a standalone "Proportion Law" among its Constitutional Laws with no explicit grounding in Brand Philosophy — the only one of six laws that did not trace to an upstream source. Both were identified as blocking findings during the Phase 0 architectural review, ahead of freezing these documents permanently.

## Decision

Two revisions were made. First, the "What NRC Never Does" section was removed from `brand-philosophy.md`; its one genuinely non-duplicated idea — that NRC states plainly, rather than disguises, when a business's substance cannot support a claim — was preserved and folded into the end of the "NRC's Role" section. Second, the standalone Proportion Law in `design-constitution.md` was removed and its content folded into the Restraint Law as an explicit extension: addition being never the default response to a problem was extended to cover scale and intensity, not only literal added elements.

## Consequences

Brand Philosophy now states each belief exactly once, with no section restating another in inverted form. The Design Constitution's six laws became five, with every remaining law traceable to an explicit source in Brand Philosophy. Both documents became shorter and more internally consistent. No substantive content was lost — both surviving ideas were relocated, not deleted.

## Alternatives Considered

For the ungrounded law, adding new grounding language to Brand Philosophy to justify Proportion Law as a standalone sixth law was considered and rejected in favor of folding it into Restraint Law, since it required no new philosophy and touched only one document instead of two.

## Related Documents

philosophy/brand-philosophy.md; philosophy/design-constitution.md.
