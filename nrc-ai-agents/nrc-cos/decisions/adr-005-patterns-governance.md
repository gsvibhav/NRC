# ADR-005 — Patterns Governance

## Status

Accepted

## Date

2026-07-24

## Context

Before this decision, it was unclear whether NRC-COS required a Patterns layer at all, and if so, how it should relate to Frameworks and Products, and when it should be populated. The Framework layer architecture review was explicitly asked to decide this rather than assume it.

## Decision

Patterns were confirmed to exist as a layer, defined as proven, reusable recipes extracted from a Product only after the same specific solution has succeeded independently in a second, genuinely different Product — never written speculatively, and never designed top-down the way a Framework is. Patterns are not a sequential phase following Products in a numbered project plan; they are an independent, continuously-populated knowledge layer, fed opportunistically whenever a Product's decision proves itself twice, with no batch of Pattern documents scheduled at any fixed point.

## Consequences

The `patterns/` directory exists as a reserved location in the repository but is expected to remain empty for longer than any other layer, and this is treated as correct rather than as delayed work. This prevents a specific, realistic failure mode: a Pattern being written prematurely from a single Product's decision, then treated as general guidance before it has actually been proven to generalize.

## Alternatives Considered

Treating Patterns as a batch-written phase — for example, a phase scheduled immediately after the first Products are built — was considered and rejected, since it would have required either delaying Pattern documentation past when real evidence exists, or writing Patterns from a single unproven instance, both of which conflict with the standard that a Pattern must be proven independently, not merely observed once.

## Related Documents

README.md (Repository Structure, Layer Dependency sections); ROADMAP.md (Pattern extraction section).
