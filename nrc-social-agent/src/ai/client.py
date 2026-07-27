"""Claude API adapter — the only module in this codebase allowed to import
the `anthropic` SDK. Application code (handlers, services) must go through
ClaudeClient, never the SDK directly.

Retry strategy: transient failures (timeouts, connection errors, 429, 5xx)
are retried entirely by the Anthropic SDK's own `max_retries` client
option (Config.anthropic_max_retries) — see the Anthropic client
constructor below. We do not layer an additional application-level retry
loop on top of that, to avoid multiplying request counts (and cost)
unexpectedly. If the SDK's own retries are exhausted, the failure surfaces
here as one of the Claude*Error types and is not retried further within
this call — callers (analysis_service.py, clarification_service.py) don't
retry either; a failed attempt is persisted as FAILED or marked
`pending_retry` (see each service), never silently retried.

Five public methods share one internal `_send()` helper (added Milestone
4B, when a second structured-output call — the clarification decision —
needed the exact same request/exception-mapping/response-shaping logic as
`analyze_image`'s): `analyze_image()` (media analysis, Milestone 4A),
`decide_clarification()` (the adaptive clarification decision, Milestone
4B), `plan_content()` (the content-planning request, Milestone 5),
`generate_draft()` (the primary-draft generation request, Milestone 6),
and `edit_draft()` (the draft-edit request, Milestone 7). All five return
the same minimal `ClaudeAnalysisResponse` — never the raw SDK response, so
no thinking-block content or full response can leak into logs or
persistence by accident.

Never log: the API key, full prompts, full model responses, or raw media
payloads. Only high-level identifiers (model, duration, token counts).
"""

from __future__ import annotations

import base64
import logging
import time

import anthropic

from .errors import (
    AnalysisRefusedError,
    ClaudeAuthenticationError,
    ClaudePermanentError,
    ClaudeRateLimitError,
    ClaudeTimeoutError,
    ClaudeTransientError,
)
from .prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT, USER_PROMPT

logger = logging.getLogger(__name__)

# Request-level effort tuning for this workload: single-pass structured
# extraction from one image, not open-ended agentic work. "medium" balances
# analysis quality against cost/latency for a repetitive per-upload call —
# see README.md for the full rationale. Adaptive thinking is left at the
# model's own default (on) rather than explicitly configured, since this
# model's thinking behavior is on-by-default and disabling it is subject to
# extra restrictions we don't need to opt into.
ANALYSIS_EFFORT = "medium"

# Same reasoning as ANALYSIS_EFFORT: a single structured decision per
# turn, not open-ended work. Kept at "medium" rather than "low" because
# question quality (grounded, non-generic, correctly prioritized) is the
# entire point of this milestone and is worth the same effort tier as
# image analysis.
CLARIFICATION_EFFORT = "medium"

# Higher than the other two: content planning is a genuine strategic
# synthesis task (deciding what's worth creating and why, across several
# candidate outputs, while actively avoiding the generic/duplicate-output
# failure modes the milestone explicitly warns about) — worth the extra
# reasoning depth "high" buys, unlike the more mechanical extraction
# (analysis) or single-decision (clarification) tasks above.
CONTENT_PLANNING_EFFORT = "high"

# Back down to "medium", not "high": by the time this call happens, the
# hard strategic reasoning (what to create, for whom, why) is already
# finished and persisted by content_planning_service.py — this call's job
# is to faithfully execute one already-decided output, not to weigh
# options. "medium" matches ANALYSIS_EFFORT/CLARIFICATION_EFFORT's
# reasoning: a single, well-scoped task per call, not open-ended synthesis.
DRAFT_GENERATION_EFFORT = "medium"

# Same reasoning as DRAFT_GENERATION_EFFORT: a bounded revision of one
# already-existing draft against one specific instruction, not a fresh
# strategic decision — "medium" is the right tier, not "high".
DRAFT_EDIT_EFFORT = "medium"


class ClaudeAnalysisResponse:
    """The parts of a Claude response the rest of the pipeline needs — never
    the raw SDK response object, and never any thinking-block content."""

    __slots__ = ("text", "input_tokens", "output_tokens", "model", "duration_seconds")

    def __init__(
        self, *, text: str, input_tokens: int, output_tokens: int, model: str, duration_seconds: float
    ) -> None:
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model = model
        self.duration_seconds = duration_seconds


class ClaudeClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = anthropic.Anthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    def analyze_image(self, image_bytes: bytes, mime_type: str) -> ClaudeAnalysisResponse:
        """Send one image to Claude for single-pass structured analysis.

        Raises a Claude*Error or AnalysisRefusedError (see errors.py) for
        every expected failure. Never returns a result unless Claude
        produced a real, non-refused response with text content.
        """

        encoded = base64.standard_b64encode(image_bytes).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime_type, "data": encoded},
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ]
        return self._send(
            system_prompt=SYSTEM_PROMPT,
            messages=messages,
            response_schema=RESPONSE_SCHEMA,
            effort=ANALYSIS_EFFORT,
        )

    def decide_clarification(
        self, *, system_prompt: str, user_prompt: str, response_schema: dict
    ) -> ClaudeAnalysisResponse:
        """One adaptive-clarification decision call (Milestone 4B): text
        only (the media itself isn't re-sent — the already-persisted
        analysis summary carries what's needed, see
        clarification_prompts.py). Takes the fully-constructed prompt and
        schema as parameters rather than importing clarification_prompts.py
        here, so this adapter stays a pure low-level Claude wrapper —
        prompt construction and versioning stay owned by the caller
        (clarification_service.py).

        Raises a Claude*Error or AnalysisRefusedError (see errors.py) for
        every expected failure, exactly like analyze_image().
        """

        messages = [{"role": "user", "content": user_prompt}]
        return self._send(
            system_prompt=system_prompt,
            messages=messages,
            response_schema=response_schema,
            effort=CLARIFICATION_EFFORT,
        )

    def plan_content(
        self, *, system_prompt: str, user_prompt: str, response_schema: dict
    ) -> ClaudeAnalysisResponse:
        """One content-planning request (Milestone 5): text only, exactly
        like decide_clarification() — the media itself isn't re-sent, and
        prompt/schema construction stays owned by the caller
        (content_planning_service.py). Raises a Claude*Error or
        AnalysisRefusedError (see errors.py) for every expected failure.
        """

        messages = [{"role": "user", "content": user_prompt}]
        return self._send(
            system_prompt=system_prompt,
            messages=messages,
            response_schema=response_schema,
            effort=CONTENT_PLANNING_EFFORT,
        )

    def generate_draft(
        self, *, system_prompt: str, user_prompt: str, response_schema: dict
    ) -> ClaudeAnalysisResponse:
        """One draft-generation request (Milestone 6): text only, exactly
        like decide_clarification()/plan_content() — the media itself
        isn't re-sent, and prompt/schema construction stays owned by the
        caller (draft_generation_service.py). The planning decision
        (which output, for whom, with what strategy) is already final by
        the time this is called; this call only writes the one selected
        output, it does not re-decide anything.

        Raises a Claude*Error or AnalysisRefusedError (see errors.py) for
        every expected failure, exactly like the other methods.
        """

        messages = [{"role": "user", "content": user_prompt}]
        return self._send(
            system_prompt=system_prompt,
            messages=messages,
            response_schema=response_schema,
            effort=DRAFT_GENERATION_EFFORT,
        )

    def edit_draft(
        self, *, system_prompt: str, user_prompt: str, response_schema: dict
    ) -> ClaudeAnalysisResponse:
        """One draft-edit request (Milestone 7): text only, exactly like
        generate_draft() — the media itself isn't re-sent, and
        prompt/schema construction stays owned by the caller
        (draft_editing_service.py). The output type and platform are
        already final by the time this is called; this call only revises
        the current draft according to the user's instruction, it does
        not reconsider which output should exist.

        Raises a Claude*Error or AnalysisRefusedError (see errors.py) for
        every expected failure, exactly like the other methods.
        """

        messages = [{"role": "user", "content": user_prompt}]
        return self._send(
            system_prompt=system_prompt,
            messages=messages,
            response_schema=response_schema,
            effort=DRAFT_EDIT_EFFORT,
        )

    def _send(
        self, *, system_prompt: str, messages: list, response_schema: dict, effort: str
    ) -> ClaudeAnalysisResponse:
        started = time.monotonic()
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                output_config={
                    "format": {"type": "json_schema", "schema": response_schema},
                    "effort": effort,
                },
                messages=messages,
            )
        except anthropic.AuthenticationError as exc:
            raise ClaudeAuthenticationError("authentication failed") from exc
        except anthropic.RateLimitError as exc:
            raise ClaudeRateLimitError("rate limited after SDK retries were exhausted") from exc
        except anthropic.APITimeoutError as exc:
            raise ClaudeTimeoutError("request timed out after SDK retries were exhausted") from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeTransientError("connection error after SDK retries were exhausted") from exc
        except anthropic.BadRequestError as exc:
            raise ClaudePermanentError("invalid request") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ClaudeTransientError(
                    f"server error (status={exc.status_code}) after SDK retries were exhausted"
                ) from exc
            raise ClaudePermanentError(f"request failed with status {exc.status_code}") from exc
        duration_seconds = time.monotonic() - started

        if response.stop_reason == "refusal":
            raise AnalysisRefusedError("Claude declined to fulfill this request")

        text_blocks = [block.text for block in response.content if block.type == "text"]
        if not text_blocks:
            raise ClaudePermanentError(f"no text content in response (stop_reason={response.stop_reason})")

        usage = response.usage
        return ClaudeAnalysisResponse(
            text=text_blocks[0],
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model=response.model,
            duration_seconds=duration_seconds,
        )
