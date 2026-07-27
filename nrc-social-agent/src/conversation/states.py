"""Conversation states — distinct from WorkflowState (see src/workflow/states.py).

A conversation record tracks only whether a Telegram user currently has an
active workflow pointer, not any workflow's own lifecycle.
"""

from __future__ import annotations

from enum import Enum


class ConversationState(str, Enum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
