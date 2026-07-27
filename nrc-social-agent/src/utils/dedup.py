"""In-process, best-effort duplicate-update guard.

Not persisted anywhere — resets on restart. This exists only to avoid
reprocessing the exact same Telegram update if it's ever delivered twice
within a single process's lifetime (e.g. a transient long-polling
redelivery). It is explicitly not a substitute for persistent idempotency,
which stays out of scope until workflow-state persistence exists (see
ROADMAP.md).
"""

from __future__ import annotations

from collections import OrderedDict

DEFAULT_MAX_TRACKED_UPDATES = 1000


class SeenUpdateTracker:
    def __init__(self, max_tracked: int = DEFAULT_MAX_TRACKED_UPDATES) -> None:
        self._max_tracked = max_tracked
        self._seen: OrderedDict[int, None] = OrderedDict()

    def seen_before(self, update_id: int) -> bool:
        """Return True if `update_id` was already recorded; otherwise record
        it and return False."""

        if update_id in self._seen:
            self._seen.move_to_end(update_id)
            return True

        self._seen[update_id] = None
        if len(self._seen) > self._max_tracked:
            self._seen.popitem(last=False)
        return False
