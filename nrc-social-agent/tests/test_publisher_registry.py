import pytest

from src.execution.errors import UnknownPublisherError
from src.publisher.registry import PublisherRegistry


def test_resolve_returns_the_registered_publisher():
    fake_publisher = object()
    registry = PublisherRegistry({"instagram": fake_publisher})

    assert registry.resolve("instagram") is fake_publisher


def test_resolve_rejects_unknown_publisher_without_fallback():
    registry = PublisherRegistry({"instagram": object()})

    with pytest.raises(UnknownPublisherError):
        registry.resolve("linkedin")


def test_resolve_rejects_empty_registry():
    registry = PublisherRegistry({})

    with pytest.raises(UnknownPublisherError):
        registry.resolve("instagram")
