"""Conversation resolution: maps a Telegram user to their active workflow.

Telegram messages don't naturally carry a workflow ID — this package is
what lets a future reply (or, this milestone, /status and /cancel) find
"which workflow is this user talking about right now" without the user
supplying one. See src/conversation/resolution.py for the single entry
point application code should call to answer that question.
"""
