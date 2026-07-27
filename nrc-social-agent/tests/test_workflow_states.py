from src.workflow.states import TERMINAL_STATES, WorkflowState


def test_workflow_state_includes_showing_preview():
    # Milestone 6: SHOWING_PREVIEW is now persisted (docs/WORKFLOW.md §3.7
    # already documented it as GENERATING_CONTENT's success exit — no new
    # state was invented).
    assert WorkflowState.SHOWING_PREVIEW.value == "SHOWING_PREVIEW"
    assert WorkflowState.SHOWING_PREVIEW in WorkflowState


def test_showing_preview_is_not_terminal():
    # A draft awaiting review is not a finished workflow — the new-media
    # guard (docs/WORKFLOW.md §5) must keep blocking new uploads here.
    assert WorkflowState.SHOWING_PREVIEW not in TERMINAL_STATES


def test_terminal_states_unchanged_by_milestone_7():
    # EDITING and (deliberately never-persisted) APPROVED must not be
    # terminal — see states.py's module docstring for why APPROVED is
    # intentionally absent from the enum entirely.
    assert TERMINAL_STATES == frozenset(
        {
            WorkflowState.COMPLETED,
            WorkflowState.REJECTED,
            WorkflowState.SAVED_AS_DRAFT,
            WorkflowState.FAILED,
        }
    )


def test_workflow_state_includes_editing():
    assert WorkflowState.EDITING.value == "EDITING"
    assert WorkflowState.EDITING in WorkflowState


def test_editing_is_not_terminal():
    assert WorkflowState.EDITING not in TERMINAL_STATES


def test_workflow_state_does_not_include_approved():
    # Deliberate: see states.py's module docstring — approval transitions
    # SHOWING_PREVIEW directly to COMPLETED in one atomic write, since
    # there is no distinct finalization step for a persisted APPROVED
    # snapshot to ever meaningfully represent in this system.
    assert "APPROVED" not in {member.value for member in WorkflowState}


def test_workflow_state_full_member_set():
    assert {member.value for member in WorkflowState} == {
        "IDLE",
        "RECEIVING_MEDIA",
        "UPLOADING_MEDIA",
        "ANALYZING_MEDIA",
        "WAITING_FOR_USER",
        "GENERATING_CONTENT",
        "SHOWING_PREVIEW",
        "EDITING",
        "COMPLETED",
        "FAILED",
        "SAVED_AS_DRAFT",
        "REJECTED",
    }
