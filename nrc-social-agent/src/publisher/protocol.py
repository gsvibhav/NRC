"""The generic, platform-neutral publisher contract every adapter
(Instagram now; LinkedIn/Threads/website adapters later) implements
identically.

Checkpoint-oriented (`advance()`), not a monolithic irreversible
`publish()`, per this milestone's own guidance: Instagram's Content
Publishing flow has multiple recoverable steps (media access, container
creation, container-status polling, the publish call itself), and a
single `advance()` call performs exactly *one* durable step, returning
before continuing — `dispatch_service.py` persists the resulting
checkpoint after every single call, so a crash between any two steps
never loses more progress than the one step in flight.

`advance()` never persists the execution itself, never sends a Telegram
message, and never returns anything but the two narrow dataclasses in
`models.py` — see their own docstrings for why a raw HTTP-library object,
a secret, or a full platform response cannot travel through this
boundary even by accident.
"""

from __future__ import annotations

from typing import Protocol

from ..execution.models import ExecutionDocument
from ..publication.models import PublicationPackage
from .models import PublisherStepResult


class Publisher(Protocol):
    def advance(self, execution: ExecutionDocument, publication: PublicationPackage) -> PublisherStepResult:
        """Perform exactly one durable step forward from
        `execution.checkpoint`, using only `execution`/`publication`'s
        own fields — never a workflow document, a content-plan record,
        clarification history, or draft-version history. Raises
        `PublisherPermanentError`/`PublisherRetryableError`/
        `PublisherAmbiguousError` (see errors.py) for anything that isn't
        a durable forward step; never raises for an ordinary "not ready
        yet, check again" polling result — that's expressed as a
        returned `PublisherStepResult` whose checkpoint hasn't advanced."""
        ...
