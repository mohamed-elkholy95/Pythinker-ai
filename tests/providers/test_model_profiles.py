from pythinker.providers.model_profiles import ModelProfile, canonical_model_id, get_profile


def test_canonical_strips_provider_prefix():
    assert canonical_model_id("openai-codex/gpt-5.5") == "gpt-5.5"
    assert canonical_model_id("anthropic/claude-opus-4-7") == "claude-opus-4-7"
    assert canonical_model_id("gpt-5.5") == "gpt-5.5"


def test_gpt_5_5_profile_has_codex_oauth_caps():
    profile = get_profile("openai-codex/gpt-5.5")
    assert isinstance(profile, ModelProfile)
    assert profile.context == 400_000
    assert profile.input == 272_000
    assert profile.output == 128_000
    assert profile.encoding == "o200k_base"


def test_gpt_5_5_profile_on_openai_direct_api_has_1m_context():
    profile = get_profile("openai/gpt-5.5")
    assert profile is not None
    assert profile.context == 1_050_000
    assert profile.input == 1_050_000
    assert profile.output == 128_000


def test_bare_id_falls_back_to_direct_api_row():
    profile = get_profile("gpt-5.5")
    assert profile is not None
    assert profile.input == 1_050_000


def test_claude_opus_4_7_profile_uses_anthropic_tokenizer():
    profile = get_profile("anthropic/claude-opus-4-7")
    assert profile is not None
    assert profile.input >= 200_000
    assert profile.encoding == "cl100k_base"


def test_unknown_model_returns_none():
    assert get_profile("definitely-not-a-real-model/v999") is None


def test_gpt_5_x_family_default_when_minor_unspecified():
    codex = get_profile("openai-codex/gpt-5.6")
    assert codex is not None
    assert codex.input == 272_000
    direct = get_profile("openai/gpt-5.6")
    assert direct is not None
    assert direct.input >= 1_050_000
