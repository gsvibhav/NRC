from src.ai.prompts import ANALYSIS_PROMPT_VERSION, RESPONSE_SCHEMA, SYSTEM_PROMPT, USER_PROMPT


def test_prompt_version_is_a_positive_int():
    assert isinstance(ANALYSIS_PROMPT_VERSION, int)
    assert ANALYSIS_PROMPT_VERSION > 0


def test_system_prompt_forbids_caption_generation():
    assert "caption" in SYSTEM_PROMPT.lower()
    assert "out of scope" in SYSTEM_PROMPT.lower() or "do not generate" in SYSTEM_PROMPT.lower()


def test_system_prompt_forbids_real_person_identification():
    assert "identif" in SYSTEM_PROMPT.lower()


def test_user_prompt_is_non_empty_string():
    assert isinstance(USER_PROMPT, str)
    assert USER_PROMPT.strip()


def test_response_schema_rejects_additional_properties():
    assert RESPONSE_SCHEMA["additionalProperties"] is False


def test_response_schema_required_fields_match_properties():
    assert set(RESPONSE_SCHEMA["required"]) == set(RESPONSE_SCHEMA["properties"].keys())


def test_response_schema_summary_is_string_and_rest_are_arrays():
    assert RESPONSE_SCHEMA["properties"]["summary"]["type"] == "string"

    for name, definition in RESPONSE_SCHEMA["properties"].items():
        if name == "summary":
            continue
        assert definition["type"] == "array"
        assert definition["items"]["type"] == "string"
