"""A controlled registry mapping a publisher identifier (e.g.
`"instagram"`, resolved deterministically from the publication's channel
— see src/execution/models.py's `resolve_publisher()`) to its concrete
`Publisher` implementation.

`src/execution/dispatch_service.py` is the only caller — handlers.py
never instantiates or selects a publisher directly. Resolution is
deliberately narrow: an unknown name raises
`UnknownPublisherError` (reused from `src/execution/errors.py`, so
callers only ever handle one exception vocabulary for dispatch), never
falls back to any default.
"""

from __future__ import annotations

from ..execution.errors import UnknownPublisherError
from .protocol import Publisher


class PublisherRegistry:
    def __init__(self, publishers: dict[str, Publisher]) -> None:
        self._publishers = dict(publishers)

    def resolve(self, publisher_name: str) -> Publisher:
        publisher = self._publishers.get(publisher_name)
        if publisher is None:
            raise UnknownPublisherError(f"no publisher registered for publisher_name={publisher_name!r}")
        return publisher
