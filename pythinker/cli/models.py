"""Model information helpers for the onboard wizard.

Model database / autocomplete is temporarily disabled while litellm is
being replaced.  All public function signatures are preserved so callers
continue to work without changes.

While the dynamic catalog is offline, ``RECOMMENDED_BY_PROVIDER`` ships
short, hand-curated model lists for the providers where the wizard
otherwise falls into a "No suggestions available" dead end — most
importantly the OAuth-only providers (OpenAI Codex, GitHub Copilot) that
have no public catalog endpoint.
"""

from __future__ import annotations

from typing import Any

# Hand-curated recommendation lists. Conservative — enough to unblock the
# onboard wizard when the catalog is empty. Add new entries here when a
# provider's "Browse all models" step shows nothing useful.
RECOMMENDED_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    # The OpenAI "latest model" general docs list mini / nano variants for
    # gpt-5.5 / gpt-5.4 as supported model IDs in general — but the
    # chatgpt.com Codex *OAuth* route (https://chatgpt.com/backend-api/codex/responses,
    # the only endpoint pythinker's OpenAICodexProvider talks to) accepts a
    # narrower set. The list below is empirically verified against that
    # backend on 2026-04-29 by scripting an account_id-bound POST per
    # candidate model and reading the HTTP status. Each entry returned
    # 200 + a valid SSE stream; everything else (gpt-5.5-mini, gpt-5.5-nano,
    # gpt-5.4-nano, gpt-5.2-codex, gpt-5.1-codex, gpt-5.1-codex-mini,
    # gpt-5-codex, gpt-5, gpt-5-mini) was rejected with HTTP 400
    # ``"The '<model>' model is not supported when using Codex with a
    # ChatGPT account."``. Re-verify before adding any new entry — the
    # OAuth route's model catalog drifts independently of OpenAI's general
    # model list. Order: cheapest verified → flagship, so the wizard
    # default skews toward cost-conscious picks.
    # Verified against developers.openai.com/codex/{models,cli/features,changelog}
    # via context7 MCP on 2026-04-30. GPT-5.5 is the recommended model;
    # GPT-5.4 is the fallback when 5.5 is unavailable; GPT-5.4-mini for
    # lighter / subagent work; GPT-5.3-Codex-Spark is the ChatGPT Pro
    # research-preview fast-iteration model; GPT-5.3-Codex remains
    # available for users pinned to it.
    "openai_codex": (
        "openai-codex/gpt-5.5",
        "openai-codex/gpt-5.4",
        "openai-codex/gpt-5.4-mini",
        "openai-codex/gpt-5.3-codex-spark",
        "openai-codex/gpt-5.3-codex",
    ),
    "github_copilot": (
        "github-copilot/gpt-4.1",
        "github-copilot/o4-mini",
        "github-copilot/claude-sonnet-4-5",
        "github-copilot/gpt-5",
    ),
    "openai": (
        # Source: Context7 docs for /openai/codex (latest-model.md).
        "gpt-5.5",
        "gpt-5.5-mini",
        "gpt-5.5-nano",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
    ),
    "anthropic": (
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ),
    # MiniMax token-plan models — verified against
    # https://platform.minimax.io/docs/api-reference/text-chat-openai
    # and the Token Plan FAQ (2026-04-29). Both flavors (OpenAI-compat +
    # Anthropic-compat) accept the same underlying ids — the Anthropic
    # flavor is just a different wire format at api.minimax.io/anthropic.
    "minimax": (
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
    ),
    "minimax_anthropic": (
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
    ),
    # Z.ai GLM Coding Plan — verified against docs.z.ai/devpack/faq +
    # docs.z.ai/devpack/overview (2026-04-29). The plan accepts exactly
    # four model ids on api.z.ai/api/coding/paas/v4 (and the Anthropic
    # mirror at api.z.ai/api/anthropic). GLM-5 is exposed only on the Max
    # / Pro tiers; the Lite tier currently runs on GLM-4.7. GLM-5 burns
    # more plan quota than 4.7/4.6/4.5/4.5-air, so order = recommended
    # default first (4.7), then the heavier 5, then legacy fall-backs.
    "zhipu": (
        "glm-4.7",
        "glm-5",
        "glm-4.6",
        "glm-4.5",
        "glm-4.5-air",
    ),
    # Moonshot Kimi — verified against platform.kimi.com/docs/api/chat,
    # platform.kimi.com/docs/guide/agent-support, and the K2.6 quickstart
    # (2026-04-29). The Kimi Coding Plan ships kimi-k2.5 / kimi-k2.6 on
    # the Anthropic-compatible /anthropic endpoint; the OpenAI-compat
    # /v1/chat/completions endpoint also supports the K2 preview ids and
    # the moonshot-v1 legacy line.
    "moonshot": (
        "kimi-k2.6",
        "kimi-k2.5",
        "kimi-k2-thinking",
        "kimi-k2-thinking-turbo",
        "kimi-k2-0905-preview",
        "kimi-k2-turbo-preview",
        "moonshot-v1-128k",
        "moonshot-v1-32k",
        "moonshot-v1-8k",
    ),
    # Kimi (Coding) — same Moonshot backend, but the wizard already
    # surfaces this as a separate provider entry (registry.py:582).
    # Coding-plan-specific ids per platform.kimi.com/docs/guide/agent-support.
    "kimi_coding": (
        "kimi-k2.6",
        "kimi-k2.5",
        "kimi-k2-thinking",
    ),
    # DeepSeek — verified against api-docs.deepseek.com (2026-04-29).
    # Legacy aliases ``deepseek-chat`` and ``deepseek-reasoner`` are
    # being deprecated 2026-07-24 in favor of ``deepseek-v4-flash`` and
    # ``deepseek-v4-pro``. Order: new first, legacy after.
    "deepseek": (
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "deepseek-chat",
        "deepseek-reasoner",
    ),
    # Qwen / DashScope — verified against
    # qwenlm.github.io/qwen-code-docs/en/users/configuration/auth (Coding
    # Plan section, 2026-04-29). The Aliyun Bailian Coding Plan ships
    # qwen3-coder-plus; pay-as-you-go DashScope also exposes the broader
    # qwen3 / qwen-max / qwen-plus / qwen-turbo line.
    "dashscope": (
        "qwen3-coder-plus",
        "qwen3-coder-flash",
        "qwen3-coder-next",
        "qwen-max",
        "qwen-plus",
        "qwen-turbo",
    ),
}


def get_all_models() -> list[str]:
    return []


def find_model_info(model_name: str) -> dict[str, Any] | None:
    return None


def get_model_context_limit(model: str, provider: str = "auto") -> int | None:
    return None


def get_model_suggestions(partial: str, provider: str = "auto", limit: int = 20) -> list[str]:
    """Return up to ``limit`` candidate model ids that match ``partial``.

    Falls back to ``RECOMMENDED_BY_PROVIDER`` while the dynamic catalog is
    offline, so OAuth-only providers don't dead-end the wizard.
    """
    if not provider or provider in {"auto", "skip"}:
        return []
    seeds = RECOMMENDED_BY_PROVIDER.get(provider, ())
    needle = (partial or "").strip().lower()
    if needle:
        seeds = tuple(s for s in seeds if needle in s.lower())
    return list(seeds[:limit])


def format_token_count(tokens: int) -> str:
    """Format token count for display (e.g., 200000 -> '200,000')."""
    return f"{tokens:,}"
