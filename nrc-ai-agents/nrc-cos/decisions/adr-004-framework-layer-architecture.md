# ADR-004 — Framework Layer Architecture

## Status

Accepted

## Date

2026-07-24

## Context

Before this decision, Phase 0 and Phase 1 were frozen, but no definition existed for what a Framework is, how it differs from a Principle or a Product, what belongs inside one, or what characteristics a Framework document should have. Without this definition, future Framework documents risked drifting into restating Principles, describing one specific Product, or including implementation detail.

## Decision

A Framework was defined as a reusable structure for organizing and sequencing Principles to solve a recurring category of problem, independent of any single Product — a structure, not a new rule, and not a finished thing. Its relationship to Principles is that it inherits their rules and may never contradict them; its relationship to Products is that it gives them structure without ever describing one specific Product's content. Excluded from Frameworks: implementation, components, website-specific or product-specific decisions, and any restatement of Philosophy or Principles. Framework documents were characterized as reusable, product-independent, medium-independent, composable, scalable, testable, and silent on content — with "deterministic" explicitly rejected as a characteristic. Dependency direction was fixed as strictly Principles-to-Framework-to-Product, never the reverse.

## Consequences

Future Framework documents now have a precise boundary test to satisfy before being written, reducing the likelihood of layer drift being discovered only after a document is already frozen. Five initial candidate Frameworks (Discovery, Concept Exploration, Narrative, Proof, Invitation) were identified as genuinely deriving from work already completed, grounding Phase 2's first documents in demonstrated need rather than a generic template list.

## Alternatives Considered

Treating "deterministic, produces consistent output" as a required Framework characteristic was considered, since it appeared among the original candidate characteristics evaluated, and was rejected as contradicting Design Constitution's requirement that every creative decision involve real judgment rather than mechanical rule-following.

## Related Documents

All eleven documents in `principles/` (as the layer Frameworks must derive from); philosophy/design-constitution.md (Justification and Timelessness Laws, cited in rejecting determinism).
