"""ProviderSpec.signup_url backfill is gated by signup_url_required()."""

from pythinker.providers.registry import PROVIDERS, signup_url_required


def test_signup_url_required_predicate_excludes_gateways_locals_direct_oauth():
    """The predicate must exclude every category we don't want to backfill."""
    for spec in PROVIDERS:
        if spec.is_gateway or spec.is_local or spec.is_direct or spec.is_oauth:
            assert not signup_url_required(spec), (
                f"{spec.name} should not require a signup_url "
                f"(gateway={spec.is_gateway}, local={spec.is_local}, "
                f"direct={spec.is_direct}, oauth={spec.is_oauth})"
            )


def test_every_required_provider_has_a_signup_url():
    """Predicate-required specs must each have a non-empty signup_url."""
    missing = [
        spec.name for spec in PROVIDERS
        if signup_url_required(spec) and not spec.signup_url
    ]
    assert not missing, f"signup_url missing on: {missing}"


def test_known_signup_urls_match_token_plan_docs():
    """Spot-check a few canonical URLs."""
    by_name = {spec.name: spec for spec in PROVIDERS}
    assert by_name["minimax"].signup_url == (
        "https://platform.minimax.io/user-center/payment/token-plan"
    )
    assert by_name["minimax_anthropic"].signup_url == (
        "https://platform.minimax.io/user-center/payment/token-plan"
    )
    assert by_name["openai"].signup_url == "https://platform.openai.com/api-keys"
    assert by_name["anthropic"].signup_url == (
        "https://console.anthropic.com/settings/keys"
    )


def test_signup_url_required_predicate_returns_true_for_normal_providers():
    """Sanity check the positive path so a broken predicate can't silently
    hollow out the completeness test."""
    by_name = {spec.name: spec for spec in PROVIDERS}
    for name in ("openai", "anthropic", "deepseek", "minimax", "minimax_anthropic"):
        assert signup_url_required(by_name[name]), (
            f"{name} should require a signup_url"
        )


def test_minimax_docs_url_points_at_other_tools_page():
    """docs_url is optional but MiniMax has a single canonical setup page."""
    by_name = {spec.name: spec for spec in PROVIDERS}
    assert by_name["minimax"].docs_url == (
        "https://platform.minimax.io/docs/token-plan/other-tools"
    )
    assert by_name["minimax_anthropic"].docs_url == (
        "https://platform.minimax.io/docs/token-plan/other-tools"
    )


def test_gateway_specs_have_blank_signup_url():
    """Gateways legitimately have no canonical signup URL."""
    by_name = {spec.name: spec for spec in PROVIDERS}
    for name in (
        "openrouter", "aihubmix", "siliconflow",
        "volcengine", "volcengine_coding_plan",
        "byteplus", "byteplus_coding_plan",
    ):
        assert by_name[name].signup_url == "", (
            f"gateway {name} should not carry a signup_url"
        )
