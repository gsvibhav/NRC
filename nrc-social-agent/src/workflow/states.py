"""Persisted workflow states.

As of Milestone 6, `SHOWING_PREVIEW` is persisted: the Primary Draft
Generation Engine (`src/ai/draft_generation_service.py`) reaches it once
the selected priority-1 output has been generated, validated, and
persisted — see docs/WORKFLOW.md §3.4/§3.6/§3.7, which already documents
`GENERATING_CONTENT -> SHOWING_PREVIEW` as an existing, named state in the
full state machine.

Milestone 7 (the Telegram Draft Review, Editing and Approval Workflow)
adds exactly one new persisted state, `EDITING` — docs/WORKFLOW.md §3.8
already names and fully specifies it ("Capture what the user wants
changed... Exit conditions: user specifies the field(s)... ->
GENERATING_CONTENT"). Milestone 7's edit-generation phase itself reuses
the *existing* `GENERATING_CONTENT` state rather than inventing a new one
— exactly as docs/WORKFLOW.md §3.6 already documents ("Entry conditions:
... Or: user submitted an edit from EDITING (partial pass)"), and exactly
the same "reuse an existing multi-purpose state" precedent already
established when Milestone 5/6 both ran through `GENERATING_CONTENT` for
different work.

`APPROVED` is deliberately still **not** added here, even though
docs/WORKFLOW.md §3.10 names it: that section describes APPROVED as
strictly transitional ("Allowed user actions: None — this is a brief,
system-driven transition, not a waiting point for user input") whose only
job (a "finalization write") has no separate content in this system
beyond persisting approval metadata — a write this codebase already
performs atomically together with every state transition (see
workflow/manager.py's module docstring: "every write is a single, atomic
S3 PutObject of the entire document"). Since there is no distinct
finalization step for a reader to ever observe *between* "approved" and
"finalized," Milestone 7's approve action transitions directly from
`SHOWING_PREVIEW` to `COMPLETED` in one atomic write (see
WorkflowManager.approve_draft()) — a persisted, separately-observable
`APPROVED` snapshot would add a state no code path could ever meaningfully
occupy. This is a deliberate, documented interpretation of
docs/WORKFLOW.md's own more detailed specification (which the milestone
brief's own illustrative sketch simplifies, and even mis-names in one spot
— "EDITING_CONTENT" rather than the real `EDITING` — confirming that
sketch is shorthand, not a literal state-name source). `APPROVED` remains
available for a future milestone that introduces genuine multi-step
finalization or publishing, where the distinction would become real.
"""

from __future__ import annotations

from enum import Enum


class WorkflowState(str, Enum):
    IDLE = "IDLE"
    RECEIVING_MEDIA = "RECEIVING_MEDIA"
    UPLOADING_MEDIA = "UPLOADING_MEDIA"
    ANALYZING_MEDIA = "ANALYZING_MEDIA"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    GENERATING_CONTENT = "GENERATING_CONTENT"
    SHOWING_PREVIEW = "SHOWING_PREVIEW"
    EDITING = "EDITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SAVED_AS_DRAFT = "SAVED_AS_DRAFT"
    REJECTED = "REJECTED"


# A workflow in one of these states is done — per docs/WORKFLOW.md §9, these
# (plus FAILED, which is a holding point rather than a true completion) are
# the states after which nothing further happens to a workflow on its own.
# This is the single source of truth for "is this workflow still active,"
# used by the conversation layer's active-workflow lifecycle (see
# src/conversation/) to decide when a pointer is eligible to be cleared.
TERMINAL_STATES = frozenset(
    {
        WorkflowState.COMPLETED,
        WorkflowState.REJECTED,
        WorkflowState.SAVED_AS_DRAFT,
        WorkflowState.FAILED,
    }
)
