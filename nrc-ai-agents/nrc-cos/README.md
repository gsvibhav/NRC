# NRC-COS

## Purpose

NRC-COS is NRC's permanent creative operating system — the documented, evolving record of how the studio thinks, decides, and evaluates its own work, independent of any single product built with it.

It exists because creative judgment is easy to lose as a studio grows. Without a shared, durable record of *why* a choice was made — not just what was chosen — decisions get reinvented inconsistently, quietly contradicted, or lost entirely when the person who made them moves on. NRC-COS is that record. The NRC website is not what this system is for. It is the first thing being built using it.

Every document in this repository should be able to answer one question on request: why does this exist, and what does it derive from? A document that can't answer that question doesn't belong here.

## Design Philosophy

NRC-COS is organized as a chain of layers, each answering a different kind of question, each deriving from the layer above it rather than inventing its own authority.

**Philosophy** answers *why NRC exists*. It is the constitutional foundation — the beliefs, laws, and character that should hold true regardless of medium, technology, or how large NRC becomes. It changes rarely, and only through the most deliberate governance this system has.

**Principles** answer *what must always be true* within a specific domain — motion, color, typography, interaction, and the rest. Each Principle translates Philosophy into an enduring, testable standard for one part of the work, without yet describing any real product.

**Frameworks** answer *what shape a recurring problem takes*. A Framework organizes several Principles into a reusable structure — a sequence, a process, a composition — that can be applied to more than one Product without being rewritten each time.

**Products** answer *what NRC actually built, and why, for this one thing*. A Product applies Frameworks and Principles to real content, a real audience, and real constraints. It is where the one-off decisions a Framework deliberately withholds finally get made.

**Patterns** answer *what has already been proven to work*. A Pattern is not designed — it is extracted from a Product, and only after the same specific solution has succeeded independently in a second, genuinely different Product. Nothing is ever written here speculatively.

**Decisions** hold the permanent record of *why* — a lightweight, append-only account of what changed, what alternatives were considered, and what trade-offs were accepted, for every architecturally significant choice this system makes.

**Archive** holds what NRC-COS used to believe or used to build. Nothing is deleted from this system. When something is superseded, it moves here, with a record of what replaced it and why — so the reasoning behind a retired decision stays discoverable rather than disappearing.

## Repository Structure

```
nrc-cos/
├── README.md
├── NORTH_STAR.md
├── GLOSSARY.md
├── CHANGELOG.md
├── ROADMAP.md
│
├── philosophy/                  — frozen
│   ├── brand-philosophy.md
│   ├── design-constitution.md
│   └── design-language.md
│
├── principles/                  — frozen
│   ├── visual-language.md
│   ├── motion-language.md
│   ├── animation-principles.md
│   ├── interaction-principles.md
│   ├── typography-principles.md
│   ├── spacing-principles.md
│   ├── color-principles.md
│   ├── voice-principles.md
│   ├── representation-principles.md
│   ├── accessibility.md
│   └── performance-standards.md
│
├── frameworks/                  — frozen
│   ├── discovery-framework.md
│   ├── concept-exploration-framework.md
│   ├── narrative-framework.md
│   ├── proof-framework.md
│   └── invitation-framework.md
│
├── products/                    — constitution frozen; five Products constructed and validated
│   ├── README.md
│   ├── product-lifecycle.md
│   ├── product-001-founder-introduction.md
│   ├── product-002-service-introduction.md
│   ├── product-003-case-study.md
│   ├── product-004-discovery-call.md
│   └── product-005-selected-work.md
│
├── decisions/                   — five ADRs recorded
│   ├── adr-001-repository-architecture.md
│   ├── adr-002-phase-0-constitutional-revisions.md
│   ├── adr-003-phase-1-principles-expansion.md
│   ├── adr-004-framework-layer-architecture.md
│   └── adr-005-patterns-governance.md
│
├── docs/                        — historical record, outside the constitutional hierarchy
│   └── NRC-COS-v1.0-Validation-Report.md
│
├── patterns/                    — reserved; empty by design, not by delay
│
└── archive/                     — reserved; empty, nothing superseded yet
```

`philosophy/`, `principles/`, `frameworks/`, `products/`, and `decisions/` all contain real content now. `docs/` holds historical records that sit outside the constitutional hierarchy entirely rather than governing anything.

`patterns/` and `archive/` are the only directories without content, and neither is missing work:

- `patterns/` is empty **on principle**, not on schedule. A Pattern may only be written after a Product's specific decision has been proven independently twice, in a second, genuinely different Product. This directory may stay empty for a long time, and that is correct, not overdue.
- `archive/` is empty because nothing in this system has yet been superseded.

Neither `patterns/` nor `archive/` currently exists as a literal directory on disk — Git does not track empty directories, so both remain conceptually reserved and documented here, and will be created the moment either first receives real content.

NRC-COS v1.0 constitutional work — Philosophy, Principles, Frameworks, Product Layer Architecture, and Product Lifecycle — is complete and frozen. Five Products have been constructed and validated against it. Implementation is the next phase of work, not a further constitutional one.

## Layer Dependency

```
Philosophy
    ↓
Principles
    ↓
Frameworks
    ↓
Products
```

Each arrow is one-directional: a lower layer may cite the layer above it to justify a decision, but never the reverse. A Principle that can't be traced to Philosophy doesn't belong. A Framework that invents its own reason to exist, rather than organizing Principles that already exist, doesn't belong.

Patterns sit outside this chain, deliberately. A Pattern is not the next sequential phase after Products — it is an **independent, continuously-populated layer**, fed by Products whenever something proves itself twice, rather than something written on a schedule. The same is true of Decisions and Archive: both accumulate continuously, alongside every other layer, rather than waiting their turn.

## Governance

**Freeze.** A phase or document is frozen once it has been reviewed against defined criteria (single responsibility, duplication, dependency integrity, constitutional derivation, coverage, consistency, future stability, layer discipline), any required revisions have been applied, and it has been explicitly declared frozen. From that point, it is immutable except through the ADR process.

**ADRs.** Every architecturally significant decision is recorded as a lightweight, append-only Architecture Decision Record: context, decision, alternatives considered, trade-offs, expected impact, and related documents. ADRs are never edited after acceptance — only superseded by a new ADR that references the one it replaces.

**Immutability.** A frozen document cannot be edited directly. Any change to it requires a new ADR proposing the change, reviewed at the governance gate appropriate to that layer — Philosophy requires the Founder directly; Principles require the Creative Director and Design Systems Architect; Frameworks and Patterns have progressively lighter bars, matching how reusable and how proven each layer already is.

**Promotion.** An idea moves through this system on a defined path — Concept, Experiment, Implementation, Review, Decision, Pattern, Principle — with a specific gate and owner at each step. Most ideas correctly stop partway through this path. Reaching Pattern or Principle status is the exception, not the default outcome.

**Evolution.** The system is expected to accumulate Creative Debt — real gaps between what's documented and what's actually true — and to review it on a standing cadence rather than pretend it doesn't happen. Retirement follows the same discipline as creation: nothing is deleted, everything superseded moves to Archive with a record of why.

## Working Rules

For anyone — human or AI — working inside NRC-COS:

- Never introduce new philosophy in a lower layer. If a decision needs a reason that isn't already established upstream, that's a sign the reason belongs in Philosophy, not that it's fine to assert locally.
- Never duplicate ownership. If two documents seem to be answering the same question, one of them is wrong, or one needs to explicitly defer to the other.
- Justify architectural changes through ADRs, not through silent edits. A frozen document that needs to change needs a decision record before it needs a new sentence.
- Preserve single responsibility. Every document should be able to state, in one sentence, the one question it exists to answer.
- Maintain dependency direction. Citations flow upward, never sideways or down. A Framework may not be justified by a Product; a Principle may not be justified by a Framework.
- Distinguish, in every recommendation, between what's already established here and what's being proposed. Citing this system's authority for something it doesn't actually say is a bigger failure than not knowing the answer.

These are working rules for using this repository responsibly — they describe how to move through NRC-COS, not a restatement of what Philosophy or Principles already require of the work itself.
