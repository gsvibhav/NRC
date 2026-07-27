import logging

import pytest

from src.logging_config import configure_logging


def test_configure_logging_sets_root_level():
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_accepts_lowercase_level():
    configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_rejects_invalid_level():
    with pytest.raises(ValueError, match="NOT_A_LEVEL"):
        configure_logging("NOT_A_LEVEL")
