from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from src.ai.client import ClaudeAnalysisResponse, ClaudeClient
from src.ai.errors import (
    AnalysisRefusedError,
    ClaudeAuthenticationError,
    ClaudePermanentError,
    ClaudeRateLimitError,
    ClaudeTimeoutError,
    ClaudeTransientError,
)

FAKE_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _fake_response(*, text="{}", stop_reason="end_turn", model="claude-opus-5"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=123, output_tokens=45),
        model=model,
    )


def _make_client(monkeypatch, create_side_effect=None, create_return_value=None):
    fake_sdk_client = MagicMock()
    if create_side_effect is not None:
        fake_sdk_client.messages.create.side_effect = create_side_effect
    else:
        fake_sdk_client.messages.create.return_value = create_return_value

    monkeypatch.setattr("src.ai.client.anthropic.Anthropic", MagicMock(return_value=fake_sdk_client))

    client = ClaudeClient(
        api_key="fake-key", model="claude-opus-5", max_tokens=4096, timeout_seconds=60, max_retries=2
    )
    return client, fake_sdk_client


# --- construction ---------------------------------------------------------


def test_claude_client_configures_sdk_client_with_timeout_and_retries(monkeypatch):
    anthropic_ctor = MagicMock(return_value=MagicMock())
    monkeypatch.setattr("src.ai.client.anthropic.Anthropic", anthropic_ctor)

    ClaudeClient(api_key="fake-key", model="claude-opus-5", max_tokens=4096, timeout_seconds=30, max_retries=3)

    anthropic_ctor.assert_called_once_with(api_key="fake-key", timeout=30, max_retries=3)


# --- analyze_image: success -------------------------------------------------


def test_analyze_image_returns_typed_response_on_success(monkeypatch):
    response = _fake_response(text='{"summary": "ok"}')
    client, sdk_client = _make_client(monkeypatch, create_return_value=response)

    result = client.analyze_image(b"fake-image-bytes", "image/jpeg")

    assert isinstance(result, ClaudeAnalysisResponse)
    assert result.text == '{"summary": "ok"}'
    assert result.input_tokens == 123
    assert result.output_tokens == 45
    assert result.model == "claude-opus-5"
    assert result.duration_seconds >= 0


def test_analyze_image_sends_base64_encoded_image_and_structured_output_config(monkeypatch):
    response = _fake_response()
    client, sdk_client = _make_client(monkeypatch, create_return_value=response)

    client.analyze_image(b"raw-bytes", "image/png")

    _, kwargs = sdk_client.messages.create.call_args
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    image_block = kwargs["messages"][0]["content"][0]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"
    # Never send raw bytes directly — must be base64 text.
    assert isinstance(image_block["source"]["data"], str)


def test_analyze_image_picks_first_text_block_when_a_thinking_block_precedes_it(monkeypatch):
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="thinking", text=None),
            SimpleNamespace(type="text", text='{"summary": "ok"}'),
        ],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        model="claude-opus-5",
    )
    client, _ = _make_client(monkeypatch, create_return_value=response)

    result = client.analyze_image(b"bytes", "image/jpeg")

    assert result.text == '{"summary": "ok"}'


# --- analyze_image: refusal and missing content -----------------------------


def test_analyze_image_raises_refused_error_on_refusal_stop_reason(monkeypatch):
    response = _fake_response(stop_reason="refusal")
    client, _ = _make_client(monkeypatch, create_return_value=response)

    with pytest.raises(AnalysisRefusedError):
        client.analyze_image(b"bytes", "image/jpeg")


def test_analyze_image_raises_permanent_error_when_no_text_content(monkeypatch):
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="thinking", text=None)],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        model="claude-opus-5",
    )
    client, _ = _make_client(monkeypatch, create_return_value=response)

    with pytest.raises(ClaudePermanentError):
        client.analyze_image(b"bytes", "image/jpeg")


# --- analyze_image: SDK exception mapping -----------------------------------


def test_analyze_image_maps_authentication_error(monkeypatch):
    err = anthropic.AuthenticationError(
        "bad key", response=httpx.Response(401, request=FAKE_REQUEST), body=None
    )
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeAuthenticationError):
        client.analyze_image(b"bytes", "image/jpeg")


def test_analyze_image_maps_rate_limit_error(monkeypatch):
    err = anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=FAKE_REQUEST), body=None
    )
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeRateLimitError):
        client.analyze_image(b"bytes", "image/jpeg")


def test_analyze_image_maps_timeout_error(monkeypatch):
    err = anthropic.APITimeoutError(request=FAKE_REQUEST)
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeTimeoutError):
        client.analyze_image(b"bytes", "image/jpeg")


def test_analyze_image_maps_connection_error(monkeypatch):
    err = anthropic.APIConnectionError(request=FAKE_REQUEST)
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeTransientError):
        client.analyze_image(b"bytes", "image/jpeg")


def test_analyze_image_maps_bad_request_error(monkeypatch):
    err = anthropic.BadRequestError(
        "invalid request", response=httpx.Response(400, request=FAKE_REQUEST), body=None
    )
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudePermanentError):
        client.analyze_image(b"bytes", "image/jpeg")


def test_analyze_image_maps_5xx_status_error_to_transient(monkeypatch):
    err = anthropic.APIStatusError(
        "server error", response=httpx.Response(503, request=FAKE_REQUEST), body=None
    )
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeTransientError):
        client.analyze_image(b"bytes", "image/jpeg")


def test_analyze_image_maps_non_5xx_status_error_to_permanent(monkeypatch):
    err = anthropic.APIStatusError(
        "forbidden", response=httpx.Response(403, request=FAKE_REQUEST), body=None
    )
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudePermanentError):
        client.analyze_image(b"bytes", "image/jpeg")


# --- decide_clarification() (Milestone 4B) ----------------------------


def test_decide_clarification_sends_text_only_message(monkeypatch):
    response = _fake_response(text='{"decision": "CONTINUE"}')
    client, sdk_client = _make_client(monkeypatch, create_return_value=response)

    schema = {"type": "object"}
    result = client.decide_clarification(
        system_prompt="You are a strategist.", user_prompt="What do we know so far?", response_schema=schema
    )

    assert result.text == '{"decision": "CONTINUE"}'
    _, kwargs = sdk_client.messages.create.call_args
    assert kwargs["system"] == "You are a strategist."
    assert kwargs["messages"] == [{"role": "user", "content": "What do we know so far?"}]
    assert kwargs["output_config"]["format"]["schema"] is schema
    # No image content block anywhere in a clarification request.
    assert "image" not in str(kwargs["messages"])


def test_decide_clarification_raises_refused_error_on_refusal(monkeypatch):
    response = _fake_response(stop_reason="refusal")
    client, _ = _make_client(monkeypatch, create_return_value=response)

    with pytest.raises(AnalysisRefusedError):
        client.decide_clarification(system_prompt="s", user_prompt="u", response_schema={})


def test_decide_clarification_maps_authentication_error(monkeypatch):
    err = anthropic.AuthenticationError(
        "bad key", response=httpx.Response(401, request=FAKE_REQUEST), body=None
    )
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeAuthenticationError):
        client.decide_clarification(system_prompt="s", user_prompt="u", response_schema={})


def test_decide_clarification_maps_timeout_error(monkeypatch):
    err = anthropic.APITimeoutError(request=FAKE_REQUEST)
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeTimeoutError):
        client.decide_clarification(system_prompt="s", user_prompt="u", response_schema={})


def test_decide_clarification_uses_a_different_response_schema_per_call(monkeypatch):
    response = _fake_response()
    client, sdk_client = _make_client(monkeypatch, create_return_value=response)

    schema_a = {"type": "object", "title": "a"}
    schema_b = {"type": "object", "title": "b"}

    client.decide_clarification(system_prompt="s", user_prompt="u", response_schema=schema_a)
    client.decide_clarification(system_prompt="s", user_prompt="u", response_schema=schema_b)

    first_call, second_call = sdk_client.messages.create.call_args_list
    assert first_call.kwargs["output_config"]["format"]["schema"] is schema_a
    assert second_call.kwargs["output_config"]["format"]["schema"] is schema_b


# --- plan_content() (Milestone 5) --------------------------------------


def test_plan_content_sends_text_only_message(monkeypatch):
    response = _fake_response(text='{"strategy": {}}')
    client, sdk_client = _make_client(monkeypatch, create_return_value=response)

    schema = {"type": "object"}
    result = client.plan_content(
        system_prompt="You are a strategist.", user_prompt="Plan this asset.", response_schema=schema
    )

    assert result.text == '{"strategy": {}}'
    _, kwargs = sdk_client.messages.create.call_args
    assert kwargs["system"] == "You are a strategist."
    assert kwargs["messages"] == [{"role": "user", "content": "Plan this asset."}]
    assert kwargs["output_config"]["format"]["schema"] is schema
    assert "image" not in str(kwargs["messages"])


def test_plan_content_uses_high_effort(monkeypatch):
    from src.ai.client import CONTENT_PLANNING_EFFORT

    response = _fake_response()
    client, sdk_client = _make_client(monkeypatch, create_return_value=response)

    client.plan_content(system_prompt="s", user_prompt="u", response_schema={})

    _, kwargs = sdk_client.messages.create.call_args
    assert kwargs["output_config"]["effort"] == CONTENT_PLANNING_EFFORT == "high"


def test_plan_content_raises_refused_error_on_refusal(monkeypatch):
    response = _fake_response(stop_reason="refusal")
    client, _ = _make_client(monkeypatch, create_return_value=response)

    with pytest.raises(AnalysisRefusedError):
        client.plan_content(system_prompt="s", user_prompt="u", response_schema={})


def test_plan_content_maps_rate_limit_error(monkeypatch):
    err = anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=FAKE_REQUEST), body=None
    )
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeRateLimitError):
        client.plan_content(system_prompt="s", user_prompt="u", response_schema={})


# --- generate_draft() (Milestone 6) -------------------------------------


def test_generate_draft_sends_text_only_message(monkeypatch):
    response = _fake_response(text='{"caption": "hi"}')
    client, sdk_client = _make_client(monkeypatch, create_return_value=response)

    schema = {"type": "object"}
    result = client.generate_draft(
        system_prompt="You are a writer.", user_prompt="Write the caption.", response_schema=schema
    )

    assert result.text == '{"caption": "hi"}'
    _, kwargs = sdk_client.messages.create.call_args
    assert kwargs["system"] == "You are a writer."
    assert kwargs["messages"] == [{"role": "user", "content": "Write the caption."}]
    assert kwargs["output_config"]["format"]["schema"] is schema
    assert "image" not in str(kwargs["messages"])


def test_generate_draft_uses_medium_effort(monkeypatch):
    from src.ai.client import DRAFT_GENERATION_EFFORT

    response = _fake_response()
    client, sdk_client = _make_client(monkeypatch, create_return_value=response)

    client.generate_draft(system_prompt="s", user_prompt="u", response_schema={})

    _, kwargs = sdk_client.messages.create.call_args
    assert kwargs["output_config"]["effort"] == DRAFT_GENERATION_EFFORT == "medium"


def test_generate_draft_raises_refused_error_on_refusal(monkeypatch):
    response = _fake_response(stop_reason="refusal")
    client, _ = _make_client(monkeypatch, create_return_value=response)

    with pytest.raises(AnalysisRefusedError):
        client.generate_draft(system_prompt="s", user_prompt="u", response_schema={})


def test_generate_draft_maps_timeout_error(monkeypatch):
    err = anthropic.APITimeoutError(request=FAKE_REQUEST)
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeTimeoutError):
        client.generate_draft(system_prompt="s", user_prompt="u", response_schema={})


# --- edit_draft() (Milestone 7) -----------------------------------------


def test_edit_draft_sends_text_only_message(monkeypatch):
    response = _fake_response(text='{"caption": "revised"}')
    client, sdk_client = _make_client(monkeypatch, create_return_value=response)

    schema = {"type": "object"}
    result = client.edit_draft(
        system_prompt="You are revising a draft.", user_prompt="Make it shorter.", response_schema=schema
    )

    assert result.text == '{"caption": "revised"}'
    _, kwargs = sdk_client.messages.create.call_args
    assert kwargs["system"] == "You are revising a draft."
    assert kwargs["messages"] == [{"role": "user", "content": "Make it shorter."}]
    assert kwargs["output_config"]["format"]["schema"] is schema
    assert "image" not in str(kwargs["messages"])


def test_edit_draft_uses_medium_effort(monkeypatch):
    from src.ai.client import DRAFT_EDIT_EFFORT

    response = _fake_response()
    client, sdk_client = _make_client(monkeypatch, create_return_value=response)

    client.edit_draft(system_prompt="s", user_prompt="u", response_schema={})

    _, kwargs = sdk_client.messages.create.call_args
    assert kwargs["output_config"]["effort"] == DRAFT_EDIT_EFFORT == "medium"


def test_edit_draft_raises_refused_error_on_refusal(monkeypatch):
    response = _fake_response(stop_reason="refusal")
    client, _ = _make_client(monkeypatch, create_return_value=response)

    with pytest.raises(AnalysisRefusedError):
        client.edit_draft(system_prompt="s", user_prompt="u", response_schema={})


def test_edit_draft_maps_timeout_error(monkeypatch):
    err = anthropic.APITimeoutError(request=FAKE_REQUEST)
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeTimeoutError):
        client.edit_draft(system_prompt="s", user_prompt="u", response_schema={})


def test_edit_draft_maps_authentication_error(monkeypatch):
    err = anthropic.AuthenticationError(
        "bad key", response=httpx.Response(401, request=FAKE_REQUEST), body=None
    )
    client, _ = _make_client(monkeypatch, create_side_effect=err)

    with pytest.raises(ClaudeAuthenticationError):
        client.edit_draft(system_prompt="s", user_prompt="u", response_schema={})
