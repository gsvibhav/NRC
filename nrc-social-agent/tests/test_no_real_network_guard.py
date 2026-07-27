import httpx
import pytest


def test_real_httpx_get_is_blocked_by_the_session_wide_guard():
    with pytest.raises(AssertionError):
        httpx.get("https://graph.instagram.com/v25.0/debug_token")


def test_real_httpx_post_is_blocked_by_the_session_wide_guard():
    with pytest.raises(AssertionError):
        httpx.post("https://graph.instagram.com/v25.0/123/media")


def test_httpx_meta_client_default_host_matches_the_blocked_host():
    # Documents exactly which host the guard fixture protects against —
    # this is the one and only base URL the real client ever targets.
    from src.publisher.instagram.client import HttpxMetaHttpClient

    client = HttpxMetaHttpClient()
    assert client._base_url == "https://graph.instagram.com"


def test_no_test_file_hardcodes_a_real_looking_meta_access_token():
    import pathlib
    import re

    tests_dir = pathlib.Path(__file__).parent
    # Meta long-lived user access tokens are long opaque strings, often
    # prefixed EAAG/IGAA. Placeholder/fake tokens used throughout this
    # suite (e.g. "fake-token", "tok-1", "token-abc") never match this.
    suspicious = re.compile(r"\b(EAAG|IGAA)[A-Za-z0-9]{20,}\b")
    for path in tests_dir.glob("*.py"):
        text = path.read_text()
        assert not suspicious.search(text), f"possible real Meta access token literal in {path.name}"
