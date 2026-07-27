import json

import pytest

from src.ai.draft_models import InstagramReelCaptionContent
from src.ai.draft_parser import parse_draft_response
from src.ai.errors import DraftResponseSchemaMismatchError, MalformedDraftResponseError

VALID_RESPONSE = {"caption": "A great caption about NRC's founder.", "hashtags": ["nrc"], "cta": ""}


def _json(data):
    return json.dumps(data)


def test_parses_a_well_formed_response():
    content = parse_draft_response("instagram_reel_caption", _json(VALID_RESPONSE))

    assert isinstance(content, InstagramReelCaptionContent)
    assert content.caption == VALID_RESPONSE["caption"]
    assert content.hashtags == ["nrc"]
    assert content.cta is None  # empty string normalized to None


def test_rejects_unregistered_output_type():
    with pytest.raises(DraftResponseSchemaMismatchError):
        parse_draft_response("linkedin_post", _json(VALID_RESPONSE))


def test_rejects_invalid_json():
    with pytest.raises(MalformedDraftResponseError):
        parse_draft_response("instagram_reel_caption", "{not valid")


def test_rejects_non_object_json():
    with pytest.raises(MalformedDraftResponseError):
        parse_draft_response("instagram_reel_caption", "[1, 2]")


def test_rejects_missing_required_field():
    data = dict(VALID_RESPONSE)
    del data["hashtags"]

    with pytest.raises(DraftResponseSchemaMismatchError, match="hashtags"):
        parse_draft_response("instagram_reel_caption", _json(data))


def test_rejects_extra_field():
    data = {**VALID_RESPONSE, "extra": "nope"}

    with pytest.raises(DraftResponseSchemaMismatchError):
        parse_draft_response("instagram_reel_caption", _json(data))


def test_rejects_a_second_variant_field():
    # A response that smuggles in a second caption/variant under any name
    # is just an unrecognized extra field from the parser's point of view.
    data = {**VALID_RESPONSE, "caption_option_2": "an alternate caption"}

    with pytest.raises(DraftResponseSchemaMismatchError):
        parse_draft_response("instagram_reel_caption", _json(data))


def test_rejects_wrong_type_for_caption():
    data = {**VALID_RESPONSE, "caption": 12345}

    with pytest.raises(DraftResponseSchemaMismatchError):
        parse_draft_response("instagram_reel_caption", _json(data))


def test_rejects_markdown_fenced_response():
    fenced = "```json\n" + _json(VALID_RESPONSE) + "\n```"

    with pytest.raises(MalformedDraftResponseError):
        parse_draft_response("instagram_reel_caption", fenced)


def test_never_evals_input():
    malicious = "__import__('os').system('echo pwned')"

    with pytest.raises(MalformedDraftResponseError):
        parse_draft_response("instagram_reel_caption", malicious)


def test_never_extracts_json_from_surrounding_prose():
    wrapped = "Here is the caption: " + _json(VALID_RESPONSE)

    with pytest.raises(MalformedDraftResponseError):
        parse_draft_response("instagram_reel_caption", wrapped)
