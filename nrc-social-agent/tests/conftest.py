"""Session-wide test safety net (Milestone 11B, Part N).

`_block_real_network_calls` is autouse and applies to every single test in
this suite: any test that reaches a real `httpx.get()`/`httpx.post()` call
— which is the only way this codebase ever talks to Meta's Graph API (see
`src/publisher/instagram/client.py`) — fails loudly and immediately,
rather than silently making a real network request. A test that needs an
HTTP response uses `unittest.mock.patch("httpx.get", ...)` /
`patch("httpx.post", ...)` (see tests/test_publisher_instagram_client.py)
or, more commonly, a hand-rolled fake `MetaHttpClient` (a plain object
with `create_media_container`/`get_container_status`/`publish_media`/
`debug_token`/`get_account_identity` methods — see
tests/test_publisher_instagram_publisher.py and
tests/test_publisher_instagram_diagnostics.py); either approach replaces
`httpx.get`/`httpx.post` (or bypasses them entirely) before this fixture's
raising stub is ever reached, so legitimate mocked tests are unaffected.

This exists because Milestones 10/11A/11B all carry an explicit,
repeatedly-stated constraint: no test may ever make a real Meta API call,
under any circumstance, including when real credentials happen to be
present in the environment a test runs in."""

from __future__ import annotations

import httpx
import pytest


def _raise_on_real_network_call(*args, **kwargs):
    raise AssertionError(
        "A test attempted a real httpx.get()/httpx.post() call. Every Meta HTTP "
        "operation in this test suite must be mocked (patch httpx.get/httpx.post, "
        "or inject a fake MetaHttpClient) — no test may ever reach a real network "
        "endpoint, regardless of what credentials happen to be set in the environment."
    )


@pytest.fixture(autouse=True)
def _block_real_network_calls(monkeypatch):
    monkeypatch.setattr(httpx, "get", _raise_on_real_network_call)
    monkeypatch.setattr(httpx, "post", _raise_on_real_network_call)
    yield
