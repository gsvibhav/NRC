# ADR-001 — Repository Architecture

## Status

Accepted

## Date

2026-07-24

## Context

Before this decision, no formal record existed explaining why NRC-COS is organized as a layered dependency chain rather than a flat collection of documents or a single combined guide. The design work that produced this repository considered the risk of a flat structure: nothing would distinguish a permanent belief from a one-off product decision, which is the exact failure — judgment reinvented inconsistently, contradicted silently, or lost — this system exists to prevent.

## Decision

NRC-COS is organized as a strict dependency chain: Philosophy → Principles → Frameworks → Products. Each layer may cite the layer above it to justify a decision, never the reverse. Three supporting layers sit outside this sequential chain: Patterns (extracted from Products, populated continuously rather than written on schedule), Decisions (the permanent record of why, accumulating alongside every layer), and Archive (retired content, preserved rather than deleted).

## Consequences

Every document in the system can be evaluated against one test: does it stay within its layer's responsibility, and does it correctly derive from the layer above it. Drift — a Principle asserting new philosophy, a Framework describing one specific product — becomes mechanically detectable rather than a matter of taste. The tradeoff: the strict one-directional rule means a good idea discovered while building a Product cannot immediately become a Principle. It must first prove itself as a Pattern, which slows how quickly real-world learning becomes permanent guidance. This is an accepted cost.

## Alternatives Considered

A flat, single-tier documentation structure was implicitly rejected in favor of the layered model, because it offers no mechanism for distinguishing a permanent belief from a one-off product decision.

## Related Documents

README.md (Design Philosophy, Repository Structure, Layer Dependency sections); all documents in `philosophy/` and `principles/`.
